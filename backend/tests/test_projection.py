"""#299: the chart's look ahead - a calibrated cone, paths through the desk's levels, a checked note."""

import asyncio
import itertools
import math
from contextlib import asynccontextmanager
from datetime import date, timedelta

from desk import projection

SR = {"reference_price": 100.0, "levels": [
    {"price": 110.0, "kind": "resistance", "basis": "매물대 POC · 주봉 고점", "strength": 2.0},
    {"price": 120.0, "kind": "resistance", "basis": "52주 고점"},
    {"price": 100.5, "kind": "resistance", "basis": "바로 위"},          # too near: noise
    {"price": 92.0, "kind": "support", "basis": "스윙 저점"},
    {"price": 85.0, "kind": "support", "basis": "200일선"}]}


def _bars(n=120, vol=0.02, start=date(2026, 1, 1)):
    out, c = [], 100.0
    for i in range(n):
        c *= math.exp(vol if i % 2 else -vol)                       # +-2% a bar: sigma about 0.02
        out.append({"at": (start + timedelta(days=i)).isoformat() + "T05:00:00+00:00",
                    "open": c, "high": c * 1.01, "low": c * 0.99, "close": c})
    out[-1]["close"] = 100.0
    return out


def test_sigma_and_a_cone_that_widens_with_the_square_root_of_time():
    sig = projection.sigma(_bars())
    assert 0.018 < sig < 0.045
    assert projection.sigma(_bars(10)) is None
    cone = projection.cone(100.0, 0.02, 20)
    assert len(cone) == 20 and cone[0]["p16"] < 100 < cone[0]["p84"]
    w = lambda c: math.log(c["p84"] / c["p16"])
    assert abs(w(cone[3]) / w(cone[0]) - 2.0) < 0.05                 # 4 bars: twice one bar's width
    assert cone[-1]["p2_5"] < cone[-1]["p16"] and cone[-1]["p97_5"] > cone[-1]["p84"]


def test_first_touch_is_a_fair_walk_and_a_missing_side_is_never_met():
    even = projection.first_touch(100.0, 110.0, 100 / 1.1, 0.02, 60, sims=1500)
    assert abs(even["up"] - even["down"]) < 0.08 and abs(even["up"] + even["down"] + even["none"] - 1) < 0.005
    near_up = projection.first_touch(100.0, 103.0, 85.0, 0.02, 20, sims=1500)
    assert near_up["up"] > 0.6 and near_up["bars_up"] < near_up["bars_down"]
    only_up = projection.first_touch(100.0, 110.0, None, 0.02, 20, sims=500)
    assert only_up["down"] == 0 and only_up["bars_down"] is None


def test_paths_go_through_the_levels_and_skip_the_ones_too_near(monkeypatch):
    monkeypatch.setattr(projection, "_cal", {"frames": {"1d": {"mult68": 1.0, "mult95": 1.0, "coverage68_valid": 67.5}}})
    p = projection.project(SR, _bars(), "1d")
    names = {x["name"]: x for x in p["paths"]}
    assert set(names) == {"bull", "bear", "range"}
    bull = names["bull"]["points"]
    assert [pt["price"] for pt in bull] == [100.0, 110.0, 120.0] and bull[1]["why"] == "매물대 POC"
    assert all(a["bar"] < b["bar"] for a, b in itertools.pairwise(bull))
    assert [pt["price"] for pt in names["bear"]["points"]][:2] == [100.0, 92.0]
    assert abs(sum(x["prob"] for x in p["paths"]) - 1) < 0.01
    assert p["levels"]["above"][0]["price"] == 110.0                       # 100.5 is a bar's noise away
    assert "67.5%" in p["note_ko"] and "방향 예측이 아니라 확률 범위" in p["note_ko"]


def test_a_converging_pattern_adds_its_lines_as_levels_and_no_bars_means_no_drawing():
    sr = {"levels": [], "patterns": [{"state": "converging", "label": "대칭 삼각",
                                     "upper": {"to": {"price": 112.0}}, "lower": {"to": {"price": 90.0}}}]}
    p = projection.project(sr, _bars(), "1d")
    assert p["levels"]["above"][0]["why"] == "대칭 삼각 윗선" and p["levels"]["below"][0]["why"] == "대칭 삼각 아랫선"
    empty = projection.project(SR, _bars(5), "1d")
    assert empty["cone"] == [] and empty["paths"] == [] and "모자라" in empty["note_ko"]


