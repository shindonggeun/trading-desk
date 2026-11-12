"""#284: the chart at the plan (desk.entry_features) and R by its buckets (desk.backtest.feature_report)."""

import json

from desk import backtest, cli, entry_features, horizons, setup_filter
from tests.test_backtest import _bars, _fetch, _weekly

SWING = horizons.get("swing")


def _daily(closes, volume=1000.0, gap=0.0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev + (gap if i == len(closes) - 1 else 0.0)
        out.append({"at": f"d{i:04d}", "open": o, "high": max(o, c) + 1, "low": min(o, c) - 1, "close": c,
                    "volume": volume})
        prev = c
    return out


def test_a_steady_uptrend_reads_as_a_full_stack_near_the_top_of_its_range():
    daily = _daily([100 + i for i in range(220)])
    f = entry_features.compute(daily)
    assert f["ma_stack"] == "4/4"
    assert f["range_pos"] > 0.95 and f["rsi14"] > 70 and f["ret20"] > 0 and f["slope20"] > 0
    assert f["dist20_atr"] > 0 and f["vol_ratio"] == 1.0
    # no index and no selling score given: those are None, not guessed
    assert f["rel20"] is None and f["risk_score"] is None and f["weekly_pos"] is None


def test_a_short_window_still_reads_what_it_can():
    f = entry_features.compute(_daily([100 - 0.5 * i for i in range(30)]))
    assert f["ma_stack"] == "0/1"                     # only the 20-day line is known
    assert f["ret20"] < 0 and f["slope20"] < 0
    assert entry_features.compute(_daily([100.0] * 10)) == {}


def test_the_gap_volume_and_index_strength_are_read_off_the_last_bar():
    daily = _daily([100 + 0.1 * i for i in range(60)], gap=4.0)
    daily[-1]["volume"] = 3000.0
    f = entry_features.compute(daily, risk={"score": 62, "index": {"ret20": 0.05}})
    assert f["gap_atr"] > 1 and f["vol_ratio"] == 3.0
    assert f["risk_score"] == 62 and f["rel20"] == round(f["ret20"] - 0.05, 4)


def test_the_features_do_not_change_when_the_future_changes():
    daily = _bars(520)
    t = 450                                       # past the weekly map's 60 weeks
    snap = backtest.snapshot("SOXL", SWING, daily, _weekly(daily), t)
    other = daily[:t + 1] + [{**b, "close": b["close"] * 2, "high": b["high"] * 3, "volume": 9e9}
                             for b in daily[t + 1:]]
    snap2 = backtest.snapshot("SOXL", SWING, other, _weekly(other), t)
    a = entry_features.compute(snap.daily, snap.weekly, snap.risk)
    assert a and a == entry_features.compute(snap2.daily, snap2.weekly, snap2.risk)
    assert a["weekly_pos"] is not None


def _t(at, r, feats, setup="dip_catch", took_t1=False):
    return backtest.Trade("SOXL", at, "long", setup, "crash", 50, "t2" if r > 0 else "stop", filled_shares=1.0,
                          r_net=r, took_t1=took_t1, features=feats)


def test_buckets_come_from_the_training_quartiles_and_both_halves_must_be_above_zero(monkeypatch):
    monkeypatch.setattr(backtest, "MIN_SETTLED", 4)
    monkeypatch.setattr(backtest, "MIN_VALID", 2)
    trades = []
    # rsi under 30 wins, above loses - on the training dates and the held-out ones alike
    for i in range(40):
        rsi = 20 + (i % 8) * 10                       # 20..90
        trades.append(_t(f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", 1.0 if rsi < 40 else -1.0,
                         {"rsi14": rsi, "ma_stack": "2/4"}, took_t1=rsi < 40))
    for i in range(16):
        rsi = 20 + (i % 8) * 10
        trades.append(_t(f"2024-01-{1 + i:02d}", 1.0 if rsi < 40 else -1.0, {"rsi14": rsi, "ma_stack": "2/4"}))
    rep = backtest.feature_report(trades, "2023-01-01", SWING)
    rows = rep["setups"]["long:dip_catch"]["features"]["rsi14"]
    assert [r["bucket"].split()[0] for r in rows] == ["q1", "q2", "q3", "q4"]
    low = rows[0]
    assert low["train"]["avg_r"] == 1.0 and low["valid"]["avg_r"] == 1.0 and low["positive"] and low["beats_base"]
    assert low["train"]["t1_rate"] == 1.0
    assert all(not r["positive"] for r in rows[1:])
    assert [(r["setup"], r["feature"]) for r in rep["top"]] == [("long:dip_catch", "rsi14")]
    # a category keeps its own value
    assert [r["bucket"] for r in rep["setups"]["long:dip_catch"]["features"]["ma_stack"]] == ["2/4"]


def test_a_thin_bucket_is_never_listed_on_top():
    trades = [_t(f"2020-01-{1 + i:02d}", 1.0, {"rsi14": 20.0 + i}) for i in range(20)] + \
             [_t(f"2024-01-{1 + i:02d}", 1.0, {"rsi14": 20.0 + i}) for i in range(5)]
    rep = backtest.feature_report(trades, "2023-01-01", SWING)
    assert rep["top"] == []
    assert all(not r["enough"] for r in rep["setups"]["long:dip_catch"]["features"]["rsi14"])


def test_unfilled_and_unscored_trades_stay_out():
    t = _t("2020-01-01", 1.0, {"rsi14": 20.0})
    t.filled_shares = 0.0
    rep = backtest.feature_report([t, _t("2020-01-02", 1.0, None)], "2023-01-01", SWING)
    assert rep["setups"] == {}


def test_a_run_with_features_reports_them_and_leaves_the_rest_as_it_was():
    daily = _bars(330)
    plain = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    rich = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None, features=True)
    assert plain.features == {} and rich.features["setups"]
    assert rich.baseline == plain.baseline and rich.by_setup == plain.by_setup and rich.split == plain.split


