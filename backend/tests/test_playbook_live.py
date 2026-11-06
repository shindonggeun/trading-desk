from desk import playbook, playbook_live
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
        assert first["long"]["state_since"] and first["ticker"] == "NVDA" and first["horizon"] == "swing"
        again = client.get("/api/playbooks", params={"ticker": "NVDA"}).json()
        assert again["as_of"] == first["as_of"]                    # one computation per TTL


def test_dense_levels_make_several_narrow_zones_not_one_range():
    # SOXL-like: levels every 0.4 ATR from 64 down to 45 (ATR 5)
    cands = [{"price": 64.0 - 2.0 * i, "source": "sr", "weight": 1.0, "basis": "x"} for i in range(10)]
    found = playbook.zones(cands, ref=66.7, atr=5.0, below=True, max_atr=6.0)
    assert len(found) >= 2 and all(z["high"] - z["low"] <= 5.0 + 1e-9 for z in found)
