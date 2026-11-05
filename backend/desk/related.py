"""What moves an instrument besides itself: its underlying, peers and drivers (issue #66).

A leveraged ETF follows an index ETF (GDXU ~ 3x GDX, SOXL ~ 3x SOXX); that ETF follows its
largest holdings (GDX: Newmont, Agnico Eagle ...) and its drivers (gold futures, the dollar,
yields). The agents get a short, numeric picture of those — price, trend, correlation — and
the latest headlines about them, so a call on GDXU can rest on what gold and the miners did.

The table is kept by hand: Yahoo has no "leveraged ETF -> index" mapping, and its holdings
list Canadian lines (AEM.TO) where the US listing trades in the same hours. ETFs missing from
the table fall back to Yahoo's top holdings. Daily closes for every related ticker come
from one Yahoo download (Toss lists no futures, indices or FX); news only for a same-day
analysis, since a headline list cannot be cut to a past date.

Arithmetic and headlines, not a forecast.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

log = logging.getLogger(__name__)

HISTORY_DAYS = 140         # calendar days: 60 trading days of returns plus the 50-day average
CORR_DAYS = 60
NEWS_PER_TICKER = 6
NEWS_MAX = 12
NEWS_TICKERS = 5           # underlying, the top peers and drivers: the headlines that move it


@dataclass(frozen=True)
class Relation:
    underlying: str | None = None   # the ETF or stock a leveraged/inverse product tracks
    leverage: float | None = None   # daily multiple of the underlying, negative for inverse
    peers: tuple[str, ...] = ()
    drivers: tuple[str, ...] = ()   # futures, indices, FX, yields
    note: str | None = None
    proxy: bool = False             # the product tracks its own index; the underlying stands in for it


# Groups an underlying belongs to: its main holdings and what moves it.
_GOLD_MINERS = Relation(peers=("NEM", "AEM", "B", "WPM", "FNV", "KGC"),
                        drivers=("GC=F", "SI=F", "DX-Y.NYB", "^TNX"),
                        note="gold miners: gold futures, the dollar and real yields lead them")
_JUNIOR_MINERS = Relation(peers=("KGC", "AGI", "PAAS", "EQX", "HMY"),
                          drivers=("GC=F", "SI=F", "DX-Y.NYB", "^TNX"))
_SEMIS = Relation(peers=("NVDA", "AVGO", "AMD", "TSM", "MU", "ASML", "QCOM"),
                  drivers=("^SOX", "NQ=F", "^TNX", "^VIX"),
                  note="semiconductors: the PHLX semiconductor index and Nasdaq futures lead them")
_NASDAQ = Relation(peers=("AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL"),
                   drivers=("NQ=F", "^TNX", "^VIX", "DX-Y.NYB"))
_SP500 = Relation(peers=("AAPL", "MSFT", "NVDA", "AMZN", "JPM"), drivers=("ES=F", "^TNX", "^VIX"))
_SMALL_CAPS = Relation(drivers=("RTY=F", "^TNX", "^VIX"))
_BIOTECH = Relation(drivers=("^TNX", "^VIX", "IWM"))
_BANKS = Relation(peers=("JPM", "BAC", "WFC", "GS", "MS"), drivers=("^TNX", "^IRX", "^VIX"))
_TECH = Relation(peers=("AAPL", "MSFT", "NVDA", "AVGO", "ORCL"), drivers=("NQ=F", "^TNX", "^VIX"))
_ENERGY = Relation(peers=("XOM", "CVX", "COP", "EOG"), drivers=("CL=F", "NG=F", "DX-Y.NYB"))
_BITCOIN = Relation(peers=("COIN", "MSTR", "IBIT"), drivers=("BTC-USD", "ETH-USD", "NQ=F"))
_KOSPI = Relation(peers=("005930.KS", "000660.KS", "005380.KS"), drivers=("^KS11", "KRW=X", "^SOX", "ES=F"))

GROUPS: dict[str, Relation] = {
    "GDX": _GOLD_MINERS, "GDXJ": _JUNIOR_MINERS, "SOXX": _SEMIS, "SMH": _SEMIS, "QQQ": _NASDAQ,
    "SPY": _SP500, "IWM": _SMALL_CAPS, "XBI": _BIOTECH, "XLF": _BANKS, "XLK": _TECH, "XLE": _ENERGY,
    "BTC-USD": _BITCOIN, "069500.KS": _KOSPI,
    # single stocks with an obvious group
    "NVDA": Relation(peers=("SOXX", "AMD", "AVGO", "TSM", "MU"), drivers=("^SOX", "NQ=F", "^VIX")),
    "AMD": Relation(peers=("SOXX", "NVDA", "INTC", "TSM"), drivers=("^SOX", "NQ=F")),
    "TSM": Relation(peers=("SOXX", "NVDA", "ASML", "AMAT"), drivers=("^SOX", "NQ=F", "TWD=X")),
    "MU": Relation(peers=("SOXX", "000660.KS", "005930.KS", "WDC"), drivers=("^SOX", "NQ=F")),
    "TSLA": Relation(peers=("QQQ", "RIVN", "NIO", "BYDDY"), drivers=("NQ=F", "^VIX")),
    "COIN": _BITCOIN, "MSTR": _BITCOIN,
    "NEM": Relation(peers=("GDX", "AEM", "B", "WPM"), drivers=_GOLD_MINERS.drivers),
    "005930.KS": Relation(peers=("000660.KS", "MU", "SOXX"), drivers=("^KS11", "^SOX", "KRW=X")),
    "000660.KS": Relation(peers=("005930.KS", "MU", "NVDA", "SOXX"), drivers=("^KS11", "^SOX", "KRW=X")),
}

# Leveraged and inverse products: (underlying, daily multiple).
LEVERAGED: dict[str, tuple[str, float]] = {
    "GDXU": ("GDX", 3), "GDXD": ("GDX", -3), "NUGT": ("GDX", 2), "DUST": ("GDX", -2),
    "JNUG": ("GDXJ", 2), "JDST": ("GDXJ", -2),
    "SOXL": ("SOXX", 3), "SOXS": ("SOXX", -3), "USD": ("SMH", 2), "SSG": ("SMH", -2),
    "TQQQ": ("QQQ", 3), "SQQQ": ("QQQ", -3), "QLD": ("QQQ", 2), "QID": ("QQQ", -2),
    "UPRO": ("SPY", 3), "SPXU": ("SPY", -3), "SPXL": ("SPY", 3), "SPXS": ("SPY", -3),
    "SSO": ("SPY", 2), "SDS": ("SPY", -2), "TNA": ("IWM", 3), "TZA": ("IWM", -3),
    "LABU": ("XBI", 3), "LABD": ("XBI", -3), "FAS": ("XLF", 3), "FAZ": ("XLF", -3),
    "TECL": ("XLK", 3), "TECS": ("XLK", -3), "ERX": ("XLE", 2), "ERY": ("XLE", -2),
    "NVDL": ("NVDA", 2), "NVDU": ("NVDA", 2), "NVDD": ("NVDA", -1), "NVD": ("NVDA", -2),
    "TSLL": ("TSLA", 2), "TSLQ": ("TSLA", -2), "AMDL": ("AMD", 2), "CONL": ("COIN", 2),
    "MSTU": ("MSTR", 2), "BITX": ("BTC-USD", 2), "MUU": ("MU", 2),
    "122630.KS": ("069500.KS", 2), "252670.KS": ("069500.KS", -2), "114800.KS": ("069500.KS", -1),
}

# Products on their own index, close to but not the same as the listed underlying.
PROXY = {"GDXU", "GDXD", "USD", "SSG"}

# When nothing is known: the broad market the ticker trades in.
_MARKET_US = Relation(drivers=("SPY", "QQQ", "^VIX", "^TNX"))
_MARKET_KR = Relation(drivers=("^KS11", "KRW=X", "ES=F"))


def relation_for(ticker: str, holdings: Callable[[str], list[str]] | None = None) -> Relation:
    """The underlying, peers and drivers for ``ticker``; never empty (the market at least)."""
    ticker = ticker.upper()
    underlying, leverage = LEVERAGED.get(ticker, (None, None))
    base = underlying or ticker
    group = GROUPS.get(base)
    if group is None and holdings is not None:
        try:
            top = holdings(base)
        except Exception as exc:  # noqa: BLE001 - not an ETF, or Yahoo down: the market fallback
            log.info("no holdings for %s: %s", base, exc)
            top = []
        if top:
            group = Relation(peers=tuple(top[:6]))
    korean = base.endswith((".KS", ".KQ")) or base.startswith(("^KS", "^KQ"))   # ^KS11 · ^KS200 · ^KQ11
    market = _MARKET_KR if korean else _MARKET_US
    group = group or Relation()
    drivers = group.drivers or market.drivers
    peers = tuple(p for p in group.peers if p not in (ticker, base))
    return Relation(underlying=underlying, leverage=leverage, peers=peers,
                    drivers=tuple(d for d in drivers if d not in (ticker, base)), note=group.note,
                    proxy=ticker in PROXY)


# --- numbers ------------------------------------------------------------------------

Closes = list[tuple[date, float]]


def _returns(closes: list[float]) -> list[float]:
    return [b / a - 1 for a, b in itertools.pairwise(closes) if a]


def _change(closes: list[float], days: int) -> float | None:
    if len(closes) <= days or not closes[-1 - days]:
        return None
    return round((closes[-1] / closes[-1 - days] - 1) * 100, 2)


def _vs_average(closes: list[float], days: int) -> float | None:
    if len(closes) < days:
        return None
    avg = sum(closes[-days:]) / days
    return round((closes[-1] / avg - 1) * 100, 2)


def _corr(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = math.sqrt(sum((x - ma) ** 2 for x in a))
    vb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return round(cov / (va * vb), 2) if va and vb else None


def _beta(target: list[float], base: list[float]) -> float | None:
    """Slope of the target's daily returns on the base's: what a 3x product actually delivered."""
    n = min(len(target), len(base))
    if n < 10:
        return None
    t, b = target[-n:], base[-n:]
    mb, mt = sum(b) / n, sum(t) / n
    var = sum((x - mb) ** 2 for x in b)
    return round(sum((x - mb) * (y - mt) for x, y in zip(b, t)) / var, 2) if var else None


def _aligned(target: Closes, other: Closes) -> tuple[list[float], list[float]]:
    """Daily returns of both on the days both traded (US and Korean holidays differ). Dates are
    each exchange's own: a US close on day D comes after the Korean close on D, so for a Korean
    ticker a US driver's same-date return is half a session ahead (and a past-date analysis sees it)."""
    mine = dict(target)
    days = [d for d, _ in other if d in mine]
    theirs = dict(other)
    a = [mine[d] for d in days][-(CORR_DAYS + 1):]
    b = [theirs[d] for d in days][-(CORR_DAYS + 1):]
    return _returns(a), _returns(b)


