"""#389: daily investor flow paged back into a file, and the direction's flow part at weight 0."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import httpx
import pytest

from desk import direction, flow_history
from desk.broker import toss
from desk.broker.mock import MockBroker
from tests.test_direction import _days

TODAY = date(2026, 11, 13)          # a Friday


class Counting(MockBroker):
    """The mock's paging, counted; ``fail_after`` pages then a broker error (a stopped run)."""

    def __init__(self, fail_after: int | None = None):
        super().__init__()
        self.calls: list[date | None] = []
        self.fail_after = fail_after

    async def investor_flow(self, market, interval, count, until=None):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("connection reset")
        self.calls.append(until)
        return await super().investor_flow(market, interval, count, until=until or TODAY)


async def _no_sleep(_s):
    return None


def _collect(broker, path, start):
    return asyncio.run(flow_history.collect(broker, "KOSPI", start, path, today=TODAY, sleep=_no_sleep,
                                            say=lambda _m: None))


def _weekdays(start: date, end: date) -> list[str]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_it_pages_back_to_the_start_and_leaves_out_today(tmp_path):
    start = TODAY - timedelta(days=400)
    path = tmp_path / "kospi.json"
    data = _collect(Counting(), path, start)
    want = _weekdays(start, TODAY - timedelta(days=1))
    assert sorted(data["rows"]) == want                             # today (provisional) is not kept
    row = data["rows"][want[-1]]
    assert set(row) == {"individual", "foreigner", "institution", "other_corporation"}
    assert sum(row.values()) == 0                                   # the mock's nets net out across classes
    assert flow_history.load(path)["rows"] == data["rows"] and data["market"] == "KOSPI"


def test_a_stopped_run_keeps_its_pages_and_the_next_one_carries_on(tmp_path):
    start = TODAY - timedelta(days=400)
    path = tmp_path / "kospi.json"
    with pytest.raises(RuntimeError):
        _collect(Counting(fail_after=2), path, start)
    kept = sorted(flow_history.load(path)["rows"])
    assert len(kept) == 199                                          # two pages, today's row left out
    again = Counting()
    data = _collect(again, path, start)
    assert sorted(data["rows"]) == _weekdays(start, TODAY - timedelta(days=1))
    # one page for the newest gap (it meets a kept day), then on from below the oldest kept day
    assert again.calls[0] is None and again.calls[1] == date.fromisoformat(kept[0]) - timedelta(days=1)
    assert len(again.calls) == 2                                    # the last 87 days fit one page


def test_the_toss_flow_paging_waits_out_a_429(monkeypatch):
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        pages["n"] += 1
        if pages["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        rec = {"date": "2026-11-12", "updatedAt": "2026-11-12T18:10:00+09:00"}
        for field in ("individual", "foreigner", "institution", "otherCorporation"):
            rec[field] = {"buyAmount": "100", "sellAmount": "40" if field == "foreigner" else "100"}
        rec["institution"]["breakdown"] = {}
        return httpx.Response(200, json={"result": {"records": [rec], "nextUntil": None}})

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(toss.asyncio, "sleep", fake_sleep)
    broker = toss.TossBroker("id", "secret", http=httpx.AsyncClient(base_url=toss.BASE_URL,
                                                                       transport=httpx.MockTransport(handler)))
    records, nxt = asyncio.run(broker.investor_flow("KOSPI", "1d", 100))
    assert slept == [2.0] and nxt is None and records[0].investors["foreigner"] == (100, 40)


def test_the_window_is_the_sessions_before_the_day():
    data = {"rows": {"2026-11-10": {"foreigner": 1}, "2026-11-11": {"foreigner": 2}, "2026-11-12": {"foreigner": 3}}}
    assert flow_history.window(data, "2026-11-12") == [1, 2]          # the day itself is not known that morning
    assert flow_history.window(data, "2026-11-13", n=2) == [2, 3]
    assert flow_history.window(None, "2026-11-13") == []
    assert flow_history.market_of("005930.KS") == "KOSPI" and flow_history.market_of("NVDA") is None


def test_the_flow_part_shows_at_weight_0_and_leaves_the_score_as_it_was():
    daily = _days([100 * 1.004 ** i for i in range(150)], wiggle=0.01)
    plain = direction.score(daily)
    buying = direction.score(daily, flow={"market": "KOSPI", "foreigner": [5e11] * 15 + [-1e11] * 5})
    assert (buying["score"], buying["bias"], buying["turning"]) == (plain["score"], plain["bias"], plain["turning"])
    part = next(p for p in buying["parts"] if p["id"] == "flow")
    assert part["weight"] == 0 and not part["validated"] and part["value"] == pytest.approx(0.88, abs=0.01)
    assert "외국인" in part["text"]
    assert all(p["id"] != "flow" for p in plain["parts"])
    few = direction.score(daily, flow={"market": "KOSPI", "foreigner": [1e11] * (direction.FLOW_MIN - 1)})
    assert all(p["id"] != "flow" for p in few["parts"])               # too few sessions: no part


def test_the_backtest_hands_korean_plans_the_flow_before_their_day(tmp_path, monkeypatch):
    from desk import backtest, playbook
    from tests.test_backtest import _bars, _fetch

    daily = _bars(330)
    first = date.fromisoformat(daily[0]["at"][:10]) - timedelta(days=40)
    rows = {(first + timedelta(days=i)).isoformat(): {"foreigner": 10 ** 9} for i in range(420)}
    path = flow_history.path_for(tmp_path, "KOSPI")
    path.write_text(json.dumps({"version": 1, "market": "KOSPI", "rows": rows}), encoding="utf-8")
    seen: list[tuple[str, dict | None]] = []
    build = playbook.build

    def spy(*a, **kw):
        seen.append((kw.get("now"), kw.get("flow")))
        return build(*a, **kw)

    monkeypatch.setattr(playbook, "build", spy)
    report = backtest.run(["005930.KS", "SOXL"], "swing", fetch=_fetch(daily), vix=None,
                          flow_dir=str(tmp_path), flow_weight=0.5)
    assert direction.WEIGHTS["flow"] == 0.0                                 # put back after the run
    korean = [f for _, f in seen if f is not None]
    assert korean and all(f["market"] == "KOSPI" and len(f["foreigner"]) == flow_history.WINDOW for f in korean)
    assert len(korean) < len(seen)                                          # SOXL plans get none
    assert any("가중치 0.5" in n for n in report.notes)
    assert any("kosdaq.json" in n for n in report.notes)                    # the missing file is named


def test_attach_flow_takes_only_the_sessions_before_the_day(tmp_path):
    from desk import backtest

    rows = {f"2026-11-{d:02d}": {"foreigner": d} for d in range(2, 14)}
    flow_history.path_for(tmp_path, "KOSDAQ").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    snap = backtest.Snapshot("091990.KQ", 0, "", "2026-11-13", "", {}, [], [])
    notes = backtest.attach_flow([snap], str(tmp_path))
    assert snap.flow == {"market": "KOSDAQ", "foreigner": list(range(2, 13))}
    assert not any("모자란" in n for n in notes)                       # 11 sessions are enough
    assert any("kospi.json" in n for n in notes)                         # the other market's file is missing
