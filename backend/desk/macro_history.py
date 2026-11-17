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
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path


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
    """Upsert one theme's pass into the year files; returns the days written (both modes)."""
    vol, tone = daily(volume, tones)
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
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.glob("*.json")):
        shutil.copy2(f, dst / f.name)
        n += 1
    return n
