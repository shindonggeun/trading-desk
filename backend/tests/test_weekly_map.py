from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from desk import weekly_map as wm

MONDAY = datetime(2018, 1, 1, tzinfo=UTC)


def _weeks(closes: list[float], start=MONDAY, wick: float = 0.5) -> list[dict]:
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append({"at": (start + timedelta(weeks=i)).isoformat(), "open": o, "close": c,
                    "high": max(o, c) + wick, "low": min(o, c) - wick})
    return out


def _rise(n: int, start: float = 100.0, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(n)]


def test_a_map_reads_only_the_bars_up_to_its_week():
    closes = _rise(90) + [175, 160, 150, 140, 150, 165, 180, 190]
    bars = _weeks(closes)
    for i in (70, 85, len(bars) - 3):
        past = wm.build("SOXX", bars, i)
        cut = wm.build("SOXX", bars[:i + 1], today=date(2100, 1, 1))
        assert {k: v for k, v in past.items() if k != "week_complete"} == \
            {k: v for k, v in cut.items() if k != "week_complete"}
    assert wm.build("SOXX", bars[:wm.RED - 1]) is None


def test_lines_position_and_a_break_of_the_black_line():
    closes = _rise(80) + [165]                         # far above, then a week that closes under the 20-week line
    m = wm.build("SOXX", _weeks(closes), today=date(2100, 1, 1))
    black, red = m["lines"]["black"]["price"], m["lines"]["red"]["price"]
    assert m["cross"] == "broke_black" and m["position"] == "between" and red < m["close"] < black
    assert m["lines"]["red"]["pct"] < 0 and m["scenario"]["down"]["price"] == red and m["scenario"]["up"]["price"] == black
    assert m["candle"]["kind"] == "long_black" and m["candle"]["label"] == "장대음봉"
    assert "검정선을 깨고 마감" in m["text"]["headline"] and len(m["text"]["checklist"]) == 4
    up = wm.build("SOXX", _weeks(closes + [200]), today=date(2100, 1, 1))
    assert up["cross"] == "reclaimed_black" and up["position"] == "above_black"
    assert up["scenario"]["down"]["price"] == up["lines"]["red"]["price"]


def test_the_record_counts_reaches_v_exceptions_and_pending_breaks():
    # a break that falls to the red line, a V that takes the black line straight back, and a fresh break
    reach = _rise(80) + [165, 160, 155, 150, 145] + _rise(40, 145, 3)     # breaks, falls to the 60-week line
    v = [reach[-1] + 3 * i for i in range(1, 30)]
    v_break = v[-1] - 35                               # one week under the 20-week line ...
    v_back = [v[-1] + 3 * i for i in range(1, 40)]     # ... then straight back above it
    closes = reach + v + [v_break] + v_back + [v_back[-1] - 45]
    bars = _weeks(closes)
    rec = wm.build("SOXX", bars, today=date(2100, 1, 1))["record"]["black_break"]
    assert (rec["n"], rec["reached_red"], rec["rate"], rec["median_weeks"]) == (2, 1, 0.5, 3)
    assert rec["v_exceptions"] == [bars[len(reach) + len(v)]["at"][:10]]
    assert rec["pending"] == 1                         # the last break: its outcome was not known yet


def test_a_lower_band_touch_is_counted_once_and_judged_weeks_later():
    flat = [100 + (1 if i % 2 else -1) for i in range(70)]
    closes = flat + [80] + [85 + i for i in range(20)]     # a plunge through the lower band, then a recovery
    m = wm.build("SOXX", _weeks(closes), today=date(2100, 1, 1))
    touch = m["record"]["bb_lower_touch"]
    assert touch["n"] == 1 and touch["rebounded"] == 1 and touch["rate"] == 1.0   # the weeks hugging the band: one touch
    early = wm.build("SOXX", _weeks(closes), 75)["record"]["bb_lower_touch"]
    assert early["n"] == 0 and early["pending"] == 1           # 13 weeks had not passed yet
    assert "13주 뒤 더 높았다" in " ".join(m["text"]["lines"])


def test_candles_in_words():
    base = _weeks(_rise(30))
    bar = lambda o, h, low, c: base[:-1] + [{**base[-1], "open": o, "high": h, "low": low, "close": c}]
    assert wm.candle(bar(130, 131, 121, 122), 29)["kind"] == "long_black"
    assert wm.candle(bar(120, 131, 119, 130), 29)["kind"] == "long_white"
    assert wm.candle(bar(128, 129, 118, 128.5), 29)["kind"] == "lower_wick"
    assert wm.candle(bar(128, 138, 127.5, 128.2), 29)["kind"] == "upper_wick"
    assert wm.candle(bar(128, 129, 127.5, 128.6), 29)["kind"] == "plain"


