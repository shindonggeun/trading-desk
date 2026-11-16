"""#314: the desk chat - threads per account, an answer streamed as SSE with its desk lookups,
the shared rate-limit cooldown, and nothing secret in what a tool hands the model."""

import asyncio
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from desk import chat
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.prices import Quote
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD

OTHER = "other"


class RateLimitError(Exception):        # what openai raises, by name
    status_code = 429


def quote_then_answer(fail: Exception | None = None):
    """A model that looks one quote up, then answers in two pieces (or fails)."""
    seen: list[list[dict]] = []
    models: list[str | None] = []

    async def chatter(messages, tools, emit, model=None):
        seen.append(messages)
        models.append(model)
        if fail is not None:
            raise fail
        used: list[dict] = []
        await chat.LangChainChatter._run(tools, "quote", {"ticker": "KORU"}, emit, used)
        for piece in ("데스크 숫자: KORU 8.01. ", "예측이 아니며 투자 권유가 아닙니다."):
            emit({"type": "token", "text": piece})
        return "데스크 숫자: KORU 8.01. 예측이 아니며 투자 권유가 아닙니다.", used

    chatter.seen, chatter.models = seen, models
    return chatter


def _app(db_url, chatter, monkeypatch):
    async def quote(self, ticker):
        return Quote(ticker, 8.01, "USD", "toss", "2026-11-13T08:42:00+00:00")

    monkeypatch.setattr("desk.prices.PriceSource.quote", quote)
    app = create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0), runner=make_fake_runner(0),
                     chatter=chatter)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    engine.dispose()
    return app


def _login(client, who=OWNER):
    assert client.post("/api/auth/login", json={"username": who, "password": PASSWORD}).status_code == 204


def _events(body: str) -> list[dict]:
    out = []
    for frame in body.split("\n\n"):
        data = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


def test_every_chat_route_needs_a_session(db_url, monkeypatch):
    with TestClient(_app(db_url, quote_then_answer(), monkeypatch), base_url="https://testserver") as client:
        assert client.get("/api/chat/threads").status_code == 401
        assert client.post("/api/chat/threads", json={}).status_code == 401
        assert client.get("/api/chat/threads/x/messages").status_code == 401
        assert client.post("/api/chat/threads/x/messages", json={"text": "hi"}).status_code == 401
        assert client.delete("/api/chat/threads/x").status_code == 401
        assert client.get("/api/chat/models").status_code == 401


def test_an_answer_streams_its_lookups_and_tokens_and_both_messages_are_kept(db_url, monkeypatch):
    model = quote_then_answer()
    with TestClient(_app(db_url, model, monkeypatch), base_url="https://testserver") as client:
        _login(client)
        thread = client.post("/api/chat/threads", json={}).json()
        assert thread["title"] == "새 대화"
        r = client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "KORU 타점?", "ticker": "koru"})
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        events = _events(r.text)
        assert [e["type"] for e in events] == ["tool_call", "tool_result", "token", "token", "done"]
        assert events[0] == {"type": "tool_call", "name": "quote", "args": {"ticker": "KORU"}}
        assert "8.01" in events[1]["summary"] and "toss" in events[1]["summary"]
        # the model got the rules and the ticker with the question
        sent = model.seen[0]
        assert sent[0]["role"] == "system" and "투자 권유가 아닙니다" in sent[0]["content"]
        assert sent[-1] == {"role": "user", "content": "KORU 타점?\n(관심 종목: KORU)"}
        kept = client.get(f"/api/chat/threads/{thread['id']}/messages").json()
        assert [m["role"] for m in kept["items"]] == ["user", "assistant"]
        assert kept["items"][1]["tools"][0]["name"] == "quote" and kept["thread"]["title"] == "KORU 타점?"
        assert client.get("/api/chat/threads").json()["items"][0]["id"] == thread["id"]


def test_another_accounts_thread_is_not_there(db_url, monkeypatch):
    with TestClient(_app(db_url, quote_then_answer(), monkeypatch), base_url="https://testserver") as client:
        _login(client)
        mine = client.post("/api/chat/threads", json={"title": "내 대화"}).json()
        client.post("/api/auth/logout")
        _login(client, OTHER)
        assert client.get("/api/chat/threads").json()["items"] == []
        assert client.get(f"/api/chat/threads/{mine['id']}/messages").status_code == 404
        assert client.post(f"/api/chat/threads/{mine['id']}/messages", json={"text": "hi"}).status_code == 404
        assert client.delete(f"/api/chat/threads/{mine['id']}").status_code == 204     # the same answer...
        client.post("/api/auth/logout")
        _login(client)
        assert client.get(f"/api/chat/threads/{mine['id']}/messages").status_code == 200   # ...and still there


