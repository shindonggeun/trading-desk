"""#689 How many times this process called a broker's REST API (Toss · KIS), for /api/status.

With the backend split (#688) and the shared cache (#689), the api process should read the collector's
values rather than call the brokers itself - these counters are how that is checked in production
(``GET /api/status`` on each process; they restart from zero with the process). Data requests only (a retry
after 401 · 429 counts again), not token issuance. Counts only: no path,
no account, no token.

``by_kind`` splits each total by what the calls were for (/api/status ``outbound_by_kind``): Toss' endpoint
(TOSS_KINDS) or KIS' TR id, so what is still called directly shows by name. Only those labels are kept - anything
else (a ticker, a market, a query value) counts as ``other`` - and the kinds of a name add up to its total.
"""

from __future__ import annotations

import re
import threading
import time
from collections import Counter
from datetime import UTC, datetime

OTHER = "other"
# every label desk.broker.toss._kind gives
TOSS_KINDS = frozenset({"accounts", "holdings", "list_stocks", "quote", "candles", "candles_1m", "usd_krw",
                        "index_prices", "investor_flow", "market_calendar", "fills"})
_KIS_TR = re.compile(r"[A-Z]{4,6}[0-9]{6,10}")       # FHKIF03020100 - never a ticker (NVDA, 005930) or KOSPI

_lock = threading.Lock()
_counts: Counter[str] = Counter()
_kinds: dict[str, Counter[str]] = {}
_since = time.time()


def _label(name: str, kind: str | None) -> str:
    if not isinstance(kind, str):
        return OTHER
    if name == "toss" and kind in TOSS_KINDS:
        return kind
    if name == "kis" and _KIS_TR.fullmatch(kind):
        return kind
    return OTHER


def count(name: str, kind: str | None = None) -> None:
    label = _label(name, kind)
    with _lock:
        _counts[name] += 1
        _kinds.setdefault(name, Counter())[label] += 1


def snapshot() -> dict:
    return report()[0]


def by_kind() -> dict[str, dict[str, int]]:
    return report()[1]


def report() -> tuple[dict, dict[str, dict[str, int]]]:
    """(``snapshot``, ``by_kind``) read at once: the kinds add up to these very totals."""
    with _lock:
        return ({"since": datetime.fromtimestamp(_since, UTC).isoformat(timespec="seconds"), **dict(_counts)},
                {name: dict(kinds) for name, kinds in _kinds.items()})


def reset() -> None:
    global _since
    with _lock:
        _counts.clear()
        _kinds.clear()
        _since = time.time()