def test_cli_features_flag_writes_them(tmp_path, monkeypatch, capsys):
    daily = _bars(300)
    monkeypatch.setattr(backtest, "history", _fetch(daily))
    monkeypatch.setattr(backtest, "vix_closes", lambda start: {})
    monkeypatch.setattr(backtest, "market_risk", lambda start, tickers: backtest.MarketRisk({}))
    out = tmp_path / "r.json"
    assert cli.main(["backtest", "soxl", "--horizon", "swing", "--features", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "setups" in data["features"] and "top" in data["features"]
    assert "features: above 0 R" in capsys.readouterr().out


def test_features_are_only_computed_when_asked(monkeypatch):
    monkeypatch.setattr(setup_filter, "ENABLED", False)     # #285 reads the chart for every plan
    daily = _bars(330)
    plain = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)

    def boom(*a, **k):
        raise AssertionError("computed without --features")

    monkeypatch.setattr(entry_features, "compute", boom)
    again = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    assert again.baseline == plain.baseline and again.features == {}


def test_held_out_values_past_the_training_range_land_in_the_end_buckets(monkeypatch):
    monkeypatch.setattr(backtest, "MIN_SETTLED", 1)
    monkeypatch.setattr(backtest, "MIN_VALID", 1)
    trades = [_t(f"2020-01-{1 + i:02d}", 1.0, {"rsi14": 40.0 + i}) for i in range(8)] + \
             [_t("2024-01-01", 1.0, {"rsi14": 5.0}), _t("2024-01-02", -1.0, {"rsi14": 99.0})]
    rows = backtest.feature_report(trades, "2023-01-01", SWING)["setups"]["long:dip_catch"]["features"]["rsi14"]
    assert rows[0]["bucket"].startswith("q1 [-inf") and rows[0]["valid"]["n"] == 1
    assert rows[-1]["bucket"].endswith("+inf)") and rows[-1]["valid"]["avg_r"] == -1.0


def test_tied_values_drop_cut_points():
    assert backtest._quartile_edges([0.0] * 6 + [5.0, 9.0]) == [0.0, 5.0]
    assert backtest._bucket(0.0, [0.0, 5.0]) == "q2 [0, 5)"
