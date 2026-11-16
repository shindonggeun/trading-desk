"""#339: a chat lookup that lost its arguments - the page's ticker fills an empty one, otherwise the
model reads a Korean reason it can act on; one call at a time; JSON-string arguments are read."""

import asyncio

from langchain_core.messages import AIMessageChunk
from tradingagents import default_config, llm_clients

from desk import chat


def _tools(seen: list[dict]):
    async def quote(a: dict):
        seen.append(a)
        return {"ticker": a["ticker"], "price": 8.01}

    async def broken(a: dict):
        return a["horizon"]                       # a KeyError inside a lookup

    async def macro(_a: dict):
        return {"themes": []}

    return {"quote": chat.Tool("quote", "", chat.TICKER_ARG, quote),
            "broken": chat.Tool("broken", "", chat.NO_ARGS, broken),
            "macro_brief": chat.Tool("macro_brief", "", chat.NO_ARGS, macro)}


def test_an_empty_ticker_takes_the_pages_ticker_or_says_so_in_korean():
    seen: list[dict] = []
    tools = _tools(seen)
    assert asyncio.run(chat.call_tool(tools, "quote", {}, "KORU")) == {"ticker": "KORU", "price": 8.01}
    assert asyncio.run(chat.call_tool(tools, "quote", {"ticker": "  "}, "NVDA"))["ticker"] == "NVDA"
    assert asyncio.run(chat.call_tool(tools, "quote", {"ticker": "SOXL"}, "NVDA"))["ticker"] == "SOXL"
    missing = asyncio.run(chat.call_tool(tools, "quote", {}))
    assert "ticker" in missing["error"] and "다시 조회" in missing["error"] and len(seen) == 3
    assert asyncio.run(chat.call_tool(tools, "broken", {}))["error"].startswith("필요한 값(horizon)")
    assert asyncio.run(chat.call_tool(tools, "macro_brief", {})) == {"themes": []}


def test_the_pages_ticker_and_string_arguments_are_read():
    msgs = chat.build_messages([{"role": "user", "content": "앞 질문\n(관심 종목: NVDA)"}], "롱일까?", "KORU", False)
    assert chat.note_ticker(msgs) == "KORU"
    assert chat.note_ticker(chat.build_messages([], "거시 어때?", None, False)) is None
    assert chat.call_args('{"ticker": "KORU"}') == {"ticker": "KORU"}
    assert chat.call_args("") == {} and chat.call_args("{broken") == {} and chat.call_args("[1]") == {}
    assert chat.call_args(None) == {} and chat.call_args({"a": 1}) == {"a": 1}


def _model(monkeypatch, always_call: bool = False):
    """A scripted model: round 1 asks for a quote whose arguments never arrived plus an orphan
    argument piece; later rounds answer (or, with always_call, keep asking while tools are allowed)."""
    binds: list[dict] = []
    rounds: list[tuple[str, list]] = []

    class Bound:
        def __init__(self, kind: str):
            self.kind = kind

        async def astream(self, convo):
            rounds.append((self.kind, list(convo)))
            if self.kind == "tools" and (len(rounds) == 1 or always_call):
                n = len(rounds)
                yield AIMessageChunk(content="", tool_call_chunks=[
                    {"name": "quote", "args": "", "id": f"call_{n}", "index": 0, "type": "tool_call_chunk"}])
                # what interleaved parallel deltas leave behind: a piece with no name or id
                yield AIMessageChunk(content="", tool_call_chunks=[
                    {"name": None, "args": "{}", "id": None, "index": 3, "type": "tool_call_chunk"}])
            else:
                for piece in ("데스크 숫자: KORU 8.01. ", "예측이 아니며 투자 권유가 아닙니다."):
                    yield AIMessageChunk(content=piece)

    class Llm:
        def bind_tools(self, specs, **kwargs):
            binds.append(kwargs)
            return Bound("final" if kwargs.get("tool_choice") == "none" else "tools")

    class Client:
        def get_llm(self):
            return Llm()

    monkeypatch.setattr(default_config, "build_default_config", dict)
    monkeypatch.setattr(llm_clients, "create_tier_client", lambda *_a, **_k: Client())
    return binds, rounds


def test_the_model_calls_one_tool_at_a_time_and_an_argless_call_uses_the_page_ticker(monkeypatch):
    binds, rounds = _model(monkeypatch)
    seen: list[dict] = []
    events: list[dict] = []
    messages = chat.build_messages([], "롱일까 숏일까?", "KORU", False)
    text, used = asyncio.run(chat.LangChainChatter()(messages, _tools(seen), events.append))
    assert all(b["parallel_tool_calls"] is False for b in binds)
    assert seen == [{"ticker": "KORU"}] and used[0]["name"] == "quote" and "8.01" in used[0]["summary"]
    assert text.startswith("데스크 숫자: KORU 8.01") and len(rounds) == 2
    assert [e["type"] for e in events][:2] == ["tool_call", "tool_result"]
    # the orphan piece is not sent back: the next round sees only the real call and its answer
    sent = rounds[1][1][-2]
    assert [c["id"] for c in sent.tool_calls] == ["call_1"] and not sent.invalid_tool_calls
    assert rounds[1][1][-1].tool_call_id == "call_1"


