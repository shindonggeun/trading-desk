"""#680 the stocks that drove a sector's big day (desk.sector_movers)."""

import asyncio
from datetime import UTC, date, datetime

import pytest

from desk import alert_tone, market_alerts, morning_brief, push, sector_movers

SOX = sector_movers.SECTORS["sox"]
WEIGHT = sum(SOX["members"].values())


def _yahoo(day: str, prev: float, close: float) -> list[dict]:
    """Yahoo's US daily bars: stamped at New York's midnight (05:00 UTC in winter)."""
    return [{"at": "2026-11-18T05:00:00+00:00", "close": prev}, {"at": f"{day}T05:00:00+00:00", "close": close}]


def _toss(day: str, prev: float, close: float) -> list[dict]:
    """The broker's daily bars: stamped at UTC midnight of the date."""
    return [{"at": "2026-11-18T00:00:00+00:00", "close": prev}, {"at": f"{day}T00:00:00+00:00", "close": close}]


def _bars(index_ret: float, moves: dict[str, float], day: str = "2026-11-19") -> dict[str, list[dict]]:
    out = {"^SOX": _yahoo(day, 100.0, 100.0 * (1 + index_ret))}
    for t in SOX["members"]:
        out[t] = _toss(day, 100.0, 100.0 * (1 + moves.get(t, 0.0)))
    return out


def test_the_culprits_are_weight_times_return_with_their_causes():
    bars = _bars(-0.034, {"AMD": -0.10, "NVDA": -0.03, "MU": -0.06, "AVGO": 0.01})
    got = sector_movers.movers("sox", bars, {"AMD": "전날 실적 발표"})
    assert got["date"] == "2026-11-19" and got["big"] and got["move_pct"] == -3.4 and got["index_pct"] == -3.4
    assert [r["ticker"] for r in got["down"]] == ["AMD", "NVDA", "MU"]
    amd = got["down"][0]
    assert amd["contribution_pp"] == pytest.approx(8.0 / WEIGHT * -10, abs=1e-3) and amd["return_pct"] == -10.0
    assert [r["ticker"] for r in got["up"]] == ["AVGO"]
    assert got["sentence"].startswith("미국 반도체 급락(−3.4%) 주범: AMD −10.0%(기여 −0.86%p · 전날 실적 발표)")
    assert "AMD −10.0%(기여 −0.8" in got["sentence"] and "전날 실적 발표" in got["sentence"]
    assert "NVDA −3.0%" in got["sentence"] and "원인 미확인" in got["sentence"]
    assert len(got["members"]) == len(SOX["members"])          # Yahoo's and the broker's dates agree
    assert all("cause" not in r for r in got["members"])           # the members' rows keep one shape


def test_a_big_move_with_no_one_to_name_is_no_big_day():
    got = sector_movers.movers("sox", _bars(-0.03, {t: 0.01 for t in SOX["members"]}))
    assert not got["big"] and got["sentence"] is None and sector_movers.alerts(got) == []


def test_a_quiet_day_names_no_one_and_a_jump_names_its_drivers():
    quiet = sector_movers.movers("sox", _bars(-0.012, {"AMD": -0.05}))
    assert not quiet["big"] and quiet["sentence"] is None and quiet["down"][0]["ticker"] == "AMD"
    jump = sector_movers.movers("sox", _bars(0.025, {"NVDA": 0.06, "AMD": 0.04}))
    assert jump["sentence"].startswith("미국 반도체 급등(+2.5%) 주역: NVDA +6.0%")


def test_a_member_whose_last_bar_is_another_day_is_left_out():
    bars = _bars(-0.03, {"AMD": -0.1})
    bars["AMD"] = _toss("2026-11-17", 100.0, 90.0)                    # a stale answer
    got = sector_movers.movers("sox", bars)
    assert "AMD" not in [r["ticker"] for r in got["members"]]


def test_causes_are_an_earnings_report_that_moved_this_session():
    rows = {"2026-11-18": [{"ticker": "NVDA", "when": "after"}, {"ticker": "XOM"}, {"ticker": "MU", "when": "pre"}],
            "2026-11-19": [{"ticker": "AMD", "when": "pre"}, {"ticker": "INTC", "when": "after"},
                           {"ticker": "TXN"}]}
    got = sector_movers.causes_from_earnings(rows, {"NVDA", "AMD", "MU", "INTC", "TXN"}, date(2026, 11, 19))
    # MU's report before yesterday's open moved yesterday; INTC's after today's close has not moved it yet
    assert got == {"NVDA": "전날 실적 발표", "AMD": "실적 발표", "TXN": "실적 발표"}


