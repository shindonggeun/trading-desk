"""Toss Securities Open API, read-only (spec v1.2.14, the copy in the semibot project:
D:/ProjectWorkSpace/stock/docs/toss-openapi-1.2.14.json).

Reads holdings, the per-market listing (for the name search), and quotes / candles for
desk.prices, which caches them and falls back on Yahoo. The regime/trigger loop stays in
semibot; this desk reads semibot's database for that.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import re
import time
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps

import httpx

from .. import outbound
from ..demand import MAX_KEYS as DEMAND_MAX_KEYS
from ..token_store import Issued, TokenStore, TokenUnavailable
from .base import INSTITUTION_KINDS, Bar, Broker, Fill, FlowRecord, Holding, IndexPrice, Listing, MarketDay

BASE_URL = "https://openapi.tossinvest.com"
REFRESH_MARGIN_SEC = 300
# A revoked token is renewed at most once per this many seconds. Two clients on one key revoke
# each other on every renewal; without a pause they would both hammer /oauth2/token (AUTH limit).
RENEW_COOLDOWN_SEC = 60.0

log = logging.getLogger(__name__)
# Pages of 100 closed orders read per review (ORDER_HISTORY rate limit group): 2,000 orders.
FILL_PAGES = 20
RATE_LIMIT_RETRIES = 4
MAX_RETRY_AFTER_SEC = 10.0
_MALFORMED_RESPONSE = "Toss response is malformed"
# #994 GET /api/v1/prices takes 1-200 symbols, comma-joined (spec 1.2.28; past that, 400 invalid-request)
PRICES_MAX_SYMBOLS = 200
# #994 a many-symbol prices ask refused while each of its symbols has a price alone, or answered with one row or
# none while the symbols it left out had a price read alone: quotes() reads one symbol at a time for this long
# (this process), then tries it again. A symbol a refused ask's reads alone blame is asked alone for as long
BATCH_OFF_SEC = 600.0
# #994 symbols kept out of many-symbol asks at once (the soonest due back goes first): as many as the collector's
# wanted keys (desk.demand.MAX_KEYS), so no blamed symbol is let back in while it is still wanted
ALONE_MAX = DEMAND_MAX_KEYS
# #994 the form of a symbol the prices read is asked for (005930, NVDA, BRK.B): anything else - a comma, a
# caret, '=' - is never sent, so one odd ticker cannot get a many-symbol ask refused for every other one
_SYMBOL = re.compile(r"[A-Za-z0-9.\-]{1,20}")


def _symbol_form(symbol: object) -> bool:
    return isinstance(symbol, str) and _SYMBOL.fullmatch(symbol) is not None


class TossError(RuntimeError):
    def __init__(self, *args: object, status: int | None = None, token: bool = False) -> None:
        super().__init__(*args)
        self.status = status     # #994 the HTTP status of a data GET Toss refused (_get), else None
        self.token = token       # #994 raised getting the token for a data GET (_get)
        self.unpriced = False    # #994 quote(): Toss answered, with no usable price for the symbol


def _provider_errors(method):
    """Keep provider and parser details out of protected errors and tracebacks."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        try:
            return await method(self, *args, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL, httpx.StreamError):
            if self._redact_errors:
                raise TossError("Toss request failed") from None
            raise
        except (AttributeError, KeyError, OverflowError, RecursionError, TypeError, ValueError):
            if self._redact_errors:
                raise TossError(_MALFORMED_RESPONSE) from None
            raise

    return wrapped


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value) if value else 1.0
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.5), MAX_RETRY_AFTER_SEC)


def _refuses_many(status: int | None) -> bool:
    """#994 a 4xx that says the many-symbol ask itself was refused - not 401 (the token: _get renews once),
    403 (an IP not registered with Toss) or 429 (the rate limit: _get waits and retries), which one symbol's
    ask would get as well."""
    return status is not None and 400 <= status < 500 and status not in (401, 403, 429)


