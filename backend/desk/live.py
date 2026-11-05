"""The live chart's feed (issue #26): the current quote and the newest 1-minute bars of a ticker,
pushed over SSE while its market is open.

Toss has no push feed, so this polls desk.prices. PriceSource caches quotes (~10 s) and candles
(~30 s) per ticker on the server, so any number of open tabs on the same ticker cost Toss one
call per window — the MARKET_DATA rate limit is the cache's job, not each stream's.

Outside regular hours the stream sends one ``closed`` frame and then only checks the clock, once a
minute: no quote or candle calls while nothing trades. Crypto (-USD) never closes. Exchange
holidays are not known here (Toss' market calendar is not wired yet): on a holiday the stream
keeps polling and simply repeats the last price.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from .estimate import KRX, US, session_of

log = logging.getLogger(__name__)

TICK_SEC = 5.0
# While the market is closed the stream fetches nothing, but still re-checks the session this
# often (docs/security.md: a logout ends an open stream within 15 s).
CLOSED_SEC = 15.0
# After this many rounds in a row with neither quote nor bars (a typo ticker, Toss 403 with Yahoo
# down), poll this much slower: failures are not cached, so each round would call out again.
FAIL_ROUNDS = 3
FAILING_SEC = 60.0
BARS = 3            # the newest bars each frame carries: enough to finish the bar that just closed
MAX_STREAMS = 6     # open live streams per session (each one polls)


def market_open(ticker: str, now: datetime | None = None) -> bool:
    """Regular session of the ticker's exchange (KRX for .KS/.KQ, else US); crypto is always open."""
    t = ticker.upper()
    if t.endswith("-USD"):
        return True
    if t.endswith("=X"):   # FX: Sunday 17:00 to Friday 17:00 New York time
        ny = (now or datetime.now(UTC)).astimezone(US.zone)
        day, hour = ny.weekday(), ny.hour
        return not (day == 5 or (day == 4 and hour >= 17) or (day == 6 and hour < 17))
    # Korean indices (^KS11 KOSPI, ^KQ11 KOSDAQ, ^KS200) trade in KRX hours
    session = KRX if t.startswith(("^KS", "^KQ")) else session_of(ticker)
    local = (now or datetime.now(UTC)).astimezone(session.zone)
    if local.weekday() >= 5:
        return False
    opens, closes = session.hours(local.date())
    return opens <= local.time() < closes


async def frame(prices, ticker: str) -> dict:
    """{"state": "open", "quote", "bars"} — either part may be missing when its source failed."""
    out: dict = {"state": "open", "ticker": ticker}
    # The page gets a fixed word; the details (upstream URLs, hosts) stay in the server log.
    try:
        out["quote"] = (await prices.quote(ticker)).to_dict()
    except Exception as exc:  # noqa: BLE001 - the next tick tries again
        log.warning("live quote for %s failed: %s", ticker, exc)
        out["quote_error"] = "unavailable"
    try:
        candles = await prices.candles(ticker, "1m", 30)
        out["bars"] = candles["bars"][-BARS:]
        out["source"] = candles["source"]
    except Exception as exc:  # noqa: BLE001
        log.warning("live bars for %s failed: %s", ticker, exc)
        out["bars_error"] = "unavailable"
    return out
