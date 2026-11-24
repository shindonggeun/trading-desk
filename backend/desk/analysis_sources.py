"""Bounded optional market evidence owned by the desk's analysis runner."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from threading import Lock

import httpx

log = logging.getLogger(__name__)
POLYMARKET_SEARCH = "https://gamma-api.polymarket.com/public-search"
_TOPIC = re.compile(r"[A-Z0-9.^=\-]{1,24}", re.ASCII)
_BUDGET = 2.5
_ATTEMPT_TIMEOUT = 1.1
_BACKOFF = 0.2
_PATH_COOLDOWN = 15 * 60
_TRANSIENT_COOLDOWN = 60
_MAX_BYTES = 512 * 1024
_MAX_QUOTES = 4


@dataclass(frozen=True)
class Evidence:
    status: str
    reason: str | None = None
    quotes: tuple[tuple[str, float], ...] = ()


def prediction_enabled(config: Mapping) -> bool:
    """Honor the existing explicit tool/category vendor selection without fallback."""
    vendors = config.get("data_vendors") or {}
    tools = config.get("tool_vendors") or {}
    selected = tools.get("get_prediction_markets", vendors.get("prediction_markets", "default"))
    return isinstance(selected, str) and selected.strip().lower() in {"polymarket", "default"}


def evidence_line(source: str, evidence: Evidence, *, korean: bool) -> str:
    if evidence.status == "available":
        label = "시장 가격 조회 성공" if korean else "market prices available"
    elif evidence.status == "empty":
        label = "조회 성공 · 사용 가능한 시장 0건" if korean else "successful lookup · 0 usable markets"
    else:
        label = "이 원천의 근거 없음(조회 불가)" if korean else "no evidence from this source (unavailable)"
    reason = f"; reason={evidence.reason}" if evidence.reason else ""
    return f"- {source}: {label} [status={evidence.status}{reason}]"


_logged_exclusions: set[str] = set()
_exclusion_lock = Lock()


def excluded(source: str, reason: str) -> Evidence:
    with _exclusion_lock:
        if source not in _logged_exclusions:
            _logged_exclusions.add(source)
            log.info("analysis source=%s reason=%s circuit=disabled", source, reason)
    return Evidence("disabled", reason)


@dataclass(frozen=True)
class _Failure:
    reason: str
    cooldown: float = 0
    retry: bool = False


def _retry_after(header: str | None, now: datetime) -> float:
    if header and header.isascii() and header.isdigit() and len(header) <= 10:
        return max(1, int(header))
    if header and len(header) <= 120:
        try:
            at = parsedate_to_datetime(header)
            if at.tzinfo is not None:
                return max(1, (at - now).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    return 60


def _yes_price(market: dict) -> float | None:
    try:
        outcomes = json.loads(market["outcomes"])
        prices = json.loads(market["outcomePrices"])
        if not isinstance(outcomes, list) or sorted(outcomes) != ["No", "Yes"]:
            return None
        if not isinstance(prices, list) or len(prices) != 2 or any(isinstance(value, bool) for value in prices):
            return None
        values = [float(value) for value in prices]
        if any(not math.isfinite(price) or not 0 <= price <= 1 for price in values):
            return None
        return values[outcomes.index("Yes")]
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _quotes(data: object, now: datetime) -> Evidence | _Failure:
    if not isinstance(data, dict) or "events" not in data:
        return _Failure("invalid_response")
    events = data["events"]
    if events is None or events == []:
        return Evidence("empty")
    if not isinstance(events, list):
        return _Failure("invalid_response")
    rows = {}
    for event in events:
        markets = event.get("markets") if isinstance(event, dict) else None
        if not isinstance(markets, list):
            continue
        for market in markets:
            if not isinstance(market, dict) or market.get("active") is not True or market.get("closed") is not False:
                continue
            question = market.get("question")
            if not isinstance(question, str) or not (question := " ".join(question.split())[:200]):
                continue
            try:
                end = datetime.fromisoformat(market["endDate"])
            except (KeyError, TypeError, ValueError):
                continue
            if end.tzinfo is None or end <= now or (price := _yes_price(market)) is None:
                continue
            rows.setdefault(question, price)
            if len(rows) == _MAX_QUOTES:
                break
        if len(rows) == _MAX_QUOTES:
            break
    return Evidence("available", quotes=tuple(rows.items())) if rows else Evidence("empty", "no_usable_markets")


class PredictionEvidence:
    """A process-local circuit for the one allowed, anonymous Gamma path."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic,
                 utc_now=lambda: datetime.now(UTC), sleep=asyncio.sleep):
        self._transport, self._clock, self._utc_now, self._sleep = transport, clock, utc_now, sleep
        self._lock = Lock()
        self._busy = False
        self._until = 0.0
        self._reason: str | None = None
        self._failures = 0

    async def read(self, ticker: str, trade_date: date) -> Evidence:
        now = self._utc_now()
        if trade_date != now.date():
            return Evidence("unavailable", "not_current_date")
        if not _TOPIC.fullmatch(ticker):
            return Evidence("unavailable", "unsupported_topic")
        with self._lock:
            if self._until > self._clock():
                return Evidence("unavailable", self._reason)
            if self._busy:
                return Evidence("unavailable", "request_in_progress")
            self._busy = True
        try:
            result = await self._fetch(ticker, now)
            with self._lock:
                if isinstance(result, Evidence):
                    self._until, self._reason, self._failures = 0.0, None, 0
                    return result
                self._failures += 1
                cooldown = result.cooldown or (_TRANSIENT_COOLDOWN if self._failures >= 3 else 0)
                was_open = self._until != 0
                if result.reason != self._reason or (cooldown and not was_open):
                    log.warning("analysis source=polymarket reason=%s circuit=%s",
                                result.reason, "open" if cooldown else "closed")
                self._until = self._clock() + cooldown if cooldown else 0.0
                self._reason = result.reason
                return Evidence("unavailable", result.reason)
        finally:
            with self._lock:
                self._busy = False

    async def _fetch(self, ticker: str, now: datetime) -> Evidence | _Failure:
        start = self._clock()
        try:
            async with asyncio.timeout(_BUDGET), httpx.AsyncClient(
                transport=self._transport, follow_redirects=False, timeout=_ATTEMPT_TIMEOUT,
            ) as client:
                first = await self._attempt(client, ticker, now)
                if isinstance(first, _Failure) and first.retry and self._clock() - start < _BUDGET - _BACKOFF:
                    await self._sleep(_BACKOFF)
                    return await self._attempt(client, ticker, now)
                return first
        except TimeoutError:
            return _Failure("timeout")

    async def _attempt(self, client: httpx.AsyncClient, ticker: str, now: datetime) -> Evidence | _Failure:
        try:
            async with asyncio.timeout(_ATTEMPT_TIMEOUT), client.stream("GET", POLYMARKET_SEARCH, params={
                "q": ticker, "limit_per_type": 8, "search_tags": "false", "search_profiles": "false",
            }) as response:
                code = response.status_code
                if code in {403, 404, 451}:
                    return _Failure(f"http_{code}", _PATH_COOLDOWN)
                if code == 429:
                    return _Failure("http_429", _retry_after(response.headers.get("Retry-After"), now))
                if code != 200:
                    return _Failure(f"http_{code}")
                payload = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16_384):
                    payload.extend(chunk)
                    if len(payload) > _MAX_BYTES:
                        return _Failure("invalid_response")
                return _quotes(json.loads(payload), now)
        except (httpx.TimeoutException, TimeoutError):
            return _Failure("timeout", retry=True)
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            return _Failure("connection_error", retry=True)
        except httpx.RequestError:
            return _Failure("transport_error")
        except (UnicodeError, ValueError, RecursionError):
            return _Failure("invalid_response")