def test_the_runs_cooldown_holds_the_chat_and_a_rate_limit_starts_it(db_url, monkeypatch):
    app = _app(db_url, quote_then_answer(fail=RateLimitError("Error code: 429")), monkeypatch)
    with TestClient(app, base_url="https://testserver") as client:
        _login(client)
        thread = client.post("/api/chat/threads", json={}).json()
        events = _events(client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "거시 어때?"}).text)
        assert events == [{"type": "error", "message": "모델 속도 제한 — 잠시 뒤 다시 물어봐 주세요"}]
        assert app.state.manager.rate_limit_wait() > 0                 # the runs slow down too
        held = client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "다시"})
        assert held.status_code == 429 and int(held.headers["Retry-After"]) > 0 and "분 뒤" in held.json()["detail"]


def test_a_failure_says_a_fixed_word_not_the_error(db_url, monkeypatch):
    app = _app(db_url, quote_then_answer(fail=RuntimeError("upstream https://internal.example/x said no")), monkeypatch)
    with TestClient(app, base_url="https://testserver") as client:
        _login(client)
        thread = client.post("/api/chat/threads", json={}).json()
        events = _events(client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "hi"}).text)
        assert events == [{"type": "error", "message": "답을 만들지 못했습니다"}]
        assert app.state.manager.rate_limit_wait() == 0


