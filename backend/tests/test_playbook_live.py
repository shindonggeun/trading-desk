from desk import calibration, playbook, playbook_live
from desk.broker.base import Bar
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_playbook import SWING, CrashBroker, _crash, _crash_sr
from tests.test_prices import _client

LONG = {"setup": "dip_catch", "zone": {"low": 78.0, "high": 80.0}, "stop": 77.0,
        "conditions": [{"id": "reclaim", "met": False, "how": "computed"}]}
SHORT = {"setup": "fade", "zone": {"low": 88.0, "high": 89.0}, "stop": 89.6, "conditions": []}


def test_states_walk_from_waiting_to_confirmed_and_invalid():
    atr = 2.0
    assert playbook.side_state(LONG, True, 90.0, atr) == "waiting"
    assert playbook.side_state(LONG, True, 81.5, atr) == "near"
    assert playbook.side_state(LONG, True, 79.0, atr) == "in_zone"
    turned = {**LONG, "conditions": [{"id": "reclaim", "met": True, "how": "computed"}]}
    assert playbook.side_state(turned, True, 80.8, atr) == "confirmed"
    assert playbook.side_state(LONG, True, 76.5, atr) == "invalid"
    assert playbook.side_state(SHORT, False, 88.5, atr) == "in_zone"
    assert playbook.side_state(SHORT, False, 86.5, atr) == "near" and playbook.side_state(SHORT, False, 90, atr) == "invalid"
    assert playbook.side_state({"setup": "none"}, True, 80, atr) is None


def test_tracking_an_analysis_keeps_its_levels_and_moves_only_conditions():
    bars = _crash()
    then = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    later = bars[:-1] + [{**bars[-1], "close": then["long"]["zone"]["mid"], "low": then["long"]["zone"]["low"]}]
    fresh = playbook.build(_crash_sr(later[-1]["close"]), later, later, SWING, live={"price": then["long"]["zone"]["mid"]})
    live = playbook.track(fresh, then, later, SWING)
    assert live["long"]["zone"] == then["long"]["zone"] and live["long"]["stop"] == then["long"]["stop"]
    assert live["long"]["targets"] == then["long"]["targets"] and live["long"]["state"] in ("in_zone", "confirmed")
    assert live["frame"]["reference_price"] == then["long"]["zone"]["mid"]
    alone = playbook.track(fresh, None, later, SWING)
    assert alone["long"]["zone"] == fresh["long"]["zone"]


def _source():
    bars = _crash()
    weekly = [Bar(**b) for b in bars[::5]]
    return PriceSource(CrashBroker(), yahoo_candles=lambda t, i, c: weekly[-c:])


def test_the_api_needs_a_session_validates_and_caches(db_url):
    playbook_live._CACHE.clear()
    with _client(db_url, _source(), fake_runs=False) as client:
        assert client.get("/api/playbooks", params={"ticker": "NVDA"}).status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/playbooks").status_code == 422
        assert client.get("/api/playbooks", params={"run": "nope"}).status_code == 404
        assert client.get("/api/playbooks", params={"ticker": "NVDA", "horizon": "weekly"}).status_code == 422
        first = client.get("/api/playbooks", params={"ticker": "NVDA"}).json()
        assert first["regime"]["id"] == "crash" and first["long"]["state"] in playbook.STATES
        # the mock broker has no quote: the last close, marked stale, records no transition
        assert first["long"]["stale"] is True and first["long"]["state_since"] is None
        assert first["ticker"] == "NVDA" and first["horizon"] == "swing" and first["levels_from"] == "fresh"
        again = client.get("/api/playbooks", params={"ticker": "NVDA"}).json()
        assert again["as_of"] == first["as_of"]                    # one computation per TTL


def test_dense_levels_make_several_narrow_zones_not_one_range():
    # SOXL-like: levels every 0.4 ATR from 64 down to 45 (ATR 5)
    cands = [{"price": 64.0 - 2.0 * i, "source": "sr", "weight": 1.0, "basis": "x"} for i in range(10)]
    found = playbook.zones(cands, ref=66.7, atr=5.0, below=True, max_atr=6.0)
    assert len(found) >= 2 and all(z["high"] - z["low"] <= 5.0 + 1e-9 for z in found)