def test_a_big_day_is_one_alert_a_session_with_its_tone_and_words():
    m = sector_movers.movers("sox", _bars(-0.034, {"AMD": -0.10}))
    first = sector_movers.alerts(m, datetime(2026, 11, 19, 14, 45, tzinfo=UTC))[0]     # 23:45 KST · 09:45 ET
    assert (first["kind"], first["rule"], first["market"], first["severity"]) == ("sector_mover", "sector_drop", "sox", "strong")
    later = sector_movers.alerts(m, datetime(2026, 11, 19, 15, 30, tzinfo=UTC))[0]     # 00:30 KST: the same session
    assert not market_alerts.admit(later, [], [first])
    next_day = sector_movers.alerts(m, datetime(2026, 11, 20, 14, 45, tzinfo=UTC))[0]
    assert market_alerts.admit(next_day, [], [first])
    assert alert_tone.tone(first) == "warning"
    words = market_alerts.text(first)
    assert words.startswith("🔴 경고 · 미국 반도체 급락(−3.4%) 주범: AMD") and "원인부터 확인" in words
    jump = sector_movers.alerts(sector_movers.movers("sox", _bars(0.022, {"NVDA": 0.05})))[0]
    assert alert_tone.tone(jump) == "positive" and jump["severity"] == "caution"
    assert sector_movers.alerts(sector_movers.movers("sox", _bars(-0.01, {}))) == []


class _Prices:
    def __init__(self, bars):
        self.bars, self.asked = bars, []

    async def candles(self, ticker, interval, count):
        self.asked.append(ticker)
        if ticker not in self.bars:
            raise ConnectionError("down")
        return {"bars": self.bars[ticker]}


NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)                  # 10:00 ET on the bars' day


def test_a_quiet_index_costs_one_request_and_a_big_one_reads_the_members():
    quiet = _Prices(_bars(-0.01, {}))
    assert asyncio.run(sector_movers.big_day("sox", quiet, now=NOW)) is None and quiet.asked == ["^SOX"]
    bars = _bars(-0.03, {"AMD": -0.1})
    del bars["MU"]                                                   # one member failing leaves the rest
    big = _Prices(bars)
    got = asyncio.run(sector_movers.big_day("sox", big, lambda tickers, day: {"AMD": "실적 발표"}, now=NOW))
    assert got["down"][0]["ticker"] == "AMD" and got["down"][0]["cause"] == "실적 발표"
    assert "MU" not in [r["ticker"] for r in got["members"]]


def test_the_last_sessions_bar_or_a_side_already_said_reads_no_members():
    stale = _Prices(_bars(-0.03, {"AMD": -0.1}))          # a holiday the calendar missed: Thursday's bar on Friday
    assert asyncio.run(sector_movers.big_day("sox", stale, now=datetime(2026, 11, 20, 15, 0, tzinfo=UTC))) is None
    said = _Prices(_bars(-0.03, {"AMD": -0.1}))
    assert asyncio.run(sector_movers.big_day("sox", said, now=NOW, said={("sox", "2026-11-19", "sector_drop")})) is None
    assert stale.asked == said.asked == ["^SOX"]
    other_side = _Prices(_bars(0.03, {"NVDA": 0.1}))
    assert asyncio.run(sector_movers.big_day("sox", other_side, now=NOW, said={("sox", "2026-11-19", "sector_drop")}))


def test_a_korean_sector_reads_its_own_date():
    def kr(prev, close):          # Yahoo's .KS bars at Seoul's midnight, the broker's at UTC midnight
        return [{"at": "2026-11-18T15:00:00+00:00", "close": prev}, {"at": "2026-11-19T15:00:00+00:00", "close": close}]
    sec = sector_movers.SECTORS["kr_semi"]
    bars = {sec["index"]: kr(100, 97)}
    bars |= {t: _toss("2026-11-20", 100, 90 if t == "000660.KS" else 99) for t in sec["members"]}
    got = sector_movers.movers("kr_semi", bars)
    assert got["date"] == "2026-11-20" and got["down"][0]["ticker"] == "000660.KS" and len(got["members"]) == 10


