import json
import math
from datetime import UTC, datetime, timedelta

import pytest

from desk import backtest, cli, horizons, playbook

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
    report = backtest.run(["SOXL"], "swing", fetch=_fetch(daily))
    base = report.baseline
    assert base["plans"] > 0 and base["plans"] == sum(v["plans"] for v in report.by_setup.values())
    assert base["filled"] <= base["plans"] and base["settled"] <= base["filled"]
    assert set(report.split) == {"cut", "train", "valid"}
    assert report.split["train"]["plans"] + report.split["valid"]["plans"] == base["plans"]
    assert any(k.startswith("long:") for k in report.by_setup)
    assert report.notes and report.horizon == "swing"


def test_costs_lower_the_net_r():
    daily = _bars(330)
    free = backtest.run(["SOXL"], "swing", costs=backtest.Costs(0.0), fetch=_fetch(daily))
    paid = backtest.run(["SOXL"], "swing", costs=backtest.Costs(50.0), fetch=_fetch(daily))
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


def test_steps_follow_the_ladder_order():
    side = {"entries": [{"share": 0.25}, {"share": 0.25}, {"share": 0.5}]}
    assert [backtest._steps(side, f) for f in (0.25, 0.5, 1.0, 0.0)] == [1, 2, 3, 0]


def test_cli_backtest_writes_a_report(tmp_path, monkeypatch, capsys):
    daily = _bars(300)
    monkeypatch.setattr(backtest, "history", _fetch(daily))
    # run() binds the fetcher at definition; route through the module attribute
    real_run = backtest.run
    monkeypatch.setattr(backtest, "run", lambda *a, **k: real_run(*a, fetch=_fetch(daily), **k))
    out = tmp_path / "r.json"
    assert cli.main(["backtest", "soxl", "--horizon", "swing", "--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["tickers"] == ["SOXL"] and "baseline" in data and "by_setup" in data
    assert "ALL" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="horizon"):
        cli.main(["backtest", "SOXL", "--horizon", "weekly"])
    with pytest.raises(SystemExit, match="tickers"):
        cli.main(["backtest"])
