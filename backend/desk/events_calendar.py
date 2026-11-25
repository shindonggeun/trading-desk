"""US scheduled macro events — FOMC decisions and the BLS releases that move markets (#148).

Two keyless official sources:

- Federal Reserve FOMC calendar page (federalreserve.gov/monetarypolicy/fomccalendars.htm): meeting
  months and day ranges per year. The decision (statement + press conference) comes at 14:00 ET on
  the last day of a meeting.
- BLS release calendar (bls.gov/schedule/news_release/bls.ics): every release with its time, Eastern.
  Only the market movers are kept: CPI, the jobs report, PPI, JOLTS, ECI.

A background loop (desk.api lifespan) refreshes ``CACHE`` a few times a day; runs and the API read
the snapshot. A failed source keeps its previous events. ``blackout()`` answers "is a major release
close enough that a new entry should wait" — the playbook wiring comes in a later change.

#996 each pass is saved (``CACHE_FILE``, on the volume the split services share): the api and the analyst
do not run the pass, they follow that file (desk.feed_snapshots).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import net

log = logging.getLogger(__name__)

FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BLS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
ET = ZoneInfo("America/New_York")
TIMEOUT_SEC = 20.0
MAX_BYTES = 3_000_000
FOMC_DECISION = time(14, 0)
NOTE_DAYS = 7                 # the agents' note lists what is due in the next week
BLACKOUT_BEFORE = timedelta(hours=12)
BLACKOUT_AFTER = timedelta(hours=1)
CACHE_FILE = Path.home() / ".tradingagents" / "desk-cache" / "economic_calendar.json"   # the backend's volume
SOURCES = ("fed", "bls")

# BLS summary → (id, label, importance). Importance 2 = moves the whole market.
BLS_KEEP: dict[str, tuple[str, str, int]] = {
    "Consumer Price Index": ("cpi", "CPI (consumer prices)", 2),
    "Employment Situation": ("jobs", "Jobs report (payrolls, unemployment)", 2),
    "Producer Price Index": ("ppi", "PPI (producer prices)", 1),
    "Job Openings and Labor Turnover Survey": ("jolts", "JOLTS (job openings)", 1),
    "Employment Cost Index": ("eci", "Employment Cost Index", 1),
}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}


def user_agent() -> str:
    # federalreserve.gov / bls.gov / sec.gov refuse library default agents; SEC asks for a contact
    return os.getenv("SEC_EDGAR_USER_AGENT") or "trading-desk/1.0 (personal research)"


@dataclass(frozen=True)
class Event:
    id: str            # fomc | cpi | jobs | ppi | jolts | eci
    label: str
    at: datetime       # UTC
    importance: int    # 2 = market-wide mover, 1 = notable
    source: str        # fed | bls

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "at": self.at.isoformat(), "importance": self.importance,
                "source": self.source, "at_et": self.at.astimezone(ET).strftime("%Y-%m-%d %H:%M ET")}

    @classmethod
    def from_saved(cls, row) -> Event | None:
        """One event of a saved pass (``to_dict``); None for a row that is not one."""
        try:
            at = datetime.fromisoformat(row["at"])
            if at.tzinfo is None:
                return None
            return cls(str(row["id"]), str(row["label"]), at.astimezone(UTC), int(row["importance"]), str(row["source"]))
        except (KeyError, TypeError, ValueError):
            return None


def parse_fomc(html: str) -> list[Event]:
    """Every meeting on the page: "<year> FOMC Meetings" sections with month / day-range rows.
    A month cell can span two months ("Jan/Feb"), the date then reads "31-1"."""
    events = []
    sections = re.split(r"(\d{4}) FOMC Meetings", html)
    # [before, year, body, year, body, ...]
    for year_text, body in zip(sections[1::2], sections[2::2], strict=False):
        year = int(year_text)
        rows = re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<',
                          body, flags=re.DOTALL)
        for month_text, days_text in rows:
            # unscheduled meetings and notation votes come at any hour: not a 14:00 decision
            if re.search(r"unscheduled|notation", days_text, flags=re.IGNORECASE):
                continue
            months = [MONTHS.get(_month(m)) for m in month_text.split("/")]
            days = re.findall(r"\d+", days_text)
            if not days or not months or None in months:
                continue
            # a meeting that runs into January ("Dec/Jan", "31-1") ends in the next year
            end_year = year + 1 if months[-1] < months[0] else year
            try:
                last = date(end_year, months[-1], int(days[-1]))
            except ValueError:
                continue
            at = datetime.combine(last, FOMC_DECISION, ET).astimezone(UTC)
            events.append(Event("fomc", "FOMC rate decision + press conference", at, 2, "fed"))
    return sorted(set(events), key=lambda e: e.at)


def _month(name: str) -> str:
    name = name.strip().lower()
    for full in MONTHS:
        if full.startswith(name[:3]):
            return full
    return name


def parse_bls(ics: str) -> list[Event]:
    events = []
    for block in ics.split("BEGIN:VEVENT")[1:]:
        summary = re.search(r"^SUMMARY:(.*)$", block, flags=re.MULTILINE)
        start = re.search(r"^DTSTART[^:\n]*:(\d{8}T\d{6})(Z?)", block, flags=re.MULTILINE)
        if not summary or not start:
            continue
        keep = BLS_KEEP.get(summary.group(1).strip())
        if keep is None:
            continue
        # TZID=US-Eastern today; a trailing Z would mean UTC
        stamp = datetime.strptime(start.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC if start.group(2) else ET)
        events.append(Event(keep[0], keep[1], stamp.astimezone(UTC), keep[2], "bls"))
    return sorted(set(events), key=lambda e: e.at)


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, headers={"User-Agent": user_agent()}, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError(f"{url} answered {len(response.content)} bytes")
    return response.text


@dataclass
class Calendar:
    """The last good events of each source. A source that fails keeps what it had."""
    by_source: dict[str, list[Event]] = field(default_factory=dict)
    refreshed_at: str | None = None
    error: str | None = None
    cache_file: Path | None = None            # tests; CACHE_FILE otherwise
    saved: bool = True                        # the last pass's snapshot reached its file (#996)

    async def refresh(self, client: httpx.AsyncClient | None = None, now: datetime | None = None) -> int:
        own = client is None
        client = client or net.async_client(timeout=TIMEOUT_SEC)
        failures, ok = [], 0
        # built aside and swapped in whole: a request reading events() never sees the dict change
        fresh = dict(self.by_source)
        try:
            for source, url, parse in (("fed", FOMC_URL, parse_fomc), ("bls", BLS_URL, parse_bls)):
                try:
                    events = parse(await _get(client, url))
                    if not events:
                        raise ValueError("no events parsed")
                    fresh[source] = events
                    ok += 1
                except Exception as exc:  # noqa: BLE001 - one source down keeps its last events
                    failures.append(f"{source}: {type(exc).__name__}")
                    log.info("economic calendar %s failed: %s", source, exc)
        finally:
            if own:
                await client.aclose()
        self.by_source = fresh
        if ok:
            self.refreshed_at = (now or datetime.now(UTC)).isoformat()
        self.error = "; ".join(failures) or None
        self.saved = await asyncio.to_thread(self.save)   # #996 what the api and the analyst follow, off the loop
        return ok

    # --- persistence (#996) ------------------------------------------------------------------

    def _path(self) -> Path:
        return self.cache_file or CACHE_FILE

    def save(self) -> bool:
        path = self._path()
        by_source = self.by_source             # the dict refresh() swapped in whole
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"by_source": {s: [e.to_dict() for e in rows] for s, rows in by_source.items()},
                                       "refreshed_at": self.refreshed_at, "error": self.error}), encoding="utf-8")
            tmp.replace(path)          # a stop mid-write leaves the last whole file
        except OSError as exc:
            log.warning("economic calendar snapshot not saved (%s): %s", path, exc)
            return False
        return True

    def take(self, data: dict) -> bool:
        """A pass another process saved (the collector's, #996): its events, when and how it went - as that
        process has them now. False (nothing changed) for a file that is not a saved pass."""
        saved = data.get("by_source")
        if not isinstance(saved, dict):
            return False
        fresh = {}
        for source, rows in saved.items():
            if source in SOURCES and isinstance(rows, list):
                events = [e for e in map(Event.from_saved, rows) if e is not None]
                fresh[source] = sorted(set(events), key=lambda e: e.at)
        self.by_source = fresh                 # swapped in whole, as refresh() does
        self.refreshed_at = data.get("refreshed_at")
        self.error = data.get("error")
        return True

    def load(self) -> bool:
        """The last saved pass, before the first one in this process answers (the collector's restart, #996).
        A missing or broken file, or one with no events, leaves the cache as it is."""
        path = self._path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            log.warning("economic calendar snapshot unreadable (%s): %s", path, exc)
            return False
        saved = Calendar()
        if not isinstance(data, dict) or not saved.take(data) or not saved.events():
            return False
        self.by_source, self.refreshed_at = saved.by_source, saved.refreshed_at
        return True

    def events(self) -> list[Event]:
        return sorted((e for rows in self.by_source.values() for e in rows), key=lambda e: e.at)

    def upcoming(self, now: datetime | None = None, days: int = 14, importance: int = 1) -> list[Event]:
        now = now or datetime.now(UTC)
        end = now + timedelta(days=days)
        return [e for e in self.events() if now - BLACKOUT_AFTER <= e.at <= end and e.importance >= importance]

    def blackout(self, now: datetime | None = None, before: timedelta = BLACKOUT_BEFORE,
                 after: timedelta = BLACKOUT_AFTER) -> Event | None:
        """The market-wide event (importance 2) due within ``before`` from now or released within
        ``after`` ago — a fresh entry should wait for it. None when the coast is clear."""
        now = now or datetime.now(UTC)
        for e in self.events():
            if e.importance >= 2 and e.at - before <= now <= e.at + after:
                return e
        return None

    def view(self, now: datetime | None = None, days: int = 14) -> dict:
        """GET /api/events/calendar."""
        now = now or datetime.now(UTC)
        hit = self.blackout(now)
        return {"events": [e.to_dict() for e in self.upcoming(now, days)],
                "blackout": hit.to_dict() if hit else None,
                "refreshed_at": self.refreshed_at, "error": self.error}


CACHE = Calendar()


def prompt_note(cache: Calendar | None = None, now: datetime | None = None, days: int = NOTE_DAYS) -> str | None:
    """The agents' "Scheduled US macro events" block, or None when nothing is due (or not loaded)."""
    cache = cache or CACHE
    now = now or datetime.now(UTC)
    rows = cache.upcoming(now, days)
    if not rows:
        return None
    lines = [("Scheduled US macro events (official Fed / BLS calendars; times US Eastern). Price often whipsaws "
              "around these: do not place a fresh entry in the hours before a market-wide release, size down or "
              "wait for the reaction, and say in the plan which event it rides through.")]
    for e in rows:
        hours = (e.at - now).total_seconds() / 3600
        when = f"in {hours:.0f}h" if 0 <= hours < 48 else ("just released" if hours < 0 else f"in {hours / 24:.0f} days")
        flag = " - MARKET-WIDE" if e.importance >= 2 else ""
        lines.append(f"- {e.to_dict()['at_et']} ({when}): {e.label}{flag}")
    hit = cache.blackout(now)
    if hit:
        lines.append(f"Event window now: {hit.label} at {hit.to_dict()['at_et']} - treat new entries as waiting for it.")
    return "\n".join(lines)
