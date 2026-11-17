"""#485: the live macro_news pass's GDELT tone and article counts, kept by day in news_history's format."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from desk import macro_history as mh
from desk import macro_news as m
from desk import news_history

NOW = datetime(2026, 11, 17, 2, 30, tzinfo=UTC)          # Tue 11:30 KST: the Korean session is open


def _hours(start: datetime, n: int, value):
    return [{"date": (start + timedelta(hours=i)).strftime("%Y%m%dT%H%M%SZ"), "value": value(i)} for i in range(n)]


def test_hours_fold_into_days_and_the_cut_oldest_day_is_left_out():
    vol = _hours(datetime(2026, 11, 14, 20, tzinfo=UTC), 30, lambda i: 10)    # 14th: 4 h (cut), 15th: 24 h, 16th: 2 h
    tone = _hours(datetime(2026, 11, 14, 20, tzinfo=UTC), 30, lambda i: -2.0 if i < 16 else -4.0)
    v, t = mh.daily(vol, tone)
    assert v == {"2026-11-15": 240.0, "2026-11-16": 20.0}
    # 15th: 12 h at -2 and 12 h at -4, weighted by the equal counts -> -3
    assert t == {"2026-11-15": -3.0, "2026-11-16": -4.0}
    assert mh.daily([], []) == ({}, {})


def test_tone_is_weighted_by_the_hours_article_counts():
    start = datetime(2026, 11, 15, tzinfo=UTC)
    vol = [{"date": "20261114T230000Z", "value": 1}] + _hours(start, 2, lambda i: [30, 10][i])
    tone = [{"date": "20261114T230000Z", "value": 0}] + _hours(start, 2, lambda i: [-1.0, -5.0][i])
    assert mh.daily(vol, tone)[1] == {"2026-11-15": -2.0}          # (30x-1 + 10x-5) / 40


def test_record_upserts_by_day_in_the_files_news_history_reads(tmp_path):
    vol = _hours(datetime(2026, 12, 30, 22, tzinfo=UTC), 28, lambda i: 5)   # 30th (cut) .. 31st .. Jan 1st
    tone = _hours(datetime(2026, 12, 30, 22, tzinfo=UTC), 28, lambda i: 1.5)
    assert mh.record(tmp_path, "fed", vol, tone) == 4
    assert json.loads((tmp_path / "fed.vol.2026.json").read_text()) == {"2026-12-31": 120.0}
    assert json.loads((tmp_path / "fed.vol.2027.json").read_text()) == {"2027-01-01": 10.0}
    # a later pass overwrites the same day and keeps the others
    mh.record(tmp_path, "fed", _hours(datetime(2026, 12, 31, 23, tzinfo=UTC), 25, lambda i: 2), [])
    assert json.loads((tmp_path / "fed.vol.2027.json").read_text()) == {"2027-01-01": 48.0}
    assert json.loads((tmp_path / "fed.vol.2026.json").read_text()) == {"2026-12-31": 120.0}
    loaded = news_history.load(tmp_path, first_year=2026)
    assert loaded["fed"]["vol"]["2026-12-31"] == 120.0 and loaded["fed"]["tone"]["2026-12-31"] == 1.5
    assert mh.path_for(tmp_path, "fed", "vol", 2026) == news_history.path_for(tmp_path, "fed", "vol", 2026)


def test_export_copies_the_files(tmp_path):
    src = tmp_path / "src"
    mh.record(src, "fed", _hours(datetime(2026, 11, 14, tzinfo=UTC), 48, lambda i: 1), [])
    assert mh.export(src, tmp_path / "out") == 1 and (tmp_path / "out" / "fed.vol.2026.json").exists()


class Gdelt:
    """Answers the timelines; counts the requests by mode; refuses from ``refuse_at`` on."""

    def __init__(self, refuse_at: int | None = None):
        self.modes: list[str] = []
        self.sent = 0
        self.refuse_at = refuse_at

    async def get(self, query, mode, timespan, **extra):
        self.sent += 1
        self.modes.append(mode)
        if self.refuse_at is not None and self.sent >= self.refuse_at:
            raise ValueError("Please limit requests to one every 5 seconds")
        if mode == "ArtList":
            return {"articles": []}
        hint = "Article Count" if mode == "TimelineVolRaw" else "Average Tone"
        start = NOW - timedelta(days=2)
        return {"timeline": [{"series": hint, "data": _hours(start, 48, lambda i: 3 if mode == "TimelineVolRaw" else -1.0)}]}


async def _rss(theme, now):
    return {"id": theme.id, "label": theme.label, "articles_24h": 1, "spike": None, "tone": None, "headlines": [],
            "why": theme.why, "source": "google-news", "fetched_at": now.isoformat()}


def test_a_pass_keeps_every_themes_days_and_skips_headlines_outside_market_hours(tmp_path):
    cache = m.Cache(cache_file=tmp_path / "macro_news.json")
    g = Gdelt()
    asyncio.run(cache.refresh(gdelt=g, now=NOW, rss=_rss))
    assert g.modes.count("ArtList") == len(m.THEMES)                     # market hours: headlines too
    files = sorted(p.name for p in cache.history_dir().glob("*.json"))
    assert len(files) == 2 * len(m.THEMES) and "fed.tone.2026.json" in files
    night = datetime(2026, 11, 17, 13, 0, tzinfo=UTC)                   # 22:00 KST, 08:00 New York
    assert not m.market_hours(night) and m.market_hours(NOW)
    assert m.market_hours(datetime(2026, 11, 17, 15, 0, tzinfo=UTC))    # 10:00 New York
    assert not m.market_hours(datetime(2026, 11, 21, 15, 0, tzinfo=UTC))  # Saturday
    cache.themes["fed"]["headlines"] = [{"title": "kept from the last pass"}]
    g2 = Gdelt()
    asyncio.run(cache.refresh(gdelt=g2, now=night, rss=_rss))
    assert "ArtList" not in g2.modes and g2.sent == 2 * len(m.THEMES)    # 16 requests, not 24
    assert cache.themes["fed"]["headlines"] == [{"title": "kept from the last pass"}]


def test_a_429_keeps_the_last_gdelt_values_for_the_rest_of_the_pass(tmp_path):
    cache = m.Cache(cache_file=tmp_path / "macro_news.json")
    asyncio.run(cache.refresh(gdelt=Gdelt(), now=NOW, rss=_rss))
    before = {k: dict(v) for k, v in cache.themes.items()}
    g = Gdelt(refuse_at=4)                                               # the 2nd theme's first request
    asyncio.run(cache.refresh(gdelt=g, now=NOW + timedelta(hours=1), rss=_rss))
    assert g.sent == 4                                                   # no more GDELT this pass
    assert all(t["source"] == "gdelt" for t in cache.themes.values())    # no Google News mixed in
    second = m.THEMES[1].id
    assert cache.themes[second] == before[second] and cache.gdelt_resume_at is not None


def test_the_default_pass_interval_is_an_hour(monkeypatch):
    from desk.settings import Settings

    monkeypatch.delenv("DESK_MACRO_INTERVAL_SEC", raising=False)
    assert Settings().macro_interval_sec == 3600


def test_the_export_command_copies_from_the_live_history_dir(tmp_path, monkeypatch, capsys):
    from desk import cli

    monkeypatch.setattr(m, "CACHE_FILE", tmp_path / "cache" / "macro_news.json")
    mh.record(m.CACHE.history_dir(), "fed", _hours(datetime(2026, 11, 14, tzinfo=UTC), 48, lambda i: 1), [])
    assert cli.main(["news-history", "export", "--dir", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "fed.vol.2026.json").exists() and "1 file(s)" in capsys.readouterr().out
    assert cli.main(["news-history", "export", "--dir", str(tmp_path / "o2"), "--src", str(tmp_path / "none")]) == 1
