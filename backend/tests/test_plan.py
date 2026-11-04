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


# SOXL 2026-11-04: the risk debate moved the stop from the trader's 110.77 to 98.26 and the
# PM sized the target (60.73) off the revised stop. The PM schema has no entry/stop fields.
SOXL_TRADER = """**Action**: Sell

**Entry Price**: 85.75

**Stop Loss**: 110.77

**Position Sizing**: 인버스/숏 익스포저 2x, 자본의 최대 25% 규모로 제한하고 손절 시 계좌 자본 손실은 2% 이내."""

SOXL_PM = """**Rating**: Underweight

**Executive Summary**: SOXL 기존 579주 중 우선 약 145~191주(25~33%)를 감축하고, 약세 진입은 반등 저항 확인 전까지 보류합니다. 조건부 숏·인버스 진입가 $85.75, 손절가 $98.26, 목표가 $60.73이며, 진입 후 보유기간은 수일~3주입니다. SOXL 2배 명목 숏 노출은 계좌 자본의 10% 이하(예: 증거금 5%에 2배 노출)로 제한하여 손절 시 예상 자본 손실을 약 1.46%, 최대 1.5%로 관리합니다.

**Investment Thesis**: 공격적 분석가의 $85.75 진입/$110.77 손절은 약 1.15:1에 그쳐

**Price Target**: 60.73

**Time Horizon**: 수일~3주"""


def test_pm_summary_levels_override_the_trader_draft():
    plan = parse_plan({"trader_investment_plan": SOXL_TRADER, "final_trade_decision": SOXL_PM})
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (85.75, 98.26, 60.73)
    assert plan["reward_risk"] == 2.0
    assert plan["levels_origin"] == {"entry_price": "pm", "stop_loss": "pm", "price_target": "pm"}
    assert plan["trader_levels"] == {"stop_loss": 110.77}   # entry agreed, so only the stop is kept


def test_summary_numbers_that_are_not_levels_are_ignored():
    pm = """**Rating**: Hold

**Executive Summary**: 005930.KS 기존 40주는 유지합니다. 단기 매수 재검토 기준은 200일선 회복을 확인한 234,800원이며, 해당 조건부 진입의 손절가는 208,000원, 목표가는 최소 2:1 보상위험비에 해당하는 288,400원입니다. 손절 시 예상 손실이 계좌자본의 2% 이내가 되도록 합니다.

**Price Target**: 288400"""
    trader = "**Entry Price**: 227,700\n\n**Stop Loss**: 205,000"
    plan = parse_plan({"trader_investment_plan": trader, "final_trade_decision": pm})
    assert plan["entry_price"] == 227700 and plan["levels_origin"]["entry_price"] == "trader"
    assert plan["stop_loss"] == 208000 and plan["levels_origin"]["stop_loss"] == "pm"
    assert plan["price_target"] == 288400


def test_english_labels_in_the_summary_are_read():
    pm = ("**Rating**: Buy\n\n**Executive Summary**: Buy the dip. Entry Price: 1,182.5 / "
          "Stop Loss: 1,150 / Price Target: 1,250\n\n**Price Target**: 1250")
    plan = parse_plan({"trader_investment_plan": TRADER, "final_trade_decision": pm})
    assert (plan["entry_price"], plan["stop_loss"]) == (1182.5, 1150.0)
    assert plan["trader_levels"] == {"stop_loss": 171.0}


def test_without_summary_levels_the_trader_draft_stays():
    plan = parse_plan({"trader_investment_plan": TRADER, "final_trade_decision": PM})
    assert plan["levels_origin"] == {"entry_price": "trader", "stop_loss": "trader"}
    assert "trader_levels" not in plan


def _pm(summary: str, target: str = "60.73") -> str:
    return f"**Rating**: Underweight\n\n**Executive Summary**: {summary}\n\n**Price Target**: {target}"


def test_counts_units_and_dates_are_not_levels():
    trader = "**Entry Price**: 85.75\n\n**Stop Loss**: 110.77"
    for summary in ("손절가 2.5 ATR 위에 둔다.", "손절 2회 이상 반복되면 중단.", "진입 2분할, 1차 85.",
                    "진입 1차 85.", "진입 10만원 단위.", "stop 50-day MA 위.", "stop 50DMA 위.",
                    "손절가를 2026-11-04 종가 위로.", "stop 2 : 1 비율.", "Stop: 2.5R."):
        plan = parse_plan({"trader_investment_plan": trader, "final_trade_decision": _pm(summary)})
        assert (plan["entry_price"], plan["stop_loss"]) == (85.75, 110.77), summary


def test_a_revised_level_reads_the_new_number():
    trader = "**Entry Price**: 85.75\n\n**Stop Loss**: 110.77"
    plan = parse_plan({"trader_investment_plan": trader,
                       "final_trade_decision": _pm("트레이더 손절가 110.77을 98.26으로 낮춘다. 손절 98.26.")})
    assert plan["stop_loss"] == 98.26


def test_labeled_line_wins_and_the_last_one_counts():
    trader = "**Entry Price**: 85.75\n\n**Stop Loss**: 110.77"
    summary = ("초안은 Stop Loss: 110.77 였다. 손절가 $95. "
               "Entry Price: 85.75 / Stop Loss: 98.26 / Price Target: 60.73")
    plan = parse_plan({"trader_investment_plan": trader, "final_trade_decision": _pm(summary)})
    assert plan["stop_loss"] == 98.26


def test_english_prose_forms():
    trader = "**Entry Price**: 85.75\n\n**Stop Loss**: 110.77"
    for summary in ("Short with a stop at 98.26.", "Use a stop-loss of $98.26.", "손절 라인 98.26 위.",
                    "손절선 98.26.", "손절 기준은 98.26달러."):
        plan = parse_plan({"trader_investment_plan": trader, "final_trade_decision": _pm(summary)})
        assert plan["stop_loss"] == 98.26, summary


def test_prose_number_far_from_every_level_is_dropped():
    trader = "**Entry Price**: 85.75\n\n**Stop Loss**: 110.77"
    plan = parse_plan({"trader_investment_plan": trader, "final_trade_decision": _pm("손절가 9.8 로 둔다.")})
    assert plan["stop_loss"] == 110.77
