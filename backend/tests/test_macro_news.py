import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from desk import macro_news as m
from tests.test_api import OWNER, PASSWORD, _client

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _timeline(series: str, points: list[tuple[datetime, float]]) -> dict:
    return {"timeline": [{"series": series, "data": [{"date": _stamp(at), "value": v} for at, v in points]}]}


def test_spike_compares_the_last_day_with_the_average_day_before_it():
    points = [(NOW - timedelta(hours=h), 10) for h in range(1, 24)]            # 230 in the last day
    points += [(NOW - timedelta(days=d, hours=1), 50) for d in range(1, 7)]    # 50 a day before
    count, ratio = m.spike(_timeline("Article Count", points)["timeline"][0]["data"], NOW)
    assert count == 230 and ratio == 4.6


def test_tone_and_headlines_read_gdelt_shapes():
    assert m.tone([{"value": -4.0}, {"value": -2.0}]) == -3.0
    rows = m.headlines({"articles": [{"title": " AI spending fears ", "url": "u1", "domain": "x.com",
                                      "seendate": _stamp(NOW), "sourcecountry": "US"}, {"title": "", "url": "u2"}]})
    assert rows == [{"title": "AI spending fears", "url": "u1", "domain": "x.com", "country": "US",
                     "seen": NOW.isoformat()}]


