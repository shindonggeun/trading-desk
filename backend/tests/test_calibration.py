"""#147: playbook score -> T1 rate and expected R from the desk's record."""

from desk import calibration, playbook
from tests.test_playbook import SWING, _crash, _crash_sr


def _trades(n: int, hit: int, r: float, setup="dip_catch", regime="crash", score=70, side="long") -> list[dict]:
    return [{"side": side, "setup": setup, "regime": regime, "score": score, "status": "t2" if i < hit else "stop",
             "took_t1": i < hit, "r_net": r} for i in range(n)]


def test_a_thin_sample_gives_no_probability():
    table = calibration.Table(calibration.from_trades(_trades(10, 6, 0.3), "swing"))
    got = table.look("swing", "long", "dip_catch", "crash", 70)
    assert got["thin"] and got["p_t1"] is None and got["exp_r"] is None and got["n"] == 10
    # nothing at all for an unknown setup: thin, n 0
    assert table.look("swing", "short", "fade", "range", 50) | {} == {
        "p_t1": None, "exp_r": None, "n": 0, "level": None, "thin": True, "p_range": None, "live_n": 0,
        "negative": False}


def test_a_thin_band_falls_back_to_the_setup_in_its_regime_then_the_setup():
    cells = calibration.from_trades(_trades(20, 10, 0.1, score=85) + _trades(40, 30, 0.5, score=65), "swing")
    table = calibration.Table(cells)
    band = table.look("swing", "long", "dip_catch", "crash", 66)          # 40 trades in the 60s band
    assert band["level"] == "band" and band["p_t1"] == 0.75 and band["exp_r"] == 0.5
    lo, hi = band["p_range"]
    assert lo < 0.75 < hi
    wide = table.look("swing", "long", "dip_catch", "crash", 90)          # 20 in the 80s: the regime's 60
    assert wide["level"] == "regime" and wide["n"] == 60
    other = table.look("swing", "long", "dip_catch", "downtrend", 90)     # no downtrend rows: the setup's 60
    assert other["level"] == "setup" and other["n"] == 60


def test_the_backtest_prior_is_capped_and_live_trades_count_fully():
    prior = calibration.from_trades(_trades(1000, 500, -0.1), "swing")
    table = calibration.Table(prior)
    assert table.look("swing", "long", "dip_catch", "crash", 70)["n"] == calibration.PRIOR_CAP
    run = {"plan": {"horizon": "swing", "playbooks": {"regime": {"id": "crash"},
                                                       "long": {"setup": "dip_catch", "score": 70},
                                                       "short": {"setup": "none"}}},
           "outcome": {"playbooks": {"long": {"status": "t2", "took_t1": True, "r_size": 1.2},
                                     "short": {"status": "stop"}}}}
    pending = {**run, "outcome": {"playbooks": {"long": {"status": "open", "r_size": None}}}}
    table.set_live([run] * 50 + [pending])
    got = table.look("swing", "long", "dip_catch", "crash", 70)
    assert got["n"] == calibration.PRIOR_CAP + 50 and got["live_n"] == 50
    assert got["p_t1"] == round((100 + 50) / 250, 3)


def test_a_setup_negative_on_enough_trades_is_marked_and_only_turned_off_by_the_switch(monkeypatch):
    bars = _crash()
    ref = bars[-1]["close"]
    plain = playbook.build(_crash_sr(ref), bars, bars, SWING, calibrate=False)
    setup = plain["long"]["setup"]
    regime = plain["regime"]["id"]
    assert setup != "none" and "calibration" not in plain["long"]       # the backtest path: no record
    rows = [{**t, "r_net": -0.4 if i % 4 else 0.2} for i, t in enumerate(_trades(300, 120, 0, setup, regime))]
    table = calibration.Table(calibration.from_trades(rows, "swing"))
    pb = playbook.calibrate_sides(playbook.build(_crash_sr(ref), bars, bars, SWING, calibrate=False), SWING, table)
    long = pb["long"]
    assert long["setup"] == setup and long["calibration"]["negative"] and long["exp_r"] < 0
    assert long["n"] == long["calibration"]["n"] and long["p_t1"] is not None
    note = playbook.prompt_note(pb)
    assert "Desk record: " in note and "- negative." in note and calibration.PROMPT_RULE in note
    monkeypatch.setattr(calibration, "DISABLE", True)
    off = playbook.calibrate_sides(playbook.build(_crash_sr(ref), bars, bars, SWING, calibrate=False), SWING, table)
    assert off["long"]["setup"] == "none" and "음수라 쉼" in off["long"]["reason"]
    assert off["long"]["entries"] == [] and off["long"]["calibration"]["negative"]


def test_a_thin_record_says_so_in_the_note():
    bars = _crash()
    ref = bars[-1]["close"]
    pb = playbook.calibrate_sides(playbook.build(_crash_sr(ref), bars, bars, SWING, calibrate=False), SWING,
                                  calibration.Table())
    assert pb["long"]["calibration"]["thin"] and pb["long"]["p_t1"] is None
    assert "Desk record: thin sample (0)" in playbook.prompt_note(pb)


def test_reliability_compares_the_training_table_with_the_held_out_hits():
    train = calibration.from_trades(_trades(100, 60, 0.2), "swing")
    rows = calibration.reliability(train, _trades(50, 25, -0.1), "swing")
    assert rows == [{"band": 60, "n": 50, "predicted": 0.6, "actual": 0.5, "gap": 0.1,
                     "exp_r_predicted": 0.2, "r_actual": -0.1}]


def test_the_shipped_table_loads(tmp_path):
    assert calibration.Table.load(tmp_path / "missing.json").prior == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert calibration.Table.load(bad).prior == {}
    calibration.write({"swing|long|x|*|*": {"n": 1, "t1": 1, "r": 0.5, "r2": 0.25}}, {"source": "test"}, bad)
    assert calibration.Table.load(bad).meta["source"] == "test"
