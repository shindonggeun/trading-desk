"""#485: the GDELT tone and article counts the live macro_news refresh already fetches, kept by day.

GDELT refuses this address's new history queries (#390: the live refresh uses the address's limit),
so instead of reaching back the desk keeps what each pass receives, going forward, at no extra
request: per theme the 7-day article-count timeline (hourly) and the 72-hour tone timeline (hourly)
are folded into days and upserted into the files desk.news_history reads
(``<dir>/<theme>.<mode>.<year>.json``, ``{"YYYY-MM-DD": value}``) - so ``backtest --news-dir`` takes
them as they are. The directory sits next to the macro_news snapshot (the ``tradingagents_home``
volume on the server); ``python -m desk.cli news-history export --dir ...`` copies it out.

A window's oldest day is cut off by the window itself (7 days back from now starts mid-day), so it is
left out: a later pass would otherwise overwrite a whole day with a part of it. Today is written and
rewritten by every pass, so the day's last pass leaves the most of it - and the next day's window
covers it whole.

#485 (2): this address's GDELT mostly refuses, and a refused pass reads Google News RSS instead. Those
passes are kept too, by day, in the same layout under ``<dir>/google-news/``: the day's count of the
feed's dated articles and the day's headline-word tone (desk.macro_news.headline_tone - cruder, and on
its own scale). The source is the directory, so no point mixes the two: GDELT's files stay where
``backtest --news-dir`` has always read them and ``--news-source google-news`` reads the others. The
feed holds at most 100 of the week's articles, so its oldest day is a part one (dropped, as GDELT's
window edge) and a busy week's counts are a floor.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path

SOURCES = ("gdelt", "google-news")


def source_dir(root: Path | str, source: str = "gdelt") -> Path:
    """Where a source's day files sit: GDELT at the root (desk.news_history's layout), the others below it."""
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}")
    return Path(root) if source == "gdelt" else Path(root) / source


def path_for(root: Path | str, theme_id: str, mode: str, year: int) -> Path:
    """desk.news_history.path_for's layout (not imported: news_history imports macro_news, which imports this)."""
    return Path(root) / f"{theme_id}.{mode}.{year}.json"


log = logging.getLogger(__name__)

Series = dict[str, float]


def _day(raw: str) -> str | None:
    """GDELT's "20261110T130000Z" (or "20261110") -> "2026-11-10"."""
    raw = str(raw or "")
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 and raw[:8].isdigit() else None


def daily(volume: list[dict], tones: list[dict]) -> tuple[Series, Series]:
    """Hourly timelines -> per-day article count (the hours summed) and tone (the hours' tones weighted
    by their article counts where known, else plain). Each window's oldest (partial) day is dropped."""
    counts: dict[str, float] = defaultdict(float)
    per_hour: dict[str, float] = {}
    for p in volume:
        d = _day(p.get("date"))
        if d is not None:
            counts[d] += float(p["value"])
            per_hour[str(p.get("date"))] = float(p["value"])
    weighted: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for p in tones:
        d = _day(p.get("date"))
        if d is not None:
            weighted[d].append((float(p["value"]), per_hour.get(str(p.get("date")), 0.0)))
    first_vol = min(counts) if counts else None
    vol = {d: round(v, 2) for d, v in counts.items() if d != first_vol}
    first_tone = min(weighted) if weighted else None
    tone: Series = {}
    for d, pts in weighted.items():
        if d == first_tone:
            continue
        total = sum(w for _, w in pts)
        value = sum(v * w for v, w in pts) / total if total > 0 else sum(v for v, _ in pts) / len(pts)
        tone[d] = round(value, 4)
    return vol, tone


def _upsert(path: Path, series: Series) -> None:
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        old = old if isinstance(old, dict) else {}
    except (OSError, ValueError):
        old = {}
    old.update(series)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(old, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def record(root: Path | str, theme_id: str, volume: list[dict], tones: list[dict]) -> int:
    """Upsert one theme's GDELT pass into the year files; returns the days written (both modes)."""
    return record_daily(root, theme_id, *daily(volume, tones))


def rss_daily(dated: list[tuple[str, str]], tone_of) -> tuple[Series, Series]:
    """#485 a Google News pass -> per-day article count and tone. ``dated`` is (ISO time, title) of the
    feed's dated articles; ``tone_of(titles)`` scores a day's on-topic titles (None when none score).
    The oldest day is a part one (the feed's 7-day window and its 100-article cap) and is dropped."""
    days: dict[str, list[str]] = defaultdict(list)
    for at, title in dated:
        days[str(at)[:10]].append(title)
    first = min(days) if days else None
    vol = {d: float(len(t)) for d, t in days.items() if d != first}
    tone = {d: v for d, t in days.items() if d != first and (v := tone_of(t)) is not None}
    return vol, tone


def record_daily(root: Path | str, theme_id: str, vol: Series, tone: Series) -> int:
    """Upsert one theme's day values into the year files under ``root``; returns the days written."""
    written = 0
    for mode, series in (("vol", vol), ("tone", tone)):
        by_year: dict[int, Series] = defaultdict(dict)
        for d, v in series.items():
            by_year[int(d[:4])][d] = v
        for year, part in by_year.items():
            _upsert(path_for(root, theme_id, mode, year), part)
            written += len(part)
    return written


def export(src: Path | str, dst: Path | str) -> int:
    """Copy the kept files to ``dst`` (for the PC's backtest); returns how many."""
    src, dst = Path(src), Path(dst)
    n = 0
    for f in sorted(src.rglob("*.json")):          # GDELT's files and the google-news/ ones, as laid out
        out = dst / f.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        n += 1
    return n
