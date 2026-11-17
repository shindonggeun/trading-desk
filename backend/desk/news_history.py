"""#390: the news themes' past from GDELT, so the backtest can replay desk.direction's "macro" part.

A live run reads desk.macro_news's brief: per theme the article count of the last 24 hours against
the average day of the six before it (the spike) and the average tone of the last 72 hours.
``brief_at(history, ticker, day)`` builds the same rows from daily points, using only the days
before ``day`` (a plan at the close must not read the day's own late articles - and a Korean close
comes before most of that UTC day is written).

GDELT DOC 2.0 serves timelines back to 2017 but allows one request every five seconds and blocks
the address for about a quarter of an hour after a 429 - the address the production server's
macro_news refresh shares. So the collection is its own command, run as often as it takes::

    python -m desk.cli news-history --out-dir data/gdelt

It fetches each theme, mode and year once, REQUEST_GAP_SEC (20 s) apart, stops at the first refusal
and keeps every series on disk (``<dir>/<theme>.<mode>.<year>.json``): a past year never changes, the
current one is fetched again after a day. The next run carries on with what is missing. The backtest
only reads the files (``backtest --news-dir``) - it never asks GDELT itself.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import fmean

import httpx

from . import macro_history
from .macro_news import GDELT_DOC, THEMES, Theme, groups_of, relevance

log = logging.getLogger(__name__)

FIRST_YEAR = 2017
MODES = {"tone": "TimelineTone", "vol": "TimelineVolRaw"}
TONE_DAYS = 3        # the live brief's 72 hours
SPIKE_BASE_DAYS = 6  # the days before the last one that make the live spike's average day
CURRENT_TTL_SEC = 24 * 3600
TIMEOUT_SEC = 120.0
# GDELT's rule is one request per 5 s per address - and the production server's desk.macro_news
# refresh asks from the same address. At 6 s the two together were refused (2026-11-08): leave room.
REQUEST_GAP_SEC = 20.0

Series = dict[str, float]                 # "YYYY-MM-DD" -> value
History = dict[str, dict[str, Series]]    # theme id -> {"tone": Series, "vol": Series}


class Refused(Exception):
    """GDELT refused (429 or its plain-text limit note): stop for now, try again in a quarter hour."""


def path_for(root: Path | str, theme_id: str, mode: str, year: int) -> Path:
    return Path(root) / f"{theme_id}.{mode}.{year}.json"


def _points(payload: dict) -> Series:
    timeline = (payload or {}).get("timeline") or []
    out: Series = {}
    for p in (timeline[0].get("data") or []) if timeline else []:
        if isinstance(p.get("value"), (int, float)) and p.get("date"):
            out[f"{p['date'][:4]}-{p['date'][4:6]}-{p['date'][6:8]}"] = float(p["value"])
    return out


def fetch_year(theme: Theme, mode: str, year: int, get: Callable[..., httpx.Response] = httpx.get,
               today: date | None = None) -> Series:
    """One theme's daily series for one calendar year (to today for the current one)."""
    end = min(date(year, 12, 31), today or date.today())
    params = {"query": theme.query, "mode": MODES[mode], "format": "json",
              "startdatetime": f"{year}0101000000", "enddatetime": end.strftime("%Y%m%d") + "235959"}
    response = get(GDELT_DOC, params=params, timeout=TIMEOUT_SEC)
    text = response.text.lstrip()
    if response.status_code == 429 or not text.startswith("{"):
        raise Refused(text[:120] or f"HTTP {response.status_code}")
    response.raise_for_status()
    return _points(response.json())


def _read(path: Path) -> Series | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))} if isinstance(data, dict) else None


def load(root: Path | str, themes: tuple[Theme, ...] = THEMES, first_year: int = FIRST_YEAR,
         source: str = "gdelt") -> History:
    """Every theme's series from the files under ``root`` - no request. Missing files are empty.
    ``source`` (#485): "gdelt" reads ``root`` itself, "google-news" the kept RSS days below it."""
    root = macro_history.source_dir(root, source)
    out: History = {t.id: {m: {} for m in MODES} for t in themes}
    for theme in themes:
        for mode in MODES:
            for year in range(first_year, date.today().year + 1):
                series = _read(path_for(root, theme.id, mode, year))
                if series:
                    out[theme.id][mode].update(series)
    return out