def test_the_morning_brief_says_last_nights_culprits():
    m = sector_movers.movers("sox", _bars(-0.034, {"AMD": -0.10}))
    small = sector_movers.movers("ndx", {"^NDX": _yahoo("2026-11-19", 100, 97.9),
                                         **{t: _toss("2026-11-19", 100, 90) for t in ("NVDA", "MSFT")}})
    raw = {"now": "2026-11-19T22:00:00+00:00", "missing": [], "bars": {}, "movers": [small, m]}   # 07:00 KST 11-20
    b = morning_brief.bundle(raw)
    assert b["movers"][1] == {"label": "미국 반도체", "sentence": m["sentence"], "move_pct": -3.4, "falling": True}
    brief = morning_brief.rules(b)
    said = [line for line in brief["bad"] if line.startswith("간밤 ")]
    assert said == ["간밤 " + m["sentence"]]                       # the bigger day of the side only
    allowed = morning_brief.allowed_numbers(b)
    assert {"-3.4%", "-10.0%"} <= allowed.subjects["미국 반도체"]   # the sentence's numbers with their signs
    assert "나스닥100" in allowed.subjects and "나스닥" not in allowed.subjects   # no other subject's name
    assert allowed.sentence_ok("간밤 미국 반도체가 -3.4% 내렸어요.") and allowed.sentence_ok("AMD가 -10.0% 떨어졌어요.")
    assert allowed.sentence_ok("간밤 미국 반도체가 −3.4% 내렸어요.")
    assert not allowed.sentence_ok("간밤 미국 반도체가 3.4% 올랐어요.")
    assert not allowed.sentence_ok("간밤 미국 반도체가 +3.4% 올랐어요.")
    assert not allowed.sentence_ok("AMD가 10.0% 올랐어요.")
    old = {**m, "date": "2026-11-10"}
    assert morning_brief.bundle({**raw, "movers": [old]})["movers"] == []


def test_api_sector_movers(db_url, monkeypatch):
    from tests.test_api import _client

    async def read(key, prices, causes=None, name_of=None, day=None):
        return {"sector": key, "big": False, "sentence": None}

    monkeypatch.setattr(sector_movers, "read", read)
    with _client(db_url) as client:
        got = client.get("/api/sectors/movers", params={"sector": "sox"})
        assert got.status_code == 200 and got.json()["sector"] == "sox"
        assert client.get("/api/sectors/movers", params={"sector": "nope"}).status_code == 422
        assert client.get("/api/sectors").status_code == 200                # the user's own sectors still answer



def test_a_big_day_is_on_the_timeline_and_its_push_opens_the_sectors_page(db_url):
    # #740 the timeline shows the kind beside the others (a holding's alert: the owner's, #756)
    import json

    from fastapi.testclient import TestClient

    from tests.test_holding_watch import _hold
    from tests.test_push import OWNER, _app, _login, _pusher, _Sent

    app = _app(db_url)
    with TestClient(app, base_url="https://testserver") as client:
        _login(client, OWNER)
        engine = app.state.market_alerts.engine
        push.PushStore(engine).subscribe(1, "https://fcm.googleapis.com/fcm/send/abc", {"p256dh": "k", "auth": "a"}, "폰", {})
        sent = _Sent()
        alerts = market_alerts.MarketAlerts(engine, _pusher(engine, sent))
        m = sector_movers.movers("sox", _bars(-0.034, {"AMD": -0.10}))
        alerts.dispatch(sector_movers.alerts(m, datetime(2026, 11, 19, 14, 45, tzinfo=UTC)))
        alerts.dispatch([{**_hold("hold_trend_down", "2026-11-19T23:50:00+09:00"), "stop": 95.0, "price": 96.0}])
        body = client.get("/api/alerts/today?date=2026-11-19").json()
        assert [(a["kind"], a["rule"], a["market"]) for a in body["items"]] == [
            ("sector_mover", "sector_drop", "sox"), ("holding_alert", "hold_trend_down", "AAA")]   # by time
        assert body["items"][0]["tone"] == "warning"
        assert body["items"][0]["text"].startswith("🔴 경고 · 미국 반도체 급락(−3.4%) 주범: AMD")
        assert [a["kind"] for a in alerts.list("2026-11-19")] == ["sector_mover", "holding_alert"]
        payload = json.loads(next(d for _, d in sent.calls if json.loads(d)["kind"] == "sector_mover"))
        assert payload["url"] == "/#/sectors?movers"
