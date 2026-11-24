"""Stock-social reads the observed cache behind the existing session wall."""

from datetime import UTC, datetime

import httpx

from desk import api, dart_rss, filings, macro_news, stock_social
from tests.test_api import OWNER, PASSWORD, _client
from tests.test_dart_rss import RECEIPT, SAMSUNG, SAMSUNG_ID, document, item

URL = "/api/stock-social"
NOW = datetime(2026, 11, 24, 8, tzinfo=UTC)


def test_stock_social_reads_news_only_without_an_analysis_run(db_url, monkeypatch):
    class SocialClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", SocialClock)
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Synthetic Desk synthetic@example.com")

    async def unexpected_filings(*args, **kwargs):
        raise AssertionError("fake-run API must not fetch SEC filings")

    monkeypatch.setattr(filings.FILINGS, "public_recent", unexpected_filings, raising=False)
    monkeypatch.setattr(dart_rss.RSS, "read", unexpected_filings)
    heads = [
        {"title": "SOXX semiconductor ETF rises after chips beat estimates", "url": "https://first.example/one"},
        {"title": "SOXX warns of weakening chip demand", "url": "https://second.example/two"},
        {"title": "Nvidia shares launch plans for the next data-center chip", "url": "https://third.example/three"},
    ]
    for row in heads:
        row["seen"] = "2026-11-24T07:00:00+00:00"
        row["domain"] = row["url"].split("/")[2]
    cache = macro_news.Cache()
    cache.themes = {"chips": {"id": "chips", "source": "gdelt", "headlines": heads,
                              "fetched_at": "2026-11-24T07:01:00+00:00"}}
    monkeypatch.setattr(macro_news, "CACHE", cache)
    with _client(db_url, login=False, role="api") as client:
        assert client.get(URL).status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(URL, params={"ticker": "bad ticker!"}).status_code == 422
        assert client.get(URL, params={"days": 0}).status_code == 422
        assert client.get(URL, params={"days": 31}).status_code == 422

        response = client.get(URL, params={"ticker": "soxx"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ticker"] == "SOXX" and body["days"] == 7
        assert body["method"]["baseline"] == 50
        assert body["news"]["sample_count"] == 3
        assert body["news"]["score"] is not None
        assert body["community"]["score"] is None
        assert {item["relevance"] for item in body["items"]} == {"direct", "indirect"}
        assert {item["kind"] for item in body["items"]} == {"news"}
        sources = {item["id"]: item for item in body["sources"]}
        assert sources["sec_edgar"]["status"] == "unavailable"
        assert sources["dart_rss"]["reason"] == "not_configured"
        assert sources["reddit"]["prior_http_status"] == 404
        assert sources["stocktwits"]["prior_http_status"] == 403
        assert client.get(URL).json() == body


def test_dart_rss_metadata_uses_existing_authentication_and_validation(db_url, monkeypatch, tmp_path):
    calls = []

    def serve(request):
        calls.append(request.url)
        return httpx.Response(200, content=document("삼성전자", item()))

    # Keep all background jobs in their fake mode while enabling this read's synthetic filing source.
    read = stock_social.read

    async def read_with_synthetic_rss(ticker, days, **_):
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            monkeypatch.setattr(dart_rss, "RSS", dart_rss.DartRss(client))
            return await read(ticker, days, now=NOW, include_filings=True)

    monkeypatch.setattr(stock_social, "read", read_with_synthetic_rss)
    monkeypatch.setattr(macro_news, "CACHE", macro_news.Cache(cache_file=tmp_path / "macro_news.json"))
    with _client(db_url, login=False, role="api") as client:
        assert client.get(URL, params={"ticker": SAMSUNG}).status_code == 401
        assert not calls
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        assert client.get(URL, params={"ticker": SAMSUNG, "days": 31}).status_code == 422
        assert not calls
        response = client.get(URL, params={"ticker": SAMSUNG})
        assert response.status_code == 200
        body = response.json()
        [notice] = body["items"]
        assert notice["source"] == "dart_rss" and notice["relevance"] == "direct"
        assert notice["url"] == dart_rss.LINK_PREFIX + RECEIPT
        assert body["news"]["score"] is None
    assert [str(url) for url in calls] == [f"{dart_rss.RSS_URL}?crpCd={SAMSUNG_ID}"]
