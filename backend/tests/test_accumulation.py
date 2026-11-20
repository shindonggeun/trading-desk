"""#751 accumulation bands by view, the legs of the move, and their past record (desk.accumulation)."""

from datetime import date, timedelta

import pytest

from desk import accumulation, accumulation_bt


def _bars(closes: list[float], vols: list[float] | None = None, wick: float = 0.008) -> list[dict]:
    d0 = date(2024, 1, 1)
    return [{"at": f"{d0 + timedelta(days=i)}T00:00:00+00:00", "open": c, "high": c * (1 + wick), "low": c * (1 - wick),
             "close": c, "volume": (vols[i] if vols else 1000.0)} for i, c in enumerate(closes)]


def _line(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * (i + 1) / n for i in range(n)]


def _box_story() -> list[dict]:
    """A rise, a quiet box (volume drying), a breakout far up, a pullback that stays above the box."""
    rise = _line(70, 100, 40)
    box = [101.5 + (0.5 if i % 2 else -0.5) for i in range(40)]
    run = _line(104, 140, 30)
    back = _line(139, 122, 15)
    vols = [1000.0] * 40 + [1000.0] * 20 + [600.0] * 20 + [1500.0] * 30 + [900.0] * 15
    return _bars(rise + box + run + back, vols)


def test_an_untested_box_below_the_price_is_an_accumulation_band():
    bars = _box_story()
    z = accumulation.zones(bars, "mid")
    band = next(b for b in z["below"] if "box" in b["kinds"])
    assert band["role"] == "accumulation" and band["box"]["drying"] and band["box"]["broke"] == "up"
    assert 95 < band["low"] < 101 and 102 < band["high"] < 104            # the box (and the rise's last bars in its range)
    assert band["distance_pct"] < 0 and band["score"] >= accumulation.MIN_SCORE
    assert "위로 벗어나 다시 오지 않은 자리" in band["basis"][0] and band["basis"][1] == "횡보 중 거래량 감소"


def test_a_box_the_price_came_back_into_is_not_a_candidate_any_more():
    bars = _box_story()
    bars += _bars(_line(121, 101, 10))[:10]                                  # back into the box
    for i, b in enumerate(bars[-10:]):
        b["at"] = f"{date(2024, 1, 1) + timedelta(days=125 + i)}T00:00:00+00:00"
    z = accumulation.zones(bars, "mid")
    assert not any("box" in b["kinds"] for b in z["below"] + z["above"])


def _staircase() -> list[dict]:
    """Three up legs, each shorter and steeper, with 15% pullbacks between (higher lows), the last one over."""
    c = (_line(100, 140, 60) + _line(138, 119, 10) + _line(121, 160, 30) + _line(158, 136, 8) + _line(138, 177, 15)
         + _line(175, 150, 8))
    return _bars(c, wick=0.004)


def test_legs_measure_each_rise_and_say_when_they_get_shorter_and_steeper():
    got = accumulation.legs(_staircase(), 5.0)
    ups = [x for x in got["items"] if x["direction"] == "up"]
    assert [x["days"] for x in ups] == [59, 30, 15]
    assert ups[0]["pct"] == pytest.approx(40.6, abs=1.5) and not ups[-1]["live"] and got["items"][-1]["live"]
    assert got["accelerating"] and "점점 짧고 가팔라져요" in got["sentence"]
    assert got["sentence"].startswith("상승 다리 +")
    downs = [x for x in got["items"] if x["direction"] == "down"]
    assert all(-17 < x["pct"] < -13 for x in downs)


def test_a_rise_just_begun_is_not_a_short_steep_leg():
    c = _line(100, 140, 60) + _line(138, 119, 10) + _line(121, 160, 30) + _line(158, 136, 8) + _line(138, 150, 4)
    got = accumulation.legs(_bars(c, wick=0.004), 5.0)
    assert got["items"][-1]["live"] and got["items"][-1]["direction"] == "up"
    assert not got["accelerating"]                               # +9% in 4 days so far: no leg to compare yet


def test_a_leg_within_a_day_is_told_in_bars():
    x = {"pct": 3.1, "days": 0, "bars": 5}
    assert accumulation._leg_words(x) == "+3.10% · 5봉"


def test_legs_of_a_slowing_rise_are_not_called_accelerating():
    c = _line(100, 130, 20) + _line(128, 110, 10) + _line(112, 150, 60)
    got = accumulation.legs(_bars(c, wick=0.004), 5.0)
    assert not got["accelerating"] and "가팔라" not in (got["sentence"] or "")


