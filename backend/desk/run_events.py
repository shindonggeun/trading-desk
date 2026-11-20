"""#688 run events across processes: PostgreSQL LISTEN/NOTIFY wakes this process's SSE readers.

The events table stays the record - SSE replays it from ``Last-Event-ID`` - and a notification only says
"run <id> has something new". A lost one costs a reader no event, only the wait to its next heartbeat
re-read (``api.HEARTBEAT_SEC``).

- ``desk.store`` sends ``pg_notify`` inside the transaction that writes an event or deletes a run. PostgreSQL
  delivers it at commit, so a reader it wakes already sees the row, whichever process wrote it.
- ``follow`` keeps one connection of its own (not the pool's) on ``LISTEN`` in a daemon thread and hands each
  notification to ``Hub.notify`` on the event loop thread. After a (re)connect it wakes every open stream once,
  since whatever came while it was not listening is gone.
- SQLite (one local process) has no channel: the in-process ``Hub`` is all there is.

The payload is a run id - nothing else crosses the channel. A channel is per database, and only the desk's own
account (and a superuser) can connect to it (Infra: CONNECT revoked from PUBLIC); a forged notification costs at
most one extra re-read of a run. The listener takes its driver options from the URL alone (``_connect``): keep
PostgreSQL options (sslmode, search_path) in the URL's query, not in ``make_engine``'s ``connect_args``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy.engine import Engine

log = logging.getLogger(__name__)

CHANNEL = "desk_run_events"
APP_NAME = "desk-run-events"   # the listening connection in pg_stat_activity
POLL_SEC = 1.0            # how often the listening thread looks up from the socket (stop, liveness)
PING_SEC = 30.0           # a quiet connection is checked this often: a dead server must not look like silence
RETRY_SEC = (1.0, 2.0, 5.0, 15.0, 30.0)
CONNECT_TIMEOUT_SEC = 10   # a host that drops packets must not hold the thread for the OS's TCP timeout


def shared(engine: Engine) -> bool:
    """Whether this database carries run events between processes (PostgreSQL)."""
    return engine.dialect.name == "postgresql"


def _connect(engine: Engine, app_name: str = APP_NAME):
    """A connection of its own, outside the pool, in autocommit (LISTEN takes effect at once). The arguments
    are the ones SQLAlchemy would give the driver for this URL."""
    import psycopg

    cargs, cparams = engine.dialect.create_connect_args(engine.url)
    cparams["application_name"] = app_name
    cparams.setdefault("connect_timeout", CONNECT_TIMEOUT_SEC)
    # a connection that goes dark (no RST) fails within a minute instead of the kernel's ~15 min of retries
    for key, value in (("keepalives", 1), ("keepalives_idle", 30), ("keepalives_interval", 10),
                       ("keepalives_count", 3), ("tcp_user_timeout", int(PING_SEC * 2 * 1000))):
        cparams.setdefault(key, value)
    # and the server's side of it: a session (with the analyst's queue lock) whose client host died must end in
    # about a minute, not at the server OS's ~2 h keepalive. Added to the URL's own options (search_path).
    server = ("-c tcp_keepalives_idle=30 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=3 "
              f"-c tcp_user_timeout={int(PING_SEC * 2 * 1000)}")
    cparams["options"] = f"{cparams['options']} {server}" if cparams.get("options") else server
    return psycopg.connect(*cargs, **cparams, autocommit=True)


def _listen(engine: Engine, wake: Callable[[str], None], wake_all: Callable[[], None],
            listening: Callable[[bool, str | None], None], stop: threading.Event,
            ended: Callable[[], bool]) -> None:
    failures = 0
    while not ended():
        try:
            with _connect(engine) as conn:
                conn.execute(f"LISTEN {CHANNEL}")
                wake_all()             # before saying so: whoever sees "listening" finds the wake done
                listening(True, None)
                pinged = time.monotonic()
                while not ended():
                    for note in conn.notifies(timeout=POLL_SEC):
                        wake(note.payload)
                    if time.monotonic() - pinged >= PING_SEC:
                        conn.execute("SELECT 1")
                        pinged = time.monotonic()
                        listening(True, None)   # still listening: the status line stays fresh
                        failures = 0   # the backoff starts over only once a connection has held for a while
        except Exception as exc:  # noqa: BLE001 - a lost connection is retried; readers fall back to heartbeats
            if ended():
                return
            listening(False, type(exc).__name__)
            stop.wait(RETRY_SEC[min(failures, len(RETRY_SEC) - 1)])
            failures += 1


async def follow(hub, engine: Engine, on_state: Callable[[bool, str | None], None] | None = None) -> None:
    """Listen until cancelled. ``on_state(listening, error)`` runs on the loop thread at each (re)connect, each
    liveness check (every PING_SEC) and each failure; ``error`` is the type name of the exception - all that is logged or shown."""
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def call(fn, *args) -> None:
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:          # the loop is closed: the app is gone
            stop.set()

    def listening(up: bool, error: str | None) -> None:
        if error is not None:
            log.warning("run events: not listening (%s), retrying", error)
        if on_state is not None:
            call(on_state, up, error)

    # the task's cancellation sets stop; a loop closed before the cancellation ran ends the thread as well
    thread = threading.Thread(target=_listen, name="run-events", daemon=True,
                              args=(engine, lambda run_id: call(hub.notify, run_id), lambda: call(hub.wake_all),
                                    listening, stop, lambda: stop.is_set() or loop.is_closed()))
    thread.start()
    try:
        await asyncio.Event().wait()
    finally:
        stop.set()
