"""#689 How many times this process called a broker's REST API (Toss · KIS), for /api/status.

With the backend split (#688) and the shared cache (#689), the api process should read the collector's
values rather than call the brokers itself - these counters are how that is checked in production
(``GET /api/status`` on each process; they restart from zero with the process). Data requests only (a retry
after 401 · 429 counts again), not token issuance. Counts only: no path,
no account, no token.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import UTC, datetime

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_since = time.time()


def count(name: str) -> None:
    with _lock:
        _counts[name] += 1


def snapshot() -> dict:
    with _lock:
        return {"since": datetime.fromtimestamp(_since, UTC).isoformat(timespec="seconds"), **dict(_counts)}


def reset() -> None:
    global _since
    with _lock:
        _counts.clear()
        _since = time.time()
