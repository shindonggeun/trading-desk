"""Explainable, read-only headline observation for an existing public news cache."""

from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from . import dart_rss, filings, macro_news

VERSION = "headline-rules-v1"
MIN_SAMPLES = 3
MIN_PUBLISHERS = 2
MAX_PUBLISHER_SAMPLES = 2
MAX_PUBLISHER_SHARE = 0.6
MAX_SNAPSHOT_BYTES = 512_000
MAX_ARTICLES = 80
MAX_HEADLINES_OUT = 12
NEWS_WINDOW = timedelta(hours=macro_news.WINDOW_H)
MAX_FEED_AGE = timedelta(seconds=macro_news.STALE_SEC)
FILING_TIMEOUT = 4.0

ALIASES = {
    "SOXX": ("SOXX", "iShares Semiconductor ETF", "아이셰어즈 반도체 ETF"),
    "SOXL": ("SOXL", "Direxion Daily Semiconductor Bull 3X", "디렉시온 반도체 3배"),
    "NVDA": ("NVIDIA", "엔비디아"),
    "AMD": ("Advanced Micro Devices", "어드밴스드 마이크로 디바이시스"),
    "AVGO": ("Broadcom", "브로드컴"),
    "INTC": ("Intel", "인텔"),
    "MU": ("Micron Technology", "마이크론 테크놀로지"),
    "ASML": ("ASML", "에이에스엠엘"),
    "TSM": ("TSMC ADR", "TSMC ADRs", "TSMC ADS", "TSMC 미국 ADR", "TSMC's ADR", "TSMC’s ADR"),
    "2330.TW": ("2330.TW", "TSMC", "Taiwan Semiconductor", "대만 TSMC"),
    "285A.T": ("285A.T", "Kioxia", "키옥시아"),
    "005930.KS": ("005930.KS", "Samsung Electronics", "삼성전자"),
    "000660.KS": ("000660.KS", "SK Hynix", "SK하이닉스"),
    "AAPL": ("Apple Inc", "애플"),
    "MSFT": ("Microsoft", "마이크로소프트"),
    "META": ("Meta Platforms", "메타 플랫폼스"),
    "GOOGL": ("Alphabet Inc", "알파벳"),
    "GOOG": ("Alphabet Inc", "알파벳"),
    "AMZN": ("Amazon.com", "아마존"),
    "TSLA": ("Tesla", "테슬라"),
}
CHIP_WORDS = ("semiconductor", "semiconductors", "chipmaker", "chipmakers", "chip stocks", "반도체",
              "NVIDIA", "엔비디아", "TSMC", "삼성전자", "SK하이닉스", "Broadcom", "브로드컴",
              "Advanced Micro Devices", "Micron Technology", "ASML", "Kioxia", "키옥시아")
POSITIVE = ("beats estimates", "beat estimates", "beats expectations", "raises guidance", "raises outlook",
            "record profit", "approval granted", "실적 상회", "예상치 상회", "가이던스 상향", "전망 상향", "승인 획득")
NEGATIVE = ("misses estimates", "missed estimates", "misses expectations", "cuts guidance", "cuts outlook",
            "profit warning", "export ban", "실적 하회", "예상치 하회", "가이던스 하향", "전망 하향", "수출 금지")
TOPICS = {
    "earnings": ("earnings", "quarterly results", "profit", "estimates", "실적", "영업이익"),
    "guidance": ("guidance", "outlook", "가이던스", "전망"),
    "regulation": ("regulation", "export controls", "export ban", "antitrust", "규제", "수출 금지"),
    "supply_demand": ("order backlog", "chip demand", "capacity", "공급", "수요", "증설"),
}
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z][a-z0-9-]*\Z", re.ASCII)
_UNSAFE_QUERY = re.compile(r"(?:passw|token|secret|signatur|credential|session|authorization|"
                           r"api[_-]?key|(?:^|[_-])(?:auth|key|sig|code)(?:$|[_-]))", re.IGNORECASE)
