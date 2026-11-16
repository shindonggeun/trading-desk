"""#403: the chat's news search - only a short, non-private query leaves the desk, the answer is
a few dated, linked headlines (never an article body), and the page gets the links."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk import chat, web_search
from desk.api import create_app
from desk.runner import make_fake_runner
from desk.settings import Settings

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)


def _rss(items: list[tuple[str, str, datetime]]) -> str:
    rows = "".join(
        f"<item><title>{t} - {s}</title><link>https://news.example/{i}</link><source>{s}</source>"
        f"<pubDate>{d.strftime('%a, %d %b %Y %H:%M:%S GMT')}</pubDate><description>본문 {i}</description></item>"
        for i, (t, s, d) in enumerate(items))
    return f"<?xml version='1.0'?><rss><channel>{rows}</channel></rss>"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    web_search._cache.clear()
    monkeypatch.setattr(web_search, "REQUEST_GAP_SEC", 0)


def _client(text: str, seen: list[httpx.Request] | None = None, status: int = 200) -> httpx.AsyncClient:
    def answer(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(status, text=text, headers={"content-type": "text/xml"})

    return httpx.AsyncClient(transport=httpx.MockTransport(answer))


def test_a_search_sends_the_query_alone_and_keeps_newest_dated_headlines():
    items = [(f"Fed story {k}", "Reuters", NOW - timedelta(hours=k)) for k in range(12)]
    items += [("Fed story 0", "Copycat", NOW - timedelta(hours=1)),          # the same title again
              ("Old story", "AP", NOW - timedelta(days=9))]                   # outside the week
    seen: list[httpx.Request] = []
    got = asyncio.run(web_search.search("  Fed  rate\ndecision ", client=_client(_rss(items), seen), now=NOW))
    assert seen[0].url.host == "news.google.com" and seen[0].url.params["q"] == "Fed rate decision when:7d"
    assert seen[0].url.params["hl"] == "en-US"
    assert got["query"] == "Fed rate decision" and len(got["articles"]) == web_search.MAX_ARTICLES
    assert got["articles"][0] == {"title": "Fed story 0", "source": "Reuters",
                                  "published": NOW.isoformat(), "url": "https://news.example/0"}
    assert "Old story" not in json.dumps(got) and "본문" not in json.dumps(got, ensure_ascii=False)


def test_a_korean_query_reads_the_korean_edition_and_the_window_is_bounded():
    seen: list[httpx.Request] = []
    got = asyncio.run(web_search.search("코스피 외국인 매도", "1d", client=_client(_rss([]), seen), now=NOW))
    assert seen[0].url.params["ceid"] == "KR:ko" and seen[0].url.params["q"].endswith("when:1d")
    assert got["articles"] == [] and "기사 없음" in got["note"]
    web_search._cache.clear()
    asyncio.run(web_search.search("x", "365d", client=_client(_rss([]), seen), now=NOW))
    assert seen[1].url.params["q"] == "x when:7d"


@pytest.mark.parametrize("query", ["", "   ", "계좌 110-123-456789 잔고", "010 1234 5678", "me@mail.example 뉴스"])
def test_an_empty_or_private_looking_query_never_leaves_the_desk(query):
    seen: list[httpx.Request] = []
    with pytest.raises(ValueError):
        asyncio.run(web_search.search(query, client=_client(_rss([]), seen), now=NOW))
    assert seen == []


def test_tickers_and_dates_are_fine_and_a_long_query_is_cut():
    assert web_search.clean_query("005930 삼성전자 2026-11-16 실적") == "005930 삼성전자 2026-11-16 실적"
    assert len(web_search.clean_query("w " * 200)) == web_search.MAX_QUERY


def test_a_repeated_search_is_served_from_the_cache():
    seen: list[httpx.Request] = []
    client = _client(_rss([("A", "S", NOW)]), seen)
    first = asyncio.run(web_search.search("Fed", client=client, now=NOW))
    assert asyncio.run(web_search.search("fed", client=client, now=NOW)) is first and len(seen) == 1


def test_a_failing_feed_is_a_readable_error_for_the_model():
    async def run(args):
        return await web_search.search(args["query"], client=_client("busy", status=503), now=NOW)

    tools = {"news_search": chat.Tool("news_search", "", web_search.ARGS, run)}
    got = asyncio.run(chat.call_tool(tools, "news_search", {"query": "Fed"}))
    assert got == {"error": "조회 실패(HTTPStatusError)"}
    assert asyncio.run(chat.call_tool(tools, "news_search", {}))["error"].startswith("필요한 값(query)")


def test_the_page_gets_only_http_links_and_the_stored_message_keeps_them():
    result = {"articles": [{"title": "ok", "source": "S", "published": "t", "url": "https://a.example/1"},
                           {"title": "js", "url": "javascript:alert(1)"},
                           {"title": "long", "url": "https://a.example/" + "x" * 700}]}
    assert web_search.links(result) == [{"title": "ok", "source": "S", "published": "t", "url": "https://a.example/1"}]
    assert web_search.links({"themes": []}) == [] and web_search.links([1]) == []

    async def run(_args):
        return result

    events: list[dict] = []
    used: list[dict] = []
    tools = {"news_search": chat.Tool("news_search", "", web_search.ARGS, run)}
    asyncio.run(chat.LangChainChatter._run(tools, "news_search", {"query": "Fed"}, events.append, used))
    assert events[1]["links"] == used[0]["links"] == web_search.links(result)


def test_the_scripted_session_searches_without_the_network():
    app = create_app(Settings(database_url="sqlite://", fake_runs=True, fake_delay=0), runner=make_fake_runner(0))
    tools = app.state.chat_tools
    events: list[dict] = []
    text, used = asyncio.run(chat.FakeChatter()(chat.build_messages([], "거시 어때?", None, False), tools, events.append))
    assert [u["name"] for u in used] == ["macro_brief", "news_search"]
    assert used[1]["links"][0]["url"].startswith("https://") and "뉴스: (대본 기사)" in text
    with pytest.raises(ValueError):
        web_search.scripted("110-123-456789")


def test_an_oversized_feed_is_cut_off_while_reading():
    huge = "<rss>" + "x" * (web_search.macro_news.RSS_MAX_BYTES + 10) + "</rss>"
    got = asyncio.run(chat.call_tool({"news_search": chat.Tool("news_search", "", web_search.ARGS, lambda a: web_search.search(
        a["query"], client=_client(huge), now=NOW))}, "news_search", {"query": "Fed"}))
    assert got["error"].startswith("잘못된 값") and "너무 큼" in got["error"]