def summarize(ticker: str, closes: Closes, target: Closes | None, role: str) -> dict | None:
    if len(closes) < 2:
        return None
    values = [c for _, c in closes]
    row = {"ticker": ticker, "role": role, "price": _round(values[-1]), "as_of": closes[-1][0].isoformat(),
           "chg_1d": _change(values, 1), "chg_5d": _change(values, 5), "chg_20d": _change(values, 20),
           "vs_sma20": _vs_average(values, 20), "vs_sma50": _vs_average(values, 50), "corr_60d": None}
    if target:
        mine, theirs = _aligned(target, closes)
        row["corr_60d"] = _corr(mine, theirs)
    return row


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


# --- Yahoo ----------------------------------------------------------------------------

def yahoo_daily(tickers: list[str], end: date) -> dict[str, Closes]:
    """Daily closes up to ``end`` for all tickers in one request."""
    import yfinance as yf

    data = yf.download(tickers, start=end - timedelta(days=HISTORY_DAYS), end=end + timedelta(days=1),
                       interval="1d", auto_adjust=False, progress=False, threads=True, group_by="column")
    closes = data["Close"]
    out: dict[str, Closes] = {}
    for ticker in tickers:
        if ticker not in closes:
            continue
        series = closes[ticker].dropna()
        out[ticker] = [(stamp.date(), float(value)) for stamp, value in series.items()
                       if math.isfinite(float(value)) and value > 0]
    return out