def collect(root: Path | str, themes: tuple[Theme, ...] = THEMES, first_year: int = FIRST_YEAR,
            get: Callable[..., httpx.Response] = httpx.get, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], float] = time.monotonic, today: date | None = None,
            progress: Callable[[str], None] | None = None, max_files: int | None = None) -> dict:
    """Fetch what is missing (or the current year's file older than a day), politely, until GDELT
    refuses or ``max_files`` came in (a small batch shares GDELT's limit with the live macro_news
    refresh). Returns {"fetched", "kept", "missing", "refused"} - counts of files, and the refusal."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    fetched = kept = 0
    missing: list[str] = []
    refused: str | None = None
    last_call: float | None = None
    for theme in themes:
        for mode in MODES:
            for year in range(first_year, today.year + 1):
                path = path_for(root, theme.id, mode, year)
                fresh = path.exists() and (year < today.year or time.time() - path.stat().st_mtime < CURRENT_TTL_SEC)
                if fresh:
                    kept += 1
                    continue
                if refused or (max_files is not None and fetched >= max_files):
                    missing.append(path.name)
                    continue
                if last_call is not None:
                    wait = last_call + REQUEST_GAP_SEC - clock()
                    if wait > 0:
                        sleep(wait)
                try:
                    series = fetch_year(theme, mode, year, get, today)
                except Refused as exc:
                    log.warning("GDELT refused %s %s %s: %s", theme.id, mode, year, exc)
                    refused = str(exc)
                    missing.append(path.name)
                    continue
                finally:
                    last_call = clock()
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(series, sort_keys=True), encoding="utf-8")
                tmp.replace(path)
                fetched += 1
                if progress:
                    progress(f"gdelt {theme.id} {mode} {year}: {len(series)}일")
    return {"fetched": fetched, "kept": kept, "missing": missing, "refused": refused}


def thin(history: History) -> list[str]:
    """Themes with no tone or no article counts at all (the replay leaves them out)."""
    return [k for k, v in history.items() if not v.get("tone") or not v.get("vol")]


def _days_before(day: str, n: int, skip: int = 0) -> list[str]:
    d = date.fromisoformat(day[:10])
    return [(d - timedelta(days=k)).isoformat() for k in range(1 + skip, 1 + skip + n)]


def brief_at(history: History | None, ticker: str, day: str, themes: tuple[Theme, ...] = THEMES) -> dict | None:
    """desk.macro_news.Cache.brief's shape for ``ticker`` on ``day``, from the days before it."""
    if not history:
        return None
    tags = groups_of(ticker)
    rows = []
    for theme in themes:
        series = history.get(theme.id) or {}
        tone_s, vol_s = series.get("tone") or {}, series.get("vol") or {}
        tones = [tone_s[d] for d in _days_before(day, TONE_DAYS) if d in tone_s]
        last = vol_s.get(_days_before(day, 1)[0])
        base = [vol_s[d] for d in _days_before(day, SPIKE_BASE_DAYS, skip=1) if d in vol_s]
        if not tones and last is None:
            continue
        avg = fmean(base) if base else 0.0
        rows.append({"id": theme.id, "label": theme.label, "relevance": relevance(theme, tags), "stale": False,
                     "tone": round(fmean(tones), 2) if tones else None,
                     "spike": round(last / avg, 2) if last is not None and avg > 0 else None,
                     "articles_24h": int(last) if last is not None else None, "source": "gdelt-history"})
    if not rows:
        return None
    rows.sort(key=lambda r: (-(r["relevance"] or 0), -(r["spike"] or 0)))
    return {"ticker": ticker, "themes": rows, "as_of": day[:10],
            "refreshed_at": datetime.now(UTC).isoformat(timespec="seconds")}
