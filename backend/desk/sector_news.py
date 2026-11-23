"""Sector and key-person news surges (#244): the company / CEO / product stories the macro themes miss.

desk.macro_news watches the world's themes (rates, tariffs, wars). A story like a chip CEO's
scandal, an export licence or a memory maker's qualification moves a sector without touching
those. Here each sector has watched topics - a company, its CEO, a product - each a Google News
RSS search (keyless) in English or Korean. A topic whose last 24 hours carry more than its usual
share of the week's articles is an issue: its headlines are grouped into stories by title overlap,
tagged (person, legal, regulation, earnings, product, deal) by words in them, and handed to
desk.news_digest for a Korean title and a market read like the briefing's.

Fetched only by the background loop (desk.api, with the briefing); a failed topic keeps its last
value. Headlines are untrusted text: shown as data, never followed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

import httpx

from . import net
from .macro_news import GOOGLE_NEWS, RSS_MAX_BYTES, TIMEOUT_SEC, parse_rss

log = logging.getLogger(__name__)

SPIKE = 1.5            # the last day's share of the week at least this many times the topics' median share
MIN_24H = 5            # and at least this many articles in it
HEADLINES = 5
STORY_OVERLAP = 0.5    # titles sharing this share of their words are one story
GAP_SEC = 1.0          # between feed requests
STALE_SEC = 6 * 3600


@dataclass(frozen=True)
class Topic:
    id: str
    label: str                  # Korean, for the page
    query: str                  # Google News search
    lang: str                   # en | ko
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class Sector:
    id: str
    label: str
    tickers: tuple[str, ...]    # a run on one of these gets the sector's issues in its note
    topics: tuple[Topic, ...]


SEMIS = ("NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "ASML", "AMAT", "LRCX", "QCOM", "ARM", "SMH", "SOXX", "SOXL",
         "SOXS", "^SOX", "005930.KS", "000660.KS", "SNDK")
BIGTECH = ("AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "NVDA", "QQQ", "TQQQ", "SQQQ", "ORCL", "PLTR")

SECTORS: tuple[Sector, ...] = (
    Sector("semis", "반도체", SEMIS, (
        Topic("nvidia", "엔비디아 · 젠슨 황", '(Nvidia OR "Jensen Huang")', "en", ("NVDA",)),
        Topic("tsmc", "TSMC", '(TSMC OR "Taiwan Semiconductor")', "en", ("TSM",)),
        Topic("micron", "마이크론 · 메모리", '(Micron OR "memory chip" OR HBM)', "en", ("MU", "000660.KS", "005930.KS")),
        Topic("asml", "ASML · 장비", '(ASML OR "Applied Materials" OR "Lam Research")', "en", ("ASML", "AMAT", "LRCX")),
        Topic("amd_intel", "AMD · 인텔", '(AMD OR Intel) chip', "en", ("AMD", "INTC")),
        Topic("chip_export", "반도체 수출 규제", '("chip export" OR "export controls" semiconductor)', "en", SEMIS),
        Topic("samsung", "삼성전자", "삼성전자 반도체", "ko", ("005930.KS",)),
        Topic("hynix", "SK하이닉스", "SK하이닉스", "ko", ("000660.KS",)),
    )),
    Sector("bigtech", "빅테크 · AI", BIGTECH, (
        Topic("apple", "애플 · 팀 쿡", '(Apple OR "Tim Cook") iPhone OR Apple stock', "en", ("AAPL",)),
        Topic("microsoft", "마이크로소프트", '(Microsoft OR Nadella) AI OR Azure', "en", ("MSFT",)),
        Topic("alphabet", "구글 · 알파벳", '(Alphabet OR Google) antitrust OR Gemini OR stock', "en", ("GOOGL", "GOOG")),
        Topic("meta", "메타 · 저커버그", '(Meta OR Zuckerberg) stock OR AI', "en", ("META",)),
        Topic("tesla", "테슬라 · 일론 머스크", '(Tesla OR "Elon Musk") stock', "en", ("TSLA",)),
        Topic("openai", "오픈AI", '(OpenAI OR "Sam Altman")', "en", ("MSFT", "NVDA", "ORCL")),
    )),
)

# word -> tag; the first tag a story's titles hit, in this order
TAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("person", ("ceo", "affair", "scandal", "resign", "resigns", "steps down", "ousted", "health", "divorce", "arrest",
                "불륜", "사임", "사퇴", "구속", "스캔들", "건강")),
    ("legal", ("lawsuit", "sues", "sued", "court", "trial", "fine", "fined", "settlement", "소송", "재판", "벌금")),
    ("regulation", ("export control", "export ban", "ban", "sanction", "antitrust", "probe", "investigation", "license",
                    "tariff", "규제", "제재", "조사", "수출 통제", "허가")),
    ("earnings", ("earnings", "revenue", "guidance", "profit", "outlook", "forecast", "quarter", "실적", "매출", "영업이익",
                  "가이던스")),
    ("deal", ("acquire", "acquisition", "merger", "deal", "stake", "invest", "investment", "인수", "합병", "투자", "지분")),
    ("product", ("launch", "unveil", "chip", "model", "hbm", "qualification", "supply", "order", "출시", "공개", "공급",
                 "수주", "인증")),
)
TAG_KO = {"person": "인물", "legal": "소송", "regulation": "규제", "earnings": "실적", "deal": "거래 · 투자",
          "product": "제품 · 공급"}
_WORD = re.compile(r"[a-z0-9]+|[가-힣]+")


def _words(title: str) -> set[str]:
    return {w for w in _WORD.findall(title.lower()) if len(w) > 2 or re.match(r"[가-힣]", w)}


def stories(items: list[dict]) -> list[list[dict]]:
    """Headlines grouped by title overlap (newest first inside a story), the biggest story first."""
    groups: list[tuple[set[str], list[dict]]] = []
    for item in items:
        words = _words(item["title"])
        for gw, members in groups:
            if words and gw and len(words & gw) / min(len(words), len(gw)) >= STORY_OVERLAP:
                members.append(item)
                gw |= words
                break
        else:
            groups.append((set(words), [item]))
    return [m for _, m in sorted(groups, key=lambda g: -len(g[1]))]


def _has(text: str, word: str) -> bool:
    """English words whole ("sues" is not in "issues", "ban" not in "bank"); Korean as a substring
    (particles attach to the word)."""
    if re.search(r"[가-힣]", word):
        return word in text
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?:e?s)?(?![a-z0-9])", text) is not None


def tag_of(titles: list[str]) -> str | None:
    text = " ".join(titles).lower()
    for tag, words in TAGS:
        if any(_has(text, w) for w in words):
            return tag
    return None


def summarise(topic: Topic, sector: Sector, items: list[dict], now: datetime) -> dict:
    dated = [i for i in items if i["seen"] is not None]
    last = [i for i in dated if (now - i["seen"]).total_seconds() < 86400]
    ratio = round(len(last) / len(dated) * 7, 2) if len(dated) >= 10 else None
    newest = sorted(last or dated, key=lambda i: i["seen"], reverse=True)
    grouped = stories(newest)
    top = grouped[0] if grouped else []
    tag = tag_of([i["title"] for i in top]) if top else None
    heads = top[:HEADLINES] + [i for g in grouped[1:] for i in g[:1]][:max(0, HEADLINES - len(top[:HEADLINES]))]
    return {"id": topic.id, "label": topic.label, "sector": sector.id, "lang": topic.lang,
            "tickers": list(topic.tickers), "articles_24h": len(last), "spike": ratio, "spike_raw": ratio,
            "spike_kind": "raw-share",
            # an issue is decided against the other topics (``rebase``), never on one feed's share
            "saturated": len(dated) >= 95 and len(last) == len(dated), "issue": False,
            "tag": tag, "tag_ko": TAG_KO.get(tag) if tag else None, "story_size": len(top),
            "headlines": [{"title": i["title"], "url": i["url"], "domain": i.get("domain"),
                           "seen": i["seen"].isoformat()} for i in heads],
            "fetched_at": now.isoformat()}


async def fetch_topic(topic: Topic, sector: Sector, now: datetime, client: httpx.AsyncClient) -> dict:
    hl = {"en": ("en-US", "US", "US:en"), "ko": ("ko", "KR", "KR:ko")}[topic.lang]
    params = {"q": f"{topic.query} when:7d", "hl": hl[0], "gl": hl[1], "ceid": hl[2]}
    response = await client.get(GOOGLE_NEWS, params=params)
    response.raise_for_status()
    if len(response.content) > RSS_MAX_BYTES:
        raise ValueError("RSS too large")
    return summarise(topic, sector, parse_rss(response.text), now)


def rebase(topics: dict[str, dict]) -> None:
    """Google News returns the newest 100 of the week, so every busy topic looks "surging" on its
    own share. Like desk.macro_news's fallback, a topic is measured against the others: its share
    over the median share (never below an even seventh - a quiet day must not inflate the rest)."""
    raws = [t["spike_raw"] for t in topics.values() if t.get("spike_raw") is not None]
    if len(raws) < 4:            # too few to compare (a pass mostly refused): no issues, no ratio
        for k, t in topics.items():
            topics[k] = {**t, "spike": None, "spike_kind": None, "issue": False}
        return
    base = max(median(raws), 1.0)
    for k, t in topics.items():
        raw = t.get("spike_raw")
        rel = round(raw / base, 2) if raw is not None else None
        topics[k] = {**t, "spike": rel, "spike_kind": "topics",       # new dicts: a reader keeps the old ones
                     "issue": rel is not None and rel >= SPIKE and t["articles_24h"] >= MIN_24H}


class Cache:
    def __init__(self) -> None:
        self.topics: dict[str, dict] = {}
        self.refreshed_at: str | None = None
        self.error: str | None = None

    async def refresh(self, client: httpx.AsyncClient | None = None, now: datetime | None = None,
                      gap: float = GAP_SEC) -> int:
        own = client is None
        client = client or net.async_client(timeout=TIMEOUT_SEC, follow_redirects=True)
        fresh, ok, failures = dict(self.topics), 0, []
        try:
            for sector in SECTORS:
                for topic in sector.topics:
                    try:
                        fresh[topic.id] = await fetch_topic(topic, sector, now or datetime.now(UTC), client)
                        ok += 1
                    except Exception as exc:  # noqa: BLE001 - one topic down keeps its last value
                        failures.append(f"{topic.id}: {type(exc).__name__}")
                    if gap:
                        await asyncio.sleep(gap)
        finally:
            if own:
                await client.aclose()
        rebase(fresh)
        self.topics = fresh                       # swapped whole: readers never see a half pass
        if ok:
            self.refreshed_at = (now or datetime.now(UTC)).isoformat()
        self.error = "; ".join(failures) or None
        return ok

    def as_themes(self) -> dict[str, dict]:
        """The issues in desk.news_digest's shape (only the issues are worth a model call)."""
        return {t["id"]: t for t in self.topics.values() if t["issue"]}

    def view(self, sector: str | None = None, now: datetime | None = None, digest=None) -> dict:
        """GET /api/sector-news: per sector its issues (surging topics, biggest first) and every
        topic's count, each headline with the digest's read when there is one."""
        from .news_digest import DIGEST

        digest = digest or DIGEST
        now = now or datetime.now(UTC)
        out = []
        for s in SECTORS:
            if sector and s.id != sector:
                continue
            rows = [{**self.topics[t.id], "stale": _age(self.topics[t.id], now) > STALE_SEC}
                    for t in s.topics if t.id in self.topics]
            read = digest.annotate({"themes": rows})["themes"]
            issues = sorted((r for r in read if r["issue"] and not r["stale"]),
                            key=lambda r: (-(r["spike"] or 0), -r["articles_24h"]))
            out.append({"id": s.id, "label": s.label, "issues": issues,
                        "topics": [{k: r[k] for k in ("id", "label", "articles_24h", "spike", "issue", "stale")}
                                   for r in read]})
        return {"sectors": out, "refreshed_at": self.refreshed_at, "error": self.error,
                "note": "기사 수 급증으로 잡은 이슈이며, 영향 판정은 헤드라인을 모델이 해석한 값입니다"}

    def note_lines(self, ticker: str, now: datetime | None = None, digest=None, limit: int = 3) -> list[str]:
        """Lines for the agents' macro note: the surging issues of the instrument's sectors."""
        from .news_digest import DIGEST

        digest = digest or DIGEST
        now = now or datetime.now(UTC)
        mine = {t.id for s in SECTORS if ticker in s.tickers for t in s.topics}
        lines = []
        for t in sorted((t for t in self.topics.values() if t["id"] in mine and t["issue"]
                         and _age(t, now) <= STALE_SEC), key=lambda t: -(t["spike"] or 0))[:limit]:
            top = t["headlines"][0] if t["headlines"] else None
            if not top:
                continue
            title = " ".join(top["title"].replace('"', "'").split())[:200]
            r = digest.reads.get(top["url"])
            read = (f" Read as positive {r['sentiment']['pos']}% / negative {r['sentiment']['neg']}%, "
                    f"impact {r['impact']} (a model reading of the headline, not a forecast).") if r else ""
            lines.append(f"- Sector issue ({t['id']}, {t['tag'] or 'news'}): {t['articles_24h']} articles in 24h, "
                         f"{t['spike']:.1f}x the other watched topics' pace. Top story: \"{title}\" ({top.get('domain')}).{read}")
        return lines


def _age(row: dict, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(row["fetched_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return float("inf")


CACHE = Cache()
