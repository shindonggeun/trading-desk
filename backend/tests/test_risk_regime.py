from datetime import date, timedelta
from types import SimpleNamespace

from desk import backtest, horizons, playbook, risk_regime
from tests.test_backtest import _bars, _fetch
from tests.test_playbook import _crash, _crash_sr

SWING = horizons.get("swing")
START = date(2026, 1, 5)


def _series(closes: list[float], opens: list[float] | None = None) -> list[dict]:
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else (closes[i - 1] if i else c)
        out.append({"day": (START + timedelta(days=i)).isoformat(), "open": o, "high": max(o, c), "low": min(o, c),
                    "close": c})
    return out


def _calm(n: int = 60) -> dict:
    flat = [100.0 + (i % 2) * 0.2 for i in range(n)]
    return {"^VIX": _series([15.0] * n), "^VIX9D": _series([14.0] * n), "^VIX3M": _series([18.0] * n),
            "^NDX": _series(flat), "^SOX": _series(flat), "DX-Y.NYB": _series([100.0] * n),
            "^TNX": _series([4.0] * n)}


def _dump(n: int = 60) -> dict:
    """Calm, then six days of selling into the last bar: VIX 15 -> 40, inverted, the index -14%."""
    s = _calm(n)
    vix = [15.0] * (n - 6) + [20, 25, 30, 34, 37, 40]
    s["^VIX"] = _series(vix)
    s["^VIX9D"] = _series([14.0] * (n - 6) + [22, 29, 35, 40, 44, 48])
    s["^VIX3M"] = _series([18.0] * (n - 6) + [20, 23, 26, 29, 31, 33])
    idx = [100.0] * (n - 6) + [98, 95, 93, 90, 88, 86]
    s["^NDX"] = _series(idx)
    s["^SOX"] = _series([100.0] * (n - 6) + [97, 93, 90, 86, 83, 80])
    s["DX-Y.NYB"] = _series([100.0] * (n - 6) + [100.5, 101, 101.5, 102, 102.5, 103])
    return s


def _turn(n: int = 60) -> dict:
    """The dump, then the VIX off its peak, the term structure back to normal and an up day."""
    s = _dump(n - 3)
    vix = [b["close"] for b in s["^VIX"]] + [36, 33, 30]
    s["^VIX"] = _series(vix)
    s["^VIX9D"] = _series([b["close"] for b in s["^VIX9D"]] + [38, 32, 28])
    s["^VIX3M"] = _series([b["close"] for b in s["^VIX3M"]] + [33, 32, 31])
    idx = [b["close"] for b in s["^NDX"]] + [85, 84, 87]
    s["^NDX"] = _series(idx)
    s["^SOX"] = _series([b["close"] for b in s["^SOX"]] + [79, 78, 82])
    for k in ("DX-Y.NYB", "^TNX"):
        s[k] = _series([b["close"] for b in s[k]] + [s[k][-1]["close"]] * 3)
    return s


def test_calm_markets_score_low_and_leave_the_playbooks_alone():
    risk = risk_regime.score(_calm())
    assert risk["phase"] == "calm" and risk["score"] < 20 and risk["missing"] == []
    pb = playbook.build(_crash_sr(_crash()[-1]["close"]), _crash(), _crash(), SWING)
    out = risk_regime.apply(pb, risk)
    assert out["long"] == pb["long"] and out["short"] == pb["short"] and out["risk_regime"] is risk


def test_a_market_wide_dump_is_selling_and_a_turn_is_exhausting():
    selling = risk_regime.score(_dump())
    assert selling["phase"] == "selling" and selling["score"] >= risk_regime.SELLING
    assert {p["id"] for p in selling["parts"]} >= {"vix", "term", "index", "streak", "dollar", "rates"}
    assert any("역전" in p["text"] for p in selling["parts"] if p["id"] == "term")
    turn = risk_regime.score(_turn())
    ids = {s["id"] for s in turn["signals"]}
    assert turn["phase"] == "exhausting" and turn["peak"] >= risk_regime.STRESS
    assert {"vix_turn", "term_normal"} <= ids


def test_one_indicator_down_still_scores_on_the_rest():
    s = _dump()
    del s["^VIX3M"], s["^VIX9D"]
    risk = risk_regime.score(s)
    assert risk is not None and set(risk["missing"]) == {"^VIX3M", "^VIX9D"}
    assert "term" not in {p["id"] for p in risk["parts"]} and risk["phase"] == "selling"
    only = risk_regime.score({"^NDX": _dump()["^NDX"]})
    assert only is not None and [p["id"] for p in only["parts"]] == ["index", "streak"]
    assert risk_regime.score({}) is None


