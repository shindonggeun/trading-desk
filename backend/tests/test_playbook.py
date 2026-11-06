from datetime import UTC, date, datetime, timedelta

from desk import horizons, playbook
from desk.broker.base import Bar
from desk.broker.mock import MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD, _wait_done
from tests.test_prices import _client

SWING = horizons.get("swing")
INTRADAY = horizons.get("intraday")


def _daily(closes: list[float], volumes: list[float] | None = None, start=date(2026, 3, 2), gap_last=0.0) -> list[dict]:
    out = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        o = prev + (gap_last if i == len(closes) - 1 else 0.0)
        out.append({"at": datetime(start.year, start.month, start.day, tzinfo=UTC).isoformat()
                    if i == 0 else (datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(days=i)).isoformat(),
                    "open": o, "high": max(o, c) + 1.0, "low": min(o, c) - 1.0, "close": c,
                    "volume": (volumes[i] if volumes else 1_000_000)})
    return out


def _crash_closes() -> tuple[list[float], list[float]]:
    box = [100 + (5 if (i // 5) % 2 else -5) * ((i % 5) / 5) for i in range(180)]
    fall = [110 - 1.5 * i for i in range(20)]                      # 110 -> 81.5
    vols = [1_000_000] * 197 + [3_000_000] * 3
    return box + fall, vols


def _crash_sr(ref: float) -> dict:
    return {"atr": 2.0, "reference_price": ref, "last_close": ref,
            "levels": [{"price": 78.0, "kind": "support", "timeframe": "1w", "strength": 3.5, "basis": "주봉 저점 ×2"},
                       {"price": 77.2, "kind": "support", "timeframe": "1d", "strength": 1.5, "basis": "일봉 저점"},
                       {"price": 83.0, "kind": "support", "timeframe": "1d", "strength": 2.5, "basis": "전일 저가"},
                       {"price": 88.0, "kind": "resistance", "timeframe": "1d", "strength": 2.0, "basis": "갭 하단"}],
            "volume_profile": {"poc": 95.0, "value_area": {"low": 78.5, "high": 104.0},
                               "nodes": [{"price": 79.0, "kind": "thick", "share": 0.1}]},
            "fibonacci": {"direction": "down", "levels": [{"ratio": 0.382, "price": 92.0}, {"ratio": 0.5, "price": 95.8}]},
            "trendlines": []}


def _crash():
    closes, vols = _crash_closes()
    return _daily(closes, vols, gap_last=-2.5)


def test_a_crash_is_read_from_drawdown_and_its_companions():
    f = playbook.features(_crash())
    regime = playbook.detect_regime(f)
    assert regime["id"] == "crash" and f["dd20"] <= -0.10 and f["down_streak"] >= 3
    assert sum(e["hit"] for e in regime["evidence"]) >= 3 and regime["flags"]["oversold"]
    assert playbook.detect_regime(playbook.features(_crash()[:30]))["id"] == "unknown"   # too few bars


def test_crash_playbooks_dip_catch_long_and_short_scalp():
    bars = _crash()
    ref = bars[-1]["close"]
    pb = playbook.build(_crash_sr(ref), bars, bars, SWING)
    long, short = pb["long"], pb["short"]
    assert pb["regime"]["id"] == "crash" and long["setup"] == "dip_catch" and long["size_hint"] == "half"
    zone = long["zone"]
    assert zone["high"] < ref and zone["score"] >= 3                       # the 78 / 79 / 78.5 confluence below
    assert [e["share"] for e in long["entries"]] == [0.25, 0.25, 0.5] and long["entries"][2]["kind"] == "stop"
    assert long["entries"][2]["after"] == ["reclaim", "higher_low"]
    assert long["stop"] == round(zone["low"] - 0.5 * 2.0, 2)
    t1, t2 = long["targets"]
    assert long["avg_entry"] < t1["price"] < t2["price"] and long["time_stop"]["bars"] == playbook.TIME_STOP_BARS["swing"]
    assert long["time_stop"]["required"] is True
    assert short["setup"] in ("fade", "breakdown") and short["zone"]["low"] > ref - 1e-9 and short["flat_by_close"]
    assert short["stop"] == round(short["zone"]["high"] + 0.3 * 2.0, 2)
    assert all(t["price"] < short["avg_entry"] for t in short["targets"])
    assert pb["bias_note"] == {"side": "both", "text": "폭락 국면 — 숏 단타가 주, 롱 하따는 확인 뒤 절반만", "source": "desk"}


def test_an_uptrend_buys_the_pullback_and_rests_the_short():
    closes = [80 + 0.25 * i for i in range(160)]
    bars = _daily(closes)
    ref = closes[-1]
    sr = {"atr": 2.0, "reference_price": ref, "levels": [
        {"price": ref - 3, "kind": "support", "timeframe": "1d", "strength": 2.0, "basis": "20일선 부근"}],
        "fibonacci": {"direction": "up", "levels": [{"ratio": 0.618, "price": ref - 3.5}]}}
    pb = playbook.build(sr, bars, bars, SWING)
    assert pb["regime"]["id"] == "uptrend" and pb["long"]["setup"] == "pullback"
    assert [e["share"] for e in pb["long"]["entries"]] == [0.5, 0.5]
    assert pb["short"]["setup"] == "none" or pb["regime"]["flags"]["overextended"]


def test_the_note_is_bounded_and_the_mandate_names_three_lines():
    bars = _crash()
    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    note = playbook.prompt_note(pb)
    assert note.startswith("Desk-computed two-sided playbook") and "Regime: crash" in note and len(note) <= 1200
    assert "LONG dip-catch" in note and "SHORT " in note
    text = playbook.mandate()
    assert all(label in text for label in ("Long Playbook:", "Short Playbook:", "Playbook Bias:"))


def test_three_lines_are_read_and_insane_prices_are_set_aside():
    parsed = playbook.parse("blah\n" + playbook.FAKE_LINES)
    assert parsed["long"]["zone"] == (178.0, 180.0) and parsed["long"]["entries"] == [180.0, 179.0, 181.0]
    assert parsed["long"]["stop"] == 176.5 and parsed["long"]["t2"] == 188.0 and parsed["long"]["time_stop"] == 12
    assert parsed["short"]["stop"] == 188.2 and parsed["bias"]["side"] == "both"
    assert playbook.parse("Long Playbook: none\nShort Playbook: none")["long"] is None

    bars = _crash()
    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    ref = pb["frame"]["reference_price"]
    avg = pb["long"]["avg_entry"]
    good_stop = round(avg - 3, 2)
    merged = playbook.merge(pb, {"long": {"raw": "x", "stop": good_stop, "t1": ref * 3, "t2": None},
                                 "short": None, "bias": {"side": "short", "text": "t", "source": "model"}})
    long = merged["long"]
    assert long["stop"] == good_stop and long["source"]["stop"] == "model"
    assert long["model"]["ignored"]["t1"] == ref * 3 and long["source"]["t1"] == "desk"
    assert merged["bias_note"]["source"] == "model" and merged["short"] == pb["short"]


def test_a_newer_quote_refreshes_only_the_distance_conditions():
    bars = _crash()
    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    zone = pb["long"]["zone"]
    moved = playbook.refresh(pb, {"price": zone["mid"]})
    in_zone = next(c for c in moved["long"]["conditions"] if c["id"] == "in_zone")
    assert in_zone["met"] is True and moved["frame"]["reference_price"] == zone["mid"]
    assert playbook.refresh(pb, None) is pb


class CrashBroker(MockBroker):
    name = "toss"

    async def candles(self, symbol, interval, count):
        return [Bar(**b) for b in _crash()[-count:]]


def test_a_run_carries_both_playbooks_and_the_agents_get_the_note(db_url):
    bars = _crash()
    weekly = [Bar(**b) for b in bars[::5]]
    source = PriceSource(CrashBroker(), yahoo_candles=lambda t, i, c: weekly[-c:])
    with _client(db_url, source, fake_runs=False) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        run = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": bars[-1]["at"][:10]}).json()
        done = _wait_done(client, run["id"])
    assert done["status"] == "done"
    pb = done["plan"]["playbooks"]
    assert pb["version"] == 1 and pb["regime"]["id"] == "crash"
    assert pb["long"]["setup"] == "dip_catch" and pb["bias_note"]["source"] == "model"   # the scripted line