def test_fib_levels_and_a_flip_join_a_band():
    bars = _staircase()
    levels = accumulation.fibs(accumulation.clean(bars), 5.0)
    ext = {lv["ratio"]: lv["price"] for lv in levels if lv["name"].startswith("피보 확장")}
    assert ext[0.0] == pytest.approx(bars[0]["low"], rel=1e-6) and ext[1.0] > ext[0.0]
    assert {lv["name"] for lv in levels} >= {"피보 되돌림 0.5", "피보 확장 2"}


def test_the_view_carries_its_record_and_says_reference_only_without_an_edge():
    bars = _box_story()
    no_edge = {"views": {"mid": {"n": 140, "bounce_pct": 58.0, "baseline_pct": 51.0, "verdict": "no_edge",
                                 "horizon_bars": 20}}}
    v = accumulation.view(bars, "mid", table=no_edge)
    assert v["reference_only"] and v["reference_label"] == accumulation.NO_EDGE_KO and not v["small_sample"]
    assert v["sentence"].startswith("중기(일봉): ")
    assert "과거 닿았을 때 20봉 안 반등 58%(섞은 가격 51%, 표본 140)" in v["sentence"] and "참고용" in v["sentence"]
    assert ". 지금보다 −" in v["sentence"]
    edge = {"views": {"mid": {"n": 25, "bounce_pct": 70.0, "baseline_pct": 50.0, "verdict": "edge", "horizon_bars": 20}}}
    v = accumulation.view(bars, "mid", table=edge)
    assert not v["reference_only"] and v["reference_label"] is None and v["small_sample"]
    v = accumulation.view(bars, "mid", table={})
    assert v["reference_only"] and v["reference_label"] == accumulation.NO_STATS_KO and "과거 검증 없음" in v["sentence"]
    four = {"views": {"short": {"n": 900, "bounce_pct": 40.0, "baseline_pct": 42.0, "verdict": "no_edge",
                                "horizon_bars": 20, "timeframe": "240m"}}}
    assert accumulation.odds("short", four, "60m")["stats"] is None           # measured on 4h bars: not these
    assert accumulation.odds("short", four, "240m")["stats"]["n"] == 900


def test_the_bands_are_placed_against_the_price_now():
    bars = _box_story()
    v = accumulation.view(bars, "mid", table={}, price=102.0)                 # fallen back into the box today
    band = next(b for b in v["below"] if "box" in b["kinds"])
    assert band.get("inside") and v["price"] == 102.0 and "지금 구간 안" in v["sentence"]


def test_a_flip_is_an_old_high_closed_through_and_held():
    c = _line(100, 120, 20) + _line(119, 105, 10) + _line(106, 140, 25) + _line(139, 126, 8)
    bars = accumulation.clean(_bars(c, wick=0.004))
    got = accumulation.flips(bars, accumulation.atrs(bars), 3, bars[-1]["close"])
    assert [(round(x["price"]), x["kind"]) for x in got if x["kind"] == "flip"] == [(round(bars[19]["high"]), "flip")]
    lost = accumulation.flips(bars, accumulation.atrs(bars), 3, 110.0)       # the price back under it: no support
    assert not [x for x in lost if x["kind"] == "flip"]


def test_a_box_the_price_fell_out_of_is_supply_above():
    fall = _line(160, 120, 30)
    box = [118.5 + (0.5 if i % 2 else -0.5) for i in range(30)]
    down = _line(115, 100, 15)
    bars = _bars(fall + box + down)
    z = accumulation.zones(bars, "mid")
    band = next(b for b in z["above"] if "box_down" in b["kinds"])
    assert band["role"] == "supply" and band["distance_pct"] > 0 and "아래로 벗어나" in band["basis"][0]
    assert not any("box_down" in b["kinds"] for b in z["below"])


def test_views_split_the_bands_and_the_legs_and_a_short_history_says_so():
    acc, legs = accumulation.views({"short": _bars([100.0] * 10), "mid": _box_story(), "long": None}, table={})
    assert acc["short"]["error"] == "봉이 모자라요" and acc["long"] is None and legs["long"] is None
    assert acc["mid"]["below"] and "legs" not in acc["mid"] and legs["mid"]["items"]
    assert acc["note"] == accumulation.NOTE


def test_the_walk_counts_a_band_once_and_judges_its_bounce():
    bars = _box_story()[:110]                                     # the breakout run, then back to the box and up
    tail = _line(130, 103.6, 12) + _line(104, 125, 25)
    bars += _bars(tail)
    for i, b in enumerate(bars[110:]):
        b["at"] = f"{date(2024, 1, 1) + timedelta(days=110 + i)}T00:00:00+00:00"
    spec = {"interval": "1d", "window": 100, "step": 2, "watch": 40, "react": 20}
    rows = accumulation_bt.walk(bars, "mid", spec)
    # one touch (the band found again at the later steps is the same case), and the price went up from it
    assert [(r["at"][:10], r["outcome"]) for r in rows] == [("2024-05-01", "bounce")]
    assert rows[0]["score"] >= accumulation.MIN_SCORE