def yahoo_news(ticker: str) -> list[dict]:
    """[{title, source, at (ISO UTC), ticker}] newest first."""
    import yfinance as yf

    items = []
    for raw in yf.Ticker(ticker).news[:NEWS_PER_TICKER]:
        content = raw.get("content", raw)   # yfinance >= 0.2.50 nests the story under content
        # one line, bounded: titles go into the prompt one per line
        title = " ".join((content.get("title") or "").split())[:200]
        at = content.get("pubDate") or content.get("displayTime")
        if not at and raw.get("providerPublishTime"):
            at = datetime.fromtimestamp(raw["providerPublishTime"], UTC).isoformat()
        # Yahoo files general market stories under big names; keep the ones tagged with this ticker.
        tagged = [t.get("symbol") for t in ((content.get("finance") or {}).get("stockTickers") or [])]
        if tagged and ticker.upper() not in tagged:
            continue
        provider = content.get("provider") or {}
        source = provider.get("displayName") if isinstance(provider, dict) else raw.get("publisher")
        if title and at:
            items.append({"title": title.strip(), "source": source, "at": at, "ticker": ticker})
    return items


def yahoo_holdings(ticker: str) -> list[str]:
    import yfinance as yf

    return list(yf.Ticker(ticker).funds_data.top_holdings.index)


# --- the whole picture ---------------------------------------------------------------------

@dataclass
class RelatedSources:
    """Where the numbers come from; None = this module's Yahoo functions, looked up at call time."""
    daily: Callable[[list[str], date], dict[str, Closes]] | None = None
    news: Callable[[str], list[dict]] | None = None
    holdings: Callable[[str], list[str]] | None = None

    def resolved(self) -> RelatedSources:
        return RelatedSources(self.daily or yahoo_daily, self.news or yahoo_news, self.holdings or yahoo_holdings)


