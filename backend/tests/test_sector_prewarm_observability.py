"""Relate the authenticated status's sector job time to logs from the same offline pass."""

import asyncio
import logging
import re
from datetime import UTC, timedelta

import pytest

from desk import day_sectors as sectors
from desk import prewarm
from tests.sector_prewarm_profile import BEFORE_CLOSE, DAY, Origin, _fixture
from tests.test_favorites import _app, _login


def test_status_and_sector_logs_distinguish_rebuild_hit_failure_and_recovery(db_url, monkeypatch, caplog):
    before, after = _fixture(260)
    boundary = sectors.session_close(sectors.KR, DAY) + sectors.SCORE_AFTER + timedelta(seconds=41)
    times = (BEFORE_CLOSE, BEFORE_CLOSE + timedelta(minutes=4), boundary, boundary + timedelta(minutes=4))
    origins = (Origin(before, 0), Origin(before, 0), Origin(after, 0, "timeout"), Origin(after, 0))
    phase = 0
    cache = sectors.Cache(clock=lambda: (times[phase] - times[0]).total_seconds())
    snapshots = []
    rebuilds = []
    caplog.set_level(logging.INFO, logger=sectors.__name__)
    monkeypatch.setattr(prewarm, "LAST", {})

    class Clock:
        @staticmethod
        def now(tz):
            return times[phase].astimezone(tz)

    monkeypatch.setattr(prewarm, "datetime", Clock)

    async def sector_job():
        await asyncio.gather(*(prewarm.off(cache.read, market, origins[phase], times[phase], prewarm.AHEAD_SEC)
                               for market in sectors.MARKETS))

    with _login(_app(db_url)) as client:
        assert client.get("/api/status").json()["prewarm"] is None

        async def after_pass(delay):
            nonlocal phase
            if delay == 0:  # first_delay
                return
            response = client.get("/api/status")
            assert response.status_code == 200
            snapshots.append(response.json()["prewarm"])
            lines = [r.getMessage().removeprefix("day sectors: rebuild ")
                     for r in caplog.records if r.getMessage().startswith("day sectors: rebuild ")]
            rebuilds.append([dict(field.split("=", 1) for field in line.split()) for line in lines])
            caplog.clear()
            if len(snapshots) == len(times):
                raise asyncio.CancelledError
            phase += 1

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(prewarm.run(lambda: {"sectors": sector_job}, list, lambda _: asyncio.sleep(0),
                                    first_delay=0, sleep=after_pass))

    for at, snapshot in zip(times, snapshots, strict=True):
        assert snapshot["at"] == at.astimezone(UTC).isoformat(timespec="seconds")
        assert isinstance(snapshot["ms"], int) and snapshot["ms"] >= 0
        assert re.fullmatch(r"\d+ms", snapshot["jobs"]["sectors"])

    assert rebuilds[1] == [] and origins[1].requests == []
    totals = []
    for index in (0, 2, 3):
        rows = rebuilds[index]
        assert len(rows) == len(sectors.MARKETS)
        assert len(origins[index].requests) == len(before)
        assert {row["market"] for row in rows} == set(sectors.MARKETS)
        assert all(row["complete"] == ("False" if index == 2 else "True") for row in rows)
        assert all(float(row[key]) >= 0 for row in rows for key in ("lock_ms", "bars_ms", "view_ms", "origin_max_ms"))
        totals.append({key: sum(int(row[key]) for row in rows) for key in
                       ("full_calls", "recent_calls", "bar_hits", "origin_errors", "origin_empty", "reused_failures")})

    symbols = len(before)
    assert totals == [
        {"full_calls": symbols, "recent_calls": 0, "bar_hits": symbols,
         "origin_errors": 0, "origin_empty": 0, "reused_failures": 0},
        {"full_calls": 0, "recent_calls": symbols, "bar_hits": symbols - 1,
         "origin_errors": 1, "origin_empty": 0, "reused_failures": 1},
        {"full_calls": 0, "recent_calls": symbols, "bar_hits": symbols,
         "origin_errors": 0, "origin_empty": 0, "reused_failures": 0},
    ]
