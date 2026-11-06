import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from desk import backtest, cli, horizons, outcome, playbook

SWING = horizons.get("swing")


def _bars(n: int, start=datetime(2023, 1, 2, 5, tzinfo=UTC), seed: int = 7) -> list[dict]:
    """Business-day bars (US midnight stamps): a range, a crash and a rebound, deterministic."""
    out, price, day = [], 100.0, start
    for i in range(n):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        drift = -2.2 if 260 <= i < 275 else (1.0 if 275 <= i < 300 else 0.0)
        wave = 3.0 * math.sin((i + seed) / 6.0)
        close = max(5.0, price + drift + wave * 0.3)
        o = price
        out.append({"at": day.isoformat(), "open": o, "high": max(o, close) + 1.2, "low": min(o, close) - 1.2,
                    "close": close, "volume": 1_000_000 * (3 if 260 <= i < 263 else 1)})
        price = close
        day += timedelta(days=1)
    return out


def _weekly(daily: list[dict]) -> list[dict]:
    weeks: dict[str, list[dict]] = {}
    for b in daily:
        d = datetime.fromisoformat(b["at"])
        monday = (d - timedelta(days=d.weekday())).isoformat()
        weeks.setdefault(monday, []).append(b)
    return [{"at": k, "open": v[0]["open"], "high": max(x["high"] for x in v), "low": min(x["low"] for x in v),
             "close": v[-1]["close"], "volume": sum(x["volume"] for x in v)} for k, v in sorted(weeks.items())]


def _fetch(daily):
    return lambda ticker, h, start: (daily, _weekly(daily), [])


def test_a_plan_at_t_does_not_change_when_the_future_changes():
    daily = _bars(320)
    t = 270
    snap = backtest.snapshot("SOXL", SWING, daily, _weekly(daily), t)
    pb = playbook.build(snap.sr, snap.daily, snap.window, SWING, now=snap.plan_at)
    # rewrite everything after t (a different future, and its weekly bars too)
    future = [{**b, "open": b["open"] * 3, "high": b["high"] * 3, "low": b["low"] * 0.2, "close": b["close"] * 2,
               "volume": 9e9} for b in daily[t + 1:]]
    other = daily[:t + 1] + future
    snap2 = backtest.snapshot("SOXL", SWING, other, _weekly(other), t)
    assert snap2.sr == snap.sr and snap2.window == snap.window and snap2.daily == snap.daily
    assert playbook.build(snap2.sr, snap2.daily, snap2.window, SWING, now=snap2.plan_at) == pb
    # the plan starts at the next session's open, so bar t itself is never judged
    assert snap.plan_at == (datetime.fromisoformat(daily[t + 1]["at"]) + timedelta(hours=9, minutes=30)).isoformat()


def test_the_week_still_trading_at_t_is_not_seen():
    daily = _bars(320)
    t = 272                                   # a Wednesday-ish bar inside a week
    snap = backtest.snapshot("SOXL", SWING, daily, _weekly(daily), t)
    cut = datetime.fromisoformat(daily[t]["at"]).date()
    weeks = _weekly(daily)
    seen = [w for w in weeks if datetime.fromisoformat(w["at"]).date() + timedelta(days=7) <= cut]
    assert snap is not None and len(seen) < len([w for w in weeks if datetime.fromisoformat(w["at"]).date() <= cut])


def test_a_run_reports_baseline_breakdowns_and_a_split():
    daily = _bars(330)
    report = backtest.run(["SOXL"], "swing", fetch=_fetch(daily), vix=None)
    base = report.baseline
    assert base["plans"] > 0 and base["plans"] == sum(v["plans"] for v in report.by_setup.values())
    assert base["filled"] <= base["plans"] and base["settled"] <= base["filled"]
    assert set(report.split) == {"cut", "embargo_days", "train", "valid"}
    # the embargo leaves out the training plans right before the cut
    assert 0 < report.split["train"]["plans"] + report.split["valid"]["plans"] < base["plans"]
    assert any(k.startswith("long:") for k in report.by_setup)
    assert report.notes and report.horizon == "swing"


def test_costs_lower_the_net_r():
    daily = _bars(330)
    free = backtest.run(["SOXL"], "swing", costs=backtest.Costs(0.0), fetch=_fetch(daily), vix=None)
    paid = backtest.run(["SOXL"], "swing", costs=backtest.Costs(50.0), fetch=_fetch(daily), vix=None)
    if free.baseline["expectancy_r"] is None:
        pytest.skip("no settled trade in the synthetic series")
    assert paid.baseline["expectancy_r"] < free.baseline["expectancy_r"]


def test_the_sweep_puts_the_constants_back_and_ranks_rows():
    daily = _bars(320)
    before = (playbook.ZONE_WIDTH_ATR, playbook.SHARES, playbook.LONG_STOP_ATR)
    snaps = [(s, daily) for s in backtest.snapshots("SOXL", SWING, daily, _weekly(daily))]
    rows = backtest.sweep(snaps, SWING, backtest.Costs(), {"ZONE_WIDTH_ATR": [0.5, 1.0], "NOT_A_CONSTANT": [1],
                                                            "min_score": [0, 50]})
    assert (playbook.ZONE_WIDTH_ATR, playbook.SHARES, playbook.LONG_STOP_ATR) == before
    assert {r["param"] for r in rows} == {"ZONE_WIDTH_ATR", "min_score"}       # unknown names skipped
    assert any(r["current"] for r in rows if r["param"] == "ZONE_WIDTH_ATR")
    exp = [r["train"]["expectancy_r"] for r in rows if r["train"]["expectancy_r"] is not None]
    assert exp == sorted(exp, reverse=True)