def collect(ticker: str, trade_date: str, sources: RelatedSources | None = None,
            today: date | None = None) -> dict | None:
    """Underlying, peers and drivers with their numbers, and headlines for a same-day analysis.
    Blocking (Yahoo); run it in a thread. None when nothing could be read."""
    sources = (sources or RelatedSources()).resolved()
    ticker = ticker.upper()
    relation = relation_for(ticker, sources.holdings)
    end = date.fromisoformat(trade_date)
    wanted = [ticker] + ([relation.underlying] if relation.underlying else []) + list(relation.peers) \
        + list(relation.drivers)
    wanted = list(dict.fromkeys(wanted))
    closes = sources.daily(wanted, end)
    target = closes.get(ticker)
    rows = []
    for role, names in (("underlying", [relation.underlying] if relation.underlying else []),
                        ("peer", relation.peers), ("driver", relation.drivers)):
        for name in names:
            row = summarize(name, closes.get(name, []), target, role)
            if row:
                rows.append(row)
    if not rows:
        return None
    result: dict = {"ticker": ticker, "underlying": relation.underlying, "leverage": relation.leverage,
                    "proxy": relation.proxy, "note": relation.note, "instruments": rows,
                    "self": summarize(ticker, target or [], None, "self")}
    if relation.underlying and target and closes.get(relation.underlying):
        mine, base = _aligned(target, closes[relation.underlying])
        result["tracking"] = {"beta_20d": _beta(mine[-20:], base[-20:]), "beta_60d": _beta(mine, base)}
    if end == (today or date.today()):
        result["news"] = _news(relation, ticker, sources.news)
    return result


def _news(relation: Relation, ticker: str, fetch: Callable[[str], list[dict]]) -> list[dict]:
    names = ([relation.underlying] if relation.underlying else []) + list(relation.peers[:2]) \
        + list(relation.drivers[:2])
    names = [n for n in dict.fromkeys(names) if n != ticker][:NEWS_TICKERS]
    seen, items = set(), []
    for name in names:
        try:
            got = fetch(name)
        except Exception as exc:  # noqa: BLE001 - one source failing leaves the others
            log.info("no news for %s: %s", name, exc)
            continue
        for item in got:
            key = item["title"].lower()
            if key not in seen:
                seen.add(key)
                items.append(item)
    items.sort(key=lambda i: i["at"], reverse=True)
    return items[:NEWS_MAX]


def _fmt(value: float | None, suffix: str = "%") -> str:
    return "n/a" if value is None else f"{value:+.2f}{suffix}"


def prompt_note(related: dict | None) -> str | None:
    """The picture as a compact English block for the deciding agents."""
    if not related or not related.get("instruments"):
        return None
    lines = [("Related instruments (desk-computed from daily closes; chg = % change over 1/5/20 "
             "sessions (calendar days for crypto), vsSMA = % from the 20/50-day average, corr = 60-day correlation of daily "
             f"returns with {related['ticker']}):")]
    if related.get("underlying"):
        tracking = related.get("tracking") or {}
        proxy = " (its own index; this ETF is a close proxy)" if related.get("proxy") else ""
        beta = {k: "n/a" if tracking.get(k) is None else tracking[k] for k in ("beta_20d", "beta_60d")}
        lines.append(f"- {related['ticker']} is a {related['leverage']:+g}x daily product on "
                     f"{related['underlying']}{proxy}; realized beta to it 20d {beta['beta_20d']}, "
                     f"60d {beta['beta_60d']}. Read the chart structure on the underlying.")
    if related.get("note"):
        lines.append(f"- Context: {related['note']}.")
    for row in [related.get("self")] + related["instruments"]:
        if not row:
            continue
        lines.append(f"- {row['role']} {row['ticker']} {row['price']} ({row['as_of']}): chg "
                     f"{_fmt(row['chg_1d'])}/{_fmt(row['chg_5d'])}/{_fmt(row['chg_20d'])}, vsSMA "
                     f"{_fmt(row['vs_sma20'])}/{_fmt(row['vs_sma50'])}"
                     + (f", corr {row['corr_60d']}" if row.get("corr_60d") is not None else ""))
    news = related.get("news") or []
    if news:
        lines.append("Recent headlines on the related instruments (newest first):")
        lines += [f"- [{n['at'][:16]}] {n['ticker']}: {n['title']}" + (f" ({n['source']})" if n.get("source") else "")
                  for n in news]
    lines.append("Weigh these with the analysts' reports: say whether the underlying, peers and drivers "
                 "confirm or contradict the call, and if your call goes against them, explain why.")
    return "\n".join(lines)
