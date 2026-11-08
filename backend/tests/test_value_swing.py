"""#167: the oversold swing buy - evidence bundles, conviction, the plan and its risk budget."""

from datetime import UTC, date, datetime, timedelta

from desk import value_swing as vs


def _days(closes: list[float], vols: list[float] | None = None, start=date(2023, 1, 2)) -> list[dict]:
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        at = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(days=i)
        out.append({"at": at.isoformat(), "open": prev, "high": max(prev, c) * 1.01, "low": min(prev, c) * 0.99,
                    "close": c, "volume": vols[i] if vols else 1_000_000})
        prev = c
    return out


def _weeks(days: list[dict]) -> list[dict]:
    out = []
    for i in range(0, len(days), 5):
        w = days[i:i + 5]
        out.append({"at": w[0]["at"], "open": w[0]["open"], "high": max(b["high"] for b in w),
                    "low": min(b["low"] for b in w), "close": w[-1]["close"], "volume": sum(b["volume"] for b in w)})
    return out


def _crash(bottom_days: int = 0) -> tuple[list[dict], list[dict]]:
    """Three calm years around 100 (a crash to 70 a year in), then a fall from 110 to 55 with a
    capitulation day, and ``bottom_days`` of drifting at the low."""
    calm = [100 + 5 * ((i % 40) / 40) for i in range(500)]
    calm[250:270] = [100 - 1.5 * k for k in range(20)]            # an old crash to ~71
    rise = [105 + 0.1 * i for i in range(50)]                      # to 110
    fall = [110 * (0.97 ** k) for k in range(1, 26)]               # ~110 -> ~51
    base = [fall[-1] * (1 + 0.003 * ((k % 3) - 1)) for k in range(bottom_days)]
    base += [base[-1] * 1.06] if base else []                    # the new low taken back
    closes = calm + rise + fall + base
    vols = [1_000_000.0] * len(closes)
    vols[len(calm) + len(rise) + 22] = 3_500_000.0                 # the capitulation day
    for k in range(len(calm) + len(rise) + 23, len(closes)):
        vols[k] = 600_000.0                                        # volume dries up after it
    days = _days(closes, vols)
    return days, _weeks(days)


def test_a_deep_oversold_fall_makes_the_setup_with_its_ladder_targets_and_time_stop():
    days, weeks = _crash(bottom_days=3)
    ev = vs.evidence(days[-200:], weeks[-156:], risk={"phase": "selling", "score": 72}, vix=34)
    g = ev["groups"]
    assert g["oversold"]["met"] and g["depth"]["met"] and g["fear"]["met"]
    assert g["flows"]["met"] is None                                # not computed: unknown, not False
    assert ev["setup"] and ev["count"] >= 3
    side = vs.plan(ev)
    assert side["setup"] == "value_swing" and side["flat_by_close"] is False
    assert [e["share"] for e in side["entries"]] == list(vs.SHARES)
    assert [e["kind"] for e in side["entries"]] == ["limit", "limit", "stop"]
    assert side["entries"][1]["price"] < side["entries"][0]["price"]
    assert side["stop"] < min(e["price"] for e in side["entries"])
    roles = [t["role"] for t in side["targets"]]
    assert roles[0] == "t1" and all(a["price"] < b["price"] for a, b in zip(side["targets"], side["targets"][1:]))
    assert side["time_stop"]["bars"] == vs.TIME_STOP_BARS and side["time_stop"]["required"]
    assert "Oversold swing buy" in vs.prompt_line(side)


def test_no_conviction_without_four_bundles_or_without_a_positive_record():
    days, weeks = _crash(bottom_days=3)
    rich = vs.evidence(days[-200:], weeks[-156:], risk={"phase": "exhausting", "score": 80}, vix=35)
    good = {"thin": False, "n": 120, "exp_r": 0.15}
    if rich["count"] >= vs.CONVICTION_GROUPS:
        assert vs.conviction(rich, good)["active"]
        assert not vs.conviction(rich, None)["active"]                       # no record yet: never
        assert not vs.conviction(rich, {**good, "exp_r": -0.05})["active"]   # a negative record
        assert not vs.conviction(rich, {**good, "thin": True})["active"]
    calm = vs.evidence(days[-200:], weeks[-156:], risk={"phase": "calm", "score": 10}, vix=15)
    three = {**calm, "count": 3}
    conv = vs.conviction(three, good)
    assert not conv["active"] and {c["id"] for c in conv["checklist"]} >= set(vs.GROUPS) | {"record"}


