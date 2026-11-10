"""#244: sector / key-person news surges - stories, tags, the digest's read and the agents' note."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx

from desk import macro_news, news_digest
from desk import sector_news as sn

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
NVDA = next(t for s in sn.SECTORS for t in s.topics if t.id == "nvidia")
SEMIS = next(s for s in sn.SECTORS if s.id == "semis")


def _items(recent: list[str], old: int = 10) -> list[dict]:
    out = [{"title": t, "url": f"https://n.example/{i}", "domain": "n", "seen": NOW - timedelta(hours=1 + i)}
           for i, t in enumerate(recent)]
    out += [{"title": f"Older story {i}", "url": f"https://o.example/{i}", "domain": "o",
             "seen": NOW - timedelta(days=2 + i % 5)} for i in range(old)]
    return out


AFFAIR = ["Nvidia CEO Jensen Huang affair report shakes investors", "Jensen Huang affair report: Nvidia shares slip",
          "Report on Jensen Huang affair weighs on Nvidia", "Nvidia shares slip after Jensen Huang affair report",
          "Analysts weigh Jensen Huang affair report", "Nvidia unveils new networking chip"]


def test_a_surge_is_an_issue_with_its_biggest_story_and_tag():
    t = sn.summarise(NVDA, SEMIS, _items(AFFAIR, old=10), NOW)
    assert t["articles_24h"] == 6 and t["spike_raw"] == round(6 / 16 * 7, 2) and t["issue"] is False   # rebase decides
    assert t["tag"] == "person" and t["tag_ko"] == "인물" and t["story_size"] == 5
    assert len(t["headlines"]) == sn.HEADLINES and all("affair" in h["title"] for h in t["headlines"])
    few = sn.summarise(NVDA, SEMIS, _items(AFFAIR[:3], old=0), NOW)
    assert few["spike_raw"] is None


def test_tags_read_korean_and_english_words():
    assert sn.tag_of(["SK하이닉스, HBM 공급 인증 통과"]) == "product"
    assert sn.tag_of(["Microsoft sued over AI training data"]) == "legal"
    assert sn.tag_of(["US widens chip export controls"]) == "regulation"
    assert sn.tag_of(["Weather is nice"]) is None
    # whole English words: no "sues" in "issues", "ban" in "Bank", "trial" in "industrial"
    assert sn.tag_of(["Nvidia shares slip on export issues"]) is None
    assert sn.tag_of(["Bank of America upgrades Nvidia"]) is None
    assert sn.tag_of(["Nvidia industrial AI push"]) is None


def _rss(titles_hours: list[tuple[str, float]]) -> str:
    items = "".join(f"<item><title>{t} - Src</title><link>https://n.example/{i}</link><source>Src</source>"
                    f"<pubDate>{format_datetime(NOW - timedelta(hours=h))}</pubDate></item>"
                    for i, (t, h) in enumerate(titles_hours))
    return f"<?xml version='1.0'?><rss><channel>{items}</channel></rss>"


def test_refresh_reads_every_topic_keeps_failures_and_the_view_carries_the_digest_read():
    feed = _rss([(t, 1 + i) for i, t in enumerate(AFFAIR)] + [(f"Old {i}", 60 + i) for i in range(10)])
    asked = []

    def handler(request):
        q = request.url.params["q"]
        asked.append((q, request.url.params["hl"]))
        if "TSMC" in q:
            return httpx.Response(500)
        return httpx.Response(200, text=feed if "Jensen" in q else _rss([("Quiet", 50)] * 12))

    cache = sn.Cache()
    tsmc = next(t for t in SEMIS.topics if t.id == "tsmc")
    kept = sn.summarise(tsmc, SEMIS, _items(["TSMC quiet day"], old=20), NOW)
    cache.topics["tsmc"] = kept
    ok = asyncio.run(cache.refresh(httpx.AsyncClient(transport=httpx.MockTransport(handler)), now=NOW, gap=0))
    total = sum(len(s.topics) for s in sn.SECTORS)
    assert ok == total - 1 and cache.topics["tsmc"]["headlines"] == kept["headlines"] and "tsmc" in cache.error
    assert ("삼성전자 반도체 when:7d", "ko") in asked
    assert list(cache.as_themes()) == ["nvidia"]
    d = news_digest.Digest(lambda p: news_digest.fake_digest(p))
    asyncio.run(d.refresh(cache.as_themes()))
    view = cache.view("semis", now=NOW, digest=d)
    (semis,) = view["sectors"]
    (issue,) = semis["issues"]
    assert issue["id"] == "nvidia" and issue["headlines"][0]["title_ko"].startswith("[번역 예시]")
    assert issue["digest"]["read"] >= 1 and len(semis["topics"]) == len(SEMIS.topics)
    later = cache.view(now=NOW + timedelta(hours=7), digest=d)
    assert all(not s["issues"] for s in later["sectors"])                               # stale issues drop


def test_the_note_names_the_sector_issue_for_a_chip_ticker_only():
    cache = sn.Cache()
    cache.topics["nvidia"] = {**sn.summarise(NVDA, SEMIS, _items(['He said "sell" now'] * 6, old=10), NOW),
                              "issue": True, "spike": 2.0}
    lines = cache.note_lines("NVDA", NOW, digest=news_digest.Digest())
    assert len(lines) == 1 and "Sector issue (nvidia" in lines[0] and "'sell'" in lines[0]
    assert cache.note_lines("XOM", NOW, digest=news_digest.Digest()) == []


def test_the_macro_note_carries_sector_lines_even_without_themes(monkeypatch):
    cache = sn.Cache()
    cache.topics["nvidia"] = {**sn.summarise(NVDA, SEMIS, _items(AFFAIR, old=10), NOW), "issue": True, "spike": 2.0}
    monkeypatch.setattr(sn, "CACHE", cache)
    note = macro_news.prompt_note("NVDA", macro_news.Cache(), now=NOW)
    assert note and "Sector issue (nvidia" in note and "untrusted third-party text" in note


def test_the_api_validates_the_sector(db_url):
    from tests.test_api import _client

    with _client(db_url) as client:
        assert client.get("/api/sector-news", params={"sector": "nope"}).status_code == 422
        body = client.get("/api/sector-news").json()
        assert [s["id"] for s in body["sectors"]] == [s.id for s in sn.SECTORS] and "note" in body


def test_spikes_are_rebased_on_the_other_topics_so_a_busy_feed_is_not_an_issue_by_itself():
    raw = {"a": 4.6, "b": 2.0, "c": 2.9, "d": 3.4, "e": 2.8}
    topics = {k: {"id": k, "spike_raw": v, "spike": v, "articles_24h": 30, "issue": True} for k, v in raw.items()}
    sn.rebase(topics)
    assert [k for k, t in topics.items() if t["issue"]] == ["a"] and topics["a"]["spike_kind"] == "topics"
    assert topics["a"]["spike"] == round(4.6 / 2.9, 2)
    few = {k: {"id": k, "spike_raw": v, "spike": v, "articles_24h": 30, "issue": True} for k, v in [("a", 7.0), ("b", 7.0)]}
    sn.rebase(few)                                       # a mostly refused pass: nothing to compare against
    assert not any(t["issue"] for t in few.values()) and few["a"]["spike"] is None
