"""DART's public RSS shape and per-company transport/cache contract."""

import asyncio
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk import dart_rss

SAMSUNG = "005930.KS"
HYNIX = "000660.KS"
SAMSUNG_ID = "00126380"
HYNIX_ID = "00164779"
NOW = datetime(2026, 11, 24, 8, tzinfo=UTC)
RECENT = "Mon, 23 Nov 2026 06:04:00 GMT"
RECEIPT = "20261123000100"


def item(receipt=RECEIPT, stamp=RECENT):
    link = f"https://dart.fss.or.kr/api/link.jsp?rcpNo={receipt}"
    return (
        f"<item><title>private headline marker</title><description>private body marker</description>"
        f"<link>{link}</link><guid>{link}</guid><pubDate>{stamp}</pubDate></item>"
    )


def document(name, *items):
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        f"<rss version='2.0'><channel><title>DART : (유가){name}의 공시</title>"
        f"{''.join(items)}</channel></rss>"
    ).encode()


def test_parse_scoped_metadata_and_zero_items():
    company = dart_rss.COMPANIES[SAMSUNG]
    body = document("삼성전자", item(), item(), item("20261124000100", "Tue, 24 Nov 2026 16:30:00 +0900"))
    parsed = dart_rss.parse(body, company)
    assert [(notice.receipt, notice.published_at) for notice in parsed] == [
        (RECEIPT, datetime(2026, 11, 23, 6, 4, tzinfo=UTC)),
        ("20261124000100", datetime(2026, 11, 24, 7, 30, tzinfo=UTC)),
    ]
    assert dart_rss.parse(document("삼성전자"), company) == ()


@pytest.mark.parametrize("body", [
    b"<rss>",
    b"<rss>\xff</rss>",
    b"<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E\x00>\x00",
    document("삼성전자").replace(b"UTF-8", b"ISO-8859-1"),
    b"<!DOCTYPE rss SYSTEM 'https://example.invalid/remote.dtd'><rss/>",
    document("SK하이닉스", item()),
    document("삼성전자", item().replace("dart.fss.or.kr", "dart.fss.or.kr.example.invalid")),
    document("삼성전자", item().replace("https://", "http://")),
    document("삼성전자", item().replace(f"<link>{dart_rss.LINK_PREFIX}{RECEIPT}", "<link>")),
    document("삼성전자", item().replace(f"<guid>{dart_rss.LINK_PREFIX}{RECEIPT}",
                                      f"<guid>{dart_rss.LINK_PREFIX}20261123000200")),
    document("삼성전자", item(stamp="Mon, 23 Nov 2026 06:04:00")),
    document("삼성전자", item(stamp="soon")),
    document("삼성전자", *[item()] * (dart_rss.MAX_ITEMS + 1)),
    document("삼성전자") + b" " * dart_rss.MAX_BYTES,
])
def test_parse_rejects_untrusted_or_incomplete_channels(body):
    with pytest.raises((ValueError, ET.ParseError)):
        dart_rss.parse(body, dart_rss.COMPANIES[SAMSUNG])


def test_cache_keeps_original_check_time_and_coalesces_the_same_issuer():
    async def scenario():
        calls = []
        tick = 0
        began, release = asyncio.Event(), asyncio.Event()

        async def serve(request):
            calls.append(request.url)
            began.set()
            await release.wait()
            name = {SAMSUNG_ID: "삼성전자", HYNIX_ID: "SK하이닉스"}[request.url.params["crpCd"]]
            return httpx.Response(200, content=document(name, item()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            rss = dart_rss.DartRss(client, clock=lambda: tick)
            first = asyncio.create_task(rss.read(SAMSUNG, NOW))
            await began.wait()
            second = asyncio.create_task(rss.read(SAMSUNG, NOW + timedelta(seconds=1)))
            release.set()
            feed, joined = await asyncio.gather(first, second)
            assert len(calls) == 1 and feed is joined
            assert feed.checked_at == NOW
            assert await rss.read(SAMSUNG, NOW + timedelta(minutes=1)) is feed
            assert (await rss.read(HYNIX, NOW)).checked_at == NOW
            assert (await rss.read("SAMSUNG", NOW)).reason == "unsupported_symbol"
            tick = dart_rss.CACHE_SEC
            assert (await rss.read(SAMSUNG, NOW + timedelta(seconds=tick))).checked_at == NOW + timedelta(seconds=tick)

        assert [str(url) for url in calls] == [
            f"{dart_rss.RSS_URL}?crpCd={code}" for code in (SAMSUNG_ID, HYNIX_ID, SAMSUNG_ID)
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(("status", "reason"), [
    (302, "source_error"), (403, "access_denied"), (404, "not_found"), (429, "rate_limited"),
    (503, "source_error"), (None, "source_timeout"),
])
def test_source_failure_is_bounded_cached_and_retried_without_following_redirect(status, reason):
    async def scenario():
        calls, tick = [], 0

        def serve(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                if status is None:
                    raise httpx.ReadTimeout("synthetic", request=request)
                return httpx.Response(status, headers={"Location": "https://endpoint.example.invalid/feed"})
            return httpx.Response(200, content=document("삼성전자"))

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve), follow_redirects=True) as client:
            rss = dart_rss.DartRss(client, clock=lambda: tick)
            failed = await rss.read(SAMSUNG, NOW)
            assert failed.reason == reason and failed.checked_at is None
            assert await rss.read(SAMSUNG, NOW + timedelta(seconds=1)) is failed
            tick = dart_rss.ERROR_CACHE_SEC
            recovered = await rss.read(SAMSUNG, NOW + timedelta(seconds=tick))
            assert recovered.reason is None and recovered.notices == ()
            assert recovered.checked_at == NOW + timedelta(seconds=tick)
        assert calls == [f"{dart_rss.RSS_URL}?crpCd={SAMSUNG_ID}"] * 2

    asyncio.run(scenario())
