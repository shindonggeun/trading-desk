"""The chat's news search (#403): recent articles for a query the model writes, with their sources.

The desk's own lookups only know the desk's numbers - ``macro_brief`` is a theme's tone and two
headlines, not what the articles say. This is one read-only lookup out of the desk:

- Source: Google News RSS search (keyless, the same feed ``macro_news`` falls back to). Korean
  queries read the Korean edition, others the US one. The provider's own web search was left out:
  it is tied to one LLM provider, its results would skip ``chat.scrub`` and it cannot be tested
  without spending the plan's quota.
- What leaves the desk is the query alone, and only a short one. A query that looks like it
  carries an account figure (a long digit run, an e-mail address) is refused before any request.
- What comes back is cut to MAX_ARTICLES rows of title, source, publication time and http(s) link -
  never an article's body. Third-party text goes through ``macro_news.clean`` like the briefing's.
- A short in-process cache and a gap between requests keep a chatty model from hammering the feed.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta

import httpx

from . import macro_news, net

MAX_QUERY = 100
MAX_ARTICLES = 8
RELEVANT = 20        # the feed's best-ranked rows the articles are picked from
MAX_URL = 500        # under chat.scrub's 600-character cut, so a link it cut is dropped, never shown broken
WINDOWS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30}
DEFAULT_WINDOW = "7d"
TIMEOUT_SEC = 10.0
REQUEST_GAP_SEC = 1.0
CACHE_SEC = 10 * 60
CACHE_MAX = 64
CLOCK_SKEW_SEC = 15 * 60

_HANGUL = re.compile(r"[가-힣]")
# What an account, card or phone number looks like - never sent out: nine or more digits in one run
# of digits, dashes and spaces (a ticker is six, a date like 2026-11-16 eight), or an e-mail address
_DIGIT_RUN = re.compile(r"\d[\d\- ]*\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
MAX_DIGITS = 8

_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = asyncio.Lock()
_last_request = 0.0


def clean_query(raw: object) -> str:
    """The query as sent: one line, capped. Raises ValueError for an empty one or one that looks private."""
    query = macro_news.clean(str(raw or ""), MAX_QUERY) or ""
    if not query:
        raise ValueError("검색어가 비어 있음")
    if _EMAIL.search(query) or any(sum(c.isdigit() for c in m) > MAX_DIGITS for m in _DIGIT_RUN.findall(query)):
        raise ValueError("검색어에 계좌 · 연락처처럼 보이는 숫자나 주소가 있음 — 빼고 다시 검색")
    return query


def feed_params(query: str, window: str) -> dict:
    korean = bool(_HANGUL.search(query))
    edition = {"hl": "ko", "gl": "KR", "ceid": "KR:ko"} if korean else {"hl": "en-US", "gl": "US", "ceid": "US:en"}
    return {"q": f"{query} when:{window}", **edition}


def articles(items: list[dict], now: datetime, window: str) -> list[dict]:
    """The feed's RELEVANT rows first (it is ranked; the search also matches inside an article's text,
    so the newest alone can be off topic), then newest first inside the window, one row per title,
    at most MAX_ARTICLES."""
    items = items[:RELEVANT]
    since = now - timedelta(days=WINDOWS[window])
    ahead = now + timedelta(seconds=CLOCK_SKEW_SEC)
    # a link longer than the chat keeps (chat.scrub cuts text at 600) would reach the page broken
    dated = [i for i in items if i.get("seen") and since <= i["seen"] <= ahead and len(i["url"]) <= MAX_URL]
    out, seen = [], set()
    for item in sorted(dated, key=lambda i: i["seen"], reverse=True):
        key = item["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"title": item["title"], "source": item.get("domain"), "published": item["seen"].isoformat(),
                    "url": item["url"]})
        if len(out) >= MAX_ARTICLES:
            break
    return out


async def _fetch(params: dict, client: httpx.AsyncClient | None) -> list[dict]:
    global _last_request
    own = client is None
    client = client or net.async_client(timeout=TIMEOUT_SEC, follow_redirects=False)
    try:
        wait = REQUEST_GAP_SEC - (time.monotonic() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = time.monotonic()
        # read as it comes and stop past the cap - a huge answer is never held whole
        async with client.stream("GET", macro_news.GOOGLE_NEWS, params=params) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > macro_news.RSS_MAX_BYTES:
                    raise ValueError("검색 결과가 너무 큼")
            text = body.decode(response.encoding or "utf-8", errors="replace")
        return macro_news.parse_rss(text)
    finally:
        if own:
            await client.aclose()


async def search(query: object, window: object = None, client: httpx.AsyncClient | None = None,
                 now: datetime | None = None) -> dict:
    """Recent news articles for ``query`` within ``window`` (1d · 3d · 7d · 30d)."""
    q = clean_query(query)
    w = str(window) if window in WINDOWS else DEFAULT_WINDOW
    key = (q.lower(), w)
    async with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SEC:
            return hit[1]
        at = now or datetime.now(UTC)
        found = articles(await _fetch(feed_params(q, w), client), at, w)
        result = {"query": q, "window": w, "source": "google-news", "fetched_at": at.isoformat(), "articles": found}
        if not found:
            result["note"] = "이 기간에 맞는 기사 없음 — 검색어를 넓히거나 기간을 늘려 다시 검색"
        if len(_cache) >= CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (time.monotonic(), result)
        return result


ARGS = {"type": "object", "required": ["query"], "properties": {
    "query": {"type": "string", "description": "short topic words, e.g. 'Fed rate decision' or '코스피 외국인 매도'"},
    "window": {"type": "string", "enum": list(WINDOWS), "description": "how far back (default 7d)"}}}


def scripted(query: object) -> dict:
    """DESK_FAKE_RUNS: the same checks and shape with made-up articles, no request."""
    q = clean_query(query)
    at = datetime.now(UTC).replace(microsecond=0)
    return {"query": q, "window": DEFAULT_WINDOW, "source": "scripted", "fetched_at": at.isoformat(), "articles": [
        {"title": f"(대본 기사) {q} — 첫 번째 헤드라인", "source": "Desk Script",
         "published": (at - timedelta(hours=2)).isoformat(), "url": "https://example.com/desk-script-1"},
        {"title": f"(대본 기사) {q} — 두 번째 헤드라인", "source": "Desk Script",
         "published": (at - timedelta(hours=9)).isoformat(), "url": "https://example.com/desk-script-2"}]}


def links(result: object) -> list[dict]:
    """The articles a search result points to, for the page: title, source, time and an http(s) link."""
    if not isinstance(result, dict) or not isinstance(result.get("articles"), list):
        return []
    out = []
    for a in result["articles"][:MAX_ARTICLES]:
        url = a.get("url") if isinstance(a, dict) else None
        if macro_news._web(url) and len(str(url)) <= MAX_URL and a.get("title"):
            out.append({"title": str(a["title"])[:200], "source": a.get("source"), "published": a.get("published"),
                        "url": str(url)})
    return out
