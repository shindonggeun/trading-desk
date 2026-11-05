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
import time
from datetime import UTC, date, datetime

import httpx

from .base import Bar, Broker, Fill, Holding, Listing

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


class TossError(RuntimeError):
    pass


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value) if value else 1.0
    except ValueError:
        seconds = 1.0
    return min(max(seconds, 0.5), MAX_RETRY_AFTER_SEC)


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


class TossBroker(Broker):
    name = "toss"

    def __init__(self, client_id: str, client_secret: str, account_seq: str = "",
                 kosdaq_symbols: list[str] | None = None, http: httpx.AsyncClient | None = None) -> None:
        if not client_id or not client_secret:
            raise TossError("DESK_BROKER=toss needs TOSS_CLIENT_ID and TOSS_CLIENT_SECRET.")
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._account_seq = account_seq
        self._kosdaq = set(kosdaq_symbols or [])
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=15)
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()
        self._renewed_at: float | None = None

    async def _access_token(self, revoked: str | None = None) -> str:
        """The cached token, or a new one. ``revoked`` is a token Toss just refused: Toss keeps
        one valid token per client, so a token issued anywhere else (a one-off script, another
        app on the same key) revokes ours at once. Only that token is dropped - if a concurrent
        call already replaced it, the replacement is reused instead of revoking it in turn."""
        async with self._lock:
            if self._token and self._token != revoked and time.monotonic() < self._expires_at:
                return self._token
            if revoked is not None:
                if self._renewed_at is not None and time.monotonic() - self._renewed_at < RENEW_COOLDOWN_SEC:
                    raise TossError("Toss token revoked again within a minute: another client is using "
                                    "the same key (docs/deploy.md section 6)")
                self._renewed_at = time.monotonic()
                log.warning("toss token revoked (issued elsewhere on the same key); renewing")
            r = await self._http.post(
                "/oauth2/token",
                headers={"Authorization": f"Basic {self._basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"},
            )
            if r.status_code != 200:
                raise TossError(f"Toss token request failed {r.status_code}: {r.text[:300]}")
            body = r.json()
            self._token = body["access_token"]
            self._expires_at = time.monotonic() + max(60.0, float(body.get("expires_in", 3600)) - REFRESH_MARGIN_SEC)
            return self._token

    async def _get(self, path: str, headers: dict | None = None, params: dict | None = None):
        token = await self._access_token()
        renewed = False
        attempt = 0
        while True:
            r = await self._http.get(path, params=params,
                                     headers={"Authorization": f"Bearer {token}", **(headers or {})})
            # 401: revoked by a token issued elsewhere, or expired early. Renew once and retry.
            if r.status_code == 401 and not renewed:
                renewed = True
                token = await self._access_token(revoked=token)
                continue
            # Some endpoints allow 1 call/s (/stocks/all: x-ratelimit-limit 1) and answer 429 with Retry-After.
            if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            attempt += 1
            await asyncio.sleep(_retry_after(r.headers.get("retry-after")))
        if r.status_code != 200:
            raise TossError(f"Toss GET {path} failed {r.status_code}: {r.text[:300]}")
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

    async def holdings(self) -> list[Holding]:
        account = await self._account()
        overview = await self._get("/api/v1/holdings", headers={"X-Tossinvest-Account": account}) or {}
        out = []
        for item in overview.get("items", []):
            market = item.get("marketCountry", "")
            out.append(Holding(
                symbol=item["symbol"],
                yahoo_ticker=self._yahoo(item["symbol"], market),
                name=item.get("name", item["symbol"]),
                market=market,
                currency=item.get("currency", ""),
                quantity=_num(item.get("quantity")) or 0.0,
                average_price=_num(item.get("averagePurchasePrice")),
                last_price=_num(item.get("lastPrice")),
            ))
        return out

    async def list_stocks(self, market: str) -> list[Listing]:
        # Batch data refreshed daily on Toss' side; desk.universe caches it for a day.
        rows = await self._get("/api/v1/stocks/all", params={"market": market}) or []
        return [Listing(symbol=r["symbol"], name=r.get("name") or r["symbol"], market=market,
                        security_type=r.get("securityType", "STOCK"))
                for r in rows if r.get("symbol")]

    async def quote(self, symbol: str) -> tuple[float, str | None]:
        rows = _rows(await self._get("/api/v1/prices", params={"symbols": symbol}), "prices")
        for row in rows:
            if str(row.get("symbol", "")).upper() == symbol.upper():
                price = _num(row.get("lastPrice"))
                if price:
                    return price, row.get("currency")
        raise TossError(f"Toss has no price for {symbol}")

    async def candles(self, symbol: str, interval: str, count: int) -> list[Bar]:
        payload = await self._get("/api/v1/candles", params={
            "symbol": symbol, "interval": interval, "count": min(max(count, 1), 200), "adjusted": "true"})
        bars = []
        for row in _rows(payload, "candles"):
            values = [_num(row.get(k)) for k in ("openPrice", "highPrice", "lowPrice", "closePrice")]
            stamp = row.get("timestamp") or row.get("date")
            if None in values or not stamp:
                continue
            bars.append(Bar(_iso(stamp), *values, _num(row.get("volume")) or 0.0))
        return sorted(bars, key=lambda bar: bar.at)

    async def usd_krw(self) -> float:
        # Refreshed once a minute on Toss' side. midRate (매매기준율), not rate: rate is the
        # buy-side rate with Toss' spread on top, which would overstate a USD holding in won.
        result = await self._get("/api/v1/exchange-rate",
                                 params={"baseCurrency": "USD", "quoteCurrency": "KRW"}) or {}
        rate = _num(result.get("midRate")) or _num(result.get("rate"))
        if not rate:
            raise TossError("Toss returned no USD/KRW rate")
        return rate

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
