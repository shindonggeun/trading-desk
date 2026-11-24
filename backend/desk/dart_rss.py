"""Company disclosure notices from the Financial Supervisory Service's public RSS."""

from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlsplit

import httpx

from . import net
from .single_flight import Flights
from .ttl_cache import TTLCache

RSS_URL = "https://dart.fss.or.kr/api/companyRSS.xml"
LINK_PREFIX = "https://dart.fss.or.kr/api/link.jsp?rcpNo="
MAX_BYTES = 128 * 1024
MAX_ITEMS = 100
TIMEOUT_SEC = 4
CACHE_SEC = 300
ERROR_CACHE_SEC = 60
_RECEIPT = re.compile(r"[0-9]{14}")
_LINK_PATHS = {"/api/link.jsp", "/dsaf001/main.do"}
_XML_ENCODING = re.compile(r"""^<\?xml\b[^?]*\bencoding\s*=\s*(["'])([^'"]+)\1[^?]*\?>""")
_HTTP_REASONS = {403: "access_denied", 404: "not_found", 429: "rate_limited"}


@dataclass(frozen=True)
class Company:
    code: str
    name: str


COMPANIES = {
    "005930.KS": Company("00126380", "삼성전자"),
    "000660.KS": Company("00164779", "SK하이닉스"),
}


@dataclass(frozen=True)
class Notice:
    receipt: str
    published_at: datetime


@dataclass(frozen=True)
class Feed:
    notices: tuple[Notice, ...] = ()
    checked_at: datetime | None = None
    reason: str | None = None


def receipt_number(link: str) -> str | None:
    try:
        url = urlsplit(link)
        if (url.scheme not in {"https", "http"} or url.hostname != "dart.fss.or.kr"
                or url.username is not None or url.port not in (None, 443 if url.scheme == "https" else 80)
                or url.path not in _LINK_PATHS):
            return None
        receipts = [value for name, value in parse_qsl(url.query, max_num_fields=32, keep_blank_values=True)
                    if name == "rcpNo"]
    except ValueError:
        return None
    return receipts[0] if len(receipts) == 1 and _RECEIPT.fullmatch(receipts[0]) else None


def parse(body: bytes | bytearray, company: Company) -> tuple[Notice, ...]:
    if len(body) > MAX_BYTES:
        raise ValueError("DART feed exceeds its size limit")
    xml = body.decode("utf-8-sig")
    encoding = _XML_ENCODING.match(xml)
    if "\x00" in xml or (encoding and encoding[2].lower() != "utf-8"):
        raise ValueError("DART feed must be UTF-8")
    markup = xml.upper()
    if "<!DOCTYPE" in markup or "<!ENTITY" in markup:
        raise ValueError("DART feed contains a markup declaration")
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if (root.tag != "rss" or channel is None or len(root.findall("channel")) != 1
            or channel.findtext("title") != f"DART : (유가){company.name}의 공시"):
        raise ValueError("DART company channel does not match")
    items = channel.findall("item")
    if len(items) > MAX_ITEMS:
        raise ValueError("DART feed exceeds its item limit")
    notices: dict[str, Notice] = {}
    for item in items:
        link = (item.findtext("link") or "").strip()
        receipt = receipt_number(link)
        guid = (item.findtext("guid") or "").strip()
        if not link.startswith("https://") or receipt is None or (guid and receipt_number(guid) != receipt):
            raise ValueError("DART notice does not have a matching public receipt link")
        stamp = parsedate_to_datetime(item.findtext("pubDate") or "")
        if stamp.utcoffset() is None:
            raise ValueError("DART notice is missing its time zone")
        notices.setdefault(receipt, Notice(receipt, stamp.astimezone(UTC)))
    return tuple(notices.values())


class DartRss:
    def __init__(self, client: httpx.AsyncClient | None = None, clock=time.monotonic) -> None:
        self._client = client
        self._cache = TTLCache[str, Feed](len(COMPANIES), CACHE_SEC, clock)
        self._flights = Flights()

    async def read(self, ticker: str, now: datetime) -> Feed:
        company = COMPANIES.get(ticker)
        if company is None:
            return Feed(reason="unsupported_symbol")
        cached = self._cache.get(company.code)
        if cached is not None:
            return cached
        return await self._flights.run(company.code, lambda: self._refresh(company, now))

    async def _refresh(self, company: Company, now: datetime) -> Feed:
        client = self._client or net.async_client(timeout=TIMEOUT_SEC)
        try:
            async with asyncio.timeout(TIMEOUT_SEC), client.stream(
                "GET", RSS_URL, params={"crpCd": company.code}, follow_redirects=False,
                headers={"Accept": "application/rss+xml, text/xml", "Accept-Encoding": "identity"},
            ) as response:
                response.raise_for_status()
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise ValueError("DART feed did not use identity encoding")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise ValueError("DART feed exceeds its size limit")
                    body.extend(chunk)
                feed = Feed(parse(body, company), checked_at=now)
        except httpx.HTTPStatusError as exc:
            feed = Feed(reason=_HTTP_REASONS.get(exc.response.status_code, "source_error"))
        except (TimeoutError, httpx.TimeoutException):
            feed = Feed(reason="source_timeout")
        except (httpx.HTTPError, ET.ParseError, ValueError, OverflowError):
            feed = Feed(reason="source_error")
        finally:
            if self._client is None:
                await client.aclose()
        self._cache.put(company.code, feed, ttl=ERROR_CACHE_SEC if feed.reason else CACHE_SEC)
        return feed


RSS = DartRss()