def test_the_baseline_keeps_the_bars_but_not_their_order():
    bars = _staircase()
    s = accumulation_bt.shuffled(bars, 1)
    assert len(s) == len(bars) and s[0]["close"] == bars[0]["close"]
    assert s[-1]["close"] == pytest.approx(bars[-1]["close"], rel=1e-9)     # the same returns, reordered
    assert all(b["low"] <= b["close"] <= b["high"] for b in s)


def test_the_verdict_needs_held_out_cases_and_an_edge_over_the_shuffles():
    v = accumulation_bt.verdict({"n": 60, "bounce_pct": 62.0}, {"n": 40, "bounce_pct": 64.0}, {"n": 900, "bounce_pct": 45.0})
    assert v["verdict"] == "edge" and v["edge_pct"] == 19.0
    v = accumulation_bt.verdict({"n": 60, "bounce_pct": 48.0}, {"n": 40, "bounce_pct": 47.0}, {"n": 900, "bounce_pct": 45.0})
    assert v["verdict"] == "no_edge"
    v = accumulation_bt.verdict({"n": 60, "bounce_pct": 40.0}, {"n": 40, "bounce_pct": 64.0}, {"n": 900, "bounce_pct": 45.0})
    assert v["verdict"] == "unstable"
    assert accumulation_bt.verdict({"n": 5}, {"n": 5, "bounce_pct": 80.0}, {"n": 9, "bounce_pct": 40.0})["verdict"] == "unmeasured"


def test_weekly_bars_fold_the_days_by_iso_week():
    days = _bars(_line(100, 110, 14))                         # 2024-01-01 is a Monday: two weeks
    w = accumulation_bt.weekly(days)
    assert len(w) == 2 and w[0]["close"] == days[6]["close"] and w[1]["high"] == max(b["high"] for b in days[7:])
    assert w[0]["volume"] == 7000.0



def _client(db_url, asked: list):
    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.broker import Bar, MockBroker
    from desk.db import make_engine
    from desk.prices import PriceSource
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    story = _box_story()

    def yahoo(ticker, interval, count):
        asked.append((interval, count))
        src = story                                        # one price scale: the read's price places the bands
        return [Bar(b["at"], b["open"], b["high"], b["low"], b["close"], b["volume"]) for b in src][-count:]

    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0),
                                   prices=PriceSource(MockBroker(), yahoo_candles=yahoo)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    return client, (OWNER, PASSWORD)


def test_api_chart_read_has_the_bands_and_legs_by_view(db_url):
    asked: list = []
    client, (user, password) = _client(db_url, asked)
    with client:
        client.post("/api/auth/login", json={"username": user, "password": password})
        body = client.get("/api/chart-read?ticker=GC=F&tf=1h,1d").json()
        acc, legs = body["accumulation"], body["legs"]
        assert acc["note"] == accumulation.NOTE and set(legs) == {"short", "mid", "long"}
        mid = acc["mid"]
        assert mid["timeframe"] == "1d" and any("box" in b["kinds"] for b in mid["below"])
        assert mid["sentence"].startswith("중기(일봉): ") and legs["mid"]["items"]
        assert acc["short"]["timeframe"] == "60m"                           # no 4h asked: the hourly bars
        assert acc["short"].get("stats") is None                            # its record is of 4h bars
        assert ("1w", 261) in asked and [c for i, c in asked if i == "1d"] == [501]   # the daily once
        assert mid["price"] == body["timeframes"][0]["price"]               # placed against the price now
        assert len(next(f for f in body["timeframes"] if f["timeframe"] == "1d")["trendlines"]) >= 0
        only_hours = client.get("/api/chart-read?ticker=GC=F&tf=1h").json()
        assert "accumulation" not in only_hours                             # the full read only


def test_api_chart_read_stands_without_the_bands(db_url, monkeypatch):
    from desk import api as api_mod

    def broken(*a, **k):
        raise RuntimeError("bad bars")

    monkeypatch.setattr(api_mod.accumulation, "views", broken)
    client, (user, password) = _client(db_url, [])
    with client:
        client.post("/api/auth/login", json={"username": user, "password": password})
        got = client.get("/api/chart-read?ticker=GC=F&tf=1h,1d")
        assert got.status_code == 200
        body = got.json()
        assert body["accumulation"] is None and body["legs"] is None and body["timeframes"][0].get("summary")
