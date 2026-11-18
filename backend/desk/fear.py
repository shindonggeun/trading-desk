"""Fear gauges in one place (#462): the VIX, CNN's Fear & Greed, the VKOSPI and the crypto Fear & Greed.

Each item gives its value, a band (extreme fear .. extreme greed), the value a day and a week before,
where it came from and when. Sources need no key; each is cached on its own and one that fails comes
back as that item's ``error`` (the others are still served).

- VIX: Yahoo ``^VIX`` daily closes (as desk.risk_regime reads it). High is fear, so its band runs the
  other way from the 0-100 indexes.
- CNN Fear & Greed: the public graph data behind cnn.com/markets/fear-and-greed (0-100).
- VKOSPI (#486): the KIS Open API domestic index daily price (the KIS keys desk.futures_flow already
  uses) for KRX index code 0503 "VKOSPI" (KIS' index master idxcode.mst: ``0`` + ``0503`` + name;
  KOSPI200 is 2001 there, matching KIS' own samples). Yahoo has no ^VKOSPI. Without KIS keys it stays
  ``value: null`` with the reason. The bands are the VIX ones: VKOSPI runs on the same scale (annualised
  30-day implied volatility of the index options, long-run median near the VIX's).
  When KIS is missing or fails, an estimate stands in (#486): KOSPI200's 20-session realised volatility
  (annualised %, read on KODEX 200 069500.KS - Yahoo keeps no ^KS200 history) times ESTIMATE_SCALE,
  named "VKOSPI(추정)" with the method as its source and ``estimated`` listing the estimated fields.
  A KIS answer missing the day or week before gets those two from the estimate, listed the same way.
- Crypto Fear & Greed: alternative.me (0-100, daily).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CRYPTO_URL = "https://api.alternative.me/fng/"
# CNN answers a bare client with 418; a browser-like agent is enough (no account, no key)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/120.0 Safari/537.36", "Accept": "application/json"}
TIMEOUT_SEC = 15.0
TTL_SEC = {"vix": 15 * 60, "cnn": 30 * 60, "vkospi": 15 * 60, "crypto": 60 * 60}
# #486 KIS domestic index daily price (FHPUP02120000): output1 = the index now, output2 = daily closes, newest first
KIS_INDEX_DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
KIS_INDEX_DAILY_TR = "FHPUP02120000"
VKOSPI_CODE = "0503"
NO_SOURCE = "한국투자증권 키가 없어 VKOSPI 를 받지 않음(야후에는 ^VKOSPI 가 없음)"
FAIL_TTL_SEC = 120.0
# #486 the stand-in when KIS gives no VKOSPI: KOSPI200's realised volatility. KODEX 200 tracks KOSPI200
# (Yahoo's ^KS200 has one bar, no history). ESTIMATE_SCALE: the VKOSPI KIS gave in production on
# 2026-11-17 / the day before / a week before (53.81 · 52.64 · 58.19) over this estimate on those days
# (58.22 · 58.57 · 65.67) is 0.92 · 0.90 · 0.89. That is a stormy market; in a calm one the implied
# volatility usually sits above the realised, so the estimate then reads low - it is named an estimate.
# The closes are unadjusted (desk.prices): KODEX 200's distribution days (end of Jan · Apr · Jul · Oct)
# drop the price by the payout, a return that is not volatility and lifts a calm month's estimate.
# KIS is asked first and the estimate only after it fails; asking both at once would shorten that wait.
ESTIMATE_SYMBOL = "069500.KS"
ESTIMATE_DAYS = 20
ESTIMATE_SCALE = 0.9
ESTIMATE_NAME = "VKOSPI(추정)"
ESTIMATE_SOURCE = f"추정: KODEX 200 {ESTIMATE_DAYS}거래일 실현 변동성(연율 %) × {ESTIMATE_SCALE}"

BANDS = ("extreme_fear", "fear", "neutral", "greed", "extreme_greed")
BAND_KO = {"extreme_fear": "극단적 공포", "fear": "공포", "neutral": "중립", "greed": "탐욕",
           "extreme_greed": "극단적 탐욕"}
INDEX_EDGES = (25.0, 45.0, 56.0, 76.0)    # 0-100 indexes (CNN's own cut points): below 25 extreme fear ...
VIX_EDGES = (30.0, 22.0, 17.0, 12.0)      # VIX: above 30 extreme fear, above 22 fear, above 17 neutral ...


def band_of(key: str, value: float | None) -> str | None:
    if value is None:
        return None
    if key in ("vix", "vkospi"):           # volatility: higher = more fear
        for band, edge in zip(BANDS, VIX_EDGES, strict=False):
            if value > edge:
                return band
        return BANDS[-1]
    for band, edge in zip(BANDS, INDEX_EDGES, strict=False):
        if value < edge:
            return band
    return BANDS[-1]


def _item(key: str, name: str, source: str, value=None, prev_day=None, prev_week=None, as_of=None,
          error: str | None = None) -> dict:
    r = (lambda v: None if v is None else round(float(v), 2))
    band = band_of(key, None if value is None else float(value))
    out = {"key": key, "name": name, "value": r(value), "band": band, "band_ko": BAND_KO.get(band or ""),
           "prev_day": r(prev_day), "prev_week": r(prev_week), "source": source, "as_of": as_of}
    if error:
        out["error"] = error
    return out


def vix(candles: Callable) -> dict:
    """``candles(symbol, interval, count)`` -> bars with ``at`` and ``close`` (desk.prices)."""
    bars = [b for b in candles("^VIX", "1d", 10) if b.close]
    if not bars:
        raise ValueError("no ^VIX bars")
    closes = [b.close for b in bars]
    return _item("vix", "VIX", "Yahoo ^VIX", closes[-1], closes[-2] if len(closes) > 1 else None,
                 closes[-6] if len(closes) > 5 else None, bars[-1].at)


def parse_cnn(payload: dict) -> dict:
    fg = (payload or {}).get("fear_and_greed") or {}
    if fg.get("score") is None:
        raise ValueError("no fear_and_greed score")
    return _item("cnn", "CNN 공포탐욕", "CNN Fear & Greed", fg["score"], fg.get("previous_close"),
                 fg.get("previous_1_week"), fg.get("timestamp"))


def parse_crypto(payload: dict) -> dict:
    data = (payload or {}).get("data") or []
    if not data:
        raise ValueError("no data")
    def at(i):
        return float(data[i]["value"]) if len(data) > i else None
    stamp = datetime.fromtimestamp(int(data[0]["timestamp"]), UTC).isoformat()
    return _item("crypto", "암호화폐 공포탐욕", "alternative.me Crypto Fear & Greed", at(0), at(1), at(7), stamp)


def vkospi() -> dict:
    """No KIS: the item with the reason (the card shows "출처 없음")."""
    return _item("vkospi", "VKOSPI", "없음", error=NO_SOURCE)


def _num(v) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def parse_vkospi(body: dict) -> dict:
    """KIS FHPUP02120000 for code 0503: the index now (output1, the last close after the bell or on a
    holiday) and the daily closes (output2) - the day before and five sessions before."""
    now = _num(((body or {}).get("output1") or {}).get("bstp_nmix_prpr"))
    days = []
    for row in (body or {}).get("output2") or []:
        d, close = str(row.get("stck_bsop_date") or ""), _num(row.get("bstp_nmix_prpr"))
        if len(d) == 8 and d.isdigit() and close is not None:
            days.append((d, close))
    days.sort(reverse=True)
    if now is None and days:
        now = days[0][1]
    if now is None:
        raise ValueError("no VKOSPI value")
    as_of = f"{days[0][0][:4]}-{days[0][0][4:6]}-{days[0][0][6:]}" if days else None
    prev_day = days[1][1] if len(days) > 1 else None
    prev_week = days[5][1] if len(days) > 5 else None
    return _item("vkospi", "VKOSPI", "한국투자증권 지수 일별 시세(0503)", now, prev_day, prev_week, as_of)


async def vkospi_kis(rest, today: str | None = None) -> dict:
    """``rest`` is desk.futures_flow.KisRest (keys never leave it; its errors carry codes only)."""
    today = today or datetime.now(UTC).astimezone(KST).strftime("%Y%m%d")
    body = await rest.get(KIS_INDEX_DAILY_PATH, KIS_INDEX_DAILY_TR, {
        "FID_PERIOD_DIV_CODE": "D", "FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": VKOSPI_CODE,
        "FID_INPUT_DATE_1": today})
    return parse_vkospi(body)


def _realised(closes: list[float], end: int) -> float | None:
    """Annualised % volatility of the ESTIMATE_DAYS daily log returns ending at ``closes[end]``."""
    if end < ESTIMATE_DAYS or end >= len(closes):
        return None
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(end - ESTIMATE_DAYS + 1, end + 1)]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252) * 100 * ESTIMATE_SCALE


def vkospi_estimate(candles: Callable, why: str | None = None) -> dict:
    """The stand-in VKOSPI (#486) from ``candles`` (desk.prices): now, the session before and five
    before, like the KIS item; ``why`` (the reason KIS gave none) rides along as ``fallback_reason``."""
    bars = [b for b in candles(ESTIMATE_SYMBOL, "1d", ESTIMATE_DAYS + 20) if b.close and b.close > 0]
    closes = [b.close for b in bars]
    last = len(closes) - 1
    now = _realised(closes, last)
    if now is None:
        raise ValueError(f"too few {ESTIMATE_SYMBOL} bars for an estimate ({len(closes)})")
    as_of = datetime.fromisoformat(bars[-1].at).astimezone(KST).date().isoformat()
    item = _item("vkospi", ESTIMATE_NAME, ESTIMATE_SOURCE, now, _realised(closes, last - 1),
                 _realised(closes, last - 5), as_of)
    item["estimated"] = ["value", "prev_day", "prev_week"]
    if why:
        item["fallback_reason"] = why
    return item


_PREV_KO = {"prev_day": "어제", "prev_week": "지난주"}


def fill_missing(item: dict, estimate: dict) -> dict:
    """A KIS item missing the day or week before takes them from the estimate, listed in ``estimated``
    and said in ``source`` (the gauge's tooltip): those values are another measure."""
    missing = [k for k in ("prev_day", "prev_week") if item.get(k) is None and estimate.get(k) is not None]
    if not missing:
        return item
    said = " · ".join(_PREV_KO[k] for k in missing)
    return {**item, **{k: estimate[k] for k in missing}, "estimated": missing,
            "source": f"{item['source']} · {said} 값은 {ESTIMATE_SOURCE}"}


def _get_json(url: str, get: Callable[..., httpx.Response], **params) -> dict:
    response = get(url, params=params or None, headers=HEADERS, timeout=TIMEOUT_SEC)
    response.raise_for_status()
    return response.json()


def _worse(item: dict) -> bool:
    """A failed item, or one whose value is only an estimate (#486): a real value still in date beats it."""
    return bool(item.get("error")) or "value" in (item.get("estimated") or [])


ORDER = ("vix", "cnn", "vkospi", "crypto")
NAMES = {"vix": ("VIX", "Yahoo ^VIX"), "cnn": ("CNN 공포탐욕", "CNN Fear & Greed"),
         "vkospi": ("VKOSPI", "없음"), "crypto": ("암호화폐 공포탐욕", "alternative.me Crypto Fear & Greed")}


class Fear:
    """Cached per source; ``get()`` answers every item, a failed one with its ``error``."""

    def __init__(self, candles: Callable | None = None, get: Callable[..., httpx.Response] = httpx.get,
                 clock: Callable[[], float] = time.monotonic, kis: Callable[[], object | None] | None = None) -> None:
        if candles is None:
            from .prices import yahoo_candles as candles
        self._fetch: dict[str, Callable[[], dict]] = {
            "vix": lambda: vix(candles),
            "cnn": lambda: parse_cnn(_get_json(CNN_URL, get)),
            "vkospi": vkospi,
            "crypto": lambda: parse_crypto(_get_json(CRYPTO_URL, get, limit=8)),
        }
        self._candles = candles
        self._clock = clock
        self._kis = kis or (lambda: None)        # #486 desk.futures_flow.KisRest, read when asked
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()

    def _one(self, key: str) -> tuple[dict, float]:
        try:
            return self._fetch[key](), TTL_SEC[key]
        except Exception as exc:  # noqa: BLE001 - one source down leaves the others
            log.info("fear: %s failed: %s", key, exc)
            name, source = NAMES[key]
            return _item(key, name, source, error=f"{type(exc).__name__}: {str(exc)[:80]}"), FAIL_TTL_SEC

    async def _estimate(self, why: str | None = None) -> dict | None:
        try:
            return await asyncio.to_thread(vkospi_estimate, self._candles, why)
        except Exception as exc:  # noqa: BLE001 - no estimate either: the item says why KIS gave none
            log.info("fear: vkospi estimate failed: %s", type(exc).__name__)
            return None

    async def _vkospi(self) -> tuple[dict, float]:
        """KIS first; without KIS keys, or when KIS fails, the estimate (#486) - after a failure only
        for FAIL_TTL_SEC, so KIS is asked again soon. Without an estimate either, the reason."""
        rest = self._kis()
        if rest is None:
            est = await self._estimate(NO_SOURCE)
            return (est, TTL_SEC["vkospi"]) if est else (vkospi(), TTL_SEC["vkospi"])
        try:
            item = await vkospi_kis(rest)
        except Exception as exc:  # noqa: BLE001 - KIS down leaves the other gauges; KisError carries codes only
            log.info("fear: vkospi failed: %s", type(exc).__name__)
            from .futures_flow import KisError

            # KisError carries tr_id · HTTP · KIS code only; anything else shows its class name only
            why = f"KisError: {str(exc)[:80]}" if isinstance(exc, KisError) else type(exc).__name__
            est = await self._estimate(f"한국투자증권 VKOSPI 를 받지 못함({why})")
            return (est or _item("vkospi", "VKOSPI", "한국투자증권", error=why)), FAIL_TTL_SEC
        if item["prev_day"] is None or item["prev_week"] is None:
            est = await self._estimate()
            if est:
                item = fill_missing(item, est)
        return item, TTL_SEC["vkospi"]

    async def get(self, ahead: float = 0.0) -> dict:
        """``ahead`` (#536, the prewarm loop): refetch the sources that expire within that many seconds.
        When every source is fresh the answer is read without the lock - a page never waits behind it."""
        now = self._clock()
        if all(k in self._cache and self._cache[k][0] > now + ahead for k in ORDER):
            return {"items": [self._cache[k][1] for k in ORDER]}
        async with self._lock:
            now = self._clock()
            stale = [k for k in ORDER if k not in self._cache or self._cache[k][0] <= now + ahead]
            if stale:
                got = await asyncio.gather(*(self._vkospi() if k == "vkospi" else asyncio.to_thread(self._one, k)
                                             for k in stale))
                for k, (item, ttl) in zip(stale, got, strict=True):
                    old = self._cache.get(k)
                    if _worse(item) and old and not _worse(old[1]) and old[0] > now:
                        continue                 # #536: a failed refetch-ahead keeps the gauge still in date
                    self._cache[k] = (now + ttl, item)
            return {"items": [self._cache[k][1] for k in ORDER]}
