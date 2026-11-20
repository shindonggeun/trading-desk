"""#696 one run, every horizon: the deterministic map of the four horizons, the instrument's class and the
horizon the regime suits."""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from desk import all_horizons


@pytest.mark.parametrize(("ticker", "cid", "risk"), [
    ("TQQQ", "leverage", "공격"), ("SOXL", "leverage", "공격"), ("SQQQ", "inverse", "공격 · 하락 베팅"),
    ("122630.KS", "leverage", "공격"), ("TLT", "bond", "안전자산 대피"), ("148070.KS", "bond", "안전자산 대피"),
    ("XLU", "defensive", "방어"), ("ko", "defensive", "방어"), ("AAPL", "general", "보통"),
])
def test_the_instrument_class_says_the_risk(ticker, cid, risk):
    got = all_horizons.instrument_class(ticker)
    assert (got["id"], got["risk"]) == (cid, risk) and got["basis"]
    if cid in ("leverage", "inverse"):
        assert got["underlying"] and (got["multiple"] < 0) == (cid == "inverse")


def _view(regime, box=None, adx=30.0):
    return {"regime": regime, "summary": "일봉 " + regime, "frames": {"1d": {"regime": regime, "adx": adx, "box": box,
                                                                           "basis": ["근거"], "as_of": "x"}}}


def test_a_range_suits_scalp_and_intraday_and_a_trend_the_swings():
    general = all_horizons.instrument_class("AAPL")
    box = {"high": 110, "low": 100, "touches_high": 3, "touches_low": 2, "width_pct": 10.0}
    fit = all_horizons.horizon_fit(_view("range", box, 15.0), general, 2.0)
    assert fit["best"] == ["scalp", "intraday"] and fit["fit"]["swing"] == "poor" and fit["fit"]["short_swing"] == "ok"
    assert "박스권(폭 10.0%" in fit["sentence"] and "스캘핑" in fit["sentence"]
    up = all_horizons.horizon_fit(_view("trend_up"), general, 2.0)
    assert up["best"] == ["short_swing", "swing"] and "상승 추세(ADX 30.0)" in up["sentence"]
    unclear = all_horizons.horizon_fit(_view("unknown"), general, None)
    assert unclear["best"] == [] and set(unclear["fit"].values()) == {"unknown"} and "뚜렷하지 않아요" in unclear["sentence"]


def test_the_instrument_and_the_volatility_add_their_note():
    lev = all_horizons.horizon_fit(_view("trend_down"), all_horizons.instrument_class("TQQQ"), 9.2)
    assert "변동성 끌림" in lev["sentence"] and "일봉 ATR 9.2%" in lev["sentence"]
    bond = all_horizons.horizon_fit(_view("turning"), all_horizons.instrument_class("TLT"), 0.8)
    assert "채권 · 국채 ETF 는 움직임이 느려" in bond["sentence"] and "ATR" not in bond["sentence"]


def test_an_older_run_gets_no_horizons_but_its_class():
    got = asyncio.run(all_horizons.build(object(), "TQQQ", {}, "swing", None, same_day=False))
    assert got["horizons"] is None and got["horizon_fit"] is None and got["instrument_class"]["id"] == "leverage"
    assert "지난 날짜" in got["horizons_note"]


class _Prices:
    """desk.prices' candles on a synthetic walk for every interval (a long-running market)."""

    MIN: ClassVar[dict[str, int]] = {"1m": 1, "5m": 5, "15m": 15, "60m": 60, "120m": 120, "240m": 240, "1d": 1440, "1w": 10080}

    async def candles(self, ticker, interval, count=120, indicators=()):
        step = timedelta(minutes=self.MIN[interval])
        end = datetime.now(UTC).replace(second=0, microsecond=0) - 2 * step
        bars = []
        for i in range(count):
            c = 100 * math.exp(0.08 * math.sin(i / 9) + 0.0004 * i)
            bars.append({"at": (end - (count - 1 - i) * step).isoformat(), "open": c * 0.999, "high": c * 1.006,
                         "low": c * 0.994, "close": c, "volume": 1000.0})
        return {"ticker": ticker, "interval": interval, "source": "yahoo", "bars": bars,
                "fetched_at": datetime.now(UTC).isoformat()}

    async def quote(self, ticker):
        bars = (await self.candles(ticker, "1m", 2))["bars"]
        return {"price": bars[-1]["close"], "source": "yahoo", "fetched_at": datetime.now(UTC).isoformat()}

    def __getattr__(self, name):         # anything else desk.prices has: not here
        raise AttributeError(name)


def test_build_gives_the_four_horizons_with_the_model_on_the_runs_own():
    plan = {"opinion": "buy", "bias": "long", "entry_price": 101.0, "stop_loss": 97.0, "price_target": 110.0,
            "rating": "Buy"}
    got = asyncio.run(all_horizons.build(_Prices(), "ZZZT", plan, "intraday", None, same_day=True))
    rows = got["horizons"]
    assert list(rows) == list(all_horizons.ORDER)
    assert rows["intraday"]["source"] == "model" and rows["intraday"]["model"]["entry_price"] == 101.0
    assert all(rows[h]["source"] == "map" and rows[h]["model"] is None for h in ("scalp", "short_swing", "swing"))
    for h, r in rows.items():
        assert r["label"] and r["holding"] and set(r["levels"]) == {"support", "resistance"}
        assert r["chart"] is None or r["chart"]["timeframe"] == all_horizons.CHART_TF[h]
    assert got["horizon_fit"]["regime"] in all_horizons.REGIME_KO and got["horizon_fit"]["sentence"]
    assert got["instrument_class"]["id"] == "general" and got["horizons_note"] is None


