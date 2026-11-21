import json

from desk import backtest, backtest_reports, direction_stats


def test_replay_counts_settled_net_wins_once_per_cohort_and_day():
    def row(day, r_net, *, status="stop", side="long", regime="range"):
        trade = backtest.Trade("ZZZT", day, side, "pullback", regime, 60, status,
                               filled_shares=1, took_t1=True, r_net=r_net)
        return {"day": day, "trades": {side: trade}}

    stats = direction_stats.summarize([
        row("2026-05-03", 1, status="t2"), row("2026-05-01", -0.1),
        row("2026-05-01", -0.1), row("2026-05-02", 0), row("2026-05-04", 1, status="open"),
        row("2026-05-04", 1, side="short"), row("2026-05-05", 1, regime="unknown"),
    ])
    assert stats["cells"] == {
        "long|pullback|range": {"n": 3, "wins": 1, "start": "2026-05-01", "end": "2026-05-03"},
        "short|pullback|range": {"n": 1, "wins": 1, "start": "2026-05-04", "end": "2026-05-04"},
    }


def test_history_checks_cohort_threshold_and_date_range(tmp_path):
    cell = {"n": 30, "wins": 18, "start": "2026-05-01", "end": "2026-06-01"}
    report = {"horizon": "swing", "cut": "2026-05-01", "costs_bps": 10,
              "direction_stats": {"version": direction_stats.VERSION, "rules": backtest_reports.rules_fingerprint(),
                                  "cells": {
                  "long|pullback|uptrend": cell,
                  "short|pullback|range": {**cell, "n": 29},
                  "short|pullback|uptrend": {**cell, "start": "2026-04-01"},
              }}}
    (tmp_path / "entry_now_swing.json").write_text(json.dumps(report), encoding="utf-8")
    enough = direction_stats.lookup(tmp_path, "swing", "long", "pullback", "uptrend")
    assert enough == {"status": "available", "n": 30, "win_rate": 0.6, "start": "2026-05-01", "end": "2026-06-01",
                      "win_rule": direction_stats.WIN_RULE, "source": "held_out_basket_replay", "costs_bps_per_side": 10}
    thin = direction_stats.lookup(tmp_path, "swing", "short", "pullback", "range")
    assert thin["status"] == "insufficient" and thin["n"] == 29 and thin["win_rate"] is None
    for horizon, side, regime in (("swing", "long", "range"), ("swing", "short", "uptrend"),
                                 ("intraday", "long", "uptrend")):
        missing = direction_stats.lookup(tmp_path, horizon, side, "pullback", regime)
        assert missing["status"] == "unavailable" and missing["win_rate"] is None
