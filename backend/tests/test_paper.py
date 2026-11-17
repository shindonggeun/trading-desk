"""#527 the paper account: the scored plans traded with won on paper - sizing, costs, the dollar rate, the
skips, the order of the same moment, the exits as the plan scorer judged them, and the API."""

from datetime import UTC, datetime, timedelta

import pytest

from desk import paper

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
FREE = {**paper.DEFAULTS, "fee_kr_pct": 0.0, "fee_us_pct": 0.0, "tax_kr_pct": 0.0, "slippage_pct": 0.0}
RATES = {"2026-10-01": 1400.0, "2026-10-05": 1500.0, "2026-11-17": 1450.0}


def _run(rid: str, ticker: str = "005930.KS", bias: str = "long", entry: float = 10_000, stop: float = 9_500,
         target: float = 11_000, status: str | None = "target", filled: str = "2026-10-01T01:00:00+00:00",
         exited: str | None = "2026-10-05T01:00:00+00:00", r: float | None = 2.0,
         plan_at: str = "2026-10-01T00:30:00+00:00", rating: str = "Buy") -> dict:
    outcome = None if status is None else {"status": status, "plan_at": plan_at, "r": r,
                                            "filled_at": filled if status in paper.FILLED else None,
                                            "exited_at": exited if status in paper.CLOSED else None}
    return {"id": rid, "ticker": ticker, "options": {"name": None, "horizon": "swing"},
            "plan": {"bias": bias, "entry_price": entry, "stop_loss": stop, "price_target": target,
                     "rating": rating, "horizon": "swing", "live_price": {"fetched_at": plan_at}},
            "outcome": outcome, "created_at": datetime.fromisoformat(plan_at), "scored_at": NOW}


def _sim(runs, **settings):
    return paper.simulate(runs, {**FREE, **settings}, RATES, NOW)


def _trade(result, rid):
    return next(t for t in result["trades"] if t["run_id"] == rid)


def test_risk_sizing_loses_one_percent_at_the_stop_and_makes_r_times_that_at_the_target():
    got = _sim([_run("a")])
    t = _trade(got, "a")
    assert t["qty"] == 200 and t["amount_krw"] == 2_000_000              # 1% of 10M / (10,000 - 9,500)
    assert t["status"] == "closed" and t["exit_reason"] == "target" and t["pnl_krw"] == 200_000 and t["r"] == 2.0
    assert got["summary"]["balance"] == 10_200_000 and got["summary"]["total_pnl_pct"] == 2.0
    lost = _trade(_sim([_run("b", status="stop", r=-1.0)]), "b")
    assert lost["pnl_krw"] == -100_000 and lost["exit"]["price"] == 9_500


def test_weight_sizing_buys_a_share_of_the_balance():
    t = _trade(_sim([_run("a")], sizing="weight", weight_pct=10.0), "a")
    assert t["qty"] == 100 and t["amount_krw"] == 1_000_000 and t["pnl_krw"] == 100_000


