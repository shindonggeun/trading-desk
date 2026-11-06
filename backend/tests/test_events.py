from desk.events import StateDiffer


class Msg:
    def __init__(self, id, tool_calls=None):
        self.id = id
        self.content = ""
        self.tool_calls = tool_calls or []


def test_report_emitted_once_even_when_state_repeats():
    d = StateDiffer()
    first = d.feed([], {"market_report": "up trend"})
    again = d.feed([], {"market_report": "up trend", "messages": []})
    assert [(e["agent"], e["content"]) for e in first] == [("market_analyst", "up trend")]
    assert again == []


def test_debate_emits_only_the_new_turn_without_speaker_prefix():
    d = StateDiffer()
    d.feed([], {"investment_debate_state": {"bull_history": "Bull Analyst: one", "bear_history": ""}})
    events = d.feed([], {"investment_debate_state": {
        "bull_history": "Bull Analyst: one\nBull Analyst: two", "bear_history": "Bear Analyst: no"}})
    assert [(e["agent"], e["content"]) for e in events] == [
        ("bull_researcher", "two"), ("bear_researcher", "no")]


def test_parallel_openings_in_one_update_emit_each_speaker_in_order():
    """TradingAgents' parallel first rounds (#171) grow every speaker's history in one state update."""
    d = StateDiffer()
    risk = {"aggressive_history": "", "conservative_history": "", "neutral_history": ""}
    d.feed([], {"risk_debate_state": risk})
    opening = {"aggressive_history": "\nAggressive Analyst: go", "conservative_history": "\nConservative Analyst: wait",
               "neutral_history": "\nNeutral Analyst: half"}
    events = d.feed([], {"risk_debate_state": opening})
    assert [(e["agent"], e["content"]) for e in events] == [
        ("aggressive_analyst", "go"), ("conservative_analyst", "wait"), ("neutral_analyst", "half")]
    # round two is serial again: only the new turn
    events = d.feed([], {"risk_debate_state": {
        **opening, "aggressive_history": opening["aggressive_history"] + "\nAggressive Analyst: still go"}})
    assert [(e["agent"], e["content"]) for e in events] == [("aggressive_analyst", "still go")]
    d2 = StateDiffer()
    events = d2.feed([], {"investment_debate_state": {"bull_history": "\nBull Analyst: up",
                                                      "bear_history": "\nBear Analyst: down"}})
    assert [(e["agent"], e["content"]) for e in events] == [("bull_researcher", "up"), ("bear_researcher", "down")]


def test_tool_calls_deduplicated_by_message_id():
    d = StateDiffer()
    msg = Msg("m1", [{"name": "get_news", "args": {"q": "NVDA"}}])
    assert [e["content"] for e in d.feed([msg], None)] == ["get_news"]
    assert d.feed([msg], None) == []


def test_fields_read_in_pipeline_order():
    d = StateDiffer()
    events = d.feed([], {"final_trade_decision": "pm", "trader_investment_plan": "tr", "news_report": "n"})
    assert [e["agent"] for e in events] == ["news_analyst", "trader", "portfolio_manager"]