def test_a_spent_lookup_budget_ends_with_an_answer_not_another_call(monkeypatch):
    _binds, rounds = _model(monkeypatch, always_call=True)
    monkeypatch.setattr(chat, "TOOL_BUDGET_SEC", -1.0)          # time is up after the first lookup
    text, used = asyncio.run(chat.LangChainChatter()(chat.build_messages([], "롱?", "KORU", False),
                                                     _tools([]), lambda _e: None))
    assert [k for k, _ in rounds] == ["tools", "final"] and len(used) == 1 and "8.01" in text
    _binds2, rounds2 = _model(monkeypatch, always_call=True)
    monkeypatch.setattr(chat, "TOOL_BUDGET_SEC", 999.0)
    text, used = asyncio.run(chat.LangChainChatter()(chat.build_messages([], "롱?", "KORU", False),
                                                     _tools([]), lambda _e: None))
    assert len(used) == chat.MAX_TOOL_CALLS and rounds2[-1][0] == "final" and "8.01" in text


def test_the_rules_ask_for_the_tickers_numbers_before_a_lean():
    assert "quote · levels · playbooks" in chat.SYSTEM and "판단 보류" in chat.SYSTEM
    assert chat.MAX_TOOL_ROUNDS > chat.MAX_TOOL_CALLS


def test_the_chat_takes_its_own_tier_model_and_effort_and_leaves_the_runs_config_alone(monkeypatch):
    """#404: DESK_CHAT_* pick the chat's client; the runs' config (and so the runs) stay as they are."""
    runs = {"llm_provider": "chatgpt", "quick_think_llm": "luna", "deep_think_llm": "sol",
            "quick_think_reasoning_effort": None, "deep_think_reasoning_effort": "medium"}
    made: list[tuple[dict, str]] = []
    monkeypatch.setattr(default_config, "build_default_config", lambda: dict(runs))

    def create_tier_client(config, tier, **_k):
        made.append((config, tier))
        return _Client()

    monkeypatch.setattr(llm_clients, "create_tier_client", create_tier_client)

    asyncio.run(chat.LangChainChatter()(chat.build_messages([], "거시?", None, False), {}, lambda _e: None))
    assert made[-1][1] == "deep" and made[-1][0]["deep_think_llm"] == "sol"            # default: deep tier as set
    assert chat.LangChainChatter().model_name() == "sol"

    chatter = chat.LangChainChatter(tier="deep", model="sol-pro", effort="high")
    asyncio.run(chatter(chat.build_messages([], "거시?", None, False), {}, lambda _e: None))
    config, tier = made[-1]
    assert tier == "deep" and config["deep_think_llm"] == "sol-pro" and config["deep_think_reasoning_effort"] == "high"
    assert chatter.model_name() == "sol-pro"

    quick = chat.LangChainChatter(tier="quick")
    asyncio.run(quick(chat.build_messages([], "거시?", None, False), {}, lambda _e: None))
    assert made[-1][1] == "quick" and quick.model_name() == "luna"
    assert chat.LangChainChatter(tier="nonsense").tier == "deep"
    # the runs' config was copied, never changed
    assert runs == {"llm_provider": "chatgpt", "quick_think_llm": "luna", "deep_think_llm": "sol",
                    "quick_think_reasoning_effort": None, "deep_think_reasoning_effort": "medium"}


class _Client:
    def get_llm(self):
        class Llm:
            def bind_tools(self, *_a, **_k):
                return self

            async def astream(self, _convo):
                yield AIMessageChunk(content="답")

        return Llm()


def test_the_chat_settings_fall_back_instead_of_failing(monkeypatch):
    """#404: a bad tier is deep, a misspelt effort is empty (the tier's own), an empty model is None."""
    from desk.settings import Settings

    monkeypatch.setenv("DESK_CHAT_MODEL_TIER", "Quick ")
    monkeypatch.setenv("DESK_CHAT_REASONING_EFFORT", "HIGH")
    monkeypatch.setenv("DESK_CHAT_LLM", "")
    s = Settings()
    assert (s.chat_model_tier, s.chat_reasoning_effort, s.chat_llm) == ("quick", "high", "")
    monkeypatch.setenv("DESK_CHAT_MODEL_TIER", "fast")
    monkeypatch.setenv("DESK_CHAT_REASONING_EFFORT", "hgih")
    s = Settings()
    assert (s.chat_model_tier, s.chat_reasoning_effort) == ("deep", "")
