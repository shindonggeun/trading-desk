"""The desk chat (#314): talk with the model the runs use, which looks the desk's numbers up itself.

- Threads and messages per account (``chat_threads`` / ``chat_messages``). Every lookup is scoped
  by the session's user id; another account's thread answers as missing (docs/security.md §3).
- The model is TradingAgents' quick tier (``create_tier_client(config, "quick")``) - the same
  provider, model and ChatGPT plan as the runs, no new key - with LangChain tool binding. Its
  tools are read-only desk lookups (the macro brief, the market's selling score, the week's
  calendar, a quote, levels, playbooks, the long-term bottom zone, recent analyses, and the entry
  now when that module is there). There is no order tool, and no tool reads the broker account.
- Every tool result is scrubbed before the model or the page sees it: keys that look like secrets
  or account figures are dropped (``SECRET_KEYS``), lists and text are cut (``scrub``). The stored
  message keeps only each lookup's name, arguments and a one-line summary.
- Limits: a message of MAX_TEXT characters, MAX_THREADS threads per account, HISTORY earlier
  messages sent with a new one (older ones are left out, said in the prompt), MAX_TOOL_ROUNDS
  rounds of lookups, MAX_TOOL_CALLS lookups and MAX_ANSWER_CHARS characters per answer,
  MAX_MESSAGES per thread and READ_LAST of them returned by a read.
- The answer streams as events: token · tool_call · tool_result · done · error (desk.api turns
  them into SSE). ``FakeChatter`` scripts one lookup and an answer for DESK_FAKE_RUNS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Engine

from .db import chat_messages, chat_threads, now
from .web_search import links

log = logging.getLogger(__name__)

MAX_TEXT = 2000
MAX_TITLE = 80
MAX_THREADS = 100
MAX_MESSAGES = 400          # per thread; a longer conversation starts a new thread
READ_LAST = 200             # the messages GET returns (the newest)
HISTORY = 16
MAX_TOOL_ROUNDS = 7          # #339: one lookup per round (no parallel calls) - MAX_TOOL_CALLS and an answer
MAX_TOOL_CALLS = 6
MAX_ANSWER_CHARS = 6000
TOOL_RESULT_CHARS = 4000
ANSWER_TIMEOUT_SEC = 180.0      # the whole answer, every round (#339: one lookup per round)
TOOL_BUDGET_SEC = 90.0          # past this, the next round answers without another lookup
SUMMARY_CHARS = 160
# Dropped from every tool result: what could be a secret or an account figure.
SECRET_KEYS = re.compile(r"(key|secret|token|password|passwd|cookie|session|account|credential|auth|"
                         r"quantity|qty|amount|balance|average_price|avg_price|cash|deposit|seq)", re.IGNORECASE)

Emit = Callable[[dict], None]


class TooMany(Exception):
    """The account already has MAX_THREADS threads."""


# --- storage ---------------------------------------------------------------------------------------

def _iso(value) -> str | None:
    from .db import aware

    value = aware(value)
    return value.isoformat() if value else None


class ChatStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @staticmethod
    def _thread(row) -> dict:
        return {"id": row["id"], "title": row["title"], "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"])}

    @staticmethod
    def _message(row) -> dict:
        return {"id": row["id"], "role": row["role"], "content": row["content"], "tools": row["tools"] or [],
                "created_at": _iso(row["created_at"])}

    def threads(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(chat_threads).where(chat_threads.c.user_id == user_id)
                                .order_by(chat_threads.c.updated_at.desc(), chat_threads.c.id)).mappings().all()
        return [self._thread(r) for r in rows]

    def create(self, user_id: int, title: str | None = None) -> dict:
        stamp = now()
        row = {"id": uuid.uuid4().hex[:12], "user_id": user_id, "title": (title or "새 대화").strip()[:MAX_TITLE] or "새 대화",
               "created_at": stamp, "updated_at": stamp}
        with self.engine.begin() as conn:
            count = conn.execute(select(func.count()).select_from(chat_threads)
                                 .where(chat_threads.c.user_id == user_id)).scalar_one()
            if count >= MAX_THREADS:
                raise TooMany(f"at most {MAX_THREADS} threads - delete an old one")
            conn.execute(chat_threads.insert().values(**row))
        return self._thread(row)

    def get(self, user_id: int, thread_id: str) -> dict | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(chat_threads).where(chat_threads.c.id == thread_id,
                                                          chat_threads.c.user_id == user_id)).mappings().first()
        return self._thread(row) if row else None

    def delete(self, user_id: int, thread_id: str) -> None:
        with self.engine.begin() as conn:
            mine = select(chat_threads.c.id).where(chat_threads.c.id == thread_id, chat_threads.c.user_id == user_id)
            if conn.execute(mine).first() is None:
                return
            conn.execute(delete(chat_messages).where(chat_messages.c.thread_id == thread_id))
            conn.execute(delete(chat_threads).where(chat_threads.c.id == thread_id))

    def messages(self, user_id: int, thread_id: str, last: int | None = None) -> list[dict] | None:
        if self.get(user_id, thread_id) is None:
            return None
        with self.engine.connect() as conn:
            q = select(chat_messages).where(chat_messages.c.thread_id == thread_id).order_by(chat_messages.c.id.desc())
            if last:
                q = q.limit(last)
            rows = conn.execute(q).mappings().all()
        return [self._message(r) for r in reversed(rows)]

    def add(self, user_id: int, thread_id: str, role: str, content: str, tools: list[dict] | None = None,
            title: str | None = None) -> dict:
        """Append to the account's own thread - checked here too, not only by the caller. Raises
        LookupError for another account's (or a deleted) thread, TooMany past MAX_MESSAGES."""
        stamp = now()
        with self.engine.begin() as conn:
            mine = select(chat_threads.c.id).where(chat_threads.c.id == thread_id, chat_threads.c.user_id == user_id)
            if conn.execute(mine).first() is None:
                raise LookupError("thread not found")
            count = conn.execute(select(func.count()).select_from(chat_messages)
                                 .where(chat_messages.c.thread_id == thread_id)).scalar_one()
            if count >= MAX_MESSAGES:
                raise TooMany(f"at most {MAX_MESSAGES} messages in a thread - start a new one")
            new_id = conn.execute(chat_messages.insert().values(
                thread_id=thread_id, role=role, content=content, tools=tools or None, created_at=stamp)).inserted_primary_key[0]
            values: dict[str, Any] = {"updated_at": stamp}
            if title:
                values["title"] = title[:MAX_TITLE]
            conn.execute(update(chat_threads).where(chat_threads.c.id == thread_id,
                                                    chat_threads.c.user_id == user_id).values(**values))
        return {"id": new_id, "role": role, "content": content, "tools": tools or [], "created_at": _iso(stamp)}


