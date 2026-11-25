"""Stop · take-profit alerts on each holding, by its regime (#694).

Principle: lose small. The stop is set ahead where the reason to hold breaks, and crossing it is said
at once. Every ``TICK_SEC`` while a holding's market trades, for each holding the watch takes:

- the price now (the broker's holdings with desk.prices' quote - desk.portfolio) and the average price;
- the daily regime (desk.regime_live.classify on the finished daily bars - today's bar still trading
  is left out, so a line does not move under the price while it is being judged);
- the lines, by regime:
  - stop = the higher of the max loss line (average x (1 - ``max_loss_pct``)) and the structure:
    - trend up: the trailing stop, the highest high of the last ``TRAIL_N`` bars less ``TRAIL_ATR`` ATR;
    - range: the box's low less ``BREAK_ATR`` ATR;
    - otherwise: the lowest low of the last ``SWING_N`` bars less ``BREAK_ATR`` ATR;
  - take profit (a range only): the box's top less ``TOP_ATR`` ATR, while in profit.

Rules, each once a KST day per holding (desk.market_alerts, kind ``holding_alert``, market = the ticker):

- ``hold_stop`` 🔴 손절: the price at or under the stop - strong, past the market cooldown;
- ``hold_near_stop`` 🟠 주의: within ``NEAR_ATR`` ATR above the stop;
- ``hold_trend_down`` 🟠 비중 줄이기: the daily regime is a downtrend;
- ``hold_take_profit`` 🟢 익절 고려: a range's top reached in profit.

Quantities and amounts are never read past the holdings call, stored, logged or sent. What goes out to
the channels (Telegram, a lock screen) carries no number that gives the average away with the
public price: no return against the average, and the max-loss line only by name ("평단 손실 한도"),
neither its price nor the distance to it. That an alert fired at a time still bounds the average (a
max-loss stop fires near average x (1 - max loss)). The return against the average stays in the app
(GET /api/holding-watch, as /api/portfolio). Web push goes to the owner's devices only. Off until the
owner turns it on; the setting (``holding_watch``, one row): on/off and the max loss, overall and per
ticker.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import live, regime_live
from .db import holding_watch, now
from .futures_flow import KST
from .live_bars import market_tz

log = logging.getLogger(__name__)

KIND = "holding_alert"
TICK_SEC = 60
TRAIL_N = 22
TRAIL_ATR = 4.0                         # docs/holding-watch.md: 3 ATR and a 0.5 ATR buffer stopped 9 in 10
SWING_N = 40
BREAK_ATR = 1.0
TOP_ATR = 0.25
NEAR_ATR = 0.5
BARS = 250
MAX_LOSS_PCT = 8.0                      # the default max loss under the average, %
MAX_LOSS_RANGE = (1.0, 50.0)
MAX_TICKERS = 100
RULES = ("hold_stop", "hold_near_stop", "hold_trend_down", "hold_take_profit")
KR_PROBE, US_PROBE = "005930.KS", "SPY"    # a market's calendar asked through one of its tickers
DEFAULT = {"enabled": False, "max_loss_pct": MAX_LOSS_PCT, "tickers": {}}   # off until the owner turns it on


def clean(body: dict) -> dict:
    """A setting as stored: {enabled, max_loss_pct, tickers{ticker: {enabled, max_loss_pct|None}}}."""
    def loss(x, default):
        if x is None:
            return default
        x = float(x)
        if not MAX_LOSS_RANGE[0] <= x <= MAX_LOSS_RANGE[1]:
            raise ValueError(f"max_loss_pct must be within {MAX_LOSS_RANGE[0]}~{MAX_LOSS_RANGE[1]}")
        return round(x, 2)

    tickers = body.get("tickers") or {}
    if len(tickers) > MAX_TICKERS:
        raise ValueError(f"at most {MAX_TICKERS} tickers")
    return {"enabled": bool(body.get("enabled", False)), "max_loss_pct": loss(body.get("max_loss_pct"), MAX_LOSS_PCT),
            "tickers": {t: {"enabled": bool((v or {}).get("enabled", True)),
                            "max_loss_pct": loss((v or {}).get("max_loss_pct"), None)} for t, v in tickers.items()}}


def for_ticker(cfg: dict, ticker: str) -> tuple[bool, float]:
    """(on, max loss %) for one holding."""
    own = cfg.get("tickers", {}).get(ticker) or {}
    on = cfg.get("enabled", True) and own.get("enabled", True)
    return bool(on), float(own.get("max_loss_pct") or cfg.get("max_loss_pct") or MAX_LOSS_PCT)


def _finished(bars: list[dict], now: datetime, ticker: str) -> list[dict]:
    """The daily bars without today's (the holding's own market date) still trading."""
    if not bars:
        return bars
    tz = market_tz(ticker)
    last = datetime.fromisoformat(bars[-1]["at"]).astimezone(tz).date()
    return bars[:-1] if last >= now.astimezone(tz).date() else bars


def session_day(alert: dict) -> str:
    """The holding's own market date of an alert (a US session runs over the KST midnight)."""
    return datetime.fromisoformat(alert["t"]).astimezone(market_tz(alert.get("ticker") or alert["market"])).date().isoformat()


