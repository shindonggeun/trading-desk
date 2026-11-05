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

from datetime import UTC, datetime

from .estimate import session_of

TICK_SEC = 5.0
CLOSED_SEC = 60.0
BARS = 3            # the newest bars each frame carries: enough to finish the bar that just closed


def market_open(ticker: str, now: datetime | None = None) -> bool:
    """Regular session of the ticker's exchange (KRX for .KS/.KQ, else US); crypto is always open."""
    if ticker.upper().endswith("-USD"):
        return True
    session = session_of(ticker)
    local = (now or datetime.now(UTC)).astimezone(session.zone)
    if local.weekday() >= 5:
        return False
    opens, closes = session.hours(local.date())
    return opens <= local.time() < closes


async def frame(prices, ticker: str) -> dict:
    """{"state": "open", "quote", "bars"} — either part may be missing when its source failed."""
    out: dict = {"state": "open", "ticker": ticker}
    try:
        out["quote"] = (await prices.quote(ticker)).to_dict()
    except Exception as exc:  # noqa: BLE001 - the next tick tries again
        out["quote_error"] = str(exc)
    try:
        candles = await prices.candles(ticker, "1m", 30)
        out["bars"] = candles["bars"][-BARS:]
        out["source"] = candles["source"]
    except Exception as exc:  # noqa: BLE001
        out["bars_error"] = str(exc)
    return out
