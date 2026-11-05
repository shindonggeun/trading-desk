"""#64: a symmetric checklist, a high-probability filter, and scoring plans on later bars."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from desk import horizons, outcome, verdict
from desk.db import make_engine, runs
from desk.plan import parse_plan
from desk.store import Store
from tests.test_api import OWNER, PASSWORD, _client, _wait_done

SWING, INTRADAY = horizons.get("swing"), horizons.get("intraday")


# --- the mandate ------------------------------------------------------------------------

def test_mandate_judges_long_and_short_by_the_same_six_factors():
    text = verdict.mandate(SWING)
    for factor in verdict.FACTORS:
        assert factor in text
    assert "long case and the short case" in text and "Within what the investor mandate allows" not in text
    assert "within what the investor mandate allows it decides how a bearish call is acted on" in text
    assert "at least 70%" in text and "reward:risk is at least 2" in text and "Long Trigger" in text
    assert "one to three weeks" in text and "the same session" not in text
    assert "the same trading day" in verdict.mandate(INTRADAY)


def test_every_run_gets_the_checklist_after_the_horizon_mandate(db_url):
    seen = []
    from desk import runner

    def fake(request, emit, portfolio):
        seen.append(portfolio.render(request.ticker))
        return runner.make_fake_runner(0)(request, emit, portfolio)
    with _client(db_url) as client:
        client.app.state.manager.runner = fake
        _wait_done(client, client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"])
        _wait_done(client, client.post("/api/runs", json={"ticker": "NVDA", "horizon": "intraday"}).json()["id"])
    swing, intraday = seen
    assert "Judgement checklist" in swing and "Trading horizon chosen" not in swing
    assert intraday.index("Trading horizon chosen") < intraday.index("Judgement checklist")


# --- reading the model's lines --------------------------------------------------------------

def test_reads_probability_checklist_and_triggers():
    decision = ("**Rating**: Buy\n\n**Executive Summary**: 매수.\nEntry Price: 100 / Stop Loss: 95 / Price Target: 112\n"
                "Win Probability: 72% / Long Trigger: 101.5 / Short Trigger: none\n\n"
                "**Investment Thesis**: 근거.\nChecklist: trend=long, location=long, momentum=neutral, volume=long, "
                "catalyst=short, regime=long")
    plan = parse_plan({"final_trade_decision": decision})
    assert plan["win_probability"] == 0.72 and plan["long_trigger"] == 101.5 and plan["short_trigger"] is None
    assert plan["checklist"]["trend"] == "long" and plan["score"] == {"long": 4, "short": 1}


@pytest.mark.parametrize("text, expected", [("Win Probability: 0.65", 0.65), ("Win Probability: 65", 0.65),
                                            ("**Win Probability**: 65 %", 0.65), ("Win Probability: 150%", None)])
def test_probability_formats(text, expected):
    assert verdict.parse(text)["win_probability"] == expected


def test_missing_lines_are_none():
    parsed = verdict.parse("**Rating**: Sell\n\n**Executive Summary**: 매도.")
    assert parsed == {"win_probability": None, "checklist": None, "score": None,
                      "long_trigger": None, "short_trigger": None}


# --- the filter --------------------------------------------------------------------------------

def _plan(**fields):
    base = {"bias": "long", "rating": "Buy", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 112.0,
            "reward_risk": 2.4, "win_probability": 0.75, "score": {"long": 4, "short": 1},
            "checklist": {"trend": "long"}}
    return {**base, **fields}


def test_a_buy_that_clears_every_bar_stays_a_buy():
    plan = verdict.apply(_plan(), SWING)
    assert plan["opinion"] == "buy" and plan["filter"]["passed"] is True


def test_a_buy_below_the_bar_becomes_hold_but_keeps_rating_bias_and_levels():
    plan = verdict.apply(_plan(win_probability=0.6, score={"long": 2, "short": 1}), SWING)
    assert plan["opinion"] == "hold" and plan["filter"]["passed"] is False
    assert len(plan["filter"]["reasons"]) == 2
    assert (plan["rating"], plan["bias"], plan["entry_price"]) == ("Buy", "long", 100.0)


def test_a_far_entry_or_thin_reward_fails():
    far = verdict.apply(_plan(entry_vs_live={"far": True}), SWING)
    thin = verdict.apply(_plan(reward_risk=1.6), SWING)
    assert far["opinion"] == thin["opinion"] == "hold"
    assert any("too far" in r for r in far["filter"]["reasons"])
    assert verdict.apply(_plan(reward_risk=1.6), horizons.get("scalp"))["opinion"] == "buy"   # scalp needs 1.5


def test_a_sell_is_judged_by_its_own_side():
    plan = verdict.apply(_plan(bias="short", rating="Sell", score={"long": 1, "short": 4}), SWING)
    assert plan["opinion"] == "sell"


def test_no_lines_at_all_is_review_and_hold_stays_hold():
    assert verdict.apply(_plan(win_probability=None, checklist=None), SWING)["opinion"] == "review"
    assert verdict.apply({"bias": "flat"}, SWING)["opinion"] == "hold"


# --- scoring ----------------------------------------------------------------------------------

PLAN_AT = "2026-11-02T14:30:00+00:00"


def _bars(rows, start=datetime(2026, 11, 2, 14, 30, tzinfo=UTC), step=timedelta(days=1)):
    return [{"at": (start + i * step).isoformat(), "open": o, "high": h, "low": low, "close": c, "volume": 1}
            for i, (o, h, low, c) in enumerate(rows)]


def _scored(rows, horizon=SWING, **plan):
    base = {"bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0,
            "entry_vs_live": {"kind": "limit"}}
    return outcome.judge({**base, **plan}, _bars(rows), horizon, PLAN_AT)


def test_limit_fill_then_target_first():
    result = _scored([(102, 103, 101, 102), (101, 101, 99.5, 100), (100, 111, 99, 110)])
    assert result["status"] == "target" and result["r"] == 2.0 and result["filled_at"].startswith("2026-11-03")


def test_stop_touched_in_the_fill_bar_counts_target_does_not():
    assert _scored([(101, 112, 94, 100)])["status"] == "stop"


def test_a_bar_touching_both_after_the_fill_is_both():
    assert _scored([(100, 101, 99.8, 100), (100, 111, 94, 100)])["status"] == "both"


def test_stop_entry_waits_for_the_break():
    result = _scored([(98, 99, 97, 98), (99, 101, 98.5, 100.5), (100, 100.5, 94.5, 95)],
                     entry_vs_live={"kind": "stop"})
    assert result["status"] == "stop" and result["filled_at"].startswith("2026-11-03")


def test_short_mirrors_every_rule():
    result = _scored([(98, 99, 97, 98), (99, 100.5, 98, 100), (100, 100.2, 89, 90)],
                     bias="short", stop_loss=105.0, price_target=90.0)
    assert result["status"] == "target"


def test_unfilled_after_the_entry_window_and_waiting_before_it():
    never = [(104, 106, 103, 105)] * 7
    assert _scored(never)["status"] == "unfilled"                 # swing: 5 sessions
    assert _scored(never[:3])["status"] == "waiting"
    windowed = _scored(never, valid_until="2026-11-03T00:00:00+00:00")
    assert windowed["status"] == "unfilled"


def test_swing_expiry_records_the_r_at_the_last_close():
    rows = [(100, 101, 99.9, 100)] + [(100, 104, 98, 102)] * 16
    result = _scored(rows)
    assert result["status"] == "expired" and result["r"] == 0.4


def test_intraday_holding_ends_with_the_session():
    start = datetime(2026, 11, 2, 14, 30, tzinfo=UTC)
    five = timedelta(minutes=5)
    day1 = [(100, 101, 99.9, 100)] + [(100, 101, 99, 100.5)] * 5
    day2 = [(100, 120, 90, 110)]
    bars = _bars(day1, start, five) + _bars(day2, start + timedelta(days=1), five)
    result = outcome.judge({"bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0,
                            "entry_vs_live": {"kind": "now"}}, bars, INTRADAY, PLAN_AT)
    assert result["status"] == "expired" and result["r"] == 0.1   # day 2's spike is outside the hold


def test_now_entry_fills_on_the_first_bar_and_passed_levels_are_invalid():
    assert _scored([(100, 111, 99, 110)], entry_vs_live={"kind": "now"})["status"] == "open"
    assert _scored([(100, 101, 99, 100)], entry_vs_live={"kind": "now", "stop_passed": True})["status"] == "invalid"


def test_bars_not_reaching_the_plan_time_wait():
    early = outcome.judge({"bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0},
                          _bars([(100, 101, 99, 100)], start=datetime(2026, 11, 1, tzinfo=UTC)), SWING, PLAN_AT)
    assert early["status"] == "waiting"


class _Bars:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    async def candles(self, ticker, interval, count):
        self.calls.append((ticker, interval))
        return {"bars": _bars(self.rows), "source": "yahoo"}


def _done_run(store, plan, created=None):
    run_id = store.create_run("NVDA", "2026-11-02", {"horizon": plan.get("horizon", "swing")})
    store.mark_done(run_id, plan, None)
    return run_id


def test_sweep_scores_live_plans_and_leaves_past_date_ones_unscorable(db_url):
    from desk.db import migrate

    migrate(db_url)
    store = Store(make_engine(db_url))
    live = {"bias": "long", "entry_price": 100.0, "stop_loss": 95.0, "price_target": 110.0,
            "entry_vs_live": {"kind": "limit"}, "live_price": {"price": 101, "fetched_at": PLAN_AT},
            "win_probability": 0.72, "opinion": "buy", "rating": "Buy"}
    good = _done_run(store, live)
    past = _done_run(store, {**live, "live_price": None})
    flat = _done_run(store, {"bias": "flat", "rating": "Hold"})
    prices = _Bars([(102, 103, 101, 102), (101, 101, 99.5, 100), (100, 111, 99, 110)])
    now = datetime(2026, 11, 6, tzinfo=UTC)
    assert asyncio.run(outcome.sweep(store, prices, now)) == 3
    assert store.get_run(good)["outcome_status"] == "target"
    assert store.get_run(past)["outcome"]["status"] == "unscorable"
    assert store.get_run(flat)["outcome_status"] == "invalid"
    assert asyncio.run(outcome.sweep(store, prices, now)) == 0        # terminal outcomes are not redone
    assert store.clear_outcomes() == 3
    with store.engine.connect() as conn:
        assert conn.execute(select(runs.c.outcome_status)).scalars().all() == [None, None, None]


def test_stats_group_outcomes_and_calibrate_probability():
    def run(status, probability, opinion, rating="Buy", bias="long"):
        return {"plan": {"bias": bias, "win_probability": probability, "opinion": opinion, "rating": rating,
                         "horizon": "swing"},
                "options": {"analysts": ["market"]}, "outcome_status": status,
                "outcome": {"status": status, "r": None}}
    stats = outcome.stats([run("target", 0.75, "buy"), run("stop", 0.72, "buy"), run("target", 0.55, "hold"),
                           run("unfilled", 0.8, "buy"), run(None, 0.7, "buy"),
                           {"plan": {"bias": "flat"}, "outcome_status": "invalid"}])
    assert stats["total"] == {"plans": 5, "scored": 4, "waiting": 1, "unscorable": 0}
    by_opinion = {row["key"]: row for row in stats["by_opinion"]}
    assert by_opinion["buy"]["hit_rate"] == 0.5 and by_opinion["hold"]["hit_rate"] == 1.0
    buckets = {row["bucket"]: row for row in stats["calibration"]}
    assert buckets["70-80"]["resolved"] == 2 and buckets["70-80"]["hits"] == 1


def test_stats_endpoint_needs_login_and_clamps_days(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/stats/plans").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/stats/plans?days=9999").json()
        assert body["days"] == 90 and body["thresholds"]["win_probability"] == 0.7
        assert {"total", "by_horizon", "by_opinion", "calibration"} <= set(body)


def test_a_scripted_run_carries_the_new_fields(db_url):
    with _client(db_url) as client:
        run = _wait_done(client, client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"])
    plan = run["plan"]
    assert plan["win_probability"] == 0.72 and plan["long_trigger"] == 185.0 and plan["short_trigger"] == 170.0
    assert plan["score"] == {"long": 4, "short": 1} and plan["opinion"] in ("buy", "hold")
    assert run["outcome_status"] is None      # fake runs are never scored