_TRACKING = frozenset({"fbclid", "gclid", "guccounter", "mc_cid", "mc_eid"})
_WORD = re.compile(r"[^\W_]+")
_KOREAN_END = r"(?:(?:께서|에서|으로|까지|의|이|가|은|는|을|를|에|와|과|도|만|로))?(?!\w)"
_SYMBOL_END = r"(?!\w|[.-]\w)"
_EXCHANGE = r"(?:NASDAQ|NYSE(?:ARCA)?|AMEX|CBOE|KRX)"


def _at(value: object) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
        return stamp.astimezone(UTC) if stamp is not None and stamp.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _time(stamp: datetime | None) -> str | None:
    return stamp.isoformat(timespec="seconds") if stamp is not None else None


def _clean_title(value: object) -> str:
    if not isinstance(value, str):
        return ""
    safe = "".join(" " if unicodedata.category(c) in {"Cc", "Cf", "Cs"} else c for c in value[:1000])
    return " ".join(safe.split())[:200]


def _link(value: object) -> tuple[str, str, tuple] | None:
    if not isinstance(value, str) or not value or len(value) > 2048 or "\\" in value:
        return None
    if any(c.isspace() or unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value):
        return None
    try:
        part = urlsplit(value)
        host = (part.hostname or "").rstrip(".")
        port = part.port
        fields = parse_qsl(part.query, keep_blank_values=True, max_num_fields=60)
    except ValueError:
        return None
    if (part.scheme not in {"http", "https"} or not _HOST.fullmatch(host) or part.username is not None
            or port not in (None, 443 if part.scheme == "https" else 80)
            or host.endswith((".local", ".localhost", ".internal", ".test", ".invalid", ".home.arpa"))
            or re.search(r"%(?:00|0a|0d|5c)", value, re.IGNORECASE)):
        return None
    if any(_UNSAFE_QUERY.search(name) for name, _ in fields):
        return None
    kept = [(name, val) for name, val in fields
            if not (name.lower().startswith("utm_") or name.lower() in _TRACKING)]
    path = part.path or "/"
    # The canonical key ignores http/https and common mobile host aliases, not the article path.
    outlet = host.removeprefix("www.").removeprefix("m.")
    url = urlunsplit((part.scheme, part.netloc, path, urlencode(kept), ""))
    return url, outlet, (outlet, path.rstrip("/"), tuple(sorted(kept)))


def _matching(title: str, terms: tuple[str, ...]) -> list[str]:
    matches = []
    for term in terms:
        suffix = _KOREAN_END if "가" <= term[-1] <= "힣" else r"(?!\w)"
        if re.search(rf"(?<!\w){re.escape(term)}{suffix}", title, re.IGNORECASE):
            matches.append(term)
    return matches


def _symbol(ticker: str, title: str, *, bare: bool = False) -> bool:
    prefix = rf"(?:\$|{_EXCHANGE}:\s*)" if not bare else ""
    return re.search(rf"(?<![\w.$-]){prefix}{re.escape(ticker)}{_SYMBOL_END}", title, re.IGNORECASE) is not None


def _relevance(ticker: str, title: str) -> str | None:
    aliases = ALIASES.get(ticker, ())
    if _symbol(ticker, title) or (ticker in aliases and _symbol(ticker, title, bare=True)):
        return "direct"
    if ticker == "2330.TW" and (_symbol("TSM", title) or _matching(title, ALIASES["TSM"])):
        return "indirect"
    if _matching(title, tuple(alias for alias in aliases if alias != ticker)):
        return "direct"
    if ticker in {"SOXX", "SOXL"} and _matching(title, CHIP_WORDS):
        return "indirect"
    if ticker == "TSM" and _matching(title, ("TSMC", "Taiwan Semiconductor")):
        return "indirect"
    return None


def _direction(title: str) -> tuple[str, str, str]:
    positive, negative = _matching(title, POSITIVE), _matching(title, NEGATIVE)
    if positive and negative:
        return "neutral", "conflicting_phrases", f"제목에 상반된 표현이 있습니다. {positive[0]} · {negative[0]}"
    if positive:
        return "positive", "positive_phrase", f"긍정 분류에 사용한 제목 표현은 ‘{positive[0]}’입니다."
    if negative:
        return "negative", "negative_phrase", f"부정 분류에 사용한 제목 표현은 ‘{negative[0]}’입니다."
    return "neutral", "no_directional_phrase", "제목에서 규칙에 해당하는 방향 표현을 찾지 못했습니다."