# --- tools -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict                                   # JSON schema of the arguments
    run: Callable[[dict], Awaitable[Any]]

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


TICKER_ARG = {"type": "object", "properties": {"ticker": {"type": "string", "description": "e.g. NVDA, 005930.KS, KORU"}},
              "required": ["ticker"]}
NO_ARGS = {"type": "object", "properties": {}}


def scrub(value: Any, depth: int = 0) -> Any:
    """A tool result safe for the model and the page: secret-like keys dropped, long lists and text
    cut, nothing deeper than six levels."""
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: scrub(v, depth + 1) for k, v in value.items() if not SECRET_KEYS.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        return value[:600]
    if isinstance(value, float):
        return round(value, 4)
    return value


def as_text(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + "…(cut)"


def summary(value: Any) -> str:
    """One line for the page and the stored message."""
    if isinstance(value, dict) and "error" in value and len(value) == 1:
        return f"오류: {value['error']}"[:SUMMARY_CHARS]
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:SUMMARY_CHARS] + ("…" if len(text) > SUMMARY_CHARS else "")


TICKER_NOTE = "\n(관심 종목: {})"
_NOTE_RE = re.compile(r"\n\(관심 종목: ([^)\s]+)\)\s*$")


def note_ticker(messages: list[dict]) -> str | None:
    """The page's ticker that build_messages put on the newest user message, if any."""
    for m in reversed(messages):
        if m.get("role") == "user":
            found = _NOTE_RE.search(m.get("content") or "")
            return found.group(1) if found else None
    return None