def test_groups_and_relevance_put_the_instruments_own_themes_first():
    assert {"semis", "us"} <= m.groups_of("SOXL") and m.groups_of("005930.KS") >= {"semis", "kr"}
    ai = next(t for t in m.THEMES if t.id == "ai_capex")
    korea = next(t for t in m.THEMES if t.id == "korea")
    assert m.relevance(ai, m.groups_of("NVDA")) == 2 and m.relevance(korea, m.groups_of("NVDA")) == 0
    assert m.relevance(next(t for t in m.THEMES if t.id == "fed"), m.groups_of("NVDA")) == 1


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Data center spending fears hit chip stocks - Reuters</title><link>https://n/1</link>
<source url="https://reuters.com">Reuters</source><pubDate>{d0}</pubDate></item>
<item><title>Hyperscalers rally on record deal - CNBC</title><link>https://n/2</link>
<source url="https://cnbc.com">CNBC</source><pubDate>{d1}</pubDate></item>
<item><title>Old story - AP</title><link>https://n/3</link><source>AP</source><pubDate>{d5}</pubDate></item>
</channel></rss>"""


def _rss(now=NOW) -> str:
    fmt = "%a, %d %b %Y %H:%M:%S GMT"
    return RSS.format(d0=(now - timedelta(hours=2)).strftime(fmt), d1=(now - timedelta(hours=30)).strftime(fmt),
                      d5=(now - timedelta(days=5)).strftime(fmt))


def test_rss_items_drop_the_source_suffix_and_refuse_entities():
    items = m.parse_rss(_rss())
    assert [i["title"] for i in items] == ["Data center spending fears hit chip stocks", "Hyperscalers rally on record deal",
                                          "Old story"]
    assert items[0]["domain"] == "Reuters" and items[0]["seen"] == NOW - timedelta(hours=2)
    with pytest.raises(ValueError):
        m.parse_rss('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss/>')


def test_headline_tone_counts_words():
    assert m.headline_tone(["Stocks plunge on war fears", "Chips rally"]) == 0.0      # -1 and +1
    assert m.headline_tone(["Nothing to see"]) is None


def test_fetch_theme_rss_counts_the_last_day_and_keeps_newest_headlines():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=_rss(), headers={"content-type": "text/xml"}))

    async def go():
        async with httpx.AsyncClient(transport=transport) as client:
            return await m.fetch_theme_rss(m.THEMES[3], NOW, client)
    row = asyncio.run(go())
    assert row["source"] == "google-news" and row["articles_24h"] == 1 and row["spike"] is None   # < 10 dated items
    assert row["headlines"][0]["title"].startswith("Data center") and row["tone"] == -10.0


class LimitedGdelt:
    def __init__(self):
        self.calls = 0

    async def get(self, *_, **__):
        self.calls += 1
        raise httpx.HTTPStatusError("429", request=httpx.Request("GET", "https://g"),
                                    response=httpx.Response(429, request=httpx.Request("GET", "https://g")))


def test_a_rate_limited_gdelt_falls_back_to_rss_for_the_whole_pass_and_rebases_surges():
    gdelt = LimitedGdelt()
    shares = iter([3.0, 6.0, 3.0, 3.0, 1.5, 3.0, 3.0, 3.0])

    async def rss(theme, now):
        return {"id": theme.id, "label": theme.label, "articles_24h": 10, "spike": next(shares),
                "spike_kind": "raw-share", "tone": -1.0, "tone_kind": "headline-words", "headlines": [],
                "why": theme.why, "source": "google-news", "fetched_at": now.isoformat()}
    cache = m.Cache()
    assert asyncio.run(cache.refresh(gdelt=gdelt, now=NOW, rss=rss)) == len(m.THEMES)
    assert gdelt.calls == 1                                   # tried once, then the fallback for every theme
    fed = cache.themes["fed"]
    assert fed["spike"] == 2.0 and fed["spike_kind"] == "themes"   # 6.0 against the median 3.0


def test_a_failing_theme_keeps_its_previous_value():
    cache = m.Cache()
    cache.themes["fed"] = {"id": "fed", "keep": True}

    async def rss(theme, now):
        if theme.id == "fed":
            raise ConnectionError("down")
        return {"id": theme.id, "label": theme.label, "articles_24h": 0, "spike": None, "tone": None,
                "headlines": [], "why": theme.why, "source": "google-news"}
    asyncio.run(cache.refresh(gdelt=LimitedGdelt(), now=NOW, rss=rss))
    assert cache.themes["fed"] == {"id": "fed", "keep": True} and "fed: ConnectionError" in cache.error


def _filled() -> m.Cache:
    cache = m.Cache(refreshed_at=NOW.isoformat())
    for i, theme in enumerate(m.THEMES):
        cache.themes[theme.id] = {"id": theme.id, "label": theme.label, "articles_24h": 40 + i,
                                  "spike": 2.0 if theme.id == "ai_capex" else 1.0, "spike_kind": "themes",
                                  "tone": -3.5, "tone_kind": "gdelt", "why": theme.why, "source": "gdelt",
                                  "headlines": [{"title": f"{theme.id} headline", "url": "u", "domain": "d.com"}]}
    return cache


def test_the_prompt_note_lists_the_instruments_themes_and_flags_surges():
    note = m.prompt_note("NVDA", _filled())
    assert note.startswith("Macro & policy backdrop")
    first = note.splitlines()[1]
    assert first.startswith("- ai_capex:") and "SURGING" in first and "Directly relevant" in first
    assert "korea" not in note                                 # background-only for a US chip stock
    assert m.prompt_note("NVDA", m.Cache()) is None            # before the first refresh


def test_brief_api_needs_a_session_and_orders_by_relevance(db_url, monkeypatch):
    monkeypatch.setattr(m, "CACHE", _filled())
    with _client(db_url, login=False) as client:
        assert client.get("/api/macro/brief").status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        body = client.get("/api/macro/brief", params={"ticker": "005930.KS"}).json()
        assert body["sources"] == ["gdelt"] and body["themes"][0]["relevance"] == 2
        assert {t["id"] for t in body["themes"][:3]} <= {"korea", "chips", "tariffs", "china", "ai_capex"}
        assert client.get("/api/macro/brief", params={"ticker": "bad ticker"}).status_code == 422


def test_a_broken_macro_note_never_fails_a_run():
    from desk.runner import RunManager

    def boom(_ticker):
        raise RuntimeError("cache corrupted")
    manager = RunManager.__new__(RunManager)
    manager.macro = boom
    assert manager._macro_note("NVDA") is None
    manager.macro = lambda t: f"note for {t}"
    assert manager._macro_note("SOXX") == "note for SOXX"
