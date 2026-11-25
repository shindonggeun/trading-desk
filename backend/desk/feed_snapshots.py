"""#996 what the collector refreshes, the api and the analyst follow from the snapshot files it saves.

The economic calendar (desk.events_calendar), the macro news (desk.macro_news), the earnings calendar
(desk.earnings) and - #1004 - the news digest (desk.news_digest: the headlines' Korean reads) and the sector news
(desk.sector_news) live in the memory of the process that runs their pass - the collector when the backend is split
(#688, desk.roles). Their passes save the caches to files on the volume the three services share
(``~/.tradingagents/desk-cache``, written whole: a temporary file replaced in one step). A process that reads a
cache but does not run its pass follows that file: a changed file (inode, mtime, size) is read and parsed off the
event loop, and taken in on it. A missing file (nothing saved yet) or a broken one keeps what the process had;
a broken one is reported on every pass until a good one replaces it (logged once), a file that cannot be read now
(an OSError) is tried again on the next pass. A snapshot with a size cap (``max_bytes``: the news digest's and the
sector news's, whose writers stay under it) refuses a bigger file unread, as a broken one.

Files rather than the Redis cache (desk.shared_cache): its keys carry the code's fingerprint, so a deploy would
empty them while a calendar has to outlast one, and it is off without Redis - these files are written already,
outlive a deploy and need nothing more. ``DESK_ROLE=all`` and the collector run the passes themselves and follow
nothing: the desk as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import earnings, events_calendar, macro_news, news_digest, sector_news

log = logging.getLogger(__name__)

FOLLOW_SEC = 60.0      # a pass lands at most this late in another process (the passes run every 30 min to 6 h)

Stamp = tuple[int, int, int]


@dataclass
class Snapshot:
    """One cache another process saves: its job (desk.roles.JOBS), its file and how a read file is taken in."""
    name: str
    path: Callable[[], Path]
    take: Callable[[dict], bool]          # on the loop; False = not a saved pass, the cache unchanged
    seen: Stamp | None = None             # the file last read (taken or not): read again only once it changes
    failed: str | None = None             # why the file in hand could not be taken; reported until it can
    max_bytes: int | None = None          # #1004 a bigger file is refused unread (None: no cap)


def followed(runs: Callable[[str], bool]) -> list[Snapshot]:
    """The snapshots this process reads but whose pass it does not run (``runs``: desk.roles.runs for its role).
    The caches are looked up when used, so a test can stand in its own."""
    out = []
    if not runs("economic_calendar"):
        out.append(Snapshot("economic_calendar", lambda: events_calendar.CACHE._path(),
                            lambda data: events_calendar.CACHE.take(data)))
    if not runs("macro_news"):
        out.append(Snapshot("macro_news", lambda: macro_news.CACHE._path(), lambda data: macro_news.CACHE.take(data)))
    if not runs("earnings"):
        out.append(Snapshot("earnings", lambda: earnings.CACHE._path(), lambda data: earnings.CACHE.take(data)))
    # #1004 the sector issues (GET /api/sector-news, a run's macro note) and the reads of both news parts: the
    # digest is asked inside the news pass, after its macro and after its sector part
    if not runs("sector_news"):
        out.append(Snapshot("sector_news", lambda: sector_news.CACHE._path(), lambda data: sector_news.CACHE.take(data),
                            max_bytes=sector_news.SAVE_MAX_BYTES))
    if not runs("news"):
        out.append(Snapshot("news_digest", lambda: news_digest.DIGEST._path(),
                            lambda data: news_digest.DIGEST.take(data), max_bytes=news_digest.SAVE_MAX_BYTES))
    return out


def _read(path: Path, seen: Stamp | None,
          max_bytes: int | None = None) -> tuple[str, Stamp | None, dict | str | None]:
    """In a thread: ``(what, stamp, data or why)``. ``missing`` (nothing saved yet), ``same`` (unchanged since
    ``seen``), ``read`` (its content), ``broken`` (content that is not a saved pass, or a file over ``max_bytes``:
    the stamp is kept, so it is not read again until it changes) or ``unreadable`` (an OSError: no stamp, tried
    again on the next pass)."""
    try:
        st = path.stat()
        stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
        if stamp == seen:
            return "same", seen, None
        if max_bytes is not None and st.st_size > max_bytes:
            return "broken", stamp, f"too large: {st.st_size} bytes (cap {max_bytes})"
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:          # also: replaced between the two calls - the next pass reads the new one
        return "missing", None, None
    except OSError as exc:
        return "unreadable", None, f"unreadable: {type(exc).__name__}"
    try:
        data = json.loads(text)
    except ValueError as exc:
        return "broken", stamp, f"broken: {type(exc).__name__}"
    if not isinstance(data, dict):
        return "broken", stamp, "not a saved pass"
    return "read", stamp, data


async def follow(snapshots: list[Snapshot]) -> list[str]:
    """Take in each snapshot whose file changed since it was last read. Returns what cannot be taken now
    (``name: why``) - every pass while it lasts, logged when it starts; those caches keep what they had."""
    failed = []
    for snap in snapshots:
        path = snap.path()
        what, stamp, got = await asyncio.to_thread(_read, path, snap.seen, snap.max_bytes)
        if what == "missing":
            snap.seen = snap.failed = None
            continue
        if what == "read":
            snap.seen = stamp
            try:
                why = None if snap.take(got) else "not a saved pass"
            except Exception as exc:  # noqa: BLE001 - a malformed file costs only that snapshot, never the others
                why = f"{type(exc).__name__}: {exc}"
        elif what == "same":
            why = snap.failed          # the same file: still taken, or still not
        else:                          # broken (its stamp kept) or unreadable (no stamp: read again next pass)
            snap.seen = stamp
            why = got
        if why is not None and why != snap.failed:
            log.warning("%s snapshot %s not taken: %s", snap.name, path, why)
        snap.failed = why
        if why is not None:
            failed.append(f"{snap.name}: {why}")
    return failed