def call_args(raw: Any) -> dict:
    """A tool call's arguments as a dict: a JSON string is parsed, anything else unreadable is {}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _reason(exc: Exception) -> str:
    """A failure the model and the page can read (#339: not a bare exception name)."""
    if isinstance(exc, KeyError):
        return f"필요한 값({exc.args[0] if exc.args else '?'})이 비어 있음 — 값을 넣어 다시 조회"
    if isinstance(exc, TimeoutError):
        return "시간 초과 — 잠시 뒤 다시 조회"
    if isinstance(exc, ValueError):
        return f"잘못된 값 — {str(exc)[:120] or type(exc).__name__}"
    return f"조회 실패({type(exc).__name__})"


async def call_tool(tools: dict[str, Tool], name: str, args: dict, ticker: str | None = None) -> Any:
    """Run one lookup. #339: a required ``ticker`` left empty takes the page's ticker; with none the
    model gets a readable error it can answer by calling again with the ticker."""
    tool = tools.get(name)
    if tool is None:
        return {"error": f"unknown tool {name}"}
    args = dict(args or {})
    missing = [k for k in tool.parameters.get("required", []) if not str(args.get(k) or "").strip()]
    if "ticker" in missing and ticker:
        args["ticker"] = ticker
        missing.remove("ticker")
    if missing:
        return {"error": f"필요한 값({', '.join(missing)})이 비어 있음 — "
                         "종목 코드(예: KORU, NVDA, 005930.KS)를 넣어 다시 조회"}
    try:
        return scrub(await tool.run(args))
    except Exception as exc:  # noqa: BLE001 - a lookup failing is an answer the model can read
        log.info("chat tool %s failed: %s", name, exc)
        return {"error": _reason(exc)}


# --- the model -------------------------------------------------------------------------------------

SYSTEM = (
    "너는 개인 트레이딩 데스크의 상담 상대다. 한국어로 답한다.\n"
    "- 숫자(가격 · 점수 · 확률 · 날짜)는 도구 결과에서만 쓴다. 도구로 확인하지 못한 숫자는 지어내지 말고 '확인 못 함'이라고 말한다.\n"
    "- 숫자를 쓸 때는 어느 도구의 몇 시 기준 값인지(출처 시각) 짧게 붙인다.\n"
    "- 데스크 숫자(근거)와 너의 의견을 나눠서 말한다: '데스크 숫자:' 와 '내 의견:'.\n"
    "- 종목을 묻는 질문(롱/숏 · 사도 되나 · 어디서 사나)이면 먼저 quote · levels · playbooks 로 그 종목을 조회한다. "
    "필요하면 bottom_zone · recent_runs 도 본다. 거시 · 시장 위험은 그다음 보조로만 쓴다.\n"
    "- 도구는 한 번에 하나씩 부르고, ticker 는 언제나 종목 코드로 채운다(예: KORU, NVDA, 005930.KS).\n"
    "- 도구가 오류를 주면 고쳐서 한 번 다시 부른다. 그래도 그 종목의 시세 · 레벨 · 플레이북을 못 얻었으면 "
    "롱/숏 기울기를 말하지 말고 '숫자를 확인하지 못해 판단 보류'라고 말한다.\n"
    "- 거시 · 정책 · 뉴스 질문이면 macro_brief 다음에 news_search 로 최근 기사를 찾아 무엇이 · 언제 있었는지 구체적으로 보충한다. "
    "news_search 는 질문당 2번까지만 쓰고, 검색어는 짧은 주제어로 쓴다(예: 'Fed rate decision', '코스피 외국인 매도'). "
    "검색어에 사용자의 계좌 · 보유 종목 수량 · 금액 · 개인 정보를 넣지 않는다.\n"
    "- 기사에서 가져온 사실은 '데스크 숫자'와 따로 '뉴스:' 로 묶고, 문장마다 (매체, 게시 시각)을 붙인다. "
    "기사 제목에 없는 내용은 지어내지 않는다.\n"
    "- 주문은 할 수 없고, 계좌 · 보유 수량 · 금액은 보지 않는다.\n"
    "- 끝에 한 줄: '예측이 아니며 투자 권유가 아닙니다.'"
)


def build_messages(history: list[dict], text: str, ticker: str | None, dropped: bool) -> list[dict]:
    """The model's input: the rules, the kept history and the new message (with its ticker)."""
    system = SYSTEM + ("\n- 이 대화의 앞부분은 길어서 빠졌다. 필요한 내용은 다시 물어본다." if dropped else "")
    out = [{"role": "system", "content": system}]
    out += [{"role": m["role"], "content": m["content"]} for m in history]
    out.append({"role": "user", "content": text + (TICKER_NOTE.format(ticker) if ticker else "")})
    return out


class LangChainChatter:
    """The configured quick model with the desk's tools bound; tokens are emitted as they come."""

    def __init__(self, timeout: float = ANSWER_TIMEOUT_SEC) -> None:
        self.timeout = timeout

    async def __call__(self, messages: list[dict], tools: dict[str, Tool], emit: Emit) -> tuple[str, list[dict]]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
        from tradingagents.default_config import build_default_config
        from tradingagents.llm_clients import create_tier_client

        llm = create_tier_client(build_default_config(), "quick", timeout=self.timeout, max_retries=0).get_llm()
        # #339: one call at a time - parallel calls streamed interleaved can lose their arguments.
        # Once the lookups' budget (rounds, calls or time) is spent, the last round may not call any.
        specs = [t.spec() for t in tools.values()]
        bound = llm.bind_tools(specs, parallel_tool_calls=False) if tools else llm
        final = llm.bind_tools(specs, tool_choice="none", parallel_tool_calls=False) if tools else llm
        ticker = note_ticker(messages)
        kinds = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
        convo: list = [kinds[m["role"]](content=m["content"]) for m in messages]
        text, used = "", []
        started = time.monotonic()
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            last = round_no > 0 and (round_no == MAX_TOOL_ROUNDS or len(used) >= MAX_TOOL_CALLS
                                     or time.monotonic() - started > TOOL_BUDGET_SEC)
            gathered, said = None, ""
            async for chunk in (final if last else bound).astream(convo):
                gathered = chunk if gathered is None else gathered + chunk
                piece = chunk.content if isinstance(chunk.content, str) else "".join(
                    p.get("text", "") for p in chunk.content if isinstance(p, dict))
                said += piece or ""
                if piece and len(text) < MAX_ANSWER_CHARS:
                    piece = piece[:MAX_ANSWER_CHARS - len(text)]
                    text += piece
                    emit({"type": "token", "text": piece})
            asked = list(getattr(gathered, "tool_calls", None) or [])
            # an orphan argument piece shows up as a call with no name or id - nothing to run, and
            # not sent back either: a call without an id and no answer would break the next request
            calls = [c for c in asked if c.get("name") and c.get("id")]
            if not calls or last:
                break
            convo.append(gathered if len(calls) == len(asked) and not getattr(gathered, "invalid_tool_calls", None)
                         else AIMessage(content=said, tool_calls=calls))
            for call in calls:
                if len(used) >= MAX_TOOL_CALLS:
                    convo.append(ToolMessage(content='{"error": "too many lookups"}', tool_call_id=call["id"]))
                    continue
                result = await self._run(tools, call["name"], call_args(call.get("args")), emit, used, ticker)
                convo.append(ToolMessage(content=as_text(result), tool_call_id=call["id"]))
        return text, used

    @staticmethod
    async def _run(tools: dict[str, Tool], name: str, args: dict, emit: Emit, used: list[dict],
                   ticker: str | None = None) -> Any:
        emit({"type": "tool_call", "name": name, "args": scrub(args)})
        result = await call_tool(tools, name, args, ticker)
        line = summary(result)
        found = links(result)     # #403: a news search's articles, so the page can link them
        extra = {"links": found} if found else {}
        emit({"type": "tool_result", "name": name, "summary": line, **extra})
        used.append({"name": name, "args": scrub(args), "summary": line, **extra})
        return result


class FakeChatter:
    """DESK_FAKE_RUNS: one real lookup through the same tools, then a scripted answer in pieces."""

    async def __call__(self, messages: list[dict], tools: dict[str, Tool], emit: Emit) -> tuple[str, list[dict]]:
        question = messages[-1]["content"]
        found = re.search(r"\(관심 종목: ([^)]+)\)", question)
        name, args = ("playbooks", {"ticker": found.group(1)}) if found and "playbooks" in tools else ("macro_brief", {})
        used: list[dict] = []
        if name in tools:
            await LangChainChatter._run(tools, name, args, emit, used)
        if name == "macro_brief" and "news_search" in tools:     # #403: then the news behind the themes
            await LangChainChatter._run(tools, "news_search", {"query": "Fed rate decision"}, emit, used)
        news = next((u["links"][0] for u in used if u.get("links")), None)
        text = (f"데스크 숫자: {used[0]['summary'] if used else '확인 못 함'}\n"
                + (f"뉴스: {news['title']} ({news.get('source') or '출처 미상'}, {news.get('published') or '시각 미상'})\n"
                   if news else "")
                + "내 의견: (대본 응답) 데스크 숫자를 근거로 판단하세요.\n예측이 아니며 투자 권유가 아닙니다.")
        for piece in re.findall(r".{1,24}", text, re.DOTALL):
            emit({"type": "token", "text": piece})
            await asyncio.sleep(0)
        return text, used


Chatter = Callable[[list[dict], dict[str, Tool], Emit], Awaitable[tuple[str, list[dict]]]]
