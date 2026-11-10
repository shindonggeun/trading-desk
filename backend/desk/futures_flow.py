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

from .market_flow import KST, _ttl_until_open

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
# A refused issue (or a fresh token refused again) waits longer each time, so a key without the
# rights or a changed IP does not ask KIS every minute all day (each issue notifies the owner).
TOKEN_BACKOFF_SEC = (60.0, 600.0, 1800.0)
TIMEOUT_SEC = 8.0
CACHE_OPEN_SEC = 30.0
CACHE_CLOSED_SEC = 600.0
CACHE_FAIL_SEC = 30.0
_EXPIRED_TOKEN = ("EGW00123", "EGW00121")
MULTIPLIER_MN = 0.25             # KOSPI200 futures: 250,000 KRW a point = 0.25 million KRW
# A foreign spot/futures split is shown only past noise: 10 billion KRW of stock, 500 contracts.
SPLIT_SPOT_MIN = 10_000_000_000
SPLIT_FUTURES_MIN = 500


class KisError(RuntimeError):
    """A KIS answer that is not a success: its short code and message only (no request echo)."""


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
            # KIS' index-futures master (fo_idx_code_mts.mst): "A01612 · F 202612 · KOSPI200" - A01, the
            # year's last digit, the month. The older "101X12" form answers an empty quote (#160).
            return {"code": f"A01{year % 10}{quarter:02d}", "expiry": expiry.isoformat(),
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


def futures_open(now: datetime) -> bool:
    """KRX regular derivatives session, 08:45 ~ 15:45 KST on weekdays."""
    local = now.astimezone(KST)
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return 8 * 60 + 45 <= minutes <= 15 * 60 + 45


def contract_value_ratio(investors: dict | None, price: dict) -> float | None:
    """Net value per net contract against one KOSPI200 contract's notional (price x 0.25 mn KRW),
    on the class with the most contracts. Near 1 for these futures; ~0.01 for a stock market
    answer (won per share), ~0.2 for mini futures."""
    if not investors or not price.get("price"):
        return None
    rows = [v for v in investors.values() if v.get("net_contracts") and v.get("net_value_mn") is not None]
    if not rows:
        return None
    row = max(rows, key=lambda v: abs(v["net_contracts"]))
    if abs(row["net_contracts"]) < 50:              # too few to say: a thin early session
        return None
    return (row["net_value_mn"] / row["net_contracts"]) / (price["price"] * MULTIPLIER_MN)


def _looks_like_futures(investors: dict | None, price: dict) -> bool:
    """Guards a wrong market code: the value per contract must match a KOSPI200 contract, and the
    net contracts stay within a few times the day's volume (shares would be far more)."""
    if not investors:
        return False
    ratio = contract_value_ratio(investors, price)
    if ratio is not None and not 0.5 <= ratio <= 2.0:
        return False
    if price.get("volume"):
        biggest = max(abs(v.get("net_contracts") or 0) for v in investors.values())
        if biggest > 3 * price["volume"]:
            return False
    return True


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
        self._refusals = 0                               # issues refused in a row (backoff step)
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
            if now - self._asked < self._gap():
                raise KisError("waiting before asking KIS for another access token")
            self._asked = now
            res = await (await self._client()).post(f"{self._url}{TOKEN_PATH}", json={
                "grant_type": "client_credentials", "appkey": self._key, "appsecret": self._secret})
            if res.status_code != 200:
                self._refusals += 1
                raise KisError(f"access token: HTTP {res.status_code}")
            try:
                body = res.json()
            except ValueError:
                body = {}
            token = body.get("access_token")
            if not token or not isinstance(token, str):
                self._refusals += 1
                raise KisError("access token: none in the answer")
            life = min(float(body.get("expires_in") or TOKEN_TTL_SEC), TOKEN_TTL_SEC)
            self._token = (now + life, token)
            return token

    def _gap(self) -> float:
        return TOKEN_BACKOFF_SEC[min(self._refusals, len(TOKEN_BACKOFF_SEC) - 1)]

    def _scrub(self, text: str) -> str:
        for secret in (self._key, self._secret, self._token[1] if self._token else ""):
            if secret:
                text = text.replace(secret, "***")
        return text

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
            if res.status_code == 401 or code in _EXPIRED_TOKEN:
                if attempt == 0:
                    if self._token and self._token[1] == token:
                        self._token = None               # revoked early: one fresh token, once
                    continue
                self._refusals += 1                      # a fresh token refused too: back off
                raise KisError(f"{tr_id}: token refused")
            if res.status_code != 200 or str(body.get("rt_cd")) != "0":
                # KIS' message stays in the server log (scrubbed); the page gets the codes only
                log.warning("kis %s: HTTP %s %s %s", tr_id, res.status_code, code,
                            self._scrub(str(body.get("msg1") or "").strip()[:80]))
                raise KisError(f"{tr_id}: HTTP {res.status_code} {code}".strip())
            self._refusals = 0
            return body
        raise KisError(f"{tr_id}: token refused")

    async def aclose(self) -> None:
        if self._own_http and self._http is not None:
            await self._http.aclose()


def off_reason(settings) -> str | None:
    """Why the futures flow is off, in words for the page; None when it can run."""
    if settings.fake_runs:
        return "가짜 실행(DESK_FAKE_RUNS)이라 한국투자증권을 부르지 않습니다"
    if not settings.kis_app_key or not settings.kis_app_secret:
        return "한국투자증권 키(DESK_KIS_APP_KEY · DESK_KIS_APP_SECRET)가 없어 선물 현황을 받을 수 없습니다"
    from .broker.kis_stream import REST_URL

    if not (settings.kis_rest_url or REST_URL).startswith("https://"):
        return "DESK_KIS_REST_URL 이 https:// 가 아니라 키를 보내지 않습니다"
    return None


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

    def __init__(self, rest: KisRest | None, spot=None, clock=time.monotonic, now=lambda: datetime.now(UTC),
                 off: str | None = None) -> None:
        self.rest = rest
        self.off = off or "한국투자증권 키가 없어 선물 현황을 받을 수 없습니다"
        self._ratio_logged = False
        self.spot = spot                                 # desk.market_flow.MarketFlow (for the split)
        self._clock, self._now = clock, now
        self._cache: tuple[float, float, dict] | None = None
        self._lock = asyncio.Lock()

    async def get(self, fresh: bool = False) -> dict:
        """The cached view; ``fresh`` fetches now (the #254 snapshots: no pre-open or 30 s old value)."""
        async with self._lock:
            if not fresh and self._cache and self._clock() - self._cache[0] < self._cache[1]:
                return self._cache[2]
            value, ok = await self._fetch()
            now = self._now()
            # any failed part (price, investors) is retried soon; the derivatives session sets the rest
            ttl = (CACHE_FAIL_SEC if not ok else CACHE_OPEN_SEC if futures_open(now)
                   else _ttl_until_open(now, CACHE_CLOSED_SEC))
            self._cache = (self._clock(), ttl, value)
            return value

    async def _fetch(self) -> tuple[dict, bool]:
        now = self._now()
        front = front_month(now.astimezone(KST).date())
        # every path answers the same keys
        base = {"source": "kis", "fetched_at": now.astimezone(KST).isoformat(timespec="seconds"),
                "front": front, "market_open": futures_open(now), "supported": True, "error": None,
                "price": None, "investors": None, "investors_error": None,
                "investors_unit": {"value": "백만원", "qty": "계약"}, "split": None}
        if self.rest is None:
            return {**base, "supported": False, "error": self.off}, True
        price_res, inv_res, spot_res = await asyncio.gather(
            self.rest.get(PRICE_PATH, PRICE_TR, {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": front["code"]}),
            self.rest.get(INVESTOR_PATH, INVESTOR_TR,
                          {"FID_INPUT_ISCD": INVESTOR_MARKET[0], "FID_INPUT_ISCD_2": INVESTOR_MARKET[1]}),
            self.spot.get("KOSPI", "1d", 1) if self.spot is not None else _none(),
            return_exceptions=True)
        if isinstance(price_res, BaseException):
            log.warning("futures price failed: %s", _short(price_res))
            return {**base, "error": f"선물 시세를 받지 못했습니다 — {_short(price_res)}"}, False
        price = normalize_price(price_res.get("output1") or {}, price_res.get("output3"), front)
        if price["price"] is None:                   # a success with no quote: most likely the contract code
            log.warning("futures price: empty answer for %s", front["code"])
            return {**base, "error": f"선물 시세가 비어 있습니다 — 종목 코드 {front['code']} 확인 필요"}, False
        investors, inv_error, ok = None, None, True
        if isinstance(inv_res, BaseException):
            log.warning("futures investors failed: %s", _short(inv_res))
            inv_error, ok = f"투자자별 동향을 받지 못했습니다 — {_short(inv_res)}", False
        else:
            investors = normalize_investors(inv_res.get("output"))
            ratio = contract_value_ratio(investors, price)
            if ratio is not None and not self._ratio_logged:   # once: the units on a live answer
                log.info("futures investors: value per contract / notional = %.2f", ratio)
                self._ratio_logged = True
            if investors is not None and not _looks_like_futures(investors, price):
                log.warning("futures investors: the answer for %s does not look like futures", INVESTOR_MARKET)
                investors, inv_error = None, "투자자별 동향의 시장 코드가 선물과 맞지 않습니다(확인 필요)"
        spot = spot_res if isinstance(spot_res, dict) else None
        split = foreign_split(spot, investors, now.astimezone(KST).date())
        return {**base, "price": price, "investors": investors, "investors_error": inv_error, "split": split}, ok

    async def aclose(self) -> None:
        if self.rest is not None:
            await self.rest.aclose()


async def _none():
    return None


def _short(exc: BaseException) -> str:
    return str(exc)[:100] if isinstance(exc, KisError) else type(exc).__name__


def foreign_split(spot: dict | None, investors: dict | None, today: date) -> dict | None:
    """Foreign spot net (KRW) vs futures net (contracts), the same day only, past noise: opposite
    signs = a split. ``reason`` says why there is none when the numbers cannot be compared."""
    if not investors or "foreign" not in investors:
        return None
    fut = investors["foreign"].get("net_contracts")
    record = ((spot or {}).get("records") or [None])[0]
    spot_net = ((record or {}).get("investors") or {}).get("foreign", {}).get("net") if record else None
    out = {"spot_net": spot_net, "spot_date": (record or {}).get("date"), "futures_net_contracts": fut,
           "split": False, "kind": None, "reason": None}
    if fut is None or spot_net is None:
        return {**out, "reason": "no_data"}
    if out["spot_date"] != today.isoformat():          # yesterday's stocks against today's futures
        return {**out, "reason": "spot_not_today"}
    if abs(spot_net) < SPLIT_SPOT_MIN or abs(fut) < SPLIT_FUTURES_MIN:
        return {**out, "reason": "small"}
    if spot_net > 0 and fut < 0:
        kind = "spot_buy_futures_sell"
    elif spot_net < 0 and fut > 0:
        kind = "spot_sell_futures_buy"
    else:
        kind = None
    return {**out, "split": kind is not None, "kind": kind}


_SPLIT_EN = {"spot_buy_futures_sell": "foreigners BUY stocks but SELL futures",
             "spot_sell_futures_buy": "foreigners SELL stocks but BUY futures"}


def prompt_note(view: dict | None) -> str | None:
    """#160: the front month for the agents of a Korean run, in a few lines - price, open interest,
    basis, the net contracts by investor class (only when the answer read as futures) and a foreign
    spot/futures split. None without a quote (no keys, KIS down, nothing loaded)."""
    p = (view or {}).get("price") or {}
    if not view or not view.get("supported") or p.get("price") is None:
        return None

    def num(v, fmt):
        return format(v, fmt) if isinstance(v, int | float) else "n/a"

    head = (f"KOSPI200 futures, front month {p.get('code')} (expires {p.get('expiry')}, {p.get('days_left')} days left): "
            f"{num(p['price'], '.2f')} ({num(p.get('change_pct'), '+.2f')}%), open interest {num(p.get('open_interest'), ',.0f')} "
            f"({num(p.get('open_interest_change'), '+,.0f')}), basis {num(p.get('market_basis'), '+.2f')} "
            f"vs theoretical {num(p.get('theory_basis'), '+.2f')}" + (f" ({p['structure']})" if p.get("structure") else "") + ".")
    if not view.get("market_open"):
        head += " Derivatives session closed: the last session's figures."
    lines = [head]
    inv = view.get("investors") or {}
    if inv:
        parts = [f"{name} {num((inv.get(key) or {}).get('net_contracts'), '+,.0f')}"
                 for key, name in (("foreign", "foreign"), ("institution", "institutions"), ("individual", "individuals"))
                 if key in inv]
        lines.append("Net futures contracts today: " + ", ".join(parts) + ".")
    split = view.get("split") or {}
    if split.get("split") and split.get("kind") in _SPLIT_EN:
        lines.append(f"Split: {_SPLIT_EN[split['kind']]} today (spot {split['spot_net'] / 1e9:+,.0f} bn KRW, "
                     f"futures {split['futures_net_contracts']:+,.0f} contracts) - often a hedge or an early turn.")
    lines.append("Context for the index direction only (KIS); weigh it with the spot flow, not as a signal by itself.")
    return "\n".join(lines)
