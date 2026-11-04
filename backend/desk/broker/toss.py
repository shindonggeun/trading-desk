"""Toss Securities Open API, read-only (spec v1.2.14, the copy in the semibot project:
D:/ProjectWorkSpace/stock/docs/toss-openapi-1.2.14.json).

Reads holdings and the per-market listing (for the name search). Market data and the regime/trigger loop already live in
semibot; this desk reads semibot's database rather than polling Toss a second time.
"""

from __future__ import annotations

import asyncio
import base64
import time

import httpx

from .base import Broker, Holding, Listing

BASE_URL = "https://openapi.tossinvest.com"
REFRESH_MARGIN_SEC = 300
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

    async def _access_token(self) -> str:
        async with self._lock:
            if self._token and time.monotonic() < self._expires_at:
                return self._token
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
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            r = await self._http.get(path, params=params,
                                     headers={"Authorization": f"Bearer {token}", **(headers or {})})
            # Some endpoints allow 1 call/s (/stocks/all: x-ratelimit-limit 1) and answer 429 with Retry-After.
            if r.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
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