def test_a_higher_low_above_the_zone_is_the_trend_not_a_confirmed_dip():
    atr = 2.0
    trend = {**LONG, "conditions": [{"id": "higher_low", "met": True, "how": "computed"}]}
    assert playbook.side_state(trend, True, 81.6, atr) == "near"
    assert playbook.side_state(trend, True, 79.0, atr) == "confirmed"
    assert playbook.side_state(LONG, True, 77.5, atr) == "in_zone"          # through the zone, above the stop
    lower = {**SHORT, "conditions": [{"id": "lower_high", "met": True, "how": "computed"}]}
    assert playbook.side_state(lower, False, 86.4, atr) == "near"
    assert playbook.side_state(lower, False, 88.5, atr) == "confirmed"
    rejected = {**SHORT, "conditions": [{"id": "rejection_bar", "met": True, "how": "computed"}]}
    assert playbook.side_state(rejected, False, 86.4, atr) == "confirmed"


def _run_with_playbooks(client, bars):
    run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": bars[-1]["at"][:10]}).json()
    from tests.test_api import _wait_done
    return _wait_done(client, run["id"])


def test_a_run_fixes_ticker_and_horizon_keeps_its_levels_and_invalid_sticks(db_url, monkeypatch):
    import asyncio

    playbook_live._CACHE.clear()
    playbook_live._SINCE.clear()
    bars = _crash()
    with _client(db_url, _source(), fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        done = _run_with_playbooks(client, bars)
        run_pb = done["plan"]["playbooks"]
        assert client.get("/api/playbooks", params={"run": done["id"], "ticker": "SOXX"}).status_code == 422
        assert client.get("/api/playbooks", params={"run": done["id"], "horizon": "scalp"}).status_code == 422
        body = client.get("/api/playbooks", params={"run": done["id"]}).json()
        assert body["levels_from"] == "run" and body["long"]["zone"] == run_pb["long"]["zone"]
        assert body["long"]["stop"] == run_pb["long"]["stop"]

    # invalid sticks for a tracked run, and a fallback price records no transition
    key = ("NVDA", "swing", "r1", "long")
    playbook_live._CACHE.clear()
    playbook_live._SINCE.clear()
    playbook_live._SINCE[key] = ("invalid", "2026-11-06T00:00:00+00:00")

    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    monkeypatch.setattr(playbook_live, "support_resistance",
                        lambda *a, **k: asyncio.sleep(0, result=_crash_sr(bars[-1]["close"])))
    out = asyncio.run(playbook_live.current(_source(), "NVDA", "swing", "r1", pb))
    assert out["long"]["state"] == "invalid" and out["long"]["state_since"] == "2026-11-06T00:00:00+00:00"


def test_the_cache_expires_with_the_horizon_ttl(monkeypatch):
    import asyncio

    playbook_live._CACHE.clear()
    bars = _crash()
    monkeypatch.setattr(playbook_live, "support_resistance",
                        lambda *a, **k: asyncio.sleep(0, result=_crash_sr(bars[-1]["close"])))
    t = [1000.0]
    first = asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0]))
    t[0] += 10
    assert asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0])) is first
    t[0] += 1000
    assert asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0])) is not first


def test_a_fresh_playbook_is_read_without_the_lock_and_ahead_computes_it_again(monkeypatch):
    """#536: a page never waits for the prewarm loop's refresh; the loop recomputes what would expire."""
    import asyncio

    playbook_live._CACHE.clear()
    playbook_live._LOCKS.clear()
    bars = _crash()
    monkeypatch.setattr(playbook_live, "support_resistance",
                        lambda *a, **k: asyncio.sleep(0, result=_crash_sr(bars[-1]["close"])))
    t = [1000.0]
    first = asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0]))

    async def page_while_refreshing():
        async with playbook_live._LOCKS[("NVDA", "swing", None)]:     # the loop holds the lock
            return await asyncio.wait_for(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0]), 1)

    assert asyncio.run(page_while_refreshing()) is first
    t[0] += 10
    ttl = playbook_live.horizons.get("swing").sr_ttl_sec
    assert asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0], ahead=ttl - 20)) is first
    again = asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0], ahead=ttl))
    assert again is not first
    assert asyncio.run(playbook_live.current(_source(), "NVDA", "swing", clock=lambda: t[0])) is again



def test_scoped_calibration_cannot_reuse_legacy_playbook_cache(monkeypatch):
    import asyncio

    async def compute(*_args):
        return {"long": {}, "short": {}, "fixture": calibration.current_table()}, None

    monkeypatch.setattr(playbook_live, "_compute", compute)
    protected = calibration.Table()

    async def read():
        legacy = await playbook_live.current(None, "SYNTHETIC-CACHE")
        with calibration.use_table(protected):
            scoped = await playbook_live.current(None, "SYNTHETIC-CACHE")
            assert scoped["fixture"] is protected
            assert await playbook_live.current(None, "SYNTHETIC-CACHE") is scoped
        assert legacy["fixture"] is calibration.TABLE
        assert await playbook_live.current(None, "SYNTHETIC-CACHE") is legacy

    asyncio.run(read())