def test_the_forming_week_is_marked():
    assert wm.week_complete("2026-11-02T04:00:00+00:00", today=date(2026, 11, 7)) is True     # Saturday
    assert wm.week_complete("2026-11-02T04:00:00+00:00", today=date(2026, 11, 5)) is False    # Thursday


def _bars_obj(closes):
    return [SimpleNamespace(**b) for b in _weeks(closes)]


def test_maps_fall_back_cache_and_keep_the_others_when_one_fails():
    calls = []

    def candles(symbol, interval, count):
        calls.append(symbol)
        if symbol in ("NQ=F", "^KS200"):
            return []                                  # no bars: the fallback stands in
        if symbol == "QQQ":
            raise RuntimeError("down")
        return _bars_obj(_rise(90))

    maps = wm.Maps(candles=candles)
    out = maps.all()["maps"]
    by = {m["symbol"]: m for m in out}
    assert [m["symbol"] for m in out] == [s for s, _n, _f in wm.TARGETS]
    assert by["NQ=F"]["source_symbol"] == "^NDX" and by["^KS200"]["source_symbol"] == "069500.KS"
    assert "error" in by["QQQ"] and by["SOXX"]["position"] == "above_black"
    n = len(calls)
    maps.one("SOXX")
    assert len(calls) == n                              # cached
    hist = maps.history("SOXX", 3)
    assert [h["week"] for h in hist] == sorted([h["week"] for h in hist], reverse=True) and len(hist) == 3


def test_the_note_names_the_index_behind_a_ticker():
    maps = wm.Maps(candles=lambda s, i, c: _bars_obj(_rise(80) + [170]))
    note = maps.note_for("soxl")
    assert note.startswith("Weekly map of SOXX (the index behind SOXL") and "a long down candle" in note
    assert maps.note_for("NVDA").startswith("Weekly map of SOXX") and maps.note_for("AAPL") is None
    assert "Weekly map of ^KS200" in maps.note_for("122630.KS")


def test_the_api_needs_a_session_and_validates_the_symbol(db_url):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    maps = wm.Maps(candles=lambda s, i, c: _bars_obj(_rise(90)))

    client = TestClient(api.create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), weekly=maps),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        assert client.get("/api/market/weekly").status_code == 401
        assert client.get("/api/market/weekly/SOXX/history").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/market/weekly").json()
        assert len(body["maps"]) == len(wm.TARGETS) and body["maps"][0]["text"]["headline"]
        assert client.get("/api/market/weekly", params={"symbol": "SOXX"}).json()["maps"][0]["symbol"] == "SOXX"
        assert client.get("/api/market/weekly", params={"symbol": "AAPL"}).status_code == 422
        hist = client.get("/api/market/weekly/SOXX/history", params={"weeks": 4}).json()
        assert hist["symbol"] == "SOXX" and len(hist["weeks"]) == 4
        assert client.get("/api/market/weekly/AAPL/history").status_code == 422


def test_a_leveraged_same_day_run_hands_the_index_map_to_the_agents(db_url, monkeypatch):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk import api
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    monkeypatch.setattr(api, "collect_related", lambda ticker, day: None)
    seen = []
    fake = make_fake_runner(0)

    def graph(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker) if portfolio else "")
        return fake(request, emit, portfolio)

    maps = wm.Maps(candles=lambda s, i, c: _bars_obj(_rise(80) + [170]))
    client = TestClient(api.create_app(Settings(database_url=db_url, fake_delay=0), runner=graph, weekly=maps),
                        base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        for ticker in ("SOXL", "AAPL"):
            run = client.post("/api/runs", json={"ticker": ticker, "trade_date": date.today().isoformat()}).json()
            assert _wait_done(client, run["id"])["status"] == "done"
    assert "Weekly map of SOXX (the index behind SOXL" in seen[0] and "Weekly map" not in seen[1]


@pytest.mark.parametrize("symbol", [s for s, _n, _f in wm.TARGETS])
def test_every_target_has_a_name(symbol):
    assert wm.NAMES[symbol]


def test_history_is_newest_first_and_short_series_give_none():
    bars = _weeks(_rise(90))
    hist = wm.history("SOXX", bars, 20)
    assert hist[0]["week"] == bars[-1]["at"][:10] and all(h["headline"] for h in hist)
    assert len(hist) == 20
    assert wm.history("SOXX", _weeks(_rise(wm.RED + 2)), 20)[-1]["week"] == _weeks(_rise(wm.RED))[-1]["at"][:10]


def test_under_the_lower_band_the_text_does_not_call_it_a_floor():
    closes = [100 + (1 if i % 2 else -1) for i in range(70)] + [70]      # a plunge through the band and the 52w low
    m = wm.build("SOXX", _weeks(closes), today=date(2100, 1, 1))
    assert m["position"] == "below_red" and m["bollinger"]["lower"] > m["close"]
    head = m["text"]["headline"]
    assert "볼린저 하단" in head and "보다도 아래" in head and "다음 바닥 후보 — 50주" not in head

