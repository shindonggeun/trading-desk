"""#631 the chart alone: per timeframe trend, lines, volume and plain sentences; where they disagree."""

from datetime import timedelta

from desk import chart_read
from tests.test_channels import FIVE, HOURLY


def test_a_timeframe_read_has_its_trend_lines_and_sentence():
    f = chart_read.frame(HOURLY, "60m", live=False)
    assert f["channels"][0]["direction"] == "down"
    above, below = f["levels"]["above"], f["levels"]["below"]
    assert above and below and len(above) <= chart_read.PER_SIDE and len(below) <= chart_read.PER_SIDE
    assert all(x["price"] > f["price"] > y["price"] for x in above for y in below)
    assert above == sorted(above, key=lambda x: x["price"]) and below == sorted(below, key=lambda x: -x["price"])
    assert f["summary"].startswith(f"1시간봉 — {chart_read.TREND_KO[f['trend']['state']]}, 하락 채널 ")
    assert f"위 {above[0]['name']}" in f["summary"] and f"아래 {below[0]['name']}" in f["summary"]


def test_the_trend_needs_enough_bars_and_reads_both_ways():
    assert chart_read.trend_state(HOURLY[:30])["state"] == "sideways"
    rising = [{**b, "close": 100 + i, "high": 101 + i, "low": 99 + i} for i, b in enumerate(HOURLY[:80])]
    assert chart_read.trend_state(rising)["state"] == "up"


def test_lines_close_together_are_one_and_a_daily_gap_is_a_line():
    frame = {"channels": [], "trendlines": [], "patterns": []}
    bars = [{"at": f"t{i}", "open": 89.5, "high": 90, "low": 89, "close": 89.5, "volume": 1} for i in range(30)]
    gaps = [{"fill_price": 103.0, "direction": "down", "formed_at": "2026-11-10"},
            {"fill_price": 103.1, "direction": "down", "formed_at": "2026-11-11"},
            {"fill_price": 96.0, "direction": "up", "formed_at": "2026-11-05"}]
    got = chart_read.levels(frame, bars, "1d", 100.0, 1.0, gaps)
    assert got["above"][0]["price"] == 103.0 and "하락 갭 메움(2026-11-10)" in got["above"][0]["name"]
    assert "2026-11-11" in got["above"][0]["name"]                        # within a quarter ATR: merged
    assert got["below"][0]["name"] == "상승 갭 메움(2026-11-05)"


def test_a_volume_spike_is_said():
    bars = [{"volume": 100.0} for _ in range(25)] + [{"volume": 350.0}]
    assert chart_read.volume(bars) == {"ratio": 3.5, "hot": True}
    assert chart_read.volume([{"volume": 0.0}] * 30) is None


def _frame(tf, state, channel=None, broke=None, fresh=True):
    f = {"timeframe": tf, "trend": {"state": state}, "channels": [], "events": [], "trendlines": []}
    if channel:
        f["channels"] = [{"direction": channel, "state": "inside"}]
    if broke:
        f["trendlines"] = [{"id": "L", "kind": broke, "fresh_break": fresh}]
        f["events"] = [{"kind": "trend_break", "line_id": "L"}]
    return f


def test_timeframes_that_disagree_are_said_once():
    frames = [_frame("5m", "up", broke="resistance"), _frame("15m", "up"), _frame("60m", "down", channel="down"),
              _frame("1d", "down")]
    got = chart_read.conflicts(frames)
    assert got[0] == "5분봉 · 15분봉은 상승 흐름, 1시간봉 · 일봉은 하락 흐름 — 시간봉끼리 엇갈려요."
    assert got[1] == "1시간봉은 하락 채널, 5분봉은 하락추세 이탈 — 짧은 봉이 큰 흐름과 반대로 움직였어요."
    along = chart_read.conflicts([_frame("5m", "down", broke="support"), _frame("60m", "down", channel="down")])
    assert along == ["1시간봉은 하락 채널, 5분봉은 상승추세 이탈 — 짧은 봉도 큰 흐름 쪽으로 꺾였어요."]
    assert chart_read.conflicts([_frame("5m", "sideways"), _frame("60m", "down")]) == []
    # an old break is not news; a channel the price has left is no "big flow"; the latest break counts
    assert chart_read.conflicts([_frame("5m", "down", broke="support", fresh=False),
                                 _frame("60m", "down", channel="down")]) == []
    out = _frame("60m", "down", channel="down")
    out["channels"][0]["state"] = "below"
    assert chart_read.conflicts([_frame("5m", "down", broke="support"), out]) == []
    v = _frame("5m", "sideways", broke="support")
    v["trendlines"].append({"id": "R", "kind": "resistance", "fresh_break": True})
    v["events"].append({"kind": "trend_break", "line_id": "R"})            # the V: the later break up
    assert chart_read.conflicts([v, _frame("60m", "down", channel="down")]) == [
        "1시간봉은 하락 채널, 5분봉은 하락추세 이탈 — 짧은 봉이 큰 흐름과 반대로 움직였어요."]