def test_a_model_note_with_a_number_the_desk_did_not_give_is_dropped():
    p = projection.project(SR, _bars(), "1d")
    good = f"위 {p['levels']['above'][0]['price']} 저항에 먼저 닿는 경로가 {round(100 * p['paths'][0]['prob'])}%입니다."
    assert projection.checked(good, p) == good
    assert projection.checked("목표가는 137.5 입니다.", p) is None
    assert projection.checked("20봉 안에 5% 하락합니다.", p) is None                  # a made-up percentage
    assert projection.checked(f"{round(100 * p['paths'][0]['prob'])} -20% 하락", p) is None  # a sign
    assert projection.checked("2번 경로", p) is None                                  # a bar index is no number to quote
    assert projection.checked("x" * 700, p) is None and projection.checked(None, p) is None
    assert "데스크 요약" in projection.build_prompt(p) and projection.fake_note(projection.build_prompt(p)) == p["note_ko"]


def test_projections_cache_and_fill_the_note_only_with_a_free_slot():
    calls = {"levels": 0}

    async def levels():
        calls["levels"] += 1
        return SR

    async def bars(_levels):
        return _bars(), "1d"

    @asynccontextmanager
    async def free():
        yield True

    @asynccontextmanager
    async def busy():
        yield False

    async def run(slot, note):
        svc = projection.Projections(note, clock=lambda: 0.0)
        first = await svc.get("T", "swing", 300.0, levels, bars, slot=slot)
        await asyncio.sleep(0.05)
        await asyncio.gather(*svc._tasks)
        return first, await svc.get("T", "swing", 300.0, levels, bars, slot=slot)

    first, again = asyncio.run(run(free, projection.fake_note))
    assert first["note_pending"] and again["note_source"] == "ai" and not again["note_pending"]
    assert calls["levels"] == 1                                            # the second read came from the cache
    _, kept = asyncio.run(run(busy, projection.fake_note))
    assert kept["note_source"] == "desk"
    _, wrong = asyncio.run(run(free, lambda prompt: "목표가 999.99 확실"))
    assert wrong["note_source"] == "desk"


def test_the_endpoint_needs_a_session_and_a_known_horizon(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/projection?ticker=NVDA").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/projection?ticker=NVDA&horizon=weekly").status_code == 422
        assert client.get("/api/projection?ticker=NV%20DA").status_code == 422


def test_prices_from_1000_up_read_with_separators_on_both_sides():
    sr = {"levels": [{"price": 210000.0, "kind": "resistance", "basis": "매물대"},
                     {"price": 180000.0, "kind": "support", "basis": "스윙 저점"}]}
    bars = _bars()
    for b in bars:
        for k in ("open", "high", "low", "close"):
            b[k] *= 2000
    p = projection.project(sr, bars, "1d")
    assert "210,000(매물대)" in p["note_ko"] and ".0" not in p["note_ko"]
    for written in ("210,000원", "210000원", "210000.0"):
        assert projection.checked(f"위 {written} 저항", p), written
    assert projection.fmt(1234.5) == "1,234" and projection.fmt(12.5) == "12.5" and projection.fmt(12.0) == "12"


def test_the_cache_follows_the_horizon_ttl_and_short_horizons_get_no_model_note():
    now = [0.0]
    calls = {"n": 0}

    async def levels():
        calls["n"] += 1
        return SR

    async def bars(_levels):
        return _bars(), "1d"

    async def run():
        svc = projection.Projections(projection.fake_note, clock=lambda: now[0])
        quick = await svc.get("T", "scalp", 30.0, levels, bars, slot=None)
        now[0] = 31.0
        await svc.get("T", "scalp", 30.0, levels, bars, slot=None)
        return quick

    quick = asyncio.run(run())
    assert calls["n"] == 2 and not quick["note_pending"] and quick["note_source"] == "desk"


def test_the_endpoint_answers_on_fake_bars_and_says_when_the_levels_fell_back(db_url):
    from desk.prices import PriceSource
    from tests.test_api import OWNER, PASSWORD
    from tests.test_prices import _client
    from tests.test_sr import _Bars, _source

    source, _ = _source()
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/projection?ticker=NVDA").json()
        assert body["timeframe"] == "1d" and body["cone"] and body["horizon"] == "swing"
        assert body["fallback"] is None and "방향 예측이 아니라" in body["note_ko"]
        scalp = client.get("/api/projection?ticker=NVDA&horizon=scalp")
        assert scalp.status_code == 200
        got = scalp.json()
        # minute bars this fake source lacks: the levels fall back to the swing frame and so does the cone
        assert (got["fallback"] is not None) == (got["timeframe"] == "1d") and got["horizon"] == "scalp"
    assert isinstance(source, PriceSource) and _Bars
