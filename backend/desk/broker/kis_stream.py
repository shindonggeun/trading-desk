"""Real-time trade prices from Korea Investment & Securities (KIS Open API) over its websocket (#125).

Toss has no push feed, so the live page polls it every few seconds. With a KIS app key the desk
keeps one websocket to KIS and subscribes the tickers someone is watching:

- KRX stocks and ETFs (``005930.KS``): ``H0STCNT0`` (domestic trades), keyed by the 6-digit code.
- US stocks and ETFs (``NVDA``): ``HDFSCNT0`` keyed ``D`` + exchange (NAS / NYS / AMS) + symbol.
  During the US overnight session (20:00-04:00 New York, the Korean daytime: KIS' "주간거래") the
  key is ``R`` + the day market (BAQ / BAY / BAA) + symbol instead (#238); a subscription is
  re-keyed when the session changes. Pre- and after-market trades come on the ``D`` key.
  KIS gives US trades free with no delay (the docstring of its official sample ``delayed_ccnl``).
- Everything else (indices, FX, futures, crypto, symbols with ``.`` or ``-``) is not subscribed;
  those keep the polling path.

Demand drives the subscriptions: ``want(ticker)`` marks a ticker as watched (the live stream and
the quote board call it every round), a ticker nobody asked for in ``WANT_SEC`` is unsubscribed,
and with nothing watched for ``IDLE_SEC`` the socket is closed - outside market hours the pages
stop asking, so nothing stays subscribed overnight; with nothing watched a failing connection is
not retried either. ``latest(ticker)`` answers only while the socket is up, the ticker subscribed
on it and its last trade younger than ``FRESH_SEC``; anything else is None and the caller polls.

Secrets: the app key and secret only go to KIS over HTTPS (``/oauth2/Approval``) for the websocket's
approval key. KIS serves real-time data on plain ``ws://`` only, so the approval key crosses the
network unencrypted. This app subscribes quotes with it, but the same key could also subscribe the
account's real-time fill notices (they need the HTS ID as well), and it stays valid at KIS for about
a day after it was issued - an accepted risk (docs/security.md §5). It is never logged: errors are
logged by type and KIS' own short message, never with a request, and the websockets library's
frame logging is held above DEBUG.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from datetime import time as clock_time
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)

WS_URL = "ws://ops.koreainvestment.com:21000/tryitout"      # mock trading: port 31000
REST_URL = "https://openapi.koreainvestment.com:9443"        # mock trading: openapivts ...:29443

MAX_SUBS = 40              # KIS allows 41 registrations per session; keep one spare
MAX_WANTED = 2 * MAX_SUBS  # tickers remembered as watched; more are ignored until some lapse
WANT_SEC = 30.0            # a ticker nobody asked for this long is unsubscribed
IDLE_SEC = 300.0           # nothing watched this long: close the socket
FRESH_SEC = 60.0           # an older last trade is not served (a halt, a silently dropped feed)
SILENT_SEC = 90.0          # no frame at all (KIS sends PINGPONG regularly): the socket is dead
RECONCILE_SEC = 1.0
LOOKUPS_PER_ROUND = 2      # US exchange lookups per reconcile round, so a burst cannot stall it
APPROVAL_TTL_SEC = 12 * 3600.0   # KIS keeps an approval key a day; renew well before
APPROVAL_MIN_GAP_SEC = 60.0      # never ask KIS for approval keys in a loop
REJECT_SEC = 600.0               # a ticker KIS refused is not asked again for this long
EXCHANGE_RETRY_SEC = 3600.0
EXCHANGE_CACHE = 512
BACKOFF_START_SEC = 1.0
BACKOFF_MAX_SEC = 60.0

KR = "H0STCNT0"
US = "HDFSCNT0"

# Yahoo's exchange codes -> KIS' US market codes. NYSE Arca / Cboe ETFs (SOXL, TQQQ ...) are AMS on KIS.
_EXCHANGES = {
    "NMS": "NAS", "NGM": "NAS", "NCM": "NAS", "NAS": "NAS", "NASDAQ": "NAS", "NIM": "NAS",
    "NYQ": "NYS", "NYS": "NYS", "NYSE": "NYS",
    "PCX": "AMS", "ASE": "AMS", "AMEX": "AMS", "BTS": "AMS", "ARCA": "AMS", "NYSEARCA": "AMS",
}
# KIS' day-market (overnight session) codes of each US exchange
_DAY_MARKETS = {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}
_REGULAR_MARKETS = {v: k for k, v in _DAY_MARKETS.items()}
_NEW_YORK = ZoneInfo("America/New_York")
_US_SYMBOL = re.compile(r"[A-Z]{1,5}")
_KR_CODE = re.compile(r"[0-9A-Z]{6}")


@dataclass(frozen=True)
class Tick:
    price: float
    change_pct: float | None
    received_at: str           # when the desk got it (UTC ISO)
    seen: float = field(default=0.0, compare=False)   # the same moment on the stream's clock


def market_of(ticker: str) -> str | None:
    """KR / US when KIS can stream the ticker, else None."""
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")):
        return "KR" if _KR_CODE.fullmatch(t[:-3]) else None
    return "US" if _US_SYMBOL.fullmatch(t) else None


def us_session(now: datetime | None = None) -> str | None:
    """The US equity session by the New York clock: regular (09:30-16:00), pre (04:00-09:30),
    post (16:00-20:00), overnight (20:00-04:00, Sunday night to Friday morning) or None (weekend).
    Holidays are not known here: on one nothing trades and the stream simply gets no ticks."""
    ny = (now or datetime.now(UTC)).astimezone(_NEW_YORK)
    t, day = ny.time(), ny.weekday()
    if t >= clock_time(20):
        return "overnight" if day in (6, 0, 1, 2, 3) else None
    if t < clock_time(4):
        return "overnight" if day in (0, 1, 2, 3, 4) else None
    if day >= 5:
        return None
    if t < clock_time(9, 30):
        return "pre"
    return "regular" if t < clock_time(16) else "post"


def us_key(exchange: str, symbol: str, now: datetime | None = None) -> str:
    """The HDFSCNT0 key of a US symbol now: the day market during the overnight session."""
    if us_session(now) == "overnight" and exchange in _DAY_MARKETS:
        return f"R{_DAY_MARKETS[exchange]}{symbol}"
    return f"D{exchange}{symbol}"


def yahoo_exchange(ticker: str) -> str | None:
    """The KIS market code of a US ticker from Yahoo's exchange (blocking; run it in a thread)."""
    import yfinance as yf

    code = (yf.Ticker(ticker).fast_info.get("exchange") or "").upper()
    return _EXCHANGES.get(code)