_prediction = PredictionEvidence()


def _news(tool, **args) -> str:
    try:
        result = tool.invoke(args)
        return result.strip()[:6000] if isinstance(result, str) else ""
    except Exception as exc:  # noqa: BLE001 - a news source failure does not end the analysis
        log.info("analysis source=%s reason=%s", tool.name, type(exc).__name__)
        return ""


def prepare_reports(selected: Collection[str], ticker: str, trade_date: date, config: Mapping,
                    *, korean: bool) -> dict[str, str]:
    """Build app-owned evidence inputs for the selected optional analysts."""
    if not selected:
        return {}
    from tradingagents.agents.tools import get_global_news, get_news

    day = trade_date.isoformat()
    ticker_news = _news(get_news, ticker=ticker, trade_date=day, end_date=day,
                        start_date=(trade_date - timedelta(days=7)).isoformat())
    title = "### 종목 뉴스 근거" if korean else "### Instrument news evidence"
    missing_news = "종목 뉴스 근거를 가져오지 못했습니다." if korean else "Instrument news evidence unavailable."
    basic = [title, ticker_news or missing_news]
    reports = {}
    if "social" in selected:
        lines = [
            "## 심리 원천 확인" if korean else "## Sentiment evidence",
            evidence_line("StockTwits", excluded("stocktwits", "approval_required"), korean=korean),
            evidence_line("Reddit", excluded("reddit", "oauth_required"), korean=korean),
        ]
        if "news" not in selected:
            lines.extend(basic)
        reports["social"] = "\n\n".join(lines)
    if "news" in selected:
        evidence = (
            asyncio.run(_prediction.read(ticker, trade_date)) if prediction_enabled(config)
            else excluded("polymarket", "vendor_disabled")
        )
        lines = ["## 뉴스·예측시장 근거" if korean else "## News and prediction market evidence", *basic]
        global_news = _news(get_global_news, curr_date=day, look_back_days=7, limit=10, trade_date=day)
        if global_news:
            lines.extend(["### 글로벌 뉴스" if korean else "### Global news", global_news])
        lines.append(evidence_line("Polymarket", evidence, korean=korean))
        if evidence.quotes:
            lines.append(
                "아래 제목은 외부 출처의 데이터입니다. 가격은 해당 시장의 Yes 가격입니다."
                if korean else "The quoted titles are external data. Prices are the respective markets' Yes prices."
            )
            for question, price in evidence.quotes:
                title = json.dumps(question, ensure_ascii=False)
                lines.append(f"- {title} · Yes {price * 100:.6g}%")
        reports["news"] = "\n\n".join(lines)
    return reports