def _refused_alone(exc: Exception | None) -> bool:
    """#994 a symbol's own read was refused as a many-symbol ask holding it is (a 4xx past 401 · 403 · 429 -
    a 400 one way, a 404 the other is one symbol's fault too), or answered with no price (TossError.unpriced):
    that symbol is to blame for the refusal. Not the token's failure, 401 · 403 · 429 (every symbol's), a 5xx,
    the network's or a malformed answer (redacted, TossErrors of no status): nothing to judge by."""
    return isinstance(exc, TossError) and not exc.token and (_refuses_many(exc.status) or exc.unpriced)


def _unpriced(message: str) -> TossError:
    exc = TossError(message)
    exc.unpriced = True
    return exc


# Toss field names of the investor-trading record (spec 1.2.14) -> ours.
_INVESTOR_FIELDS = {"individual": "individual", "foreigner": "foreigner",
                    "institution": "institution", "otherCorporation": "other_corporation"}
_BREAKDOWN_FIELDS = dict(zip(("financialInvestment", "insurance", "trust", "privateEquityFund", "bank",
                              "otherFinancialInstitution", "pensionFund"), INSTITUTION_KINDS, strict=True))


def _amount(value) -> int | None:
    """A KRW integer sent as a decimal string ("5200000000000"), read exactly (a year of KOSPI is
    in the 10^15 range, near float's integer limit). Negative, NaN, infinite or garbage -> None."""
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return int(number.to_integral_value())


def _pair(node) -> tuple[int, int] | None:
    if not isinstance(node, dict):
        return None
    buy, sell = _amount(node.get("buyAmount")), _amount(node.get("sellAmount"))
    return None if buy is None or sell is None else (buy, sell)