def _num(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_frame(raw: str) -> list[tuple[str, str, float, float | None]]:
    """A data frame ``0|tr_id|count|f^f^...`` -> [(tr_id, code, price, change_pct)].
    ``code`` is the 6-digit KRX code or the US symbol. Encrypted frames (``1|``, order notices)
    and anything malformed give []."""
    parts = raw.split("|", 3)
    if len(parts) != 4 or parts[0] != "0" or parts[1] not in (KR, US):
        return []
    tr_id = parts[1]
    try:
        count = int(parts[2])
    except ValueError:
        return []
    fields = parts[3].split("^")
    if count < 1 or len(fields) % count:
        return []
    size = len(fields) // count
    out = []
    for i in range(count):
        f = fields[i * size:(i + 1) * size]
        if tr_id == KR:
            if size < 6:
                continue
            code, price, rate = f[0], _num(f[2]), _num(f[5])
        else:
            # KIS documents a leading RSYM ("DNASAAPL") that its own sample omits: it is there when
            # the first field is "D" + 3-letter market + the symbol in the second
            off = 1 if size >= 2 and len(f[0]) > 4 and f[0][:1] in "DR" and f[0][4:] == f[1] else 0
            if size < off + 14:
                continue
            code, price, rate = f[off], _num(f[off + 10]), _num(f[off + 13])
        if price and price > 0:
            out.append((tr_id, code.strip().upper(), price, rate))
    return out


class ApprovalError(RuntimeError):
    pass


class ApprovalDue(ApprovalError):
    """The approval key reached APPROVAL_TTL_SEC: a planned reconnect, not a failure."""


class KisStream:
    """One KIS websocket shared by every page; see the module docstring."""

    def __init__(self, app_key: str, app_secret: str, *, ws_url: str = WS_URL, rest_url: str = REST_URL,
                 connect: Callable | None = None, http: httpx.AsyncClient | None = None,
                 exchange_of: Callable[[str], Awaitable[str | None]] | None = None,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] | None = None):
        self._key, self._secret = app_key, app_secret
        self._now = now or (lambda: datetime.now(UTC))     # the wall clock for the US session
        self._ws_url, self._rest_url = ws_url, rest_url.rstrip("/")
        self._connect = connect
        self._http = http
        self._own_http = http is None
        self._exchange_of = exchange_of or (lambda t: asyncio.to_thread(yahoo_exchange, t))
        self._clock = clock
        self._wanted: dict[str, float] = {}           # ticker -> last asked (clock)
        self._demand = asyncio.Event()
        self._subs: dict[str, tuple[str, str]] = {}    # ticker -> (tr_id, tr_key) on this socket
        self._codes: dict[tuple[str, str], str] = {}   # (tr_id, code) -> ticker
        self._ticks: dict[str, Tick] = {}
        self._rejected: dict[str, float] = {}          # ticker -> until (clock)
        self._exchanges: OrderedDict[str, tuple[float, str | None]] = OrderedDict()
        self._approval: tuple[float, str] | None = None
        self._approval_asked = -APPROVAL_MIN_GAP_SEC
        self._refused_key = False
        self.connected = False
        # #143: what GET /api/live/kis shows. Kept across reconnects; nothing here is a key.
        self._last_tick: dict[str, str | None] = {"KR": None, "US": None}   # wall-clock ISO when the last trade frame arrived
        self._connected_at: str | None = None
        self._last_drop: dict | None = None            # {at, error (exception type)}
        self._last_refusal: str | None = None          # when KIS last refused the approval key

    # --- what the pages use ------------------------------------------------------------------

    def want(self, ticker: str) -> None:
        if market_of(ticker) is None:
            return
        t = ticker.upper()
        if t not in self._wanted:
            self._prune_wants()
            if len(self._wanted) >= MAX_WANTED:
                return                   # a flood of new symbols: those keep polling
        self._wanted[t] = self._clock()
        self._demand.set()

    def subscribed(self, ticker: str) -> bool:
        """Subscribed on the open socket now (a trade may not have come yet)."""
        return self.connected and ticker.upper() in self._subs

    def latest(self, ticker: str, max_age: float = FRESH_SEC) -> Tick | None:
        """The last trade while the socket is up and the ticker subscribed, if no older than
        ``max_age`` seconds (#257: off hours a thin ETF trades minutes apart)."""
        t = ticker.upper()
        tick = self._ticks.get(t) if self.connected and t in self._subs else None
        return tick if tick is not None and self._clock() - tick.seen <= max_age else None

    def status(self) -> dict:
        """Connection health for the owner (GET /api/live/kis). Never the app key, secret or
        approval key: only counts, ticker symbols, times and exception type names."""
        # read-only (no pruning): call it on the loop thread, where the stream changes these dicts
        now = self._clock()
        return {"connected": self.connected, "subscribed": len(self._subs),
                "watched": sum(1 for at in self._wanted.values() if now - at <= WANT_SEC),
                "tickers": sorted(self._subs), "last_tick": dict(self._last_tick),
                "refused": sorted(t for t, until in self._rejected.items() if until > now),
                "approval_refused_at": self._last_refusal,
                "connected_at": self._connected_at if self.connected else None,
                "last_drop": dict(self._last_drop) if self._last_drop else None}

    # --- the connection ----------------------------------------------------------------------

    async def run(self) -> None:
        """Forever: wait for demand, connect, keep subscriptions in step, reconnect with backoff."""
        backoff = BACKOFF_START_SEC
        while True:
            await self._demand.wait()
            if not self._live_wants():
                self._demand.clear()              # the watchers left while this waited
                continue
            failed = False
            try:
                key = await self._approval_key()
                connect = self._connect or _default_connect
                async with connect(self._ws_url) as ws:
                    self.connected = True
                    self._connected_at = _now_iso()
                    backoff = BACKOFF_START_SEC
                    log.info("kis stream connected")
                    await self._session(ws, key)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - logged by type only: messages may carry a request
                failed = True
                if not isinstance(exc, ApprovalDue):          # a planned key rotation is not a drop
                    self._last_drop = {"at": _now_iso(), "error": type(exc).__name__}
                log.warning("kis stream dropped: %s", type(exc).__name__)
            finally:
                self.connected = False
                self._subs.clear()
                self._codes.clear()
                self._ticks.clear()
            if failed:
                if not self._live_wants():
                    self._demand.clear()          # nobody watches: do not retry until someone does
                    continue
                await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
                backoff = min(backoff * 2, BACKOFF_MAX_SEC)

    async def aclose(self) -> None:
        if self._own_http and self._http is not None:
            await self._http.aclose()

    async def _approval_key(self) -> str:
        now = self._clock()
        if self._approval and now < self._approval[0]:
            return self._approval[1]
        if now - self._approval_asked < APPROVAL_MIN_GAP_SEC:
            raise ApprovalError("waiting before asking KIS for another approval key")
        self._approval_asked = now
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        res = await self._http.post(f"{self._rest_url}/oauth2/Approval", json={
            "grant_type": "client_credentials", "appkey": self._key, "secretkey": self._secret})
        if res.status_code != 200:
            raise ApprovalError(f"approval key: HTTP {res.status_code}")
        try:
            key = res.json().get("approval_key")
        except ValueError:
            key = None
        if not key or not isinstance(key, str):
            raise ApprovalError("approval key: no key in the answer")
        self._approval = (now + APPROVAL_TTL_SEC, key)
        return key

    def _prune_wants(self) -> None:
        now = self._clock()
        for t in [t for t, at in self._wanted.items() if now - at > WANT_SEC]:
            del self._wanted[t]
        for t in [t for t, until in self._rejected.items() if until <= now]:
            del self._rejected[t]

    def _live_wants(self) -> list[str]:
        self._prune_wants()
        return sorted(self._wanted, key=self._wanted.get, reverse=True)[:MAX_SUBS]

    def _drop(self, ticker: str) -> tuple[str, str] | None:
        target = self._subs.pop(ticker, None)
        self._ticks.pop(ticker, None)
        if target:
            self._codes.pop(_code_key(target), None)
        return target

    async def _session(self, ws, key: str) -> None:
        reader = asyncio.create_task(self._read(ws))
        started, idle_since = self._clock(), None
        try:
            while not reader.done():
                now = self._clock()
                if self._refused_key or now - started >= APPROVAL_TTL_SEC:
                    # KIS refused the approval key, or it is due: reconnect with a new one
                    refused, self._refused_key = self._refused_key, False
                    self._approval = None
                    raise ApprovalError("approval key refused") if refused else ApprovalDue("approval key due")
                wanted = self._live_wants()
                if wanted:
                    idle_since = None
                else:
                    idle_since = idle_since or now
                    if now - idle_since >= IDLE_SEC:
                        self._demand.clear()
                        log.info("kis stream idle: closing")
                        return
                for t in [t for t in self._subs if t not in wanted or self._stale_key(t)]:
                    tr_id, tr_key = self._drop(t)       # re-keyed ones subscribe again below
                    await ws.send(json.dumps(_message(key, "2", tr_id, tr_key)))
                lookups = 0
                for t in wanted:
                    if t in self._subs or self._rejected.get(t, 0) > now:
                        continue
                    if market_of(t) == "US" and t not in self._exchanges:
                        if lookups >= LOOKUPS_PER_ROUND:
                            continue                  # next round
                        lookups += 1
                    target = await self._target(t)
                    if target is None:
                        self._rejected[t] = now + REJECT_SEC
                        continue
                    if _code_key(target) in self._codes:
                        continue    # 005930.KS and 005930.KQ are one KIS code: the first one streams
                    self._subs[t] = target
                    self._codes[_code_key(target)] = t
                    await ws.send(json.dumps(_message(key, "1", *target)))
                await asyncio.sleep(RECONCILE_SEC)
            reader.result()               # the reader's error ends the session
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader

    async def _target(self, ticker: str) -> tuple[str, str] | None:
        if market_of(ticker) == "KR":
            return KR, ticker[:-3]
        hit = self._exchanges.get(ticker)
        if hit is None or (hit[1] is None and self._clock() - hit[0] > EXCHANGE_RETRY_SEC):
            try:
                code = await asyncio.wait_for(self._exchange_of(ticker), 10.0)
            except Exception as exc:  # noqa: BLE001 - Yahoo down: keep polling this one
                log.info("kis stream: no exchange for %s (%s)", ticker, type(exc).__name__)
                code = None
            hit = (self._clock(), code)
            self._exchanges[ticker] = hit
            while len(self._exchanges) > EXCHANGE_CACHE:
                self._exchanges.popitem(last=False)
        self._exchanges.move_to_end(ticker)
        return (US, us_key(hit[1], ticker, self._now())) if hit[1] else None

    def _stale_key(self, ticker: str) -> bool:
        """A US subscription whose key belongs to another session (overnight vs the rest)."""
        tr_id, tr_key = self._subs[ticker]
        if tr_id != US or len(tr_key) < 5:
            return False
        market = tr_key[1:4]
        exchange = _REGULAR_MARKETS.get(market, market)
        return us_key(exchange, ticker, self._now()) != tr_key

    async def _read(self, ws) -> None:
        while True:
            raw = await asyncio.wait_for(ws.recv(), SILENT_SEC)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            if raw[:1] in ("0", "1"):
                seen = self._clock()
                stamp = datetime.now(UTC).isoformat(timespec="seconds")
                for tr_id, code, price, rate in parse_frame(raw):
                    ticker = self._codes.get((tr_id, code))
                    if ticker and ticker in self._subs:
                        self._ticks[ticker] = Tick(price, rate, stamp, seen)
                        self._last_tick["KR" if tr_id == KR else "US"] = stamp
                continue
            self._control(raw)
            if '"PINGPONG"' in raw:
                await ws.pong(raw.encode("utf-8"))

    def _control(self, raw: str) -> None:
        """Subscription answers: a refused ticker (bad code, over the limit) is not asked again for
        a while; a refused approval key ends the session so the next one gets a new key."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        header, body = msg.get("header") or {}, msg.get("body") or {}
        if not body or body.get("rt_cd") in (None, "0"):
            return
        reason = str(body.get("msg1") or "")[:80]
        if "approval" in reason.lower():
            log.warning("kis stream: approval key refused (%s)", reason)
            self._refused_key = True
            self._last_refusal = _now_iso()               # kept: the flag above resets on reconnect
            return
        tr_key = str(header.get("tr_key") or "")
        for ticker, (_tr, key) in list(self._subs.items()):
            if key == tr_key:
                self._drop(ticker)
                self._rejected[ticker] = self._clock() + REJECT_SEC
                log.warning("kis stream refused %s: %s", ticker, reason)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _code_key(target: tuple[str, str]) -> tuple[str, str]:
    """(tr_id, tr_key) -> (tr_id, the code a data frame carries)."""
    tr_id, tr_key = target
    return (tr_id, tr_key if tr_id == KR else tr_key[4:])


def _message(key: str, tr_type: str, tr_id: str, tr_key: str) -> dict:
    return {"header": {"approval_key": key, "custtype": "P", "tr_type": tr_type, "content-type": "utf-8"},
            "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}}}


def _default_connect(url: str):
    import websockets

    # KIS sends its own PINGPONG; the library's pings are extra traffic it may not answer
    return websockets.connect(url, ping_interval=None, open_timeout=10, close_timeout=5, max_size=2**20)


def make(settings) -> KisStream | None:
    """The stream when both KIS keys are set, else None (the polling path stays)."""
    if not settings.kis_app_key or not settings.kis_app_secret:
        return None
    rest_url = settings.kis_rest_url or REST_URL
    ws_url = settings.kis_ws_url or WS_URL
    if not rest_url.startswith("https://") or not ws_url.startswith(("ws://", "wss://")):
        # the app key and secret never go out unencrypted
        log.warning("kis stream off: DESK_KIS_REST_URL must be https:// and DESK_KIS_WS_URL ws(s)://")
        return None
    # the websockets library logs every frame at DEBUG, the approval key included
    logging.getLogger("websockets").setLevel(logging.INFO)
    return KisStream(settings.kis_app_key, settings.kis_app_secret, ws_url=ws_url, rest_url=rest_url)
