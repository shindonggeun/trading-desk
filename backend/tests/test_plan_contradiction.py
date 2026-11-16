"""#436: the PM's unbolded level line is read before the trader's draft, and levels that cannot be
one plan lose the field that breaks them (the page's levelsContradict rule)."""

import pytest

from desk.plan import drop_contradiction, parse_plan, with_sides

# KORU 2026-11-16 (run ba3086116362), the parts that carry levels - the holdings line left out
TRADER = """**Action**: Hold

**Reasoning**: 지지 구역의 하단 이탈을 확인하는 조건부 숏이 더 타당합니다.

**Entry Price**: 6.25

**Stop Loss**: 7.8

**Position Sizing**: 2x 이하 레버리지, 손절 도달 시 계좌 자본 손실 2% 이내"""

PM = """**Rating**: Hold

**Executive Summary**: KORU는 약세 추세가 우세하지만 현재가 7.21은 강한 지지대 6.28~7.64 안이고, 신규 숏은 8.54~8.94 저항대에서만 검토합니다.
Entry Price: 8.94 / Stop Loss: 10 / Price Target: 6.64
Win Probability: 48% / Long Trigger: 7.64 / Short Trigger: 6.28

**Investment Thesis**: 위치는 강한 지지 구역 안이므로 숏에 불리합니다.

**Price Target**: 6.64

**Time Horizon**: 1-3 weeks"""


def test_the_pms_unbolded_level_line_wins_over_the_traders_draft():
    plan = parse_plan({"trader_investment_plan": TRADER, "final_trade_decision": PM})
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (8.94, 10.0, 6.64)
    assert plan["levels_origin"] == {"entry_price": "pm", "stop_loss": "pm", "price_target": "pm"}
    assert plan["trader_levels"] == {"entry_price": 6.25, "stop_loss": 7.8}
    assert "levels_rejected" not in plan
    assert with_sides(plan)["levels_side"] == "short"
    assert plan["reward_risk"] == round((8.94 - 6.64) / (10 - 8.94), 2)


def test_without_that_line_the_mixed_levels_lose_the_target():
    pm = PM.replace("Entry Price: 8.94 / Stop Loss: 10 / Price Target: 6.64\n", "")
    plan = parse_plan({"trader_investment_plan": TRADER, "final_trade_decision": pm})
    # trader's short 6.25 / 7.8 with the PM's 6.64 target above the entry: the target goes
    assert (plan["entry_price"], plan["stop_loss"], plan["price_target"]) == (6.25, 7.8, None)
    assert plan["levels_rejected"] == {"price_target": 6.64}
    assert "price_target" not in plan["levels_origin"]
    assert "목표 6.64" in plan["levels_contradiction"] and plan["reward_risk"] is None


@pytest.mark.parametrize(("levels", "kept", "dropped"), [
    ((10.0, 9.0, 12.0), (10.0, 9.0, 12.0), {}),                       # long, in order
    ((10.0, 11.0, 8.0), (10.0, 11.0, 8.0), {}),                       # short, in order
    ((10.0, 9.0, 8.0), (10.0, 9.0, None), {"price_target": 8.0}),     # long stop, target below the entry
    ((10.0, 11.0, 12.0), (10.0, 11.0, None), {"price_target": 12.0}),  # short stop, target above
    ((8.64, 10.43, 10.43), (8.64, 10.43, None), {"price_target": 10.43}),   # stop = target (#430's KORU row)
    ((10.0, 10.0, 12.0), (10.0, None, 12.0), {"stop_loss": 10.0}),    # entry = stop: no side
    ((10.0, 10.0, None), (10.0, None, None), {"stop_loss": 10.0}),
    ((None, 9.0, 9.0), (None, 9.0, None), {"price_target": 9.0}),     # stop = target without an entry
    ((10.0, None, 8.0), (10.0, None, 8.0), {}),                       # missing is not contradicting
    ((None, None, None), (None, None, None), {}),
])
def test_contradicting_levels_drop_the_field_that_breaks_them(levels, kept, dropped):
    *got, rejected = drop_contradiction(*levels)
    assert tuple(got) == kept and rejected == dropped
