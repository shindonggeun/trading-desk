"""Where the shorts and the options sit, per ticker (#154) — a summary to glance at before a trade.

Keyless sources, US-listed tickers only for now:

- FINRA Reg SHO daily short sale volume (cdn.finra.org/equity/regsho/daily/CNMSshvol<YYYYMMDD>.txt):
  the share of each day's consolidated volume that was sold short. It counts market-maker shorting
  too, so ~40-50% is ordinary: the *change* against the last weeks says more than the level.
  One file a day (~0.5 MB), published after the close; a day's file never changes, so it is kept.
- Yahoo short interest (yfinance ``info``): shares short as a share of float and days to cover,
  updated twice a month by the exchanges.
- Cboe delayed option quotes (cdn.cboe.com/api/global/delayed_quotes/options/<T>.json, ~2 MB, 15-min
  delay): open interest, volume and implied volatility per contract, the underlying price and its
  30-day IV. Yahoo's chains are the fallback - they zero open interest for hours around the daily
  reset. From the expiries within OPTION_DAYS: put/call ratios, the strikes with the most call open
  interest above the price (call walls) and put open interest below it (put walls), max pain of the
  heaviest expiry and the implied move.

Korean tickers have no listed single-stock options on Yahoo and KRX short data needs a session;
they get ``supported: false`` with the reason. Arithmetic over published positions, not a forecast.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from statistics import fmean

import httpx

log = logging.getLogger(__name__)

_US = re.compile(r"[A-Z]{1,6}(?:[.-][A-Z])?")


def is_us_ticker(ticker: str) -> bool:
    """Plain US symbols and share classes (BRK.B): no exchange suffix (005930.KS), index or future."""
    return bool(_US.fullmatch(ticker or ""))


def user_agent() -> str:
    # FINRA's CDN, like SEC, wants a named client rather than a library default
    return os.getenv("SEC_EDGAR_USER_AGENT") or "trading-desk/1.0 (personal research)"

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day:%Y%m%d}.txt"
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{ticker}.json"
CBOE_MAX_BYTES = 30_000_000   # SPY is ~6 MB
FINRA_DAYS = 20               # trading days of short-volume history
FINRA_LOOKBACK = 35           # calendar days walked back to find them (holidays, weekends)
FINRA_KEEP = 40               # parsed day files kept in memory
FINRA_PARALLEL = 4
TIMEOUT_SEC = 20.0
OPTION_DAYS = 45              # expiries within this many days feed the walls and ratios
OPTION_EXPIRIES = 6           # at most this many chains per ticker
IV_MIN_DAYS = 5               # the implied move skips 0-4 day expiries (pin and gamma noise)
WALLS = 3
OPTIONS_TTL_SEC = 15 * 60
INTEREST_TTL_SEC = 12 * 3600
PUT_HEAVY = 1.2               # put/call open interest at or above: puts dominate
CALL_HEAVY = 0.7              # at or below: calls dominate
SHORT_SHIFT = 0.08            # today's short-volume share this far from the 20-day mean: rising / falling
HIGH_SHORT_FLOAT = 10.0       # % of float sold short that is crowded


# --- options: pure arithmetic ---------------------------------------------------------------

def _oi(rows: Iterable[dict]) -> float:
    return sum(max(r.get("oi") or 0, 0) for r in rows)


def _vol(rows: Iterable[dict]) -> float:
    return sum(max(r.get("volume") or 0, 0) for r in rows)


def _ratio(puts: float, calls: float) -> float | None:
    return round(puts / calls, 2) if calls > 0 else None


def max_pain(calls: list[dict], puts: list[dict]) -> float | None:
    """The strike at which option holders' total payout is smallest (where writers lose least)."""
    strikes = sorted({r["strike"] for r in calls + puts if (r.get("oi") or 0) > 0})
    if not strikes:
        return None

    def payout(at: float) -> float:
        return (sum((r.get("oi") or 0) * max(0.0, at - r["strike"]) for r in calls)
                + sum((r.get("oi") or 0) * max(0.0, r["strike"] - at) for r in puts))
    return min(strikes, key=payout)


