"""How old a flow or quote answer really is (#645): one ``freshness`` block the responses carry.

- ``fetched_at``: when this server asked the source; ``age_sec``: how long ago (the answer may come from
  the cache, up to ``cache_sec`` old).
- ``source_updated_at``: the source's own time for the figures, when it gives one (Toss' investor flow
  carries one); ``source_delay_sec`` is then now minus that time - the real delay on screen.
- A source without a time of its own (KIS' intraday investor figures) adds half its measured update
  interval (``PUBLISH_SEC``: ``python -m desk.cli lead-lag`` reports each stored value's median time between
  changes on the server's snapshots - docs/lead-lag.md): an approximation, at the snapshots' one-minute
  resolution, that cannot see the source's own publishing lag. Until measured it is None and so is the delay.

``label`` is the screens' one line ("약 3분 지연"); outside the session the figures are the day's last
and no delay is stated.
"""

from __future__ import annotations

from datetime import UTC, datetime

# a source's update interval in seconds (median time between changes), from the lead-lag cadence report;
# None until measured on the server
PUBLISH_SEC: dict[str, float | None] = {
    "kis_futures_investors": None,
    "toss_investors": None,
    "kis_spot_investors": None,     # #676 KIS spot investors (desk.kis_spot_flow); its changes are watched too
}


def _iso(at: datetime | None) -> str | None:
    return at.astimezone(UTC).isoformat(timespec="seconds") if at else None


def _parse(at: str | datetime | None) -> datetime | None:
    if at is None or isinstance(at, datetime):
        return at
    try:
        got = datetime.fromisoformat(at)
    except ValueError:
        return None
    return got if got.tzinfo else got.replace(tzinfo=UTC)


def label(delay_sec: float | None, live: bool) -> str | None:
    if not live:
        return "장 마감 · 마지막 값"
    if delay_sec is None:
        return None
    if delay_sec < 60:
        return "1분 안쪽 지연"
    return f"약 {round(delay_sec / 60)}분 지연"


def stamp(fetched_at: str | datetime | None, now: datetime, cache_sec: float, *, live: bool,
          source_at: str | datetime | None = None, source: str | None = None) -> dict:
    """The freshness block for an answer fetched at ``fetched_at`` and served at ``now``."""
    fetched, source_time = _parse(fetched_at), _parse(source_at)
    age = max(0.0, (now - fetched).total_seconds()) if fetched else None
    publish = PUBLISH_SEC.get(source) if source else None
    if not live:                       # outside the session the figures are the day's last: no delay to state
        delay = None
    elif source_time is not None:
        delay = max(0.0, (now - source_time).total_seconds())
    elif age is not None and publish is not None:
        delay = age + publish / 2      # a source updated every P seconds is P/2 old on average
    else:
        delay = None
    return {"fetched_at": _iso(fetched), "age_sec": None if age is None else round(age), "cache_sec": round(cache_sec),
            "source_updated_at": _iso(source_time), "source_delay_sec": None if delay is None else round(delay),
            "live": live, "label": label(delay, live)}
