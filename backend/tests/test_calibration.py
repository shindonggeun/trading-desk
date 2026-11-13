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


def _dated(n: int, hit: int, at: str, setup="dip_catch", regime="crash") -> list[dict]:
    """Trades on one date: a hit is +1R, a miss -1R (a spread, so a standard error)."""
    return [{"side": "long", "setup": setup, "regime": regime, "score": 70, "at": at,
             "status": "t2" if i < hit else "stop", "took_t1": i < hit, "r_net": 1.0 if i < hit else -1.0}
            for i in range(n)]


def test_the_recent_window_takes_the_last_twelve_months_both_ends_included():
    assert calibration.recent_since("2026-11-13") == "2025-11-14"
    trades = _dated(10, 5, "2025-11-13") + _dated(10, 5, "2025-11-14") + _dated(10, 5, "2026-11-13")
    recent = calibration.from_trades(trades, "swing", since=calibration.recent_since("2026-11-13"))
    assert recent["swing|long|dip_catch|*|*"]["n"] == 20


def test_a_cell_whose_last_year_is_clearly_worse_reads_weaker_and_leads_with_the_recent_numbers():
    old = _dated(400, 240, "2021-03-01")                     # +0.2R a trade over the years
    new = _dated(100, 30, "2026-06-01")                      # -0.4R in the last year
    trades = old + new
    table = calibration.Table(calibration.from_trades(trades, "swing"),
                              recent=calibration.from_trades(trades, "swing", since="2025-11-14"))
    got = table.look("swing", "long", "dip_catch", "crash", 70)
    assert got["drift"] == "weaker" and got["basis"] == "recent"
    # the recent plans take the long cell's discount (200 of 500): 100 count as 40
    assert got["exp_r"] == -0.4 and got["n"] == 40 and got["recent"] == {"n": 40, "p_t1": 0.3, "exp_r": -0.4}
    assert got["long_term"]["n"] == calibration.PRIOR_CAP and got["long_term"]["exp_r"] == 0.08
    note = calibration.prompt_bit(got)
    assert "-0.40R" in note and "Recent 12 months weaker than its long run (+0.08R)" in note
    rows = calibration.drifted(calibration.from_trades(trades, "swing"),
                               calibration.from_trades(trades, "swing", since="2025-11-14"))
    assert [r["drift"] for r in rows] == ["weaker", "weaker"] and rows[0]["recent_exp_r"] == -0.4


def test_a_small_or_close_recent_sample_has_no_drift():
    old = _dated(400, 240, "2021-03-01")
    for new in (_dated(20, 0, "2026-06-01"), _dated(100, 58, "2026-06-01")):   # thin / within the noise
        trades = old + new
        table = calibration.Table(calibration.from_trades(trades, "swing"),
                                  recent=calibration.from_trades(trades, "swing", since="2025-11-14"))
        got = table.look("swing", "long", "dip_catch", "crash", 70)
        assert got["drift"] is None and "basis" not in got
        assert got["recent"]["n"] == round(len(new) * calibration.PRIOR_CAP / len(trades))
    plain = calibration.Table(calibration.from_trades(old, "swing")).look("swing", "long", "dip_catch", "crash", 70)
    assert plain["recent"] is None and plain["drift"] is None          # a table from before #286


def test_live_results_count_in_the_recent_cells_by_their_trade_date():
    def run(day: str, r: float) -> dict:
        return {"trade_date": day,
                "plan": {"horizon": "swing", "playbooks": {"regime": {"id": "crash"},
                                                           "long": {"setup": "dip_catch", "score": 70},
                                                           "short": {"setup": "none"}}},
                "outcome": {"playbooks": {"long": {"status": "t2" if r > 0 else "stop", "took_t1": r > 0, "r_size": r}}}}

    from datetime import date
    table = calibration.Table()
    table.set_live([run("2026-10-01", 1.0)] * 3 + [run("2025-01-01", -1.0)] * 2, today=date(2026, 11, 13))
    assert table.live["swing|long|dip_catch|*|*"]["n"] == 5
    assert table.recent_live["swing|long|dip_catch|*|*"]["n"] == 3


def test_a_table_file_keeps_its_recent_cells(tmp_path):
    path = tmp_path / "cal.json"
    cell = {"swing|long|x|*|*": {"n": 40, "t1": 20, "r": 0.0, "r2": 40.0}}
    calibration.write(cell, {"source": "test"}, path, recent=cell)
    loaded = calibration.Table.load(path)
    assert loaded.recent_prior == cell and "recent_cells" not in loaded.meta


def test_recent_backtest_plans_are_discounted_like_the_long_run_not_capped_on_their_own():
    trades = _dated(3000, 1500, "2020-01-01") + _dated(180, 90, "2026-06-01")
    table = calibration.Table(calibration.from_trades(trades, "swing"),
                              recent=calibration.from_trades(trades, "swing", since="2025-11-14"))
    key = "swing|long|dip_catch|*|*"
    assert table._cell(key)["n"] == calibration.PRIOR_CAP
    assert round(table._cell(key, recent=True)["n"], 2) == round(180 * calibration.PRIOR_CAP / 3180, 2)   # ~11, not 180


def test_a_table_from_before_286_gets_no_recent_answer_from_live_runs_alone():
    from datetime import date

    run = {"trade_date": "2026-10-01",
           "plan": {"horizon": "swing", "playbooks": {"regime": {"id": "crash"},
                                                      "long": {"setup": "dip_catch", "score": 70},
                                                      "short": {"setup": "none"}}},
           "outcome": {"playbooks": {"long": {"status": "stop", "took_t1": False, "r_size": -1.0}}}}
    table = calibration.Table(calibration.from_trades(_dated(400, 240, "2021-03-01"), "swing"))
    table.set_live([run] * 60, today=date(2026, 11, 13))
    got = table.look("swing", "long", "dip_catch", "crash", 70)
    assert got["recent"] is None and got["drift"] is None and "basis" not in got


def test_a_weaker_cell_judges_negative_on_its_recent_record(monkeypatch):
    monkeypatch.setattr(calibration, "DISABLE_N", 30)
    trades = _dated(400, 240, "2021-03-01") + _dated(300, 60, "2026-06-01")      # -0.6R recently
    table = calibration.Table(calibration.from_trades(trades, "swing"),
                              recent=calibration.from_trades(trades, "swing", since="2025-11-14"))
    got = table.look("swing", "long", "dip_catch", "crash", 70)
    assert got["basis"] == "recent" and got["exp_r"] == -0.6 and got["negative"] is True