def _walls(rows: list[dict], spot: float, above: bool) -> list[dict]:
    by_strike: dict[float, float] = {}
    for r in rows:
        if (r.get("oi") or 0) > 0 and (r["strike"] >= spot if above else r["strike"] <= spot):
            by_strike[r["strike"]] = by_strike.get(r["strike"], 0) + r["oi"]
    top = sorted(by_strike.items(), key=lambda kv: -kv[1])[:WALLS]
    return [{"strike": k, "oi": int(v), "distance_pct": round((k / spot - 1) * 100, 1)} for k, v in top]


def summarise_options(chains: list[tuple[date, list[dict], list[dict]]], spot: float, today: date,
                      iv30: float | None = None) -> dict | None:
    """``chains``: (expiry, calls, puts) with rows {strike, oi, volume, iv}, nearest first.
    ``iv30``: the underlying's 30-day implied volatility in percent (Cboe), when known."""
    chains = [c for c in chains if 0 <= (c[0] - today).days <= OPTION_DAYS and (c[1] or c[2])]
    if not chains or not spot or spot <= 0:
        return None
    near_exp, near_calls, near_puts = chains[0]
    calls = [r for _, c, _ in chains for r in c]
    puts = [r for _, _, p in chains for r in p]
    heavy = max(chains, key=lambda c: _oi(c[1]) + _oi(c[2]))
    move = None
    iv_chain = next((c for c in chains if (c[0] - today).days >= IV_MIN_DAYS), None)
    if iv_chain:
        days = (iv_chain[0] - today).days
        atm = [r for r in iv_chain[1] + iv_chain[2] if r.get("iv")]
        if atm:
            nearest = min(abs(r["strike"] - spot) for r in atm)
            ivs = [r["iv"] for r in atm if abs(r["strike"] - spot) == nearest and 0 < r["iv"] < 5]
            if ivs:
                iv = fmean(ivs)
                pct = iv * math.sqrt(days / 365) * 100
                move = {"expiry": iv_chain[0].isoformat(), "days": days, "iv_pct": round(iv * 100, 1),
                        "move_pct": round(pct, 1), "low": round(spot * (1 - pct / 100), 2),
                        "high": round(spot * (1 + pct / 100), 2)}
    if iv30:
        pct = iv30 * math.sqrt(30 / 365)
        move = {**(move or {}), "iv30_pct": round(iv30, 1), "move_30d_pct": round(pct, 1)}
    pc_oi = _ratio(_oi(puts), _oi(calls))
    lean = (None if pc_oi is None else "put_heavy" if pc_oi >= PUT_HEAVY
            else "call_heavy" if pc_oi <= CALL_HEAVY else "balanced")
    pain = max_pain(heavy[1], heavy[2])
    return {
        "spot": spot,
        "expiries": [c[0].isoformat() for c in chains],
        "put_call_oi": pc_oi,
        "put_call_volume": _ratio(_vol(puts), _vol(calls)),
        "near": {"expiry": near_exp.isoformat(), "put_call_oi": _ratio(_oi(near_puts), _oi(near_calls)),
                 "put_call_volume": _ratio(_vol(near_puts), _vol(near_calls))},
        "call_oi": int(_oi(calls)), "put_oi": int(_oi(puts)),
        "call_walls": _walls(calls, spot, above=True),
        "put_walls": _walls(puts, spot, above=False),
        "max_pain": {"expiry": heavy[0].isoformat(), "strike": pain,
                     "distance_pct": round((pain / spot - 1) * 100, 1) if pain else None},
        "implied_move": move,
        "lean": lean,
    }


def parse_cboe(payload: dict, ticker: str, today: date) -> dict | None:
    """{spot, iv30, chains: [(expiry, calls, puts)]} from Cboe's delayed quotes. Contract symbols are
    ROOT + YYMMDD + C/P + strike x 1000 (8 digits); adjusted roots (after splits: "B1VDA") are left out."""
    data = (payload or {}).get("data") or {}
    root = ticker.upper().replace(".", "").replace("-", "")
    by_day: dict[date, tuple[list[dict], list[dict]]] = {}
    for row in data.get("options") or ():
        symbol = str(row.get("option") or "")
        if len(symbol) < 16 or symbol[:-15] != root:
            continue
        try:
            day = datetime.strptime(symbol[-15:-9], "%y%m%d").date()
            strike = int(symbol[-8:]) / 1000
        except ValueError:
            continue
        if not 0 <= (day - today).days <= OPTION_DAYS:
            continue
        iv = _num(row.get("iv"))
        entry = {"strike": strike, "oi": _num(row.get("open_interest")) or 0, "volume": _num(row.get("volume")) or 0,
                 "iv": iv if iv and iv > 0 else None}
        calls, puts = by_day.setdefault(day, ([], []))
        (calls if symbol[-9] == "C" else puts).append(entry)
    if not by_day:
        return None
    chains = [(day, *by_day[day]) for day in sorted(by_day)][:OPTION_EXPIRIES * 2]
    iv30 = _num(data.get("iv30"))
    return {"spot": _num(data.get("current_price")), "iv30": iv30 if iv30 and iv30 > 0 else None,
            "chains": chains, "source": "cboe"}