def _saved_themes() -> dict:
    path = macro_news.CACHE.cache_file or macro_news.CACHE_FILE
    try:
        if not path.is_file():
            return {}
        with path.open("rb") as saved:
            raw = saved.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            return {}
        data = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        return {}
    themes = data.get("themes") if isinstance(data, dict) else None
    return themes if isinstance(themes, dict) else {}


def _themes(now: datetime, persisted: dict) -> tuple[list[tuple[datetime, dict]], bool]:
    rows = []
    partial = bool(macro_news.CACHE.error)
    current = macro_news.CACHE.themes
    for theme in macro_news.THEMES:
        options = []
        for row in (current.get(theme.id), persisted.get(theme.id)):
            if not isinstance(row, dict) or row.get("source") not in ("gdelt", "google-news"):
                continue
            fetched = _at(row.get("fetched_at"))
            if fetched is not None and fetched <= now and isinstance(row.get("headlines"), list):
                options.append((fetched, row))
            else:
                partial = True
        if options:
            latest = max(options, key=lambda option: option[0])[1]
            partial |= latest["source"] != "gdelt"
            gdelt = [(fetched, row) for fetched, row in options if row["source"] == "gdelt"]
            if gdelt:
                rows.append(max(gdelt, key=lambda option: option[0]))
    return rows, partial


def _observed(row: object, fetched: datetime, now: datetime) -> tuple | None:
    if not isinstance(row, dict):
        return None
    link = _link(row.get("url"))
    stamp = _at(row.get("seen"))
    title = _clean_title(row.get("title"))
    if (not link or dart_rss.receipt_number(link[0]) or stamp is None or not title
            or stamp > min(now, fetched + timedelta(minutes=15))):
        return None
    return stamp, title, link


def _item(ticker: str, observation: tuple, checked_at: datetime, since: datetime, now: datetime) -> dict | None:
    stamp, title, link = observation
    relation = _relevance(ticker, title)
    if stamp < since or not relation:
        return None
    url, publisher, key = link
    sentiment, reason, summary = _direction(title)
    tags = [topic for topic, terms in TOPICS.items() if _matching(title, terms)]
    stale = now - checked_at > MAX_FEED_AGE
    recency = max(0.25, 1 - 0.75 * (now - stamp) / NEWS_WINDOW)
    weight = (1.0 if relation == "direct" else 0.5) * recency * (1.25 if tags else 1.0)
    words = _WORD.findall(unicodedata.normalize("NFKC", title).casefold())
    return {
        "kind": "news", "source": "gdelt", "publisher": publisher, "title": title, "summary": summary,
        "url": url, "at": _time(stamp), "filed_date": None, "time_basis": "gdelt_seen", "relevance": relation,
        "tags": tags, "sentiment": sentiment, "reason": reason, "weight": None, "contribution": None,
        "stale": stale, "_key": key, "_words": words, "_stamp": stamp, "_weight": weight,
    }


def _dedup(candidates: list[dict]) -> list[dict]:
    out = []
    for row in sorted(candidates, key=lambda r: (r["stale"], r["relevance"] != "direct", -r["_stamp"].timestamp(),
                                                r["url"], r["title"])):
        words = set(row["_words"])
        duplicate = False
        for kept in out:
            if row["_key"] == kept["_key"] or row["_words"] == kept["_words"]:
                duplicate = True
                break
            previous = set(kept["_words"])
            if (row["sentiment"] == kept["sentiment"] and min(len(words), len(previous)) >= 6
                    and len(words & previous) / len(words | previous) >= 0.85):
                duplicate = True
                break
        if not duplicate:
            out.append(row)
    return out


def _source(id_: str, status: str, reason: str | None = None, updated_at: datetime | None = None,
            prior_http: int | None = None) -> dict:
    return {"id": id_, "status": status, "reason": reason, "updated_at": _time(updated_at),
            "prior_http_status": prior_http}


