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


def test_tool_calls_deduplicated_by_message_id():
    d = StateDiffer()
    msg = Msg("m1", [{"name": "get_news", "args": {"q": "NVDA"}}])
    assert [e["content"] for e in d.feed([msg], None)] == ["get_news"]
    assert d.feed([msg], None) == []


def test_fields_read_in_pipeline_order():
    d = StateDiffer()
    events = d.feed([], {"final_trade_decision": "pm", "trader_investment_plan": "tr", "news_report": "n"})
    assert [e["agent"] for e in events] == ["news_analyst", "trader", "portfolio_manager"]
