"""#689 How many times this process called a broker's REST API (Toss · KIS), for /api/status.

With the backend split (#688) and the shared cache (#689), the api process should read the collector's
values rather than call the brokers itself - these counters are how that is checked in production
(``GET /api/status`` on each process; they restart from zero with the process). Data requests only (a retry
after 401 · 429 counts again), not token issuance. Counts only: no path,
no account, no token.

Each count also lands under a kind (``by_kind``: Toss' endpoint - holdings, quote, candles_1m, usd_krw, ... -
or KIS' TR id), so what is still called directly shows by name. A kind is a code constant: anything else (a
ticker, a market, a query value) counts as ``other``, and the kinds of a name always add up to its total.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from datetime import UTC, datetime

OTHER = "other"
_KIND = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")   # holdings, candles_1m, FHKIF03020100 - not 005930.KS

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_kinds: dict[str, Counter[str]] = {}
_since = time.time()


def count(name: str, kind: str | None = None) -> None:
    label = kind if isinstance(kind, str) and _KIND.fullmatch(kind) else OTHER
    with _lock:
        _counts[name] += 1
        _kinds.setdefault(name, Counter())[label] += 1


def snapshot() -> dict:
    with _lock:
        return {"since": datetime.fromtimestamp(_since, UTC).isoformat(timespec="seconds"), **dict(_counts),
                "by_kind": {name: dict(kinds) for name, kinds in _kinds.items()}}


def reset() -> None:
    global _since
    with _lock:
        _counts.clear()
        _kinds.clear()
        _since = time.time()