def test_a_missing_indicator_still_counts_the_rest():
    days, _ = _crash(bottom_days=3)
    ev = vs.evidence(days[-200:], None)                     # no weekly bars, no risk, no VIX
    assert ev is not None
    assert ev["groups"]["fear"]["met"] is None and ev["groups"]["depth"]["checks"][0]["met"] is None
    assert ev["groups"]["oversold"]["met"]                  # the daily evidence is still read
    assert vs.evidence(days[:30], None) is None             # too short


def test_a_calm_market_has_no_setup():
    days = _days([100 + 3 * ((i % 30) / 30) for i in range(400)])
    ev = vs.evidence(days[-200:], _weeks(days)[-156:], vix=14)
    assert not ev["setup"] and vs.plan(ev) is None


def test_a_conviction_trade_never_loses_more_than_its_account_budget():
    days, weeks = _crash(bottom_days=3)
    ev = vs.evidence(days[-200:], weeks[-156:], risk={"phase": "exhausting", "score": 80}, vix=35)
    ev = {**ev, "count": max(ev["count"], vs.CONVICTION_GROUPS)}
    side = vs.plan(ev, {"thin": False, "n": 200, "exp_r": 0.2})
    budget = side["risk_budget"]
    assert budget["kind"] == "conviction" and side["size_hint"] == "normal"
    loss_pct = budget["position_pct"] * (side["avg_entry"] - side["stop"]) / side["avg_entry"]
    assert loss_pct <= vs.ACCOUNT_RISK_PCT["conviction"] + 1e-9
    assert vs.position_pct(100, 99.99, 1.0) == vs.MAX_POSITION_PCT     # a tight stop is capped by the position
    assert vs.position_pct(100, 101, 1.0) == 0.0


def test_apply_takes_the_long_slot_on_the_swing_frame_only(monkeypatch):
    days, weeks = _crash(bottom_days=3)
    pb = {"regime": {"id": "crash"}, "long": {"setup": "dip_catch", "direction": "against"}, "short": {"setup": "none"}}
    monkeypatch.setattr(vs, "ENABLED", True)
    got = vs.apply(pb, days[-200:], weeks[-156:], "swing", risk={"phase": "selling", "score": 70}, vix=33,
                   calibrate=False)
    assert got["long"]["setup"] == "value_swing" and got["long"]["replaced"] == "dip_catch"
    assert got["long"]["direction"] == "against" and got["short"] == pb["short"]
    assert pb["long"]["setup"] == "dip_catch"                       # the input is not changed
    assert not got["long"]["conviction"]["active"]                  # no record (the backtest path)
    assert vs.apply(pb, days[-200:], weeks[-156:], "intraday", vix=33) is pb
    monkeypatch.setattr(vs, "ENABLED", False)
    assert vs.apply(pb, days[-200:], weeks[-156:], "swing", vix=33) is pb


def test_the_note_carries_the_oversold_swing_line():
    from desk import playbook

    days, weeks = _crash(bottom_days=3)
    side = vs.plan(vs.evidence(days[-200:], weeks[-156:], risk={"phase": "selling", "score": 70}, vix=33))
    pb = {"regime": {"id": "crash", "features": {}}, "frame": {}, "long": side,
          "short": {"setup": "none", "reason": "n/a"}}
    note = playbook.prompt_note(pb)
    assert "LONG oversold swing buy (2-8 weeks)" in note and "Oversold swing buy (2-8 weeks): evidence" in note


def test_a_failure_leaves_the_playbooks_as_built(monkeypatch):
    days, weeks = _crash(bottom_days=3)
    pb = {"regime": {"id": "crash"}, "long": {"setup": "dip_catch"}, "short": {"setup": "none"}}

    def boom(*_a, **_k):
        raise ZeroDivisionError("flat series")
    monkeypatch.setattr(vs, "evidence", boom)
    assert vs.apply(pb, days[-200:], weeks[-156:], "swing") is pb