def test_a_fake_run_carries_four_scripted_horizons(db_url):
    # DESK_FAKE_RUNS stays offline: the four horizons are the script's, marked fake (the page is built on them)
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0), prices=_Prices()), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        created = client.post("/api/runs", json={"ticker": "TQQQ", "trade_date": "2026-11-03", "horizon": "short_swing"})
        run = _wait_done(client, created.json()["id"])
        assert run["status"] == "done", run["error"]
        plan = run["plan"]
        assert plan["horizons_fake"] is True and plan["instrument_class"]["id"] == "leverage"
        assert set(plan["horizons"]) == set(all_horizons.ORDER)   # PostgreSQL's JSONB keeps no key order
        own = plan["horizons"]["short_swing"]
        assert own["source"] == "model" and own["model"]["entry_price"] == plan["entry_price"]
        assert all(plan["horizons"][h]["map"]["stop"] for h in all_horizons.ORDER)
        assert plan["horizon_fit"]["best"] == ["scalp", "intraday"] and "변동성 끌림" in plan["horizon_fit"]["sentence"]


def _same_shape(real, script, where="plan"):
    """The script's value has the real one's shape: the same keys all the way down, list items like the
    real first one, and the same kind of value wherever both have one (a number is a number)."""
    if real is None or script is None:
        return
    if isinstance(real, dict):
        assert isinstance(script, dict), where
        assert set(script) == set(real), (where, set(script) ^ set(real))
        for k in real:
            _same_shape(real[k], script[k], f"{where}.{k}")
    elif isinstance(real, list):
        assert isinstance(script, list), where
        if where.endswith(".refs"):        # a level's references differ by kind (swing · fibonacci · sr …)
            assert all(isinstance(x, dict) and "kind" in x for x in real + script), where
        elif real and script:
            _same_shape(real[0], script[0], f"{where}[0]")
    else:
        num = (int, float)
        assert (isinstance(real, num) and isinstance(script, num)) or type(real) is type(script),             (where, type(real), type(script))


def test_the_scripted_horizons_have_the_live_shape():
    # review #696: the page is built on DESK_FAKE_RUNS - its values must have the real nested shapes
    real = asyncio.run(all_horizons.build(_Prices(), "ZZZT", {"bias": "long"}, "swing", None, same_day=True))
    script = all_horizons.fake("ZZZT", {"bias": "long", "entry_price": 50.0}, "swing")
    for h in all_horizons.ORDER:
        r = real["horizons"][h]
        assert r["errors"] is None and r["chart"] and r["map"].get("side"), (h, r["errors"])
        assert r["chart"]["above"] or r["chart"]["below"]
        _same_shape(r, script["horizons"][h], h)
    _same_shape({k: v for k, v in real["horizon_fit"].items() if k != "frames"},
                {k: v for k, v in script["horizon_fit"].items() if k != "frames"}, "horizon_fit")
    _same_shape(real["horizon_fit"]["frames"]["1d"], script["horizon_fit"]["frames"]["1d"], "frames.1d")


def test_the_entry_reads_the_markets_score_like_the_entry_page(monkeypatch):
    # review #696: the live playbooks' cache is shared - the run must build them gated as /api/entry does
    seen = []

    async def entry(prices, ticker, horizon=None, risk=None, bottoms=None, reach=None):
        seen.append((horizon, risk))
        return {"best": None, "stand_aside": {"reason": "x"}}

    monkeypatch.setattr(all_horizons.entry_now, "entry_now", entry)
    marker = object()
    asyncio.run(all_horizons.build(_Prices(), "ZZZT", {}, "swing", None, same_day=True, risk=marker))
    assert sorted(h for h, _ in seen) == sorted(all_horizons.ORDER) and all(r is marker for _, r in seen)


def test_a_failed_build_still_says_the_class_and_nulls(db_url, monkeypatch):
    from datetime import timedelta as td

    from fastapi.testclient import TestClient

    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD, _wait_done

    async def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(all_horizons, "build", broken)
    settings = Settings(database_url=db_url, fake_delay=0, fake_runs=True)
    client = TestClient(create_app(settings, runner=make_fake_runner(0)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, td(hours=1), td(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    with client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = _wait_done(client, client.post("/api/runs", json={"ticker": "SOXL"}).json()["id"])
        plan = run["plan"]
        assert run["status"] == "done" and plan["horizons"] is None and plan["horizon_fit"] is None
        assert plan["instrument_class"]["id"] == "leverage" and "RuntimeError" in plan["horizons_note"]


def test_the_run_list_leaves_the_horizons_to_the_runs_own_page(db_url):
    from desk.db import make_engine, migrate
    from desk.store import Store

    migrate(db_url)
    store = Store(make_engine(db_url))
    run_id = store.create_run("SOXL", "2026-11-20", {})
    store.mark_done(run_id, {"rating": "Buy", "horizons": {"swing": {}}, "horizon_fit": {"best": []},
                             "instrument_class": {"id": "leverage"}}, None)
    listed = next(r for r in store.list_runs() if r["id"] == run_id)["plan"]
    assert "horizons" not in listed and "horizon_fit" not in listed and listed["instrument_class"]["id"] == "leverage"
    assert store.get_run(run_id)["plan"]["horizons"] == {"swing": {}}
