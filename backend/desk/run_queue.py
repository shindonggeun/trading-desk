"""#688 the analyst takes the runs the api queued.

``DESK_ROLE=all`` runs a submitted run in the same process, as before #688. Any other role only writes the run
``queued`` with its ``queued`` event (``RunManager(direct=False)``); the analyst's ``run_queue`` job claims them:

- every ``POLL_SEC`` and as soon as one of its runs ends, while it has room (``RunManager.room()``), it claims the
  oldest queued run (``Store.claim_queued``: ``FOR UPDATE SKIP LOCKED`` on PostgreSQL) and runs it as before -
  the run gate (``DESK_MAX_CONCURRENT_RUNS``, the 429 cooldown) and the one-instrument-at-a-time lock are this
  process's;
- one process takes the queue: it holds a PostgreSQL advisory lock on a connection of its own (server-side
  keepalives end a dead host's session in about a minute). A second analyst logs a warning, shows the error on
  ``GET /api/status`` and claims nothing while the first holds it. An ``all`` process takes the same lock, so an
  analyst started beside it by mistake claims none of its runs;
- each time it takes the lock it fails the runs left ``running`` by whoever held it before - their worker died -
  less the ones it runs itself, and leaves ``queued`` ones to be claimed. The api and the collector never touch
  unfinished runs;
- stopping (``stop``), it claims nothing more and puts back a run it claimed but had not started.

A run is not leased: one analyst is the design. A queued run waits for an analyst however long that takes
(no expiry, no cancel yet - a follow-up).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import fields

from sqlalchemy import text
from sqlalchemy.engine import Engine

from . import run_events
from .privacy import PolicyChanged
from .runner import RunManager, RunRequest
from .store import Store

log = logging.getLogger(__name__)

POLL_SEC = 2.0
BLOCKED_SEC = 30.0            # a blocked analyst asks for the lock this often, not every pass
LOCK_KEY = 688_000_001        # pg_try_advisory_lock key: the run queue (one analyst)
APP_NAME = "desk-run-queue"
RESTART_REASON = "분석 서비스가 다시 시작돼 분석이 중단됐습니다."
UNREADABLE = "대기열의 분석 요청을 읽지 못해 시작하지 못했습니다."
# what a queued run carries back; ``reused`` is set by the RunManager that runs it, never read from the row
_REQUEST_FIELDS = {f.name for f in fields(RunRequest)} - {"reused"}


def held(engine: Engine) -> bool | None:
    """Whether some process holds the run queue's lock on this database right now (PostgreSQL), None on SQLite.
    ``pg_try_advisory_lock(bigint)`` files the key as classid (high 32 bits) · objid (low) · objsubid 1."""
    if not run_events.shared(engine):
        return None
    with engine.connect() as conn:
        return bool(conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND objsubid = 1 "
            "AND ((classid::bigint << 32) | objid::bigint) = :k "
            "AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))"),
            {"k": LOCK_KEY}).scalar())


class Holder:
    """The queue's advisory lock on a PostgreSQL connection of its own (blocking calls: use a thread).
    SQLite has no other process to keep out: always held."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._conn = None
        self._closed = False

    def hold(self) -> bool:
        if self._closed:
            return False
        if not run_events.shared(self.engine):
            return True
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
                return True
            except Exception as exc:  # noqa: BLE001 - the session (and its lock) is gone: take it again
                log.warning("run queue: lock connection lost (%s)", type(exc).__name__)
                self._drop()
        conn = run_events._connect(self.engine, APP_NAME)
        try:
            held = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
        except Exception:
            conn.close()
            raise
        if held and not self._closed:
            self._conn = conn
            return True
        conn.close()
        return False

    def release(self) -> None:
        """Let the lock go now (not when the server notices the closed socket) and close for good."""
        self._closed = True
        self._drop()

    def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):   # a dead connection: its lock went with its session
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            with contextlib.suppress(Exception):
                conn.close()


class RunQueue:
    def __init__(self, store: Store, manager: RunManager, holder: Holder) -> None:
        self.store = store
        self.manager = manager
        self.holder = holder
        self._held = False
        self._stopped = False
        self._lock = asyncio.Lock()
        self._kicks: set[asyncio.Task] = set()
        self.blocked = False          # another process holds the queue
        manager.on_free = self.kick

    async def step(self) -> float:
        """One pass of the ``run_queue`` job: the wait until the next one. ``blocked`` after it: another
        process holds the queue (the caller shows it; the warning is logged once)."""
        await self.take()
        return BLOCKED_SEC if self.blocked else POLL_SEC

    def kick(self) -> None:
        """A run ended: claim the next one now instead of at the next pass."""
        if self._stopped:
            return
        task = asyncio.ensure_future(self.take())
        self._kicks.add(task)
        task.add_done_callback(self._kicked)

    def _kicked(self, task: asyncio.Task) -> None:
        self._kicks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("run queue: claiming after a run failed: %s", type(task.exception()).__name__)

    async def take(self) -> int:
        """Claim queued runs while there is room; how many were started."""
        async with self._lock:
            if self._stopped or not self.store.policy_is_current():
                return 0
            held = await asyncio.to_thread(self.holder.hold)
            if not held:
                if not self.blocked:
                    log.warning("run queue: another process holds it - this one claims no run (one analyst only)")
                self.blocked, self._held = True, False
                return 0
            if self.blocked:
                log.info("run queue: taken over")
            self.blocked = False
            if not self._held:            # just took the lock: what the previous holder left running died
                try:
                    failed = await asyncio.to_thread(self.store.fail_unfinished, RESTART_REASON, ("running",),
                                                     self.manager.run_ids())
                except PolicyChanged:
                    return 0
                if failed:
                    log.warning("marked %d run(s) left running by the previous analyst as failed", failed)
                self._held = True
            started = 0
            while self.manager.room() and not self._stopped:
                try:
                    claimed = await asyncio.to_thread(self.store.claim_queued)
                except PolicyChanged:
                    break
                if claimed is None:
                    break
                run_id, options = claimed
                if self._stopped:         # shutting down meanwhile: back to the queue for the next analyst
                    try:
                        await asyncio.to_thread(self.store.requeue, run_id)
                    except PolicyChanged:
                        pass
                    break
                try:
                    request = _request(options)
                except (TypeError, ValueError) as exc:
                    log.error("run %s: stored options make no request (%s)", run_id, type(exc).__name__)
                    self.manager.fail_claimed(run_id, UNREADABLE)
                    continue
                self.manager.start(run_id, request)
                started += 1
            return started

    def stop(self) -> None:
        """First thing at shutdown: no claim from here on (a run ending now kicks nothing)."""
        self._stopped = True
        self.manager.on_free = None
        for task in list(self._kicks):
            task.cancel()

    async def close(self) -> None:
        self.stop()
        async with self._lock:        # a claim under way finishes (and puts its run back) first
            await asyncio.to_thread(self.holder.release)


def _request(options) -> RunRequest:
    """The stored options back as a request; a key this version does not know is dropped (a newer api)."""
    if not isinstance(options, dict):
        raise TypeError("options is not an object")
    request = RunRequest(**{k: v for k, v in options.items() if k in _REQUEST_FIELDS})
    if not isinstance(request.ticker, str) or not isinstance(request.trade_date, str) \
            or not isinstance(request.analysts, list):
        raise TypeError("options of the wrong type")
    return request
