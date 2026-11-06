"""KOSPI200 futures (the front month) next to the spot investor flow (#160), from the KIS Open API.

The spot panel (desk.market_flow) reads Toss, which has no derivatives. Futures are where
foreigners and institutions lean first, so the dashboard shows the front month's price, open
interest, basis, and the net contracts by investor class, and flags a foreign spot/futures split
(buying the stocks while selling the futures, or the other way).

Three KIS REST calls, cached together:
- ``inquire-price`` (FHMIF10000000): price, open interest and its change, market / theoretical basis,
  last trading day and days left;
- ``inquire-investor-time-by-market`` (FHPTJ04030000, HTS [0403]): investor net contracts and value
  for a market code. The futures codes (``INVESTOR_MARKET``) come from KIS' list for that screen and
  are checked on the first live answer: an answer that does not look like futures is reported, not shown.
- the spot foreign net from desk.market_flow for the split.

Secrets: the app key and secret go to KIS over HTTPS only (``/oauth2/tokenP``); the access token
lives in process memory for a day. Neither is logged nor returned (docs/security.md, KIS).
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, date, datetime, timedelta

import httpx

from .market_flow import KST, _ttl_until_open, market_open

log = logging.getLogger(__name__)

PRICE_PATH = "/uapi/domestic-futureoption/v1/quotations/inquire-price"
PRICE_TR = "FHMIF10000000"
INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
INVESTOR_TR = "FHPTJ04030000"
# HTS [0403] market / sector codes for KOSPI200 futures (KIS' code list for the screen; the sample
# in the KIS repository shows the stock-futures pair "999" / "S001").
INVESTOR_MARKET = ("K2I", "F001")
TOKEN_PATH = "/oauth2/tokenP"
TOKEN_TTL_SEC = 20 * 3600.0       # KIS issues a token for a day; renew a little before
TOKEN_MIN_GAP_SEC = 60.0          # KIS allows one issue a minute (and notifies on each)
TIMEOUT_SEC = 8.0
CACHE_OPEN_SEC = 30.0
CACHE_CLOSED_SEC = 600.0
CACHE_FAIL_SEC = 30.0
# KRX year letters for derivative codes: 2006 = A, I · O · U skipped (2025 = W, 2026 = X).
_YEARS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOU"]
_EXPIRED_TOKEN = ("EGW00123", "EGW00121")


class KisError(RuntimeError):
    """A KIS answer that is not a success: its short code and message only (no request echo)."""


def _year_letter(year: int) -> str:
    return _YEARS[(year - 2006) % len(_YEARS)]


def second_thursday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(3 - first.weekday()) % 7 + 7)


def front_month(today: date) -> dict:
    """The quarterly KOSPI200 contract trading now: it rolls the day after its last trading day
    (the second Thursday of March, June, September, December)."""
    year, month = today.year, today.month
    while True:
        quarter = ((month - 1) // 3 + 1) * 3
        expiry = second_thursday(year, quarter)
        if today <= expiry:
            return {"code": f"101{_year_letter(year)}{quarter:02d}", "expiry": expiry.isoformat(),
                    "days_left": (expiry - today).days}
        month = quarter + 1
        if month > 12:
            year, month = year + 1, 1


def _num(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _signed(value, sign) -> float | None:
    """KIS gives some changes unsigned with a separate sign code (4 · 5 = down)."""
    v = _num(value)
    if v is None:
        return None
    return -abs(v) if str(sign) in ("4", "5") else v


def normalize_price(out1: dict, out3: dict | None, front: dict) -> dict:
    price = _num(out1.get("futs_prpr"))
    spot = _num((out3 or {}).get("bstp_nmix_prpr"))
    basis = _num(out1.get("mrkt_basis"))
    if basis is None and price is not None and spot is not None:
        basis = round(price - spot, 2)
    return {
        "code": front["code"], "name": out1.get("hts_kor_isnm") or "코스피200 선물",
        "expiry": _iso_day(out1.get("futs_last_tr_date")) or front["expiry"],
        "days_left": int(_num(out1.get("hts_rmnn_dynu")) or front["days_left"]),
        "price": price,
        "change": _signed(out1.get("futs_prdy_vrss"), out1.get("prdy_vrss_sign")),
        "change_pct": _signed(out1.get("futs_prdy_ctrt"), out1.get("prdy_vrss_sign")),
        "volume": _num(out1.get("acml_vol")),
        "open_interest": _num(out1.get("hts_otst_stpl_qty")),
        "open_interest_change": _num(out1.get("otst_stpl_qty_icdc")),
        "market_basis": basis,                          # futures - KOSPI200
        "theory_basis": _num(out1.get("basis")),        # theoretical price - KOSPI200
        "theory_price": _num(out1.get("hts_thpr")),
        "spot_index": spot,
        # contango: futures above spot (carry, or a bullish lean); backwardation: below
        "structure": None if basis is None else ("contango" if basis > 0 else "backwardation" if basis < 0 else "flat"),
    }


def _iso_day(text) -> str | None:
    if not text or len(str(text)) != 8:
        return None
    s = str(text)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


_CLASSES = {"foreign": "frgn", "individual": "prsn", "institution": "orgn"}


def normalize_investors(output) -> dict | None:
    """Net contracts and value (KIS: million won) per class, the latest row of the answer."""
    rows = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return None
    row = rows[0]
    out = {}
    for name, prefix in _CLASSES.items():
        qty, value = _num(row.get(f"{prefix}_ntby_qty")), _num(row.get(f"{prefix}_ntby_tr_pbmn"))
        if qty is None and value is None:
            continue
        out[name] = {"net_contracts": qty, "net_value_mn": value}
    return out or None


def _looks_like_futures(investors: dict | None, price: dict) -> bool:
    """Net contracts in a futures market are a small multiple of the day's volume; a stock market
    answer (shares) is orders of magnitude bigger. Guards a wrong market code."""
    if not investors or not price.get("volume"):
        return bool(investors)
    biggest = max(abs(v.get("net_contracts") or 0) for v in investors.values())
    return biggest <= 3 * price["volume"]


class KisRest:
    """KIS REST with its access token cached in memory (one issue a minute at most)."""

    def __init__(self, app_key: str, app_secret: str, rest_url: str, *, http: httpx.AsyncClient | None = None,
                 clock=time.monotonic) -> None:
        self._key, self._secret, self._url = app_key, app_secret, rest_url.rstrip("/")
        self._http = http
        self._own_http = http is None
        self._clock = clock
        self._token: tuple[float, str] | None = None     # (expires at, token)
        self._asked = -TOKEN_MIN_GAP_SEC
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:                           # never the key, the secret or the token
        return f"KisRest({self._url})"

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=TIMEOUT_SEC)
        return self._http

    async def token(self) -> str:
        async with self._lock:
            now = self._clock()
            if self._token and now < self._token[0]:
                return self._token[1]
            if now - self._asked < TOKEN_MIN_GAP_SEC:
                raise KisError("waiting before asking KIS for another access token")
            self._asked = now
            res = await (await self._client()).post(f"{self._url}{TOKEN_PATH}", json={
                "grant_type": "client_credentials", "appkey": self._key, "appsecret": self._secret})
            if res.status_code != 200:
                raise KisError(f"access token: HTTP {res.status_code}")
            try:
                body = res.json()
            except ValueError:
                body = {}
            token = body.get("access_token")
            if not token or not isinstance(token, str):
                raise KisError("access token: none in the answer")
            life = min(float(body.get("expires_in") or TOKEN_TTL_SEC), TOKEN_TTL_SEC)
            self._token = (now + life, token)
            return token

    async def get(self, path: str, tr_id: str, params: dict) -> dict:
        for attempt in (0, 1):
            token = await self.token()
            res = await (await self._client()).get(f"{self._url}{path}", params=params, headers={
                "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
                "appkey": self._key, "appsecret": self._secret, "tr_id": tr_id, "custtype": "P"})
            try:
                body = res.json()
            except ValueError:
                body = {}
            code = str(body.get("msg_cd") or "")
            if attempt == 0 and (res.status_code == 401 or code in _EXPIRED_TOKEN):
                self._token = None                       # revoked early: one fresh token, once
                continue
            if res.status_code != 200 or str(body.get("rt_cd")) != "0":
                msg = str(body.get("msg1") or "").strip()[:80]
                raise KisError(f"{tr_id}: HTTP {res.status_code} {code} {msg}".strip())
            return body
        raise KisError(f"{tr_id}: token refused")

    async def aclose(self) -> None:
        if self._own_http and self._http is not None:
            await self._http.aclose()


def make(settings) -> KisRest | None:
    """KIS REST when both keys are set and the URL is https (the keys never go out in the clear)."""
    if not settings.kis_app_key or not settings.kis_app_secret:
        return None
    from .broker.kis_stream import REST_URL

    url = settings.kis_rest_url or REST_URL
    if not url.startswith("https://"):
        log.warning("futures flow off: DESK_KIS_REST_URL must be https://")
        return None
    return KisRest(settings.kis_app_key, settings.kis_app_secret, url)


class FuturesFlow:
    """The cached front-month picture for /api/market/futures."""

    def __init__(self, rest: KisRest | None, spot=None, clock=time.monotonic, now=lambda: datetime.now(UTC)) -> None:
        self.rest = rest
        self.spot = spot                                 # desk.market_flow.MarketFlow (for the split)
        self._clock, self._now = clock, now
        self._cache: tuple[float, float, dict] | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> dict:
        async with self._lock:
            if self._cache and self._clock() - self._cache[0] < self._cache[1]:
                return self._cache[2]
            value, ok = await self._fetch()
            now = self._now()
            ttl = CACHE_FAIL_SEC if not ok else CACHE_OPEN_SEC if market_open(now) else _ttl_until_open(now, CACHE_CLOSED_SEC)
            self._cache = (self._clock(), ttl, value)
            return value

    async def _fetch(self) -> tuple[dict, bool]:
        now = self._now()
        front = front_month(now.astimezone(KST).date())
        base = {"source": "kis", "fetched_at": now.astimezone(KST).isoformat(timespec="seconds"),
                "front": front, "market_open": market_open(now)}
        if self.rest is None:
            return {**base, "supported": False, "error": "한국투자증권 키가 없어 선물 현황을 받을 수 없습니다",
                    "price": None, "investors": None, "split": None}, True
        price_res, inv_res, spot_res = await asyncio.gather(
            self.rest.get(PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": front["code"]}),
            self.rest.get(INVESTOR_PATH, INVESTOR_TR,
                          {"FID_INPUT_ISCD": INVESTOR_MARKET[0], "FID_INPUT_ISCD_2": INVESTOR_MARKET[1]}),
            self.spot.get("KOSPI", "1d", 1) if self.spot is not None else _none(),
            return_exceptions=True)
        if isinstance(price_res, BaseException):
            log.warning("futures price failed: %s", _short(price_res))
            return {**base, "supported": True, "error": f"선물 시세를 받지 못했습니다 — {_short(price_res)}",
                    "price": None, "investors": None, "split": None}, False
        price = normalize_price(price_res.get("output1") or {}, price_res.get("output3"), front)
        investors, inv_error = None, None
        if isinstance(inv_res, BaseException):
            log.warning("futures investors failed: %s", _short(inv_res))
            inv_error = f"투자자별 동향을 받지 못했습니다 — {_short(inv_res)}"
        else:
            investors = normalize_investors(inv_res.get("output"))
            if investors is not None and not _looks_like_futures(investors, price):
                log.warning("futures investors: the answer for %s does not look like futures", INVESTOR_MARKET)
                investors, inv_error = None, "투자자별 동향의 시장 코드가 선물과 맞지 않습니다(확인 필요)"
        split = foreign_split(spot_res if isinstance(spot_res, dict) else None, investors)
        return {**base, "supported": True, "error": None, "price": price, "investors": investors,
                "investors_error": inv_error, "investors_unit": {"value": "백만원", "qty": "계약"}, "split": split}, True

    async def aclose(self) -> None:
        if self.rest is not None:
            await self.rest.aclose()


async def _none():
    return None


def _short(exc: BaseException) -> str:
    return str(exc)[:100] if isinstance(exc, KisError) else type(exc).__name__


def foreign_split(spot: dict | None, investors: dict | None) -> dict | None:
    """Foreign spot net (KRW) vs futures net (contracts) today: opposite signs = a split."""
    if not investors or "foreign" not in investors:
        return None
    fut = investors["foreign"].get("net_contracts")
    record = ((spot or {}).get("records") or [None])[0]
    spot_net = ((record or {}).get("investors") or {}).get("foreign", {}).get("net") if record else None
    if fut is None or spot_net is None:
        return {"spot_net": spot_net, "futures_net_contracts": fut, "split": False, "kind": None}
    if spot_net > 0 and fut < 0:
        kind = "spot_buy_futures_sell"
    elif spot_net < 0 and fut > 0:
        kind = "spot_sell_futures_buy"
    else:
        kind = None
    return {"spot_net": spot_net, "spot_date": record.get("date"), "futures_net_contracts": fut,
            "split": kind is not None, "kind": kind}