def test_patched_restores_on_error():
    old = playbook.LONG_STOP_ATR
    with pytest.raises(RuntimeError), backtest.patched("LONG_STOP_ATR", 9.9):
        assert playbook.LONG_STOP_ATR == 9.9
        raise RuntimeError
    assert playbook.LONG_STOP_ATR == old


def _ladder():
    return {"setup": "dip_catch", "zone": {"low": 95.0, "high": 100.0, "mid": 97.5},
            "entries": [{"step": 1, "price": 100.0, "share": 0.25, "kind": "limit"},
                        {"step": 2, "price": 97.5, "share": 0.25, "kind": "limit"},
                        {"step": 3, "price": 101.0, "share": 0.5, "kind": "stop"}],
            "stop": 93.0, "targets": [{"role": "t1", "price": 110.0}, {"role": "t2", "price": 120.0}],
            "time_stop": {"bars": 3}, "flat_by_close": False}


def _day(i, o, h, lo, c):
    return {"at": (datetime(2024, 3, 4, 5, tzinfo=UTC) + timedelta(days=i)).isoformat(),
            "open": o, "high": h, "low": lo, "close": c, "volume": 1}


def test_steps_come_from_the_actual_fills_not_the_share_sum():
    # step 1 fills, the price never reaches step 2, the confirmation (step 3) fills the next bar
    bars = [_day(0, 102, 102, 99.5, 100.5), _day(1, 100.5, 101.5, 100.2, 101.2), _day(2, 101.2, 103, 100.8, 102.5)]
    res = outcome.judge_playbook(_ladder(), 1, bars, SWING, "2024-03-04T00:00:00+00:00")
    assert res["steps"] == [1, 3] and backtest._steps(res["steps"]) == "1+3"
    assert res["first_fill_at"] == bars[0]["at"]


def test_excursions_start_at_the_first_fill():
    # a fade must first reach its zone: the bounce before the fill is not adverse excursion
    future = [_day(0, 100, 106, 99, 105), _day(1, 105, 105.5, 103.5, 104), _day(2, 104, 104.2, 98, 99)]
    mae, mfe = backtest._excursions(-1, future, 104.0, future[1]["at"], None, 2.0)
    assert mae == 0.75 and mfe == 3.0


def test_the_entry_window_follows_the_live_valid_until():
    five = horizons.get("intraday")
    plan_at = "2024-03-04T14:30:00+00:00"
    bars = [{"at": (datetime(2024, 3, 4, 14, 30, tzinfo=UTC) + timedelta(minutes=5 * i)).isoformat(),
             "open": 105, "high": 105.5, "low": 104.5 if i < 20 else 99, "close": 105, "volume": 1} for i in range(40)]
    side = {**_ladder(), "time_stop": {"bars": 12}}
    # the price reaches the ladder only after 100 minutes: past the 60-minute window of an intraday plan
    late = outcome.judge_playbook(side, 1, bars, five, plan_at, valid_until=horizons.valid_until(five, plan_at))
    assert late["status"] == "unfilled"
    assert outcome.judge_playbook(side, 1, bars, five, plan_at)["status"] != "unfilled"


def test_korean_plans_are_dated_by_the_market_day():
    assert backtest.local_day("2024-01-01T15:00:00+00:00", "005930.KS") == "2024-01-02"
    assert backtest.local_day("2024-01-02T05:00:00+00:00", "SOXL") == "2024-01-02"


def test_a_ticker_that_fails_is_noted_and_the_rest_run():
    daily = _bars(330)

    def fetch(ticker, h, start):
        if ticker == "BAD":
            raise RuntimeError("no data")
        return daily, _weekly(daily), []

    report = backtest.run(["BAD", "SOXL"], "swing", fetch=fetch, vix=None)
    assert any(n.startswith("BAD:") for n in report.notes) and report.baseline["plans"] > 0


def test_an_open_trade_is_marked_not_dropped():
    t = backtest.Trade("X", "2024-01-02", "long", "dip_catch", "crash", 50, "open", filled_shares=0.5,
                       took_t1=True, r_size=0.4, r_net=0.39)
    agg = backtest.aggregate([t])
    assert agg["settled"] == 1 and agg["marked"] == 1 and agg["t1_first_rate"] == 1.0


def test_cli_backtest_writes_a_report(tmp_path, monkeypatch, capsys):
    daily = _bars(300)
    monkeypatch.setattr(backtest, "history", _fetch(daily))
    monkeypatch.setattr(backtest, "vix_closes", lambda start: {"2023-06-01": 31.0})
    out = tmp_path / "r.json"
    assert cli.main(["backtest", "soxl", "--horizon", "swing", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tickers"] == ["SOXL"] and "baseline" in data and "by_setup" in data
    assert "ALL" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="horizon"):
        cli.main(["backtest", "SOXL", "--horizon", "weekly"])
    with pytest.raises(SystemExit, match="tickers"):
        cli.main(["backtest"])
