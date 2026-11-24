"""The stock observation contract using only synthetic cache and EDGAR data."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk import dart_rss, filings, macro_news, stock_social
from tests.test_dart_rss import HYNIX_ID, RECEIPT, SAMSUNG, SAMSUNG_ID, document, item

NOW = datetime(2026, 11, 24, 14, tzinfo=UTC)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    value = macro_news.Cache(cache_file=tmp_path / "macro_news.json")
    monkeypatch.setattr(macro_news, "CACHE", value)
    return value


def head(title, publisher, slug, *, hours=2):
    return {"title": title, "url": f"https://{publisher}.example.com/{slug}",
            "seen": (NOW - timedelta(hours=hours)).isoformat()}


def theme(*articles, hours=1, source="gdelt"):
    return {"source": source, "fetched_at": (NOW - timedelta(hours=hours)).isoformat(),
            "headlines": list(articles)}


def read(ticker="SOXX", days=7, *, include_filings=False):
    return asyncio.run(stock_social.read(ticker, days, now=NOW, include_filings=include_filings))


def test_exact_symbols_and_sector_relationships():
    assert stock_social._relevance("SOXX", "SOXX gains as funds rebalance") == "direct"
    assert stock_social._relevance("SOXX", "SOXX.T misses estimates") is None
    assert stock_social._relevance("SOXX", "SOXL reports new share split") is None
    assert stock_social._relevance("SOXL", "NVIDIA raises guidance") == "indirect"
    assert stock_social._relevance("NVDA", "NASDAQ:NVDA. Estimates move") == "direct"
    assert stock_social._relevance("NVDA", "$NVDAX raises guidance") is None
    assert stock_social._relevance("AI", "Demand for AI helps semiconductor makers") is None
    assert stock_social._relevance("AI", "$AI estimates move") == "direct"
    assert stock_social._relevance("TSM", "$TSM.TW estimates move") is None
    assert stock_social._relevance("TSM", "TSMC raises guidance") == "indirect"
    assert stock_social._relevance("2330.TW", "TSMC raises guidance") == "direct"
    assert stock_social._relevance("2330.TW", "TSMC ADR cuts guidance") == "indirect"
    assert stock_social._relevance("2330.TW", "2330.TW and TSMC ADR cut guidance") == "direct"
    assert stock_social._relevance("005930.KS", "삼성전자가 실적 상회 발표") == "direct"
    assert stock_social._relevance("005930.KS", "삼성전자우가 실적 상회 발표") is None
    assert stock_social._direction("삼성전자가 실적 상회로 주목")[0] == "positive"


def test_direct_indirect_dedup_contradictions_and_publisher_limit(cache):
    positive = "SOXX portfolio review after industry supplier Broadcom raises guidance this quarter"
    negative = positive.replace("raises", "cuts")
    cache.themes["chips"] = theme(
        head(positive, "same", "article?utm_source=rss#some", hours=2),
        head(positive, "other", "syndication", hours=4),
        head("SOXX third duplicate URL", "same", "article", hours=5),
        head(negative, "same", "contrary", hours=3),
        head("SOXX beats estimates in an additional headline", "same", "publisher-limit", hours=5),
        head("Semiconductor research tracks overseas bookings", "third", "sector", hours=4),
    )
    result = read()
    assert result == read()
    assert result["news"]["status"] == "available"
    assert result["news"]["sample_count"] == 3
    assert result["news"]["publisher_count"] == 2
    assert 0 <= result["news"]["score"] <= 100
    by_title = {row["title"]: row for row in result["items"]}
    assert len(by_title) == 4
    assert by_title[positive]["url"] == "https://same.example.com/article"
    assert by_title[positive]["contribution"] > 0 > by_title[negative]["contribution"]
    assert by_title["SOXX beats estimates in an additional headline"]["weight"] is None
    sector = by_title["Semiconductor research tracks overseas bookings"]
    assert sector["relevance"] == "indirect" and sector["sentiment"] == "neutral"
    share = (by_title[positive]["weight"] + by_title[negative]["weight"]) / sum(
        row["weight"] or 0 for row in by_title.values()
    )
    assert share <= 0.601


def test_minimum_sample_and_neutral_are_distinct(cache):
    cache.themes["chips"] = theme(*(head(f"SOXX investor briefing number {i}", "first", str(i)) for i in range(5)))
    insufficient = read()["news"]
    assert insufficient["status"] == "insufficient" and insufficient["score"] is None
    assert insufficient["sample_count"] == 2 and insufficient["publisher_count"] == 1
    cache.themes["chips"]["headlines"].append(head("SOXX publishes its rebalance schedule", "second", "neutral"))
    neutral = read()["news"]
    assert neutral["status"] == "available" and neutral["score"] == 50.0
    assert neutral["sample_count"] == 3 and neutral["publisher_count"] == 2


def test_snapshot_refresh_is_read_only_and_partial_source_is_normalized(cache):
    cache.themes = {
        "chips": theme(head("SOXX old semiconductor data", "old", "cached", hours=10), hours=8),
        "ai_capex": theme(head("SOXX chip demand in a second theme", "old", "theme", hours=10), hours=8),
    }
    stale = read()
    assert stale["news"]["status"] == "stale" and stale["news"]["score"] is None
    assert all(item["stale"] for item in stale["items"])

    titles = [("SOXX beats estimates after review", "first"), ("SOXX cuts guidance", "second"),
              ("NVIDIA holds semiconductor analyst meeting", "third")]
    persisted = {
        "chips": theme(*(head(title, domain, "new") for title, domain in titles),
                       head("SOXX forecast from four days ago", "expired", "expired", hours=96),
                       head("SOXX future-dated item", "future", "future", hours=-1)),
        "inflation": theme(head("SOXX forbidden Google metadata", "rss", "rss"), source="google-news"),
    }
    payload = json.dumps({"themes": persisted}).encode()
    cache.cache_file.write_bytes(payload)
    cache.error = "raw_provider_error_test_secret"
    result = read(days=30)
    assert result["news"]["status"] == "partial" and result["news"]["score"] is not None
    assert result["news"]["sample_count"] == 3
    assert result["coverage"] == {"requested_days": 30, "news_max_days": 3}
    assert {item["title"] for item in result["items"] if not item["stale"]} == {title for title, _ in titles}
    assert "raw_provider_error_test_secret" not in json.dumps(result)
    assert cache.cache_file.read_bytes() == payload
    assert cache.themes["chips"]["fetched_at"] == theme(hours=8)["fetched_at"]


def test_new_timeline_does_not_refresh_carried_headlines(cache):
    cache.themes["chips"] = theme(
        head("SOXX beats estimates", "first", "e", hours=8),
        head("SOXX cuts guidance", "second", "g", hours=9),
        head("NVIDIA schedules earnings", "third", "s", hours=10),
    )
    result = read()
    assert result["news"]["status"] == "stale"
    assert result["news"]["score"] is None
    assert result["news"]["updated_at"] == (NOW - timedelta(hours=8)).isoformat()
    assert all(item["stale"] for item in result["items"])


def test_google_fallback_is_unavailable_alone_and_partial_with_gdelt(cache):
    cache.themes["ai_capex"] = theme(head("SOXX Google news", "rss", "r"), source="google-news")
    result = read()
    assert result["news"]["status"] == "unavailable" and not result["items"]
    cache.themes["chips"] = theme(
        head("SOXX beats estimates", "first", "e"),
        head("SOXX cuts guidance", "second", "g"),
        head("NVIDIA schedules earnings", "third", "s"),
    )
    result = read()
    assert result["news"]["status"] == "partial" and result["news"]["score"] is not None
    assert result["sources"][0]["reason"] == "partial_snapshot"
    assert {row["source"] for row in result["items"]} == {"gdelt"}


def test_malformed_times_links_and_surrogates_stay_out_of_response(cache):
    underflow = "0001-01-01T00:00:00+14:00"
    overflow = "9999-12-31T23:59:59-12:00"
    cache.themes["ai_capex"] = {**theme(), "fetched_at": underflow}
    missing_time = {**head("SOXX malformed time", "bad", "time"), "seen": overflow}
    unsafe = [
        "http://127.0.0.1/", "http://[::1]/", "https://portal.localhost/a", "https://desk.home.arpa/",
        "https://user:secret@news.example.com/", "https://@news.example.com/", "file:///cache/demo",
        "https://news.example.com/story?access_token=secret", "https://news.example.com/story?apiKey=secret",
        "https://news.example.com/story?%61uth=secret", "https://news.example.com/?other=\ud800",
        "https://news.example.com/a%0aB", "https://news.example.com:8080/a",
    ]
    cache.themes["chips"] = theme(
        missing_time,
        *({**head("SOXX headline with rejected link", "bad", str(i)), "url": url} for i, url in enumerate(unsafe)),
        head("SOXX \ud800 safe title", "safe", "story?article=42&utm_campaign=tracker#anchor"),
    )
    result = read()
    assert result["sources"][0]["status"] == "partial"
    assert [item["title"] for item in result["items"]] == ["SOXX safe title"]
    assert result["items"][0]["url"] == "https://safe.example.com/story?article=42"
    json.dumps(result, ensure_ascii=False).encode("utf-8")


def test_edgar_metadata_keeps_separate_dates_and_cannot_make_a_score(cache, monkeypatch):
    requests = []
    sink = {
        "forms": ["8-K", "10-Q", "6-K", "4", "8-K", "8-K", "8-K", "8-K\ud800"],
        "dates": ["2026-11-24", "2026-11-23", "2026-11-23", "2026-11-23", "2026-11-23",
                  "2026-11-23", "2026-11-24", "2026-11-24"],
        "acceptance": ["2026-11-24T08:15:00-05:00", "0001-01-01T00:00:00+14:00", None, None,
                       None, None, "2026-11-24T13:59:00-05:00", None],
        "accessions": [
            "0000000123-26-000001", "0000000123-26-000002", "0000000123-26-000003", "0000000123-26-000004",
            "0000000123-26-000003", "../\ud800", "0000000123-26-000007", "0000000123-26-000008",
        ],
    }
    def serve(request):
        requests.append(request.url)
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"ticker": "NVDA", "cik_str": 123}})
        content = json.dumps({"filings": {"recent": {
            "form": sink["forms"], "filingDate": sink["dates"], "accessionNumber": sink["accessions"],
            "acceptanceDateTime": sink["acceptance"], "items": ["2.02,\ud800"],
        }}})
        return httpx.Response(200, content=content)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            source = filings.Filings(client)
            monkeypatch.setattr(filings, "FILINGS", source)
            await source.recent("NVDA", NOW.date())
            disabled = await stock_social.read("NVDA", 7, now=NOW, include_filings=False)
            assert {row["kind"] for row in disabled["items"]} == {"news"}
            result = await stock_social.read("NVDA", 7, now=NOW)
        return result

    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "fixture")
    cache.themes["chips"] = theme(head("NVIDIA beats estimates", "first", "headline"))
    result = asyncio.run(scenario())
    assert len(requests) == 2
    assert result["news"]["status"] == "insufficient" and result["news"]["sample_count"] == 1
    metadata = [row for row in result["items"] if row["kind"] == "filing"]
    assert [row["title"] for row in metadata] == ["NVDA 8-K 공시", "NVDA 6-K 공시", "NVDA 10-Q 공시"]
    assert metadata[0]["at"] == "2026-11-24T13:15:00+00:00"
    assert metadata[0]["filed_date"] == "2026-11-24"
    assert metadata[0]["time_basis"] == "edgar_acceptance"
    assert metadata[0]["url"] == "https://www.sec.gov/Archives/edgar/data/123/000000012326000001/0000000123-26-000001-index.htm"
    assert metadata[1]["at"] is None and metadata[1]["time_basis"] == "edgar_filing_date"
    assert all(row["sentiment"] is None and row["weight"] is None for row in metadata)
    json.dumps(result, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(("status", "reason"), [(403, "access_denied"), (404, "not_found"), (429, "rate_limited")])
def test_provider_error_is_independent_and_normalized(cache, monkeypatch, status, reason):
    async def fail(*_):
        request = httpx.Request("GET", "https://data.sec.gov/example")
        raise httpx.HTTPStatusError("fixture provider failure with raw test marker", request=request,
                                   response=httpx.Response(status))

    monkeypatch.setattr(filings.FILINGS, "public_recent", fail, raising=False)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "fixture")
    cache.themes["chips"] = theme(
        head("NVIDIA beats estimates", "first", "earnings"),
        head("NVIDIA issues a profit warning", "second", "warning"),
        head("NVIDIA schedules next shareholder meeting", "third", "meeting"),
    )
    result = read("NVDA", include_filings=True)
    assert result["news"]["score"] is not None
    assert result["sources"][1]["reason"] == reason
    assert result["community"]["score"] is None
    assert "raw test marker" not in json.dumps(result)


@pytest.mark.parametrize(("titles", "score"), [
    (["SOXX misses estimates after event", "SOXX faces export ban", "SOXX follows profit warning"], 0),
    (["SOXX publishes its new calendar", "SOXX posts current index changes", "SOXX gives meeting notice"], 50),
])
def test_dart_metadata_is_deduplicated_filtered_and_cannot_change_the_news_score(cache, monkeypatch, titles, score):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    cache.themes["chips"] = theme(
        *(head(title, f"news{i}", str(i)) for i, title in enumerate(titles)),
        {**head("SOXX beats estimates in a DART notice", "dart", "filing"),
         "url": f"https://dart.fss.or.kr:443/dsaf001/main.do?rcpNo={RECEIPT}&dcmNo=1&utm_source=fixture"},
    )
    calls = []

    def serve(request):
        code = request.url.params["crpCd"]
        calls.append(code)
        if code == SAMSUNG_ID:
            body = document("삼성전자", item(), item(), item("20261124000999", "Tue, 24 Nov 2026 14:01:00 GMT"))
        else:
            body = document("SK하이닉스", item(), item("20261119000999", "Thu, 19 Nov 2026 08:00:00 GMT"))
        return httpx.Response(200, content=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            monkeypatch.setattr(dart_rss, "RSS", dart_rss.DartRss(client))
            disabled = await stock_social.read("SOXX", 2, now=NOW, include_filings=False)
            assert not calls
            etf = await stock_social.read("SOXX", 2, now=NOW)
            samsung = await stock_social.read(SAMSUNG, 2, now=NOW + timedelta(seconds=1))
            other_etf = await stock_social.read("SOXL", 2, now=NOW + timedelta(seconds=2))
        return disabled, etf, samsung, other_etf

    disabled, etf, samsung, other_etf = asyncio.run(scenario())
    assert sorted(calls) == sorted([SAMSUNG_ID, HYNIX_ID])
    assert etf["news"] == disabled["news"]
    assert etf["news"]["score"] == score and etf["news"]["sample_count"] == 3
    assert etf["community"]["score"] is None
    assert len([row for row in etf["items"] if row["kind"] == "news"]) == 3
    for result, relevance in ((etf, "indirect"), (samsung, "direct"), (other_etf, "indirect")):
        [notice] = [row for row in result["items"] if row["kind"] == "filing"]
        assert notice == {
            "kind": "filing", "source": "dart_rss", "publisher": "금융감독원 DART",
            "title": f"삼성전자 공시 ({RECEIPT})", "summary": "공식 공시 RSS 메타데이터입니다.",
            "url": dart_rss.LINK_PREFIX + RECEIPT, "at": "2026-11-23T06:04:00+00:00", "filed_date": None,
            "time_basis": "dart_rss_pubdate", "relevance": relevance,
            "tags": ["filing"], "sentiment": None, "reason": "filing_metadata_unscored",
            "weight": None, "contribution": None, "stale": False,
        }
        source = next(row for row in result["sources"] if row["id"] == "dart_rss")
        assert source["updated_at"] == NOW.isoformat() and source["status"] == "available"
        assert next(row for row in result["sources"] if row["id"] == "open_dart")["reason"] == "not_configured"


def test_dart_valid_empty_feed_is_available_and_independent_of_other_issuer_failure(cache, monkeypatch):
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)

    def serve(request):
        if request.url.params["crpCd"] == SAMSUNG_ID:
            return httpx.Response(200, content=document("삼성전자"))
        return httpx.Response(429)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            monkeypatch.setattr(dart_rss, "RSS", dart_rss.DartRss(client))
            return [await stock_social.read(ticker, 7, now=NOW) for ticker in (SAMSUNG, "SOXL", "000660.KS", "NVDA")]

    results = asyncio.run(scenario())
    expected = [("available", "no_recent_items"), ("partial", "partial_source"),
                ("unavailable", "rate_limited"), ("unavailable", "unsupported_symbol")]
    for result, (status, reason) in zip(results, expected, strict=True):
        source = next(row for row in result["sources"] if row["id"] == "dart_rss")
        assert (source["status"], source["reason"]) == (status, reason)
        assert result["items"] == []
        assert result["news"]["score"] is None and result["news"]["sample_count"] == 0