def lines(cls: dict, bars: list[dict], average: float, max_loss_pct: float) -> dict:
    """{regime, label, atr, stop, stop_basis, max_loss_stop, structure_stop, take_profit, tp_basis} from
    the daily classify and the finished daily bars. The structure line follows the regime each day: one
    held from the day before (never moved down) cost return and saved no loss in the backtest
    (docs/holding-watch.md)."""
    atr = cls.get("atr")
    hard = average * (1 - max_loss_pct / 100)
    structure, basis = None, None
    regime, box = cls.get("regime"), cls.get("box")
    if atr and len(bars) >= SWING_N:
        if regime == "trend_up":
            top = max(b["high"] for b in bars[-TRAIL_N:])
            structure, basis = top - TRAIL_ATR * atr, f"추적 손절({TRAIL_N}봉 고점 − {TRAIL_ATR:g} ATR)"
        elif regime == "range" and box:
            structure, basis = box["low"] - BREAK_ATR * atr, f"박스 하단 {_fmt(box['low'])} − {BREAK_ATR:g} ATR"
        else:
            low = min(b["low"] for b in bars[-SWING_N:])
            structure, basis = low - BREAK_ATR * atr, f"최근 {SWING_N}봉 저점 {_fmt(low)} − {BREAK_ATR:g} ATR"
    by_loss = structure is None or structure <= hard
    stop, stop_basis = (hard, f"최대 손실(평단 −{max_loss_pct:g}%)") if by_loss else (structure, basis)
    tp = tp_basis = None
    if regime == "range" and box and atr:
        tp, tp_basis = box["high"] - TOP_ATR * atr, f"박스 상단 {_fmt(box['high'])} 근처"
    return {"regime": regime, "label": cls.get("label"), "atr": atr, "stop": stop, "stop_basis": stop_basis,
            "max_loss_stop": hard, "structure_stop": structure, "structure_basis": basis, "take_profit": tp,
            "tp_basis": tp_basis,
            "stop_is_max_loss": by_loss}


def judge(ticker: str, price: float, average: float, ln: dict, now: datetime) -> list[dict]:
    """The holding's alerts now (each rule at most once a day - desk.market_alerts holds the repeats)."""
    if not price or not average:
        return []
    at = now.astimezone(KST).replace(second=0, microsecond=0).isoformat(timespec="seconds")
    profit = (price / average - 1) * 100
    to_stop = (price / ln["stop"] - 1) * 100 if ln["stop"] else None
    base = {"kind": KIND, "market": ticker[:16], "who": "all", "t": at, "value": round(profit, 2),
            "prev": None if to_stop is None else round(to_stop, 2), "ticker": ticker, "price": price,
            "stop": ln["stop"], "stop_basis": ln["stop_basis"], "stop_is_max_loss": ln["stop_is_max_loss"],
            "regime": ln["regime"], "regime_label": ln["label"], "take_profit": ln["take_profit"],
            "tp_basis": ln["tp_basis"]}
    out = []
    atr = ln.get("atr") or 0.0
    if price <= ln["stop"]:
        out.append({**base, "rule": "hold_stop", "severity": "strong"})
    elif atr and price <= ln["stop"] + NEAR_ATR * atr:
        out.append({**base, "rule": "hold_near_stop", "severity": "caution"})
    if ln["regime"] == "trend_down":
        out.append({**base, "rule": "hold_trend_down", "severity": "caution"})
    if ln["take_profit"] and price >= ln["take_profit"] and price > average:
        out.append({**base, "rule": "hold_take_profit", "severity": "caution"})
    return out


def _fmt(x: float) -> str:
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _pct(x: float) -> str:
    return f"{x:+.1f}%".replace("-", "−")


def text(alert: dict) -> str:
    """e.g. "005930.KS 손절선 아래 · 현재 70,100 · 손절선 71,000 1.3% 아래 · 근거 박스 하단 72,100 − 1 ATR
    · 일봉 박스권". No quantity, no amount, nothing that gives the average away (see the module doc)."""
    ticker, rule = alert.get("ticker") or alert["market"], alert["rule"]
    head = {"hold_stop": "손절선 아래", "hold_near_stop": "손절선 가까이", "hold_trend_down": "하락 추세",
            "hold_take_profit": "익절 고려 구간"}[rule]
    parts = [f"{ticker} {head}"]
    if alert.get("price"):
        parts.append(f"현재 {_fmt(alert['price'])}")
    if rule == "hold_take_profit" and alert.get("take_profit"):
        parts.append(f"{alert.get('tp_basis') or '익절 후보'}")
    elif alert.get("stop_is_max_loss"):
        parts.append("손절선 = 평단 손실 한도")          # neither its price nor the distance: the average
    elif alert.get("prev") is not None:
        line = f"손절선 {_fmt(alert['stop'])}"
        parts.append(f"{line}까지 {_pct(-alert['prev'])}" if alert["prev"] > 0 else f"{line} {abs(alert['prev']):.1f}% 아래")
    if alert.get("stop_basis") and rule != "hold_take_profit" and not alert.get("stop_is_max_loss"):
        parts.append(f"근거 {alert['stop_basis']}")
    if alert.get("regime_label"):
        parts.append(f"일봉 {alert['regime_label']}")
    return " · ".join(parts)


