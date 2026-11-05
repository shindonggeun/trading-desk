"""The closing summary (#94): votes, both sides' case, the debate and a one-line conclusion."""

import json

import pytest

from desk import consensus
from desk.consensus import ConsensusContext, build_prompt, fixed_votes, parse, render

STATE = {
    "market_report": "## 기술적\n20일선 위",
    "news_report": "## 뉴스\n수주 보도",
    "investment_debate_state": {"bull_history": "Bull Analyst: 사자", "bear_history": "Bear Analyst: 팔자"},
    "investment_plan": "**Recommendation**: Overweight\n\n**Rationale**: 성장 우세",
    "trader_investment_plan": "**Action**: Buy\n\nFINAL TRANSACTION PROPOSAL: **BUY**",
    "risk_debate_state": {"aggressive_history": "Aggressive Analyst: 늘리자", "conservative_history":
                          "Conservative Analyst: 줄이자", "neutral_history": ""},
    "final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: 기다린다",
}
PLAN = {"rating": "Hold", "opinion": "hold", "entry_price": None, "long_trigger": 190.0, "short_trigger": 170.0,
        "win_probability": 55}


def _answer(**over):
    data = {
        "votes": [
            {"agent": "market_analyst", "stance": "long", "reason": "추세 위"},
            {"agent": "news_analyst", "stance": "LONG", "reason": "수주"},
            {"agent": "bull_researcher", "stance": "long", "reason": "x"},
            {"agent": "bear_researcher", "stance": "short", "reason": "y"},
            {"agent": "trader", "stance": "short", "reason": "모델이 잘못 본 트레이더"},
            {"agent": "conservative_analyst", "stance": "hold", "reason": "z"},
            {"agent": "sentiment_analyst", "stance": "long", "reason": "말하지 않은 에이전트"},
            {"agent": "astrologer", "stance": "long", "reason": "모르는 에이전트"},
            {"agent": "aggressive_analyst", "stance": "moon", "reason": "모르는 입장"},
        ],
        "long_case": ["a" * 500, "b", "", "c", "d", "e", "f"],
        "short_case": ["밸류 부담"],
        "debate": "공격형은 늘리자, 보수형은 줄이자.",
        "conclusion": "관망 — 190 돌파 시 롱, 170 이탈 시 숏.",
    }
    data.update(over)
    return "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"


def test_fixed_votes_are_read_from_the_labelled_calls():
    assert fixed_votes(STATE) == {"research_manager": "long", "trader": "long", "portfolio_manager": "hold"}


def test_parse_checks_the_model_and_puts_the_fixed_votes_over_it():
    s = parse(_answer(), STATE, "NVDA")
    by = {v["agent"]: v for v in s["votes"]}
    # unknown agent, unknown stance and an agent with no text are dropped
    assert "astrologer" not in by and "aggressive_analyst" not in by and "sentiment_analyst" not in by
    assert by["news_analyst"]["stance"] == "long"
    # the trader said BUY with its fixed label: the model's "short" loses, its reason stays
    assert by["trader"] == {"agent": "trader", "stance": "long", "reason": "모델이 잘못 본 트레이더", "fixed": True}
    assert by["portfolio_manager"]["stance"] == "hold" and by["portfolio_manager"]["fixed"]
    assert by["research_manager"]["stance"] == "long"
    # pipeline order
    assert [v["agent"] for v in s["votes"]][:2] == ["market_analyst", "news_analyst"]
    assert s["tally"] == {"long": 5, "short": 1, "hold": 2}
    assert s["majority"] == "long"
    assert len(s["long_case"]) == consensus.MAX_POINTS and len(s["long_case"][0]) == consensus.POINT_CHARS
    assert s["subject"] == "NVDA"


def test_a_tie_has_no_majority():
    state = {"investment_debate_state": {"bull_history": "Bull: a", "bear_history": "Bear: b"}}
    s = parse(json.dumps({"votes": [{"agent": "bull_researcher", "stance": "long"},
                                    {"agent": "bear_researcher", "stance": "short"}]}), state, "X")
    assert s["tally"] == {"long": 1, "short": 1, "hold": 0} and s["majority"] is None
    assert s["long_case"] == [] and s["conclusion"] == ""


def test_odd_shapes_lose_only_the_odd_part():
    text = json.dumps({"votes": [{"agent": ["trader"], "stance": "long"}, "x", {"agent": "market_analyst",
                       "stance": "short"}], "long_case": "not a list", "conclusion": "관망"}) + " (see {RSI})"
    s = parse(text, STATE, "NVDA")
    assert {v["agent"]: v["stance"] for v in s["votes"]}["market_analyst"] == "short"
    assert s["long_case"] == [] and s["conclusion"] == "관망"
    assert parse(json.dumps({"votes": 5}), STATE, "NVDA")["tally"]["long"] == 2   # the fixed votes only


@pytest.mark.parametrize("text", ["", "no json here", "[1, 2]", "{broken"])
def test_unreadable_answers_raise(text):
    with pytest.raises(ValueError):
        parse(text, STATE, "NVDA")


def test_prompt_carries_the_plan_and_every_speaker_but_asks_for_no_levels():
    prompt = build_prompt(ConsensusContext("SOXL", "SOXX", STATE, PLAN, language="Korean"))
    assert "The team analysed SOXX; the final call below is on SOXL" in prompt
    assert "long trigger 190.0" in prompt and "do not add price levels" in prompt
    for agent in ("market_analyst", "bull_researcher", "conservative_analyst", "portfolio_manager"):
        assert f"### {agent}" in prompt
    assert "### neutral_analyst" not in prompt   # said nothing
    assert "Write the text in Korean" in prompt