def _news(ticker: str, since: datetime, now: datetime, persisted: dict) -> tuple[dict, dict, list[dict]]:
    rows, partial = _themes(now, persisted)
    observed, candidates = [], []
    for fetched, row in rows:
        headlines = [observation for raw in row["headlines"][:MAX_ARTICLES // len(rows)]
                     if (observation := _observed(raw, fetched, now)) is not None]
        # Outside market hours a newer timeline can carry old headlines without an ArtList request.
        checked_at = min(fetched, max((head[0] for head in headlines), default=fetched))
        observed.append(checked_at)
        candidates.extend(item for head in headlines
                          if (item := _item(ticker, head, checked_at, since, now)) is not None)
    newest = max(observed, default=None)
    if observed:
        fresh = sum(now - at <= MAX_FEED_AGE for at in observed)
        if not fresh:
            source = _source("gdelt", "stale", "snapshot_stale", newest)
        elif fresh < len(observed) or partial:
            source = _source("gdelt", "partial", "partial_snapshot", newest)
        else:
            source = _source("gdelt", "available", updated_at=newest)
    else:
        source = _source("gdelt", "unavailable", "snapshot_unavailable")
    evidence = _dedup(candidates)
    counts = Counter()
    sample = []
    for row in evidence:
        publisher = row["publisher"]
        if not row["stale"] and counts[publisher] < MAX_PUBLISHER_SAMPLES:
            counts[publisher] += 1
            sample.append(row)
    score = None
    if not rows:
        status, reason = "unavailable", "news_source_unavailable"
    elif source["status"] == "stale" or (not sample and any(row["stale"] for row in evidence)):
        status, reason = "stale", "news_snapshot_stale"
    elif len(sample) < MIN_SAMPLES or len(counts) < MIN_PUBLISHERS:
        status, reason = "insufficient", "too_few_unique_headlines"
    else:
        status, reason = source["status"], source["reason"]
        totals = Counter()
        for row in sample:
            totals[row["publisher"]] += row["_weight"]
        total = sum(totals.values())
        for row in sample:
            own = totals[row["publisher"]]
            max_weight = (total - own) * MAX_PUBLISHER_SHARE / (1 - MAX_PUBLISHER_SHARE)
            row["weight"] = row["_weight"] * min(1.0, max_weight / own)
        denominator = sum(row["weight"] for row in sample)
        sign = {"positive": 1, "negative": -1, "neutral": 0}
        for row in sample:
            row["contribution"] = 50 * row["weight"] * sign[row["sentiment"]] / denominator
        score = round(50 + sum(row["contribution"] for row in sample), 1)
    public = {"status": status, "score": score, "sample_count": len(sample), "publisher_count": len(counts),
              "reason": reason, "updated_at": _time(newest)}
    evidence.sort(key=lambda r: (r["stale"], -abs(r["contribution"] or 0), -r["_stamp"].timestamp(), r["url"]))
    items = [{key: round(val, 3) if key in {"weight", "contribution"} and val is not None else val
              for key, val in row.items() if not key.startswith("_")} for row in evidence[:MAX_HEADLINES_OUT]]
    return public, source, items


async def _edgar(ticker: str, since: datetime, now: datetime, enabled: bool) -> tuple[dict, list[dict]]:
    if not filings.is_us_ticker(ticker):
        return _source("sec_edgar", "unavailable", "unsupported_symbol"), []
    if not enabled or not os.getenv("SEC_EDGAR_USER_AGENT", "").strip():
        return _source("sec_edgar", "unavailable", "not_configured"), []
    try:
        rows = await asyncio.wait_for(filings.FILINGS.public_recent(ticker, since, now), FILING_TIMEOUT)
    except httpx.HTTPStatusError as exc:
        reasons = {403: "access_denied", 404: "not_found", 429: "rate_limited"}
        return _source("sec_edgar", "unavailable", reasons.get(exc.response.status_code, "source_error")), []
    except (TimeoutError, httpx.TimeoutException):
        return _source("sec_edgar", "unavailable", "source_timeout"), []
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return _source("sec_edgar", "unavailable", "source_error"), []
    if rows is None:
        return _source("sec_edgar", "unavailable", "symbol_unmapped"), []
    items = [{
        "kind": "filing", "source": "sec_edgar", "publisher": "SEC EDGAR", "title": f"{ticker} {row['form']} 공시",
        "summary": f"공시 항목 · {', '.join(row['items'])}" if row["items"] else "공식 제출 메타데이터입니다.",
        "url": row["url"], "at": row["at"], "filed_date": row["filed_date"],
        "time_basis": "edgar_acceptance" if row["at"] else "edgar_filing_date", "relevance": "direct",
        "tags": ["filing"], "sentiment": None, "reason": "filing_metadata_unscored",
        "weight": None, "contribution": None, "stale": False,
    } for row in rows]
    return _source("sec_edgar", "available", updated_at=now), items


async def _dart(ticker: str, since: datetime, now: datetime, enabled: bool) -> tuple[dict, list[dict]]:
    if ticker in dart_rss.COMPANIES:
        companies, relevance = (ticker,), "direct"
    elif ticker in {"SOXX", "SOXL"}:
        companies, relevance = tuple(dart_rss.COMPANIES), "indirect"
    else:
        return _source("dart_rss", "unavailable", "unsupported_symbol"), []
    if not enabled:
        return _source("dart_rss", "unavailable", "not_configured"), []

    feeds = await asyncio.gather(*(dart_rss.RSS.read(company, now) for company in companies))
    checked = [feed.checked_at for feed in feeds if feed.checked_at is not None]
    if not checked:
        reasons = {feed.reason for feed in feeds}
        reason = next(iter(reasons)) if len(reasons) == 1 else "source_error"
        return _source("dart_rss", "unavailable", reason), []

    items = {}
    for company, feed in zip(companies, feeds, strict=True):
        name = dart_rss.COMPANIES[company].name
        for notice in feed.notices:
            if since <= notice.published_at <= now and notice.receipt not in items:
                items[notice.receipt] = {
                    "kind": "filing", "source": "dart_rss", "publisher": "금융감독원 DART",
                    "title": f"{name} 공시 ({notice.receipt})", "summary": "공식 공시 RSS 메타데이터입니다.",
                    "url": dart_rss.LINK_PREFIX + notice.receipt, "at": _time(notice.published_at), "filed_date": None,
                    "time_basis": "dart_rss_pubdate", "relevance": relevance,
                    "tags": ["filing"], "sentiment": None, "reason": "filing_metadata_unscored",
                    "weight": None, "contribution": None, "stale": False,
                }
    status = (_source("dart_rss", "partial", "partial_source", min(checked)) if len(checked) < len(feeds)
              else _source("dart_rss", "available", None if items else "no_recent_items", min(checked)))
    return status, sorted(items.values(), key=lambda item: (item["at"], item["url"]), reverse=True)


async def read(ticker: str, days: int, *, now: datetime | None = None, include_filings: bool = True) -> dict:
    """Read approved metadata and compute a deterministic score without a news fetch or an LLM call."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    since = now - timedelta(days=days)
    persisted, (edgar, disclosures), (dart, notices) = await asyncio.gather(
        asyncio.to_thread(_saved_themes), _edgar(ticker, since, now, include_filings),
        _dart(ticker, since, now, include_filings),
    )
    news, gdelt, headlines = _news(ticker, max(since, now - NEWS_WINDOW), now, persisted)
    return {
        "ticker": ticker, "days": days, "generated_at": _time(now),
        "method": {"version": VERSION, "baseline": 50, "min_samples": MIN_SAMPLES, "min_publishers": MIN_PUBLISHERS},
        "coverage": {"requested_days": days, "news_max_days": macro_news.WINDOW_H // 24},
        "news": news,
        "community": {"status": "unavailable", "score": None, "sample_count": 0,
                      "reason": "sources_not_authorized", "updated_at": None},
        "sources": [gdelt, edgar,
                    _source("google_news", "unavailable", "usage_not_verified"),
                    _source("yahoo_news", "unavailable", "usage_not_verified"),
                    _source("open_dart", "unavailable", "not_configured"), dart,
                    _source("issuer_ir", "unavailable", "not_configured"),
                    _source("reddit", "unavailable", "not_authorized", prior_http=404),
                    _source("stocktwits", "unavailable", "not_authorized", prior_http=403)],
        "items": headlines + disclosures + notices,
    }