def test_fees_tax_and_slippage_come_off_the_result():
    s = {**paper.DEFAULTS}                                               # 0.015% fee, 0.18% sell tax, 0.05% slip
    t = _trade(paper.simulate([_run("a")], s, RATES, NOW), "a")
    fill_in, fill_out = 10_000 * 1.0005, 11_000 * 0.9995
    qty = int(100_000 // (fill_in - 9_500))
    cost_in = qty * fill_in * 0.00015
    out = qty * fill_out
    cost_out = out * 0.00015 + out * 0.0018
    assert t["qty"] == qty and t["entry"]["fill"] == pytest.approx(fill_in) and t["exit"]["fill"] == pytest.approx(fill_out)
    assert t["costs_krw"] == round(cost_in + cost_out)
    assert t["pnl_krw"] == round(out - qty * fill_in - cost_in - cost_out)


def test_a_us_trade_is_converted_at_the_fill_and_exit_days_rates():
    t = _trade(_sim([_run("u", ticker="SOXL", entry=100, stop=95, target=110)]), "u")
    assert t["market"] == "US" and t["currency"] == "USD" and t["fx"] == {"entry": 1400.0, "exit": 1500.0}
    assert t["qty"] == 14 and t["amount_krw"] == 14 * 100 * 1400                 # 100,000 / (5 x 1,400)
    assert t["pnl_krw"] == 14 * 110 * 1500 - 14 * 100 * 1400
    gone = paper.simulate([_run("u", ticker="SOXL", entry=100, stop=95, target=110)], FREE, {}, NOW)
    assert _trade(gone, "u")["skip"]["code"] == "no_fx"


def test_a_short_gains_when_the_price_falls_and_loses_at_its_stop():
    won = _trade(_sim([_run("s", bias="short", entry=10_000, stop=10_500, target=9_000)]), "s")
    assert won["pnl_krw"] == 200_000 and won["direction"] == "short"
    lost = _trade(_sim([_run("s", bias="short", entry=10_000, stop=10_500, target=9_000, status="stop", r=-1.0)]), "s")
    assert lost["pnl_krw"] == -100_000
    off = _trade(_sim([_run("s", bias="short", entry=10_000, stop=10_500, target=9_000)], allow_short=False), "s")
    assert off["status"] == "skipped" and off["skip"]["code"] == "short_off"


def test_expired_both_and_open_exits_follow_the_plan_scorer():
    expired = _trade(_sim([_run("e", status="expired", r=0.4)]), "e")
    assert expired["exit"]["price"] == 10_000 + 0.4 * 500 and expired["pnl_krw"] == 40_000
    both = _sim([_run("b", status="both", r=None)])
    assert _trade(both, "b")["exit"]["price"] == 9_500 and _trade(both, "b")["r"] is None
    assert both["summary"]["avg_r"] == -1.0                               # counted at the stop
    opened = _sim([_run("o", status="open", r=0.5)])
    o = _trade(opened, "o")
    assert o["status"] == "open" and o["marked"] and o["pnl_krw"] == 50_000 and o["exit"] is None
    assert opened["summary"]["unrealized_pnl"] == 50_000 and opened["summary"]["balance"] == 10_050_000
    assert opened["summary"]["invested"] == 2_000_000 and opened["summary"]["cash"] == 8_000_000


def test_unfilled_waiting_unscored_and_odd_plans():
    runs = [_run("n", status="unfilled", r=None), _run("w", status="waiting", r=None), _run("x", status=None),
            _run("i", status="invalid"), _run("q", status="unscorable", r=None),
            _run("t", ticker="7203.T", entry=100, stop=95, target=110)]
    got = _sim(runs)
    ids = {t["run_id"]: t for t in got["trades"]}
    assert "i" not in ids                                                   # an invalid plan is no trade
    assert ids["n"]["skip"]["code"] == "no_fill" and ids["q"]["skip"]["code"] == "unscorable"
    assert ids["w"]["status"] == "waiting" and ids["x"]["status"] == "waiting"
    assert ids["t"]["skip"]["code"] == "unsupported"
    assert got["counts"] == {"closed": 0, "open": 0, "waiting": 2, "skipped": 3}


def test_no_cash_and_the_position_limit_skip_a_trade():
    a = _run("a", exited="2026-10-09T01:00:00+00:00")
    b = _run("b", filled="2026-10-02T01:00:00+00:00", plan_at="2026-10-01T00:40:00+00:00")
    full = _sim([a, b], sizing="weight", weight_pct=100.0)
    assert _trade(full, "a")["amount_krw"] == 10_000_000 and _trade(full, "b")["skip"]["code"] == "no_cash"
    limited = _sim([a, b], max_positions=1)
    assert _trade(limited, "b")["skip"]["code"] == "max_positions"
    small = _sim([_run("s")], start_capital=100_000, risk_pct=0.1)           # 100 won of risk, 500 a share
    assert _trade(small, "s")["skip"]["code"] == "too_small"


def test_at_the_same_moment_the_exit_frees_cash_before_the_entry():
    a = _run("a", exited="2026-10-05T01:00:00+00:00")
    b = _run("b", filled="2026-10-05T01:00:00+00:00", exited="2026-10-09T01:00:00+00:00",
             plan_at="2026-10-04T00:00:00+00:00")
    got = _sim([b, a], sizing="weight", weight_pct=100.0)
    assert _trade(got, "a")["status"] == "closed" and _trade(got, "b")["status"] == "closed"
    assert _trade(got, "b")["amount_krw"] == 11_000_000                     # bought with a's profit too
    # two entries at the same moment: the earlier plan first
    c = _run("c", filled="2026-10-01T01:00:00+00:00", plan_at="2026-10-01T00:10:00+00:00")
    later = _sim([_run("a"), c], sizing="weight", weight_pct=100.0)
    assert _trade(later, "c")["status"] == "closed" and _trade(later, "a")["skip"]["code"] == "no_cash"


def test_the_curve_drawdown_periods_and_breakdowns():
    runs = [_run("a"), _run("b", filled="2026-10-06T01:00:00+00:00", exited="2026-10-08T01:00:00+00:00",
                            status="stop", r=-1.0, plan_at="2026-10-06T00:00:00+00:00", rating="Overweight")]
    got = _sim(runs)
    pts = {p["date"]: p["balance"] for p in got["equity"]["points"]}
    assert pts["2026-10-01"] == 10_000_000 and pts["2026-10-05"] == 10_200_000 and pts["2026-10-08"] == 10_098_000
    assert got["equity"]["points"][-1]["date"] == "2026-11-17"
    dd = got["summary"]["max_drawdown"]
    assert dd["krw"] == -102_000 and dd["peak_at"] == "2026-10-05" and dd["trough_at"] == "2026-10-08"
    assert sum(w["pnl"] for w in got["equity"]["weekly"]) == sum(m["pnl"] for m in got["equity"]["monthly"]) == 98_000
    s = got["summary"]
    assert (s["wins"], s["losses"], s["win_rate"], s["avg_r"], s["payoff"]) == (1, 1, 0.5, 0.5, 1.96)
    assert {b["key"] for b in s["by_rating"]} == {"Buy", "Overweight"} and s["by_direction"][0]["trades"] == 2
    assert s["this_month"] == {"start": "2026-11-01", "pnl": 0, "pct": 0.0}


def test_the_start_date_and_the_same_input_give_the_same_account():
    runs = [_run("a"), _run("b", plan_at="2026-11-02T00:00:00+00:00", filled="2026-11-02T01:00:00+00:00",
                            exited="2026-11-03T01:00:00+00:00")]
    assert _sim(runs) == _sim(list(reversed(runs)))
    late = _sim(runs, start_date="2026-11-01")
    assert [t["run_id"] for t in late["trades"]] == ["b"] and late["equity"]["points"][0]["date"] == "2026-11-01"


def test_every_trade_r_is_the_plan_scores_r():
    runs = [_run("a"), _run("b", status="stop", r=-1.3, filled="2026-10-10T01:00:00+00:00",
                            exited="2026-10-12T01:00:00+00:00", plan_at="2026-10-10T00:00:00+00:00"),
            _run("c", status="expired", r=0.25, filled="2026-10-20T01:00:00+00:00",
                 exited="2026-10-25T01:00:00+00:00", plan_at="2026-10-20T00:00:00+00:00")]
    got = _sim(runs)
    assert {t["run_id"]: t["r"] for t in got["trades"]} == {r["id"]: r["outcome"]["r"] for r in runs}
    lost = _trade(got, "b")
    assert lost["exit"]["price"] == pytest.approx(10_000 - 1.3 * 500)        # a gap through the stop, as scored


def test_settings_are_checked():
    base = paper.DEFAULTS
    assert paper.validate({"risk_pct": 2, "start_date": "2026-10-01", "version": 9}, base)["risk_pct"] == 2.0
    for bad in ({"risk_pct": 9}, {"start_capital": 10}, {"sizing": "all"}, {"start_date": "10/01"},
                {"allow_short": "yes"}, {"max_positions": True}, {"fee_kr_pct": float("nan")}):
        with pytest.raises(ValueError):
            paper.validate(bad, base)


def test_the_api_keeps_the_settings_and_recomputes(db_url):
    from desk.db import make_engine
    from desk.db import runs as runs_t
    from tests.test_api import _client

    with _client(db_url) as client:
        s = client.get("/api/paper/settings").json()
        assert s["version"] == 0 and s["start_capital"] == 10_000_000 and s["sizing"] == "risk"
        engine = make_engine(db_url)
        with engine.begin() as conn:
            for run in (_run("a"), _run("o", status="open", r=0.5, filled="2026-11-10T01:00:00+00:00",
                                        plan_at="2026-11-10T00:00:00+00:00")):
                conn.execute(runs_t.insert().values(
                    id=run["id"], ticker=run["ticker"], trade_date="2026-10-01", status="done", options=run["options"],
                    plan=run["plan"], outcome=run["outcome"], outcome_status=run["outcome"]["status"],
                    created_at=run["created_at"], scored_at=NOW))
        engine.dispose()
        summary = client.get("/api/paper/summary").json()
        assert summary["settings_version"] == 0 and summary["trades"]["closed"] == 1 and summary["trades"]["open"] == 1
        assert 190_000 < summary["realized_pnl"] < 200_000                    # 2R less the default costs
        assert summary["balance"] == 10_000_000 + summary["realized_pnl"] + summary["unrealized_pnl"]
        saved = client.put("/api/paper/settings", json={"start_capital": 20_000_000, "fee_kr_pct": 0}).json()
        assert saved["version"] == 1 and saved["start_capital"] == 20_000_000 and saved["risk_pct"] == 1.0
        again = client.get("/api/paper/summary").json()
        assert again["settings_version"] == 1 and again["start_capital"] == 20_000_000
        assert client.put("/api/paper/settings", json={"risk_pct": 50}).status_code == 422
        assert client.put("/api/paper/settings", json={"sizing": "all"}).status_code == 422
        trades = client.get("/api/paper/trades?status=closed").json()
        assert trades["counts"]["closed"] == 1 and [t["run_id"] for t in trades["items"]] == ["a"]
        assert client.get("/api/paper/trades?status=nope").status_code == 422
        eq = client.get("/api/paper/equity").json()
        assert eq["settings_version"] == 1 and eq["points"][0]["balance"] == 20_000_000
        client.cookies.clear()
        for path in ("/api/paper/settings", "/api/paper/summary", "/api/paper/equity", "/api/paper/trades"):
            assert client.get(path).status_code == 401
        assert client.put("/api/paper/settings", json={"risk_pct": 2}).status_code in (401, 403)


def test_a_rate_failure_skips_only_the_us_trades(tmp_path):
    from desk.db import make_engine, migrate

    url = f"sqlite:///{tmp_path / 'p.db'}"
    migrate(url)
    engine = make_engine(url)

    def boom():
        raise OSError("no network")
    desk = paper.Paper(engine, boom)
    assert desk.rates(True) == {} and desk.rates(False) == {}
    assert desk.summary()["trades"] == {"closed": 0, "open": 0, "waiting": 0, "skipped": 0}
    assert desk.settings()["version"] == 0 and desk.save({"max_positions": 3})["version"] == 1
    assert desk.save({"max_positions": 4})["version"] == 2
    engine.dispose()
    assert paper.market_of("BRK-B") == "US" and paper.market_of("BTC-USD") == "US"
    assert paper.market_of("035720.KQ") == "KR" and paper.market_of("^GSPC") is None
    assert paper.fx_on({"2026-10-01": 1400.0}, (datetime(2026, 10, 4)).date()) == 1400.0
    assert paper.fx_on({"2026-10-01": 1400.0}, (datetime(2026, 10, 1) + timedelta(days=10)).date()) is None
