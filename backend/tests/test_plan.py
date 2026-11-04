from desk.plan import parse_plan, reward_risk

TRADER = """**Action**: Buy

**Reasoning**: dip.

**Entry Price**: $1,182.50

**Stop Loss**: 171.0

**Position Sizing**: not provided

FINAL TRANSACTION PROPOSAL: **BUY**"""

PM = """**Rating**: Underweight

**Executive Summary**: trim.

**Investment Thesis**: rich.

**Price Target**: not provided

**Time Horizon**: 2-4 weeks"""


def test_levels_parsed_and_missing_ones_are_none():
    plan = parse_plan({"trader_investment_plan": TRADER, "final_trade_decision": PM})
    assert plan["entry_price"] == 1182.5
    assert plan["stop_loss"] == 171.0
    assert plan["price_target"] is None
    assert plan["position_sizing"] is None
    assert plan["rating"] == "Underweight"
    assert plan["bias"] == "short"
    assert plan["time_horizon"] == "2-4 weeks"


def test_state_rating_wins_and_unknown_is_review():
    assert parse_plan({"final_rating": "Buy", "final_trade_decision": PM})["rating"] == "Buy"
    assert parse_plan({})["rating"] == "REVIEW"
    assert parse_plan({})["bias"] == "review"


def test_reward_risk_needs_levels_on_both_sides():
    assert reward_risk(100, 90, 120) == 2.0      # long
    assert reward_risk(100, 110, 80) == 2.0      # short
    assert reward_risk(100, 110, 120) is None    # stop and target on the same side
    assert reward_risk(100, None, 120) is None
