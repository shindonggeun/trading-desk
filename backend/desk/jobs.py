"""The background loops' one runner and their status (#623).

Every loop of desk.api's lifespan runs through ``Jobs.run_forever``: a pass is an async ``step`` that
returns how long to wait before the next one. An exception in a pass is logged (``log.exception``) and
the loop carries on after ``retry`` seconds - a pass never ends the loop, and two passes never overlap
(one task, one pass at a time). ``spawn`` starts the task and logs it if it ever ends on its own.

Each job keeps what ``GET /api/status`` shows: runs, the last success, the last error (the exception's
type name, or a short fixed text - never a message that could carry a URL, a key or a response body)
and when the next pass is due. A loop that covers more than one source records each one as a ``part``
(GDELT news and sector news in one pass), so one source failing marks only its own line.

State, in order: ``off`` (not started here: a switch, no key, scripted runs), ``stopped`` (the task
ended), ``running`` (a task that keeps its own loop - only whether it is alive is known), ``error`` (the
last attempt failed), ``starting`` (no pass finished yet), ``stale`` (the next pass is overdue by more
than its grace, while no pass of it or its parent is running), ``fresh``.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

GRACE_MIN_SEC = 120.0          # a pass may run late by this much (or half its interval) before "stale"
MAX_ERROR_LEN = 80

Delay = float | Callable[[], float]


def _error_text(exc: BaseException | str) -> str:
    """What the status shows of a failure: the exception's type name, or the caller's fixed text."""
    return (exc if isinstance(exc, str) else type(exc).__name__)[:MAX_ERROR_LEN]


@dataclass
class Job:
    name: str
    label: str
    source: str
    parent: str | None = None
    enabled: bool = False
    runs: int = 0
    errors: int = 0
    last_run: datetime | None = None
    last_ok: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    next_due: datetime | None = None
    interval: float | None = None
    running: bool = False
    looped: bool = False          # run through run_forever (else a spawned task with its own loop)
    ended: str | None = None
    note: dict | None = None      # #674 what the last pass judged on (a job's own words, e.g. its source)
    _failed: bool = field(default=False, repr=False)

    def ok(self, at: datetime) -> None:
        self.last_ok = at

    def fail(self, at: datetime, exc: BaseException | str) -> None:
        self.errors += 1
        self.last_error = _error_text(exc)
        self.last_error_at = at
        self._failed = True


class Jobs:
    min_delay = 1.0            # tests lower it

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.clock = clock
        self._jobs: dict[str, Job] = {}
        self._bad_wait: set[str] = set()       # jobs whose pass returned no usable wait (warned once)

    # --- registry
    def add(self, name: str, label: str, source: str, parent: str | None = None) -> Job:
        job = self._jobs.get(name)
        if job is None:
            job = self._jobs[name] = Job(name, label, source, parent)
        return job

    def get(self, name: str) -> Job:
        return self._jobs[name]

    def mark_error(self, name: str, what: BaseException | str) -> None:
        """A pass (or a part) that did not raise but did not do its work: its line shows ``error``."""
        self.get(name).fail(self.clock(), what)

    @contextmanager
    def part(self, name: str, failed: str) -> Iterator[None]:
        """One source inside a pass: its own success or error; an exception is logged (``failed``) and
        does not reach the pass, so the pass's other sources still run."""
        job = self.get(name)
        job.enabled = True
        job.runs += 1
        job.last_run = self.clock()
        job._failed = False
        try:
            yield
        except Exception as exc:
            log.exception(failed)
            job.fail(self.clock(), exc)
        else:
            if not job._failed:
                job.ok(self.clock())

    # --- running
    async def run_forever(self, name: str, step: Callable[[], Awaitable[float]], retry: Delay,
                          failed: str | None = None) -> None:
        """Run ``step`` again and again: wait what it returns, or ``retry`` after an exception (logged).
        Cancellation ends it (the app is shutting down)."""
        job = self.get(name)
        job.enabled = job.looped = True
        while True:
            at = self.clock()
            job.runs += 1
            job.last_run = at
            job.running = True
            job._failed = False
            try:
                delay = await step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception(failed or f"{name} failed")
                job.fail(self.clock(), exc)
                delay = retry() if callable(retry) else retry
            else:
                if not job._failed:
                    job.ok(self.clock())
            finally:
                job.running = False
            delay = self._wait(name, delay, retry)
            job.interval = delay
            job.next_due = self.clock() + timedelta(seconds=delay)
            for child in self._jobs.values():          # its parts are due with it
                if child.parent == name:
                    child.interval, child.next_due = delay, job.next_due
            await asyncio.sleep(delay)

    def _wait(self, name: str, delay: object, retry: Delay) -> float:
        """What a pass returned as its wait. #628: None or a value that is no finite number would end the
        loop - it waits ``retry`` instead, with one WARNING per job."""
        try:
            value = float(delay)  # type: ignore[arg-type]
            if not math.isfinite(value):
                raise ValueError(value)
        except (TypeError, ValueError):
            value = float(retry() if callable(retry) else retry)
            if name not in self._bad_wait:
                self._bad_wait.add(name)
                log.warning("%s: a pass returned no wait (%s) - waiting %.0fs instead", name, type(delay).__name__, value)
        return max(self.min_delay, value)

    def spawn(self, name: str, coro: Awaitable[None]) -> asyncio.Task:
        """Start a job's task; if it ever ends other than by cancellation, say so in the log and status."""
        job = self.get(name)
        job.enabled = True
        task = asyncio.ensure_future(coro)

        def ended(t: asyncio.Task) -> None:
            if t.cancelled():
                job.ended = "cancelled"
                return
            exc = t.exception()
            job.ended = "error" if exc else "finished"
            if exc:
                job.fail(self.clock(), exc)
                log.error("background job %s ended: %s", name, type(exc).__name__, exc_info=exc)
            else:
                log.warning("background job %s ended on its own", name)

        task.add_done_callback(ended)
        return task

    # --- status
    def state(self, job: Job, at: datetime) -> str:
        if not job.enabled:
            return "off"
        if job.ended and job.ended != "cancelled":
            return "stopped"
        if not job.looped and job.parent is None:
            return "running"
        if job.last_error_at and (job.last_ok is None or job.last_error_at >= job.last_ok):
            return "error"
        if job.last_ok is None:
            return "starting"
        parent = self._jobs.get(job.parent) if job.parent else None
        if job.next_due is not None and not job.running and not (parent and parent.running):
            grace = max(GRACE_MIN_SEC, (job.interval or 0) / 2)
            if at > job.next_due + timedelta(seconds=grace):
                return "stale"
        return "fresh"

    def status(self) -> list[dict]:
        at = self.clock()
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds") if d else None
        return [{"name": j.name, "label": j.label, "source": j.source, "parent": j.parent, "state": self.state(j, at),
                 "runs": j.runs, "errors": j.errors, "last_run": iso(j.last_run), "last_ok": iso(j.last_ok),
                 "last_error": j.last_error, "last_error_at": iso(j.last_error_at), "next_due": iso(j.next_due),
                 "interval_sec": round(j.interval) if j.interval else None, "note": j.note}
                for j in self._jobs.values()]