def _flow_record(row) -> FlowRecord | None:
    """One Toss record, or None when a required part is missing (the rest of the page still shows)."""
    if not isinstance(row, dict) or not row.get("date"):
        return None
    investors = {}
    for field, ours in _INVESTOR_FIELDS.items():
        pair = _pair(row.get(field))
        if pair is None:
            return None
        investors[ours] = pair
    breakdown = {}
    raw = row["institution"].get("breakdown")          # _pair above checked institution is a dict
    raw = raw if isinstance(raw, dict) else {}
    for field, ours in _BREAKDOWN_FIELDS.items():
        pair = _pair(raw.get(field))
        if pair is not None:
            breakdown[ours] = pair
    return FlowRecord(date=str(row["date"])[:10], updated_at=str(row.get("updatedAt") or ""),
                      investors=investors, institution_breakdown=breakdown)


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _error_code(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    code = err.get("code") if isinstance(err, dict) else err
    return str(code)[:40] if isinstance(code, str) else ""


class TossBroker(Broker):
    name = "toss"

    def __init__(self, client_id: str, client_secret: str, account_seq: str = "",
                 kosdaq_symbols: list[str] | None = None, http: httpx.AsyncClient | None = None,
                 tokens: TokenStore | None = None, *, redact_errors: bool = False) -> None:
        if not client_id or not client_secret:
            raise TossError("DESK_BROKER=toss needs TOSS_CLIENT_ID and TOSS_CLIENT_SECRET.")
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        # #688 the token shared by the desk's processes (None: this object's own, as before)
        self._slot = tokens.slot("toss", client_secret) if tokens is not None else None
        self._account_seq = account_seq
        self.account_pinned = bool(account_seq)
        self._redact_errors = redact_errors
        self._kosdaq = set(kosdaq_symbols or [])
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=15)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._renewed_at: float | None = None
        self._batch_off_until = 0.0       # #994 quotes() reads one symbol at a time until then (monotonic)
        self._alone_until: dict[str, float] = {}   # #994 SYMBOL -> kept out of many-symbol asks until (monotonic)

    @_provider_errors
    async def _access_token(self, revoked: str | None = None) -> str:
        """The cached token, or a new one. ``revoked`` is a token Toss just refused: Toss keeps
        one valid token per client, so a token issued anywhere else (a one-off script, another
        app on the same key) revokes ours at once. Only that token is dropped - if a concurrent
        call already replaced it, the replacement is reused instead of revoking it in turn."""
        async with self._lock:
            if self._token and self._token != revoked and time.monotonic() < self._expires_at:
                return self._token
            if self._slot is not None:
                return await self._shared_token(revoked)
            if revoked is not None:
                if self._renewed_at is not None and time.monotonic() - self._renewed_at < RENEW_COOLDOWN_SEC:
                    raise TossError("Toss token revoked again within a minute: another client is using "
                                    "the same key (docs/deploy.md section 6)")
                self._renewed_at = time.monotonic()
                log.warning("toss token revoked (issued elsewhere on the same key); renewing")
            if self._redact_errors:
                issued = await self._issue()
                self._token, life_sec = issued.value, issued.life_sec
            else:
                body = (await self._token_response()).json()
                self._token = body["access_token"]
                life_sec = float(body.get("expires_in", 3600))
            self._expires_at = time.monotonic() + max(60.0, life_sec - REFRESH_MARGIN_SEC)
            return self._token

    async def _token_response(self) -> httpx.Response:
        r = await self._http.post(
            "/oauth2/token",
            headers={"Authorization": f"Basic {self._basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
        if r.status_code != 200:
            error = f"Toss token request failed {r.status_code}"
            if not self._redact_errors:
                error = f"{error} {_error_code(r)}".rstrip()
            raise TossError(error)
        return r

    @_provider_errors
    async def _issue(self) -> Issued:
        body = (await self._token_response()).json()
        token = body.get("access_token")
        if not token or not isinstance(token, str):
            raise TossError("Toss token request: no token in the answer")
        life_sec = float(body.get("expires_in", 3600))
        if self._redact_errors:
            max_life = (datetime.max.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
            if not 0 < life_sec < max_life:
                raise TossError(_MALFORMED_RESPONSE)
        return Issued(token, life_sec)

    async def _shared_token(self, revoked: str | None) -> str:
        """#688 the desk's one token (desk.token_store): issued here only in the minting process, under the
        store's lease and gap; other processes take it from the store (and ask for a new one on a 401)."""
        if revoked is not None:
            log.warning("toss token refused (401); taking a new one from the store")
        try:
            token, expires = await self._slot.get(self._issue, refused=revoked, ahead=REFRESH_MARGIN_SEC,
                                                  gap=lambda _refusals: RENEW_COOLDOWN_SEC)
        except TokenUnavailable as exc:
            raise TossError(f"Toss token unavailable: {exc}") from None
        left = (expires - datetime.now(UTC)).total_seconds() - REFRESH_MARGIN_SEC
        self._token, self._expires_at = token, time.monotonic() + max(0.0, left)
        return token

    async def keep_token(self, ahead: float) -> bool:
        """#688 the collector's ``tokens`` job: renew ahead of expiry, or for a reader's request."""
        if self._slot is None:
            return False
        got = await self._slot.keep(self._issue, ahead=ahead, gap=lambda _refusals: RENEW_COOLDOWN_SEC)
        if got is None:
            return False
        async with self._lock:          # this process uses the new token at once (the old one is revoked)
            left = (got[1] - datetime.now(UTC)).total_seconds() - REFRESH_MARGIN_SEC
            self._token, self._expires_at = got[0], time.monotonic() + max(0.0, left)
        return True

    async def _data_token(self, revoked: str | None = None) -> str:
        """#994 _access_token, every failure of it a TossError marked as the token's (TossError.token): quotes()
        reads no more symbols on one. The desk's own TossError keeps its text (already redacted when protected:
        the status, the docs/deploy.md hint); anything else (the token store's Redis or database, a client
        error _provider_errors leaves as it is) is "Toss token unavailable" - its text and traceback dropped
        when protected, chained otherwise."""
        try:
            return await self._access_token(revoked)
        except TossError as exc:
            exc.token = True
            raise
        except Exception as exc:          # every token failure stops the reads (quotes), none leaks
            unavailable = TossError("Toss token unavailable", token=True)
            if self._redact_errors:
                raise unavailable from None
            raise unavailable from exc

    @_provider_errors
    async def _get(self, path: str, headers: dict | None = None, params: dict | None = None):
        token = await self._data_token()
        renewed = False
        attempt = 0
        while True:
            outbound.count("toss")   # #689 per-process broker calls, /api/status
            r = await self._http.get(path, params=params,
                                     headers={"Authorization": f"Bearer {token}", **(headers or {})})
            # 401: revoked by a token issued elsewhere, or expired early. Renew once and retry.
            if r.status_code == 401 and not renewed:
                renewed = True
                token = await self._data_token(revoked=token)
                continue
            # Some endpoints allow 1 call/s (/stocks/all: x-ratelimit-limit 1) and answer 429 with Retry-After.
            if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            attempt += 1
            await asyncio.sleep(_retry_after(r.headers.get("retry-after")))
        if r.status_code != 200:
            if self._redact_errors:
                raise TossError(f"Toss GET request failed {r.status_code}", status=r.status_code)
            raise TossError(f"Toss GET {path} failed {r.status_code}: {r.text[:300]}", status=r.status_code)
        return r.json().get("result")

    async def _account(self) -> str:
        if not self._account_seq:
            accounts = await self._get("/api/v1/accounts") or []
            brokerage = [a for a in accounts if a.get("accountType") == "BROKERAGE"] or accounts
            if not brokerage:
                raise TossError("No Toss account is open for this API key.")
            self._account_seq = str(brokerage[0]["accountSeq"])
        return self._account_seq

    def _yahoo(self, symbol: str, market: str) -> str:
        if market == "KR":
            return f"{symbol}.KQ" if symbol in self._kosdaq else f"{symbol}.KS"
        return symbol

    def _check_text(self, *fields) -> None:
        if self._redact_errors and not all(isinstance(field, str) for field in fields):
            raise TossError(_MALFORMED_RESPONSE)

    def _number(self, value) -> float | None:
        number = _num(value)
        if self._redact_errors and number is not None and not math.isfinite(number):
            raise TossError(_MALFORMED_RESPONSE)
        return number

    @_provider_errors
    async def holdings(self) -> list[Holding]:
        account = await self._account()
        overview = await self._get("/api/v1/holdings", headers={"X-Tossinvest-Account": account}) or {}
        out = []
        for item in overview.get("items", []):
            market = item.get("marketCountry", "")
            symbol = item["symbol"]
            name, currency = item.get("name", symbol), item.get("currency", "")
            self._check_text(symbol, market, name, currency)
            out.append(Holding(
                symbol=symbol,
                yahoo_ticker=self._yahoo(symbol, market),
                name=name,
                market=market,
                currency=currency,
                quantity=self._number(item.get("quantity")) or 0.0,
                average_price=self._number(item.get("averagePurchasePrice")),
                last_price=self._number(item.get("lastPrice")),
            ))
        return out

    @_provider_errors
    async def list_stocks(self, market: str) -> list[Listing]:
        # Batch data refreshed daily on Toss' side; desk.universe caches it for a day.
        rows = await self._get("/api/v1/stocks/all", params={"market": market}) or []
        listings = []
        for row in rows:
            symbol = row.get("symbol")
            if not symbol:
                continue
            name, security_type = row.get("name") or symbol, row.get("securityType", "STOCK")
            self._check_text(symbol, name, security_type)
            listings.append(Listing(symbol=symbol, name=name, market=market, security_type=security_type))
        return listings

    @_provider_errors
    async def quote(self, symbol: str) -> tuple[float, str | None]:
        if not _symbol_form(symbol):        # #994 never sent: no symbol Toss lists has that form
            raise _unpriced("Not a symbol Toss lists")
        rows = _rows(await self._get("/api/v1/prices", params={"symbols": symbol}), "prices")
        for row in rows:
            hit = self._price_row(row)       # #994 the checks of quotes()' rows
            if hit is not None and hit[0] == symbol.upper():
                return hit[1]
        raise _unpriced("Toss has no price for the requested symbol" if self._redact_errors
                        else f"Toss has no price for {symbol}")

    @_provider_errors
    async def quotes(self, symbols: list[str]) -> dict[str, tuple[float, str | None]]:
        """#994 many symbols' quotes, one GET /api/v1/prices per PRICES_MAX_SYMBOLS of them (the MARKET_DATA
        group, as quote()). A symbol not of a Toss symbol's form is never sent. One a many-symbol answer leaves
        out, or gives no usable price, is read alone once (quote()); left out here, it failed both ways - the
        caller does not read it again. A many-symbol GET that fails after _get's 401 / 429 retries fails the
        call: reading each symbol alone then would only multiply the calls Toss refuses. Reads alone stop on a
        401 · 403 · 429 or the token (Broker.quotes).

        The spec says Toss takes many symbols; what a many-symbol ask gets says otherwise only when the symbols'
        own reads clear them (each logged, a status and counts only):
        - refused (a 4xx a single symbol would not get as well): each symbol is read alone. The ones refused the
          same way, or without a price, alone too are to blame - each is asked alone, out of the many-symbol
          asks, for BATCH_OFF_SEC (ALONE_MAX at most), and the many-symbol read stays on. Only when every one
          has a price alone is the many-symbol read off that long; a read alone failing otherwise (a 5xx, the
          network) leaves nothing to judge by - asked as many again next time.
        - answered with one row or none: off when a symbol it left out has a price read alone; else those
          symbols' fault (unlisted, delisted, a typo) and it stays on."""
        asked: dict[str, str] = {}                       # SYMBOL -> the first symbol asked as it
        for symbol in symbols:
            if _symbol_form(symbol):
                asked.setdefault(symbol.upper(), symbol)
        now = time.monotonic()
        alone = [s for s in asked.values() if self._alone_until.get(s.upper(), 0.0) > now]   # blamed before
        order = [s for s in asked.values() if self._alone_until.get(s.upper(), 0.0) <= now]
        got: dict[str, tuple[float, str | None]] = {}    # SYMBOL -> (price, currency)
        refused: list[tuple[int, list[str]]] = []        # (the status, the symbols a many-symbol ask held)
        thin: list[tuple[str, list[str]]] = []           # (a thin answer, the symbols it left out)
        for start in range(0, len(order), PRICES_MAX_SYMBOLS):
            chunk = order[start:start + PRICES_MAX_SYMBOLS]
            if len(chunk) == 1 or not self.batching():
                alone += chunk                           # one symbol: its many-symbol ask is quote()'s
                continue
            try:
                payload = await self._get("/api/v1/prices", params={"symbols": ",".join(chunk)})
            except TossError as exc:
                if not _refuses_many(exc.status):
                    raise
                refused.append((exc.status, chunk))
                alone += chunk
                continue
            rows = _rows(payload, "prices")
            for row in rows:
                hit = self._price_row(row)
                if hit is not None:
                    got.setdefault(hit[0], hit[1])
            left = [s for s in chunk if s.upper() not in got]
            alone += left
            if len(rows) <= 1 and left:
                thin.append((f"answered {len(rows)} row(s) for {len(chunk)} symbols", left))
        stop, failures = None, {}
        if alone:
            read, stop, failures = await self._quotes_alone(alone)
            for symbol, quote in read.items():
                got[symbol.upper()] = quote
            if stop is not None and not got:
                raise stop                               # nothing read: the source refuses this call
        for status, chunk in refused:
            if stop is not None:
                break                                    # reads alone cut short: nothing to judge by
            blamed = [s for s in chunk if s.upper() not in got and _refused_alone(failures.get(s))]
            if blamed:
                self._ask_alone(blamed, f"answered {status} to {len(chunk)} symbols")
            elif all(s.upper() in got for s in chunk):
                self._batch_off(f"answered {status} to {len(chunk)} symbols, each priced alone")
        for why, left in thin:
            priced = sum(1 for s in left if s.upper() in got)
            if priced:                                   # a price alone the many-symbol ask did not give
                self._batch_off(f"{why}, {priced} of the {len(left)} left out priced alone")
        return {s: got[s.upper()] for s in symbols if s.upper() in got}

    def _ask_alone(self, symbols: list[str], why: str) -> None:
        """#994 keep ``symbols`` out of many-symbol asks for BATCH_OFF_SEC, ALONE_MAX at most."""
        now = time.monotonic()
        for symbol in symbols:
            if symbol.upper() not in self._alone_until and len(self._alone_until) >= ALONE_MAX:
                for gone in [s for s, until in self._alone_until.items() if until <= now]:
                    del self._alone_until[gone]
                if len(self._alone_until) >= ALONE_MAX:
                    del self._alone_until[min(self._alone_until, key=self._alone_until.__getitem__)]
            self._alone_until[symbol.upper()] = now + BATCH_OFF_SEC
        # a status and counts only: no symbol, token or answer text
        log.warning("toss many-symbol prices %s: %d of them refused alone too, asked alone for %.0f s",
                    why, len(symbols), BATCH_OFF_SEC)

    def batching(self) -> bool:
        """#994 quotes() asks many symbols in one GET now (not within BATCH_OFF_SEC of its being refused)."""
        return time.monotonic() >= self._batch_off_until

    def _stops_quotes(self, exc: Exception) -> bool:
        """#994 401 (the token refused after one renewal), 403 (an IP not registered with Toss), 429 (the rate
        limit, after _get's retries) or no token: every next symbol's read would meet it too."""
        return isinstance(exc, TossError) and (exc.status in (401, 403, 429) or exc.token)

    def _price_row(self, row) -> tuple[str, tuple[float, str | None]] | None:
        """#994 one row of a prices answer as (SYMBOL, (price, currency)), or None when it holds no usable price
        (quote() and quotes() alike): a finite price above 0, a text currency or none. A bad row costs only its
        own symbol."""
        symbol = row.get("symbol") if isinstance(row, dict) else None
        if not isinstance(symbol, str) or not symbol:
            return None
        try:
            price = self._number(row.get("lastPrice"))
            currency = row.get("currency")
            if currency is not None:
                self._check_text(currency)
        except TossError:
            return None
        if price is None or not math.isfinite(price) or price <= 0:
            return None
        if currency is not None and not isinstance(currency, str):
            return None
        return symbol.upper(), (price, currency)

    def _batch_off(self, why: str) -> None:
        self._batch_off_until = time.monotonic() + BATCH_OFF_SEC
        # every time: a status and counts only - no symbol, token or answer text
        log.warning("toss many-symbol prices %s: quotes are read one symbol at a time for %.0f s",
                    why, BATCH_OFF_SEC)

    @_provider_errors
    async def candles(self, symbol: str, interval: str, count: int) -> list[Bar]:
        payload = await self._get("/api/v1/candles", params={
            "symbol": symbol, "interval": interval, "count": min(max(count, 1), 200), "adjusted": "true"})
        bars = []
        for row in _rows(payload, "candles"):
            values = [self._number(row.get(k)) for k in ("openPrice", "highPrice", "lowPrice", "closePrice")]
            stamp = row.get("timestamp") or row.get("date")
            if None in values or not stamp:
                continue
            bars.append(Bar(_iso(stamp), *values, self._number(row.get("volume")) or 0.0))
        return sorted(bars, key=lambda bar: bar.at)

    @_provider_errors
    async def usd_krw(self) -> float:
        # Refreshed once a minute on Toss' side. midRate (매매기준율), not rate: rate is the
        # buy-side rate with Toss' spread on top, which would overstate a USD holding in won.
        result = await self._get("/api/v1/exchange-rate",
                                 params={"baseCurrency": "USD", "quoteCurrency": "KRW"}) or {}
        rate = self._number(result.get("midRate")) or self._number(result.get("rate"))
        if not rate:
            raise TossError("Toss returned no USD/KRW rate")
        return rate

    async def investor_flow(self, market: str, interval: str, count: int,
                            until: date | None = None) -> tuple[list[FlowRecord], str | None]:
        """GET /api/v1/market-indicators/{KOSPI|KOSDAQ}/investor-trading (Market Indicators group,
        token only - no account header)."""
        params: dict = {"interval": interval, "count": min(max(count, 1), 100)}
        if until is not None:
            params["until"] = until.isoformat()
        result = await self._get(f"/api/v1/market-indicators/{market}/investor-trading", params=params) or {}
        rows = (result.get("records") if isinstance(result, dict) else None) or []
        rows = rows if isinstance(rows, list) else []
        records = [rec for rec in (_flow_record(row) for row in rows) if rec is not None]
        next_until = result.get("nextUntil") if isinstance(result, dict) else None
        return records, (str(next_until)[:10] if next_until else None)

    async def market_calendar(self, market: str, day: date | None = None) -> list[MarketDay]:
        """GET /api/v1/market-calendar/{KR|US} (Market Info group, token only). KR: the integrated
        (KRX+NXT) regular session; US: the regular session. A holiday has no session (null)."""
        # date = KST (KR) / New York (US) date, so "today" in the answer is the day we mean
        params = {"date": day.isoformat()} if day else None
        result = await self._get(f"/api/v1/market-calendar/{market}", params=params) or {}
        days = []
        for key in ("previousBusinessDay", "today", "nextBusinessDay"):
            node = result.get(key) if isinstance(result, dict) else None
            if not isinstance(node, dict) or not node.get("date"):
                continue
            hours = node.get("integrated") if market == "KR" else node
            regular = hours.get("regularMarket") if isinstance(hours, dict) else None
            try:
                day = date.fromisoformat(str(node["date"])[:10])
                opens = closes = None
                if isinstance(regular, dict) and regular.get("startTime") and regular.get("endTime"):
                    opens, closes = (_kst(regular["startTime"]), _kst(regular["endTime"]))
            except ValueError:
                if self._redact_errors:
                    log.warning("Toss %s calendar: unreadable day", market)
                else:
                    log.warning("Toss %s calendar: unreadable day %s", market, node.get("date"))
                continue
            days.append(MarketDay(day=day, opens=opens, closes=closes))
        return days

    async def index_prices(self, symbols: list[str]) -> list[IndexPrice]:
        """GET /api/v1/market-indicators/prices (KOSPI, KOSDAQ, KR_BOND_*)."""
        rows = _rows(await self._get("/api/v1/market-indicators/prices", params={"symbols": ",".join(symbols)}), "prices")
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = self._number(row.get("lastPrice"))
            if row.get("symbol") and price:
                at = row.get("timestamp")
                out.append(IndexPrice(symbol=str(row["symbol"]).upper(), price=price,
                                      at=at if isinstance(at, str) and at else None))
        return out

    async def fills(self, start: date, end: date) -> list[Fill]:
        """Closed orders with a fill, from GET /api/v1/orders?status=CLOSED (read-only), paged by cursor."""
        account = await self._account()
        params = {"status": "CLOSED", "from": start.isoformat(), "to": end.isoformat(), "limit": 100}
        out: list[Fill] = []
        for _ in range(FILL_PAGES):
            page = await self._get("/api/v1/orders", headers={"X-Tossinvest-Account": account}, params=params) or {}
            for order in page.get("orders", []):
                execution = order.get("execution") or {}
                quantity = _num(execution.get("filledQuantity")) or 0.0
                price = _num(execution.get("averageFilledPrice"))
                if quantity <= 0 or price is None:
                    continue                       # canceled / rejected without a fill
                out.append(Fill(
                    order_id=str(order["orderId"]), symbol=order["symbol"], side=order.get("side", ""),
                    quantity=quantity, price=price,
                    fee=(_num(execution.get("commission")) or 0.0) + (_num(execution.get("tax")) or 0.0),
                    currency=order.get("currency", ""),
                    filled_at=execution.get("filledAt") or order.get("orderedAt", ""),
                ))
            if not page.get("hasNext") or not page.get("nextCursor"):
                break
            params = {**params, "cursor": page["nextCursor"]}
        return out


def _kst(value) -> datetime:
    """Toss times carry +09:00; one without an offset is read as KST rather than left naive."""
    moment = datetime.fromisoformat(str(value))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone(timedelta(hours=9)))


def _rows(payload, key: str) -> list[dict]:
    """``result`` may be the list itself or an object holding it under ``key``/``items``."""
    if isinstance(payload, dict):
        payload = payload.get(key, payload.get("items", []))
    return payload if isinstance(payload, list) else []


def _iso(value) -> str:
    text = str(value)
    if len(text) == 10:                       # a bare date on daily bars
        text += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text)   # 3.11+ reads a trailing Z
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds")