def test_render_is_the_chat_message():
    text = render(parse(_answer(), STATE, "NVDA"))
    assert text.startswith("**결론**: 관망")
    assert "**투표**: 롱 5 · 숏 1 · 관망 2" in text and "**숏 관점**\n- 밸류 부담" in text


def test_the_fake_summariser_parses():
    s = parse(consensus.fake_summariser(ConsensusContext("NVDA", "NVDA", STATE, PLAN)), STATE, "NVDA")
    assert s["tally"]["short"] == 1 and s["conclusion"]


def _client(db_url, monkeypatch, summariser):
    from desk import api
    from tests.test_api import _client as client

    monkeypatch.setattr(api, "fake_summariser", summariser)   # the manager is built at startup
    return client(db_url)


def test_a_fake_run_ends_with_the_summary_after_the_portfolio_manager(db_url, monkeypatch):
    from tests.test_api import _wait_done

    with _client(db_url, monkeypatch, consensus.fake_summariser) as client:
        rid = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03"}).json()["id"]
        run = _wait_done(client, rid)
        events = client.get(f"/api/runs/{rid}/events").json()
    assert run["status"] == "done"
    s = run["plan"]["consensus"]
    assert s["subject"] == "NVDA" and sum(s["tally"].values()) == len(s["votes"]) > 0
    assert {v["agent"]: v["stance"] for v in s["votes"]}["trader"] == "long"   # FINAL TRANSACTION PROPOSAL: BUY
    speakers = [e["agent"] for e in events if e["type"] == "message"]
    assert speakers[-2:] == ["portfolio_manager", "consensus"]
    assert [e["type"] for e in events][-2:] == ["plan", "done"]


def test_a_failing_summary_still_finishes_the_run(db_url, monkeypatch):
    from tests.test_api import _wait_done

    def broken(_ctx):
        raise TimeoutError("slow model")

    with _client(db_url, monkeypatch, broken) as client:
        rid = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03"}).json()["id"]
        run = _wait_done(client, rid)
        events = client.get(f"/api/runs/{rid}/events").json()
    assert run["status"] == "done" and run["plan"]["rating"] == "Overweight"
    assert "consensus" not in run["plan"] and run["plan"]["consensus_error"].startswith("TimeoutError")
    said = [(e["type"], e["content"]) for e in events if e["agent"] == "consensus"]
    assert said == [("status", "thinking"), ("status", "skipped")]


HOLD_PLAN = {**PLAN, "stop_loss": 171.0, "price_target": 205.0,
             "support_resistance": {"levels": [{"kind": "support", "price": 176.4}, {"kind": "resistance", "price": 192.0}]}}


def test_holder_levels_come_from_the_plan_and_the_zones():
    assert consensus.holder_levels(HOLD_PLAN) == [170.0, 171.0, 176.4, 190.0, 192.0, 205.0]


def test_holder_advice_snaps_to_desk_levels_and_keeps_the_cut_below_a_long():
    h = consensus.parse_holder({"action": "TRIM", "cut_level": 176.0, "trim_level": 191.9, "reason": "r"},
                               HOLD_PLAN, 181.0, {"quantity": 10, "average_price": 190.0})
    assert h["action"] == "trim" and h["cut_level"] == 176.4 and h["trim_level"] == 192.0
    assert h["position"] == {"quantity": 10.0, "average_price": 190.0, "pnl_pct": -4.74}
    # a cut above the price (not a stop for a long), an invented price, an unknown action
    h = consensus.parse_holder({"action": "exit", "cut_level": 192.0, "trim_level": 150}, HOLD_PLAN, 181.0, None)
    assert h["cut_level"] is None and h["trim_level"] is None and h["position"] is None
    assert consensus.parse_holder({"action": "pray"}, HOLD_PLAN, 181.0, None) is None
    assert consensus.parse_holder("hold", HOLD_PLAN, 181.0, None) is None


def test_the_prompt_tells_the_position_and_the_levels_to_choose_from():
    ctx = ConsensusContext("NVDA", "NVDA", STATE, HOLD_PLAN, price=181.0,
                           position={"quantity": 10, "average_price": 190.0})
    prompt = build_prompt(ctx)
    assert "10 units at average 190.0 (-4.7% now)" in prompt
    assert "pick only from these): 170, 171, 176.4, 190, 192, 205" in prompt
    assert "Do not default to hold" in prompt
    assert "is not known" in build_prompt(ConsensusContext("NVDA", "NVDA", STATE, HOLD_PLAN, price=181.0))


def test_render_says_what_a_holder_should_do():
    text = _answer(holder={"action": "exit", "cut_level": 176.4, "trim_level": None, "reason": "추세 이탈"})
    s = parse(text, STATE, "NVDA", HOLD_PLAN, 181.0)
    assert "**보유 중이라면**: 손절 (176.4 이탈 시 전량 손절) — 추세 이탈" in render(s)


def test_a_portfolio_run_gives_the_holder_its_average_price(db_url, monkeypatch):
    from tests.test_api import _wait_done

    seen = []

    def summariser(ctx):
        seen.append(ctx.position)
        return consensus.fake_summariser(ctx)

    with _client(db_url, monkeypatch, summariser) as client:
        rid = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03",
                                             "use_portfolio": True}).json()["id"]
        run = _wait_done(client, rid)
    assert seen and seen[0]["quantity"] == 12
    holder = run["plan"]["consensus"]["holder"]
    assert holder["action"] == "hold" and holder["position"]["quantity"] == 12