def test_each_side_keeps_its_own_nearest_lines():
    """A line just above the price merged into one just below it vanished from "above" (review)."""
    frame = {"channels": [], "trendlines": [{"state": "intact", "kind": "support", "end": {"price": 99.8}}],
             "patterns": [{"label": "대칭 삼각", "state": "broke_up", "upper": {"to": {"price": 99.0}},
                           "lower": {"to": {"price": 90.0}}}]}
    bars = [{"at": f"t{i}", "open": 89.5, "high": 90, "low": 89, "close": 89.5, "volume": 1} for i in range(30)]
    gaps = [{"fill_price": 100.2, "direction": "down", "formed_at": "2026-11-10"},
            {"fill_price": 101.5, "direction": "down", "formed_at": "2026-11-01"},
            {"fill_price": 97.6, "direction": "up", "formed_at": "2026-10-01"},
            {"fill_price": 98.0, "direction": "up", "formed_at": "2026-10-02"},
            {"fill_price": 140.0, "direction": "down", "formed_at": "2026-01-02"}]   # 20 ATR away: not near
    got = chart_read.levels(frame, bars, "1d", 100.0, 2.0, gaps)
    assert [x["price"] for x in got["above"]] == [100.2, 101.5]
    assert [x["price"] for x in got["below"]] == [99.8, 99.0, 98.0]      # 97.6 within a quarter ATR of 98.0
    assert "대칭 삼각 윗선(돌파 후)" in got["below"][1]["name"] and "140" not in str(got)
    assert got["below"][2]["refs"] == [{"kind": "gap", "direction": "up", "formed_at": "2026-10-02"},
                                       {"kind": "gap", "direction": "up", "formed_at": "2026-10-01"}]
    assert got["below"][1]["refs"][0] == {"kind": "pattern", "pattern": None, "state": "broke_up", "edge": "upper"}


def test_far_lines_are_kept_apart_and_a_fresh_break_is_a_line():
    """#672 the three nearest a side, the next three folded away; the trendline just broken is near."""
    frame = {"channels": [], "patterns": [],
             "trendlines": [{"id": "T1", "state": "broken", "fresh_break": True, "kind": "support", "end": {"price": 100.5}},
                            {"id": "T2", "state": "broken", "fresh_break": False, "kind": "support", "end": {"price": 100.7}}]}
    bars = [{"at": f"t{i}", "open": 89.5, "high": 90, "low": 89, "close": 89.5, "volume": 1} for i in range(30)]
    gaps = [{"fill_price": 101.0 + i, "direction": "down", "formed_at": f"2026-11-0{i + 1}"} for i in range(7)]
    got = chart_read.levels(frame, bars, "1d", 100.0, 2.0, gaps)
    assert got["above"][0] == {"price": 100.5, "name": "방금 깨진 상승 추세선 · 하락 갭 메움(2026-11-01)",
                               "distance_pct": 0.5, "refs": [
                                   {"kind": "trendline", "id": "T1", "side": "support", "broken": True},
                                   {"kind": "gap", "direction": "down", "formed_at": "2026-11-01"}]}
    assert [x["price"] for x in got["above"]] == [100.5, 102.0, 103.0]
    assert [x["price"] for x in got["far"]["above"]] == [104.0, 105.0, 106.0]
    assert "T2" not in str(got)


def test_the_whole_read_skips_a_failed_timeframe_and_says_it_is_no_advice():
    frames = [chart_read.frame(FIVE, "5m", live=False), {"timeframe": "15m", "error": "HTTPError"},
              chart_read.frame(HOURLY, "60m", live=False)]
    r = chart_read.read("NQ=F", frames)
    assert r["summary"].startswith("5분봉 — ") and r["summary"].endswith("예측 · 추천 아님.")
    assert "1시간봉 — " in r["summary"] and "15분봉" not in r["summary"] and r["note"].endswith("추천도 아니에요.")
    assert r["timeframes"][1] == {"timeframe": "15m", "error": "HTTPError"}


def test_api_chart_read(db_url):
    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.broker import Bar, MockBroker
    from desk.db import make_engine
    from desk.prices import PriceSource
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    asked = []

    def yahoo(ticker, interval, count):
        asked.append(interval)
        if interval == "15m":
            raise RuntimeError("down")
        src = HOURLY if interval in ("60m", "1d", "1w") else FIVE
        return [Bar(b["at"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in src][-count:]

    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0),
                                   prices=PriceSource(MockBroker(), yahoo_candles=yahoo)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        assert client.get("/api/chart-read?ticker=NQ=F").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/chart-read?ticker=NQ=F&tf=3h").status_code == 422
        body = client.get("/api/chart-read?ticker=NQ=F&tf=5m,15m,1h,1d").json()
        assert [f["timeframe"] for f in body["timeframes"]] == ["5m", "15m", "60m", "1d"]
        assert body["timeframes"][1]["error"] == "RuntimeError" and body["summary"].endswith("예측 · 추천 아님.")
        assert "1w" in asked                                              # the weekly gaps beside the daily
        n = len(asked)
        again = client.get("/api/chart-read?ticker=NQ=F&tf=5m,15m,1h,1d").json()
        assert again == body and len(asked) == n                          # one cached answer