async def cboe_options(ticker: str, today: date, client: httpx.AsyncClient | None = None) -> dict | None:
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SEC, follow_redirects=True)
    try:
        response = await client.get(CBOE_URL.format(ticker=ticker.upper()), headers={"User-Agent": user_agent()})
        if response.status_code in (403, 404):
            return None                     # no listed options
        response.raise_for_status()
        if len(response.content) > CBOE_MAX_BYTES:
            raise ValueError(f"Cboe answered {len(response.content)} bytes")
        payload = response.json()
    finally:
        if own:
            await client.aclose()
    return await asyncio.to_thread(parse_cboe, payload, ticker, today)


async def any_options(ticker: str, today: date) -> dict | None:
    """Cboe, else Yahoo's chains (only when they carry open interest)."""
    try:
        got = await cboe_options(ticker, today)
        if got and any(_oi(c) + _oi(p) for _, c, p in got["chains"]):
            return got
    except Exception as exc:  # noqa: BLE001 - Yahoo next
        log.info("cboe options for %s failed: %s", ticker, type(exc).__name__)
    chains = await asyncio.to_thread(yahoo_chains, ticker, today)
    if chains and any(_oi(c) + _oi(p) for _, c, p in chains):
        return {"spot": None, "iv30": None, "chains": chains, "source": "yahoo"}
    return None