def test_selling_keeps_only_the_first_ladder_step_and_is_idempotent():
    bars = _crash()
    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    assert pb["long"]["setup"] == "dip_catch" and len(pb["long"]["entries"]) == 3
    risk = risk_regime.score(_dump())
    out = risk_regime.apply(pb, risk)
    long = out["long"]
    assert [e["step"] for e in long["entries"]] == [1] and [e["step"] for e in long["gate"]["held"]] == [2, 3]
    first = pb["long"]["entries"][0]["price"]
    assert long["avg_entry"] == first and long["risk"] == round(first - long["stop"], 2)
    t1 = long["targets"][0]
    assert t1["then_stop"] == first and t1["rr"] == round((t1["price"] - first) / (first - long["stop"]), 2)
    assert "1차만" in long["gate"]["text"] and out["short"] == pb["short"]
    assert risk_regime.apply(out, risk)["long"] == long                     # a second pass changes nothing
    assert pb["long"]["entries"][1:]                                         # the input is not mutated


def test_exhaustion_rests_the_short_and_runs_the_ladder():
    bars = _crash()
    pb = playbook.build(_crash_sr(bars[-1]["close"]), bars, bars, SWING)
    assert pb["short"]["setup"] in ("fade", "breakdown")
    out = risk_regime.apply(pb, risk_regime.score(_turn()))
    assert out["short"]["setup"] == "none" and out["short"]["gate"]["setup"] == pb["short"]["setup"]
    assert "투매 끝물" in out["short"]["reason"] and out["short"]["entries"] == []
    assert out["long"] == pb["long"]


def test_the_note_names_the_phase_and_what_is_missing():
    s = _dump()
    del s["^TNX"]
    note = risk_regime.prompt_note(risk_regime.score(s))
    assert "SELLING UNDER WAY" in note and "^TNX" in note and "VIX" in note
    assert risk_regime.prompt_note(None) is None


def test_a_korean_plan_sees_the_us_session_before_it():
    s = {"^VIX": _series([15.0, 16.0, 17.0]), "^KS11": _series([100.0, 101.0, 102.0])}
    day = s["^VIX"][1]["day"]
    cut = risk_regime.as_of(s, day, korean=True)
    assert [b["close"] for b in cut["^VIX"]] == [15.0] and [b["close"] for b in cut["^KS11"]] == [100.0, 101.0]
    assert [b["close"] for b in risk_regime.as_of(s, day)["^VIX"]] == [15.0, 16.0]
    # Yahoo stamps the KOSPI at Seoul midnight: 15:00 UTC the day before
    assert risk_regime._day("2026-03-02T15:00:00+00:00", "^KS11") == "2026-03-03"
    assert risk_regime._day("2026-03-03T05:00:00+00:00", "^VIX") == "2026-03-03"


def test_fetch_skips_a_failing_symbol_and_the_cache_reuses_a_hit():
    def candles(symbol, interval, count):
        if symbol == "^SOX":
            raise RuntimeError("down")
        return [SimpleNamespace(at="2026-03-03T05:00:00+00:00", open=1.0, high=1.0, low=1.0, close=1.0)]

    got = risk_regime.fetch(("^VIX", "^SOX"), candles=candles)
    assert list(got) == ["^VIX"]
    calls, now = [], [0.0]
    cache = risk_regime.Cache(ttl_sec=60, fetcher=lambda syms: calls.append(syms) or _dump(), clock=lambda: now[0])
    assert cache.current("NVDA")["phase"] == "selling" and cache.current("NVDA") and len(calls) == 1
    now[0] = 61.0
    cache.current("NVDA")
    assert len(calls) == 2
    empty = risk_regime.Cache(fetcher=lambda syms: {})
    assert empty.current("005930.KS") is None


def test_the_backtest_gates_the_plans_and_compares_on_and_off():
    daily = _bars(320)
    days = [backtest.local_day(b["at"], "SOXL") for b in daily]
    # the market dumps with the ticker's crash (bars 260~275) and stays there
    dump = _dump(266)
    series = {k: [{**b, "day": d} for b, d in zip(v + [v[-1]] * (len(days) - len(v)), days, strict=True)]
              for k, v in dump.items()}
    market = backtest.MarketRisk(series)
    assert market.on(days[265], "SOXL")["phase"] == "selling" and market.on(days[100], "SOXL")["phase"] == "calm"
    assert market.on(days[265], "SOXL") is market.on(days[265], "SOXL")     # once per day
    report = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None, risk=market)
    assert set(report.risk_gate) == {"on", "off"}
    on, off = report.risk_gate["on"], report.risk_gate["off"]
    assert {"all", "valid", "long:dip_catch", "short", "losing_streak", "crash:2022"} <= set(on)
    assert on["all"]["plans"] <= off["all"]["plans"] and report.by_risk["long:dip_catch:selling"]["plans"]
    assert all(k.endswith(("selling", "calm", "exhausting", "unknown")) for k in report.by_risk)
    ungated = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    assert ungated.risk_gate == {} and ungated.baseline == off["all"]
