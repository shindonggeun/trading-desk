"""Process-wide ownership for the all/collector broker token issuer.

Use blocking methods off the event loop. The owner must stop issuing and stop
its collector jobs when check() returns false. In-flight provider requests
still rely on the token store's per-token lease.
"""

from contextlib import suppress
from threading import Lock

from sqlalchemy.engine import Engine

from . import run_events

LOCK_KEY = 688_000_002
APP_NAME = "desk-broker-issuer"


class BrokerIssuerLock:
    """Hold an advisory lock on a dedicated session until shutdown.

    SQLite's single-process local mode needs no PostgreSQL lock. After losing
    the session, this instance cannot take ownership again.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._conn = None
        self._held = False
        self._closed = False
        self._mutex = Lock()

    def acquire(self) -> bool:
        """Try once. Contention or a connection failure leaves issuance disabled."""
        with self._mutex:
            if self._closed:
                return False
            if self._held:
                return self._check()
            if self._engine.dialect.name == "sqlite":
                self._held = True
                return True
            if not run_events.shared(self._engine):
                return False

            conn = None
            try:
                conn = run_events._connect(self._engine, APP_NAME)
                held = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
                if held:
                    self._conn = conn
                    self._held = True
                    return True
            except Exception:  # noqa: BLE001 - driver failures deny ownership without exposing connection details
                self._closed = True
            if conn is not None:
                with suppress(Exception):
                    conn.close()
            return False

    def check(self) -> bool:
        """Check the original session without reconnecting or reacquiring."""
        with self._mutex:
            return self._check()

    def _check(self) -> bool:
        if self._conn is not None:
            try:
                self._conn.execute("SELECT 1")
            except Exception:  # noqa: BLE001 - a broken session can no longer prove ownership
                self._close()
        return self._held

    def release(self) -> None:
        with self._mutex:
            self._close()

    def _close(self) -> None:
        self._closed = True
        self._held = False
        conn, self._conn = self._conn, None
        if conn is not None:
            with suppress(Exception):
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
            with suppress(Exception):
                conn.close()