def yahoo_chains(ticker: str, today: date) -> list[tuple[date, list[dict], list[dict]]]:
    """Blocking (yfinance): call from a thread."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    expiries = []
    for raw in t.options or ():
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            continue
        if 0 <= (day - today).days <= OPTION_DAYS:
            expiries.append((day, raw))
    out = []
    for day, raw in expiries[:OPTION_EXPIRIES]:
        chain = t.option_chain(raw)
        out.append((day, _rows(chain.calls), _rows(chain.puts)))
    return out


def _rows(frame) -> list[dict]:
    rows = []
    for r in frame.to_dict("records"):
        strike = _num(r.get("strike"))
        if strike is None:
            continue
        rows.append({"strike": strike, "oi": _num(r.get("openInterest")) or 0, "volume": _num(r.get("volume")) or 0,
                     "iv": _num(r.get("impliedVolatility"))})
    return rows


def _num(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def yahoo_short_interest(ticker: str) -> dict | None:
    """Blocking (yfinance). Shares short as % of float, days to cover, the month-on-month change."""
    import yfinance as yf

    info = yf.Ticker(ticker).info or {}
    pct, ratio = _num(info.get("shortPercentOfFloat")), _num(info.get("shortRatio"))
    short, prior = _num(info.get("sharesShort")), _num(info.get("sharesShortPriorMonth"))
    if pct is None and short is None:
        return None
    stamp = info.get("dateShortInterest")
    as_of = datetime.fromtimestamp(stamp, UTC).date().isoformat() if isinstance(stamp, int | float) else None
    return {"pct_float": round(pct * 100, 2) if pct is not None else None, "days_to_cover": ratio,
            "shares": int(short) if short else None,
            "change_pct": round((short / prior - 1) * 100, 1) if short and prior else None, "as_of": as_of}


# --- FINRA daily short volume ---------------------------------------------------------------

def parse_finra(text: str) -> dict[str, tuple[float, float]]:
    """symbol -> (short volume, total volume) from one day file."""
    out = {}
    for line in text.splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        try:
            out[parts[1].strip().upper()] = (float(parts[2]), float(parts[4]))
        except ValueError:
            continue
    return out


class ShortVolume:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._days: OrderedDict[date, dict[str, tuple[float, float]] | None] = OrderedDict()
        self._locks: dict[date, asyncio.Lock] = {}
        self._gate = asyncio.Semaphore(FINRA_PARALLEL)

    async def _day(self, day: date, today: date) -> dict[str, tuple[float, float]] | None:
        if day in self._days:
            return self._days[day]
        async with self._locks.setdefault(day, asyncio.Lock()):
            if day in self._days:
                return self._days[day]
            async with self._gate:
                client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SEC)
                try:
                    response = await client.get(FINRA_URL.format(day=day), headers={"User-Agent": user_agent()})
                finally:
                    if self._client is None:
                        await client.aclose()
            if response.status_code in (403, 404):
                table = None           # a holiday, or not published yet
                if day >= today:
                    return None        # today's file may still come: do not remember the miss
            else:
                response.raise_for_status()
                table = parse_finra(response.text)
            self._days[day] = table
            while len(self._days) > FINRA_KEEP:
                self._days.popitem(last=False)
            return table

    async def history(self, ticker: str, today: date) -> list[dict]:
        """[{date, ratio, short, total}] oldest first, up to FINRA_DAYS trading days."""
        symbol = ticker.upper().replace("-", ".")
        days = [today - timedelta(days=i) for i in range(FINRA_LOOKBACK)]
        days = [d for d in days if d.weekday() < 5]
        tables = await asyncio.gather(*(self._day(d, today) for d in days), return_exceptions=True)
        rows = []
        for day, table in zip(days, tables, strict=True):
            if isinstance(table, BaseException) or not table or symbol not in table:
                continue
            short, total = table[symbol]
            if total > 0:
                rows.append({"date": day.isoformat(), "ratio": round(short / total, 4), "short": int(short),
                             "total": int(total)})
            if len(rows) >= FINRA_DAYS:
                break
        return list(reversed(rows))


def summarise_short(history: list[dict], interest: dict | None) -> dict | None:
    if not history and not interest:
        return None
    out: dict = {"interest": interest}
    if history:
        ratios = [r["ratio"] for r in history]
        last, mean20 = ratios[-1], fmean(ratios)
        mean5 = fmean(ratios[-5:])
        trend = ("rising" if last - mean20 >= SHORT_SHIFT else "falling" if mean20 - last >= SHORT_SHIFT else "steady")
        out.update({"volume_ratio": round(last * 100, 1), "volume_ratio_5d": round(mean5 * 100, 1),
                    "volume_ratio_20d": round(mean20 * 100, 1), "as_of": history[-1]["date"], "trend": trend,
                    "history": [{"date": r["date"], "ratio": round(r["ratio"] * 100, 1)} for r in history]})
    crowded = bool(interest and (interest.get("pct_float") or 0) >= HIGH_SHORT_FLOAT)
    out["crowded"] = crowded
    return out


# --- the whole picture, cached ----------------------------------------------------------------

class Positioning:
    def __init__(self, short_volume: ShortVolume | None = None, chains: Callable = any_options,
                 interest: Callable = yahoo_short_interest, clock=time.monotonic) -> None:
        self.short_volume = short_volume or ShortVolume()
        self._chains, self._interest, self._clock = chains, interest, clock
        self._cache: dict[str, tuple[float, dict]] = {}
        self._interest_cache: dict[str, tuple[float, dict | None]] = {}

    async def _short_interest(self, ticker: str) -> dict | None:
        hit = self._interest_cache.get(ticker)
        if hit and self._clock() - hit[0] < INTEREST_TTL_SEC:
            return hit[1]
        value = await asyncio.to_thread(self._interest, ticker)
        self._interest_cache[ticker] = (self._clock(), value)
        return value

    async def get(self, ticker: str, spot: float | None, today: date | None = None) -> dict:
        ticker = ticker.upper()
        if not is_us_ticker(ticker):
            return {"ticker": ticker, "supported": False,
                    "reason": "국내 · 지수 · 선물은 아직 공매도 · 옵션 요약을 지원하지 않습니다(미국 상장 종목만)"}
        hit = self._cache.get(ticker)
        if hit and self._clock() - hit[0] < OPTIONS_TTL_SEC:
            return hit[1]
        today = today or datetime.now(UTC).date()
        history, interest, chains = await asyncio.gather(
            self.short_volume.history(ticker, today), self._short_interest(ticker),
            self._chains(ticker, today), return_exceptions=True)
        errors = [f"{name}: {type(v).__name__}" for name, v in
                  (("finra", history), ("short_interest", interest), ("options", chains)) if isinstance(v, BaseException)]
        for e in errors:
            log.info("positioning %s: %s", ticker, e)
        short = summarise_short(history if isinstance(history, list) else [],
                                interest if isinstance(interest, dict) else None)
        got = chains if isinstance(chains, dict) else None
        spot = spot or (got or {}).get("spot")
        options = summarise_options(got["chains"], spot, today, got.get("iv30")) if got and spot else None
        if options:
            options["source"] = got["source"]
        result = {"ticker": ticker, "supported": True, "short": short, "options": options,
                  "verdict": verdict(short, options), "errors": errors or None,
                  "sources": ["finra", "yahoo"] + ([options["source"]] if options else []), "fetched_at": datetime.now(UTC).isoformat(timespec="seconds")}
        if not errors:   # a partial answer is not kept: the next look tries again
            if len(self._cache) > 256:
                self._cache.clear()
            self._cache[ticker] = (self._clock(), result)
        return result


def verdict(short: dict | None, options: dict | None) -> str | None:
    """One line, Korean, for the card and the sheet."""
    parts = []
    if options and options.get("lean"):
        parts.append({"put_heavy": "옵션은 풋 쪽에 몰림", "call_heavy": "옵션은 콜 쪽에 몰림",
                      "balanced": "옵션은 풋 · 콜 균형"}[options["lean"]] + f"(풋/콜 OI {options['put_call_oi']})")
    if short and short.get("trend"):
        parts.append({"rising": "공매도 비중 늘어나는 중", "falling": "공매도 비중 줄어드는 중",
                      "steady": "공매도 비중 평소 수준"}[short["trend"]] + f"({short['volume_ratio']}%)")
    if short and short.get("crowded"):
        parts.append(f"공매도 잔고 유통주식의 {short['interest']['pct_float']}% — 숏 스퀴즈 여지")
    return " · ".join(parts) or None


POSITIONING = Positioning()


def prompt_note(p: dict | None) -> str | None:
    """The agents' "Short selling & options positioning" block (same-day US runs)."""
    if not p or not p.get("supported") or not (p.get("short") or p.get("options")):
        return None
    lines = [("Short selling & options positioning (FINRA daily short volume, exchange short interest, Cboe "
              "delayed option open interest; desk-computed). Positions, not a forecast: short-volume share includes market "
              "making, so read its change; option walls can pin price into expiry and act as support/resistance.")]
    s, o = p.get("short"), p.get("options")
    if s and s.get("volume_ratio") is not None:
        lines.append(f"- Short volume share: {s['volume_ratio']}% on {s['as_of']} (5d {s['volume_ratio_5d']}%, "
                     f"20d {s['volume_ratio_20d']}%) - {s['trend']}")
    if s and s.get("interest"):
        i = s["interest"]
        lines.append(f"- Short interest: {i.get('pct_float')}% of float, {i.get('days_to_cover')} days to cover"
                     + (f", {i['change_pct']:+.1f}% vs prior month" if i.get("change_pct") is not None else "")
                     + (f" (as of {i['as_of']})" if i.get("as_of") else "")
                     + (" - CROWDED" if s.get("crowded") else ""))
    if o:
        walls = lambda ws: ", ".join(f"{w['strike']:g} ({w['distance_pct']:+.1f}%)" for w in ws) or "none"
        lines.append(f"- Options to {o['expiries'][-1]}: put/call OI {o['put_call_oi']}, put/call volume "
                     f"{o['put_call_volume']} - {o['lean']}; near expiry {o['near']['expiry']} put/call OI "
                     f"{o['near']['put_call_oi']}")
        lines.append(f"- Call walls above: {walls(o['call_walls'])}; put walls below: {walls(o['put_walls'])}")
        mp = o["max_pain"]
        if mp.get("strike"):
            lines.append(f"- Max pain {mp['strike']:g} for {mp['expiry']} ({mp['distance_pct']:+.1f}% from {o['spot']:g})")
        m = o.get("implied_move") or {}
        if m.get("expiry"):
            lines.append(f"- Implied move to {m['expiry']} ({m['days']}d): +/-{m['move_pct']}% "
                         f"({m['low']:g}-{m['high']:g}), IV {m['iv_pct']}%")
        if m.get("iv30_pct"):
            lines.append(f"- 30-day IV {m['iv30_pct']}% (about +/-{m['move_30d_pct']}% over a month)")
    return "\n".join(lines)