class HoldingWatch:
    """The setting (one row of holding_watch) and a pass over the holdings."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.last: dict[str, dict] = {}         # ticker -> its lines on the last pass (GET /api/holding-watch)
        self._daily: dict[str, tuple[str, dict, list[dict]]] = {}   # ticker -> (market date, classify, bars)

    def settings(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(holding_watch.c.body).where(holding_watch.c.id == 1)).first()
        return clean({**DEFAULT, **(row[0] if row else {})})

    def save(self, body: dict) -> dict:
        """Write the setting: the fields ``body`` leaves out keep their value; ``tickers`` merges by ticker."""
        cur = self.settings()
        tickers = dict(cur["tickers"])
        for t, v in (body.get("tickers") or {}).items():
            if v is None:
                tickers.pop(t, None)            # null: the ticker's own setting goes (the overall one applies)
            else:
                tickers[t] = {**tickers.get(t, {}), **v}
        value = clean({**cur, **body, "tickers": tickers})
        for _ in range(2):                      # the first two writes at once: the loser updates the winner's row
            try:
                with self.engine.begin() as conn:
                    hit = conn.execute(update(holding_watch).where(holding_watch.c.id == 1)
                                       .values(body=value, updated_at=now()))
                    if not hit.rowcount:
                        conn.execute(insert(holding_watch).values(id=1, body=value, updated_at=now()))
                break
            except IntegrityError:
                continue
        return self.settings()

    async def _bars(self, prices, ticker: str, now: datetime) -> tuple[dict, list[dict]]:
        """The finished daily bars and their classify, read once a market date (they change only then)."""
        day = now.astimezone(market_tz(ticker)).date().isoformat()
        hit = self._daily.get(ticker)
        if hit and hit[0] == day:
            return hit[1], hit[2]
        bars = _finished((await prices.candles(ticker, "1d", BARS)).get("bars") or [], now, ticker)
        cls = await asyncio.to_thread(regime_live.classify, bars, "1d")
        if len(self._daily) > MAX_TICKERS * 2:
            self._daily.clear()
        self._daily[ticker] = (day, cls, bars)
        return cls, bars

    async def run_pass(self, book, prices, calendar, dispatch, now: datetime | None = None) -> int:
        """Judge each holding whose market trades now; ``dispatch`` (desk.market_alerts) records and sends.
        How many alerts went out."""
        now = now or datetime.now(UTC)
        cfg = await asyncio.to_thread(self.settings)
        if not cfg["enabled"]:
            self.last = {}
            return 0
        # no market trading: not even the holdings call (the broker, the quotes, the FX rate)
        opened = {m: await live.is_open(calendar, t, now) for m, t in (("KR", KR_PROBE), ("US", US_PROBE))}
        if not any(opened.values()):
            return 0
        snap = await book.snapshot()
        rows = [(h.get("yahoo_ticker"), h.get("last_price"), h.get("average_price"))
                for h in snap.get("holdings") or []]
        alerts, seen = [], {}
        for ticker, price, average in rows:
            if not ticker or not price or not average:
                continue
            on, max_loss = for_ticker(cfg, ticker)
            try:
                if not on:
                    continue
                if not await live.is_open(calendar, ticker, now):
                    if ticker in self.last:      # the other market's holding: its lines from its own session stay
                        seen[ticker] = self.last[ticker]
                    continue
                cls, bars = await self._bars(prices, ticker, now)
                ln = lines(cls, bars, float(average), max_loss)
                seen[ticker] = {"ticker": ticker, "regime": ln["regime"], "label": ln["label"],
                                "stop": None if ln["stop_is_max_loss"] else ln["stop"], "stop_basis": ln["stop_basis"],
                                "to_stop_pct": round((price / ln["stop"] - 1) * 100, 2),
                                "take_profit": ln["take_profit"], "tp_basis": ln["tp_basis"],
                                "profit_pct": round((price / average - 1) * 100, 2), "max_loss_pct": max_loss,
                                "basis": cls.get("basis") or [], "at": now.isoformat(timespec="seconds")}
                alerts += judge(ticker, float(price), float(average), ln, now)
            except Exception as exc:  # noqa: BLE001 - that holding only, the others still go
                log.info("holding watch %s skipped: %s", ticker, type(exc).__name__)
        self.last = seen
        if not alerts:
            return 0
        return len(await asyncio.to_thread(dispatch, alerts))