def test_input_limits_and_cross_origin_writes(db_url, monkeypatch):
    with TestClient(_app(db_url, quote_then_answer(), monkeypatch), base_url="https://testserver") as client:
        _login(client)
        thread = client.post("/api/chat/threads", json={}).json()
        url = f"/api/chat/threads/{thread['id']}/messages"
        assert client.post(url, json={"text": ""}).status_code == 422
        assert client.post(url, json={"text": "x" * (chat.MAX_TEXT + 1)}).status_code == 422
        assert client.post(url, json={"text": "hi", "ticker": "BAD TICKER!"}).status_code == 422
        assert client.post("/api/chat/threads", json={"title": "t" * (chat.MAX_TITLE + 1)}).status_code == 422
        assert client.post(url, json={"text": "hi"}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_tool_results_never_carry_secrets_or_account_figures():
    raw = {"price": 8.01, "account_seq": "12-34", "api_key": "k", "nested": {"accessToken": "t", "quantity": 120,
           "avg_price": 7.5, "levels": [1, 2]}, "rows": list(range(50)), "note": "x" * 2000}
    clean = chat.scrub(raw)
    assert clean["price"] == 8.01 and clean["nested"] == {"levels": [1, 2]}
    assert not {"account_seq", "api_key"} & clean.keys() and len(clean["rows"]) == 20 and len(clean["note"]) == 600

    async def leaky(_args):
        return raw

    tools = {"leaky": chat.Tool("leaky", "", chat.NO_ARGS, leaky)}
    got = asyncio.run(chat.call_tool(tools, "leaky", {}))
    assert "12-34" not in json.dumps(got) and "accessToken" not in json.dumps(got)
    assert asyncio.run(chat.call_tool(tools, "nope", {})) == {"error": "unknown tool nope"}


def test_the_desk_tools_are_read_only_and_the_fake_model_uses_one():
    app = create_app(Settings(database_url="sqlite://", fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    assert "/api/chat/threads/{thread_id}/messages" in {r.path for r in app.routes}
    tools = set(app.state.chat_tools)
    assert {"macro_brief", "market_risk", "calendar_week", "quote", "levels", "playbooks", "bottom_zone",
            "recent_runs"} <= tools <= {"macro_brief", "market_risk", "calendar_week", "quote", "levels", "playbooks",
                                        "bottom_zone", "recent_runs", "entry_now", "news_search"}
    assert not any(w in name for name in tools for w in ("order", "buy", "sell", "portfolio", "account"))
    events: list[dict] = []

    async def macro(_a):
        return {"themes": []}

    text, used = asyncio.run(chat.FakeChatter()(chat.build_messages([], "거시 어때?", None, False),
                                                {"macro_brief": chat.Tool("macro_brief", "", chat.NO_ARGS, macro)},
                                                events.append))
    assert used[0]["name"] == "macro_brief" and events[0]["type"] == "tool_call"
    assert "투자 권유가 아닙니다" in text and events[-1]["type"] == "token"


def test_the_store_checks_the_owner_itself_and_caps_a_thread(db_url, monkeypatch):
    import pytest

    from desk.db import migrate

    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user(OWNER, PASSWORD)
    auth.create_user(OTHER, PASSWORD)
    from sqlalchemy import select

    from desk.db import users

    with engine.connect() as conn:
        ids = dict(conn.execute(select(users.c.username, users.c.id)).all())
    owner_id, other_id = ids[OWNER], ids[OTHER]
    store = chat.ChatStore(engine)
    thread = store.create(owner_id)
    with pytest.raises(LookupError):
        store.add(other_id, thread["id"], "user", "끼어들기")          # not through the route either
    assert store.messages(owner_id, thread["id"]) == []
    monkeypatch.setattr(chat, "MAX_MESSAGES", 2)
    store.add(owner_id, thread["id"], "user", "a")
    store.add(owner_id, thread["id"], "assistant", "b")
    with pytest.raises(chat.TooMany):
        store.add(owner_id, thread["id"], "user", "c")
    assert [m["content"] for m in store.messages(owner_id, thread["id"], last=1)] == ["b"]


<<<<<<< HEAD
def test_the_answer_keeps_the_model_it_was_written_with(db_url, monkeypatch):
    """#404: the done event and the stored answer name the chat's model; a chatter that cannot say gives None."""
    named = quote_then_answer()
    named.model_name = lambda: "gpt-6-sol"
    with TestClient(_app(db_url, named, monkeypatch), base_url="https://testserver") as client:
        _login(client)
        thread = client.post("/api/chat/threads", json={}).json()
        events = _events(client.post(f"/api/chat/threads/{thread['id']}/messages", json={"text": "거시?"}).text)
        assert events[-1]["type"] == "done" and events[-1]["model"] == "gpt-6-sol"
        kept = client.get(f"/api/chat/threads/{thread['id']}/messages").json()["items"]
        assert [m["model"] for m in kept] == [None, "gpt-6-sol"]
    assert chat.model_of(quote_then_answer()) is None and chat.model_of(chat.FakeChatter()) == "fake"
=======
def test_the_chat_offers_its_models_and_answers_with_the_one_picked(db_url, monkeypatch):
    """#414: the allowed list with labels, the default when none is picked, 422 off the list."""
    model = quote_then_answer()
    with TestClient(_app(db_url, model, monkeypatch), base_url="https://testserver") as client:
        _login(client)
        offered = client.get("/api/chat/models").json()
        assert offered == {"default": "gpt-6-luna", "items": [{"id": "gpt-6-luna", "label": "Luna · 빠름"},
                                                              {"id": "gpt-6-sol", "label": "Sol · 고급 추론"}]}
        thread = client.post("/api/chat/threads", json={}).json()
        url = f"/api/chat/threads/{thread['id']}/messages"
        assert _events(client.post(url, json={"text": "hi"}).text)[-1]["model"] == "gpt-6-luna"
        done = _events(client.post(url, json={"text": "깊게", "model": "gpt-6-sol"}).text)[-1]
        assert done["type"] == "done" and done["model"] == "gpt-6-sol"
        assert model.models == ["gpt-6-luna", "gpt-6-sol"]
        for bad in ("gpt-6-astra", "", "x" * 65):
            assert client.post(url, json={"text": "hi", "model": bad}).status_code == 422, bad
        assert len(model.models) == 2                     # nothing off the list reached the model


def test_the_chat_models_come_from_the_settings():
    from desk import chat

    assert chat.model_options(["gpt-6-sol", "my-model", "gpt-6-sol"], "my-model") == {
        "default": "my-model", "items": [{"id": "gpt-6-sol", "label": "Sol · 고급 추론"},
                                         {"id": "my-model", "label": "my-model"}]}
    assert chat.model_options(["gpt-6-sol"], "not-offered")["default"] == "gpt-6-sol"
>>>>>>> 138a0e4 ([BE] feat: AI 채팅이 허용 목록의 모델을 골라 답하고 채팅 기본 모델을 분석 모델과 따로 둔다 (#414))
