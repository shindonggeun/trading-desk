"""NQ=F selling alarms judged every minute on a real-time price (#674), ahead of desk.nq_watch.

desk.nq_watch reads Yahoo's 5-minute NQ=F bars, about ten minutes late (#653 YAHOO_LAG): a slump
reached the phone 10-15 minutes after it. This module asks, every ``TICK_SEC``, whether the price now
has fallen unusually far from the price 5 and 15 minutes ago, on a source that is live:

- ``qqq`` - QQQ's live price (#653 desk.live_bars.live_price: the KIS stream's trades in US regular
  hours) against QQQ's own 1-minute bars, in US regular hours past their first minutes
  (``qqq_hours``: the opening prints and the thin pre-market fired on moves NQ did not make). A QQQ
  drop goes out only when the Binance contract made at least ``CONFIRM`` of it (``confirmed``);
- ``binance`` - otherwise the Binance QQQ perpetual's mark price (the NQ=F estimate's own contract,
  desk.estimate) as 1-minute bars, while CME trades.

A true NQ feed (KIS overseas futures) needs a paid CME subscription, so none of the two is NQ itself:
both move with it (the backtest in docs/nq-watch.md). Rules, each source on its own history:

- ``nq_drop``: the return over the last ``WINDOWS`` minutes at or under the ``LOW_QUANTILE`` of that
  source's rolling returns (CME minutes, or regular-hours minutes for QQQ) over the days before -
  caution, strong at the ``STRONG_QUANTILE``; ``nq_drop15`` the same on 15 minutes when the 5 did not;
- ``nq_from_high``: the price ``nq_watch.FROM_HIGH_PCT`` under the CME session's high - on the
  Binance contract at all hours (QQQ has no bars for most of a CME session).

They share desk.nq_watch's rule names, so desk.market_alerts' waits keep one alarm per move: the
Yahoo bars still judge, and their late alarm of a move already sent is not sent again. Each alert says
which source judged it and how old its price was (``source`` · ``delay_sec``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta

from . import nq_watch
from .estimate import US
from .ttl_cache import TTLCache

log = logging.getLogger(__name__)

TICK_SEC = 20                      # a pass this often: a move is judged within a minute of happening
STEP = timedelta(minutes=1)
WINDOWS = ((5, "nq_drop"), (15, "nq_drop15"))
LOW_QUANTILE = 0.005               # docs/nq-watch.md: as few false alarms as the 5-minute rules
STRONG_QUANTILE = 0.001
MIN_SAMPLE = 1000                  # fewer rolling returns than this: no quantile worth reading
FRESH_SEC = 90                     # a live price older than this is not live
CONFIRM = 0.5                      # a QQQ drop the Binance contract made at least half of
QQQ = "QQQ"
QQQ_FROM = time(9, 35)             # US regular hours past the opening prints
QQQ_UNTIL = time(16, 0)
QQQ_BARS = 2000                    # Yahoo's 1-minute bars: about five regular sessions
HISTORY = timedelta(days=8)        # Binance minutes kept: five CME days and a weekend


def qqq_hours(now: datetime) -> bool:
    """US regular hours on a weekday past the opening minutes (holidays: the caller's calendar)."""
    local = now.astimezone(US.zone)
    return local.weekday() < 5 and QQQ_FROM <= local.time() < QQQ_UNTIL


def _at(bar: dict) -> datetime:
    t = bar.get("_t")                    # the Binance minutes keep theirs parsed (a pass reads 11k of them)
    return t if t is not None else datetime.fromisoformat(bar["at"]).astimezone(UTC)


def _minute(at: datetime) -> datetime:
    return at.astimezone(UTC).replace(second=0, microsecond=0)


def _closes(bars: list[dict], now: datetime) -> dict[datetime, float]:
    """Each finished bar's close by its end."""
    out = {}
    for b in bars:
        end = _at(b) + STEP
        if b.get("close") and end <= now:
            out[end] = float(b["close"])
    return out


def _quantile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[int(q * (len(xs) - 1))]


def thresholds(closes: dict[datetime, float], keep) -> dict[int, tuple[float, float] | None]:
    """Per window, (low, strong) quantiles of the rolling returns whose end ``keep`` takes, or None
    on a thin sample."""
    out = {}
    for mins, _ in WINDOWS:
        span = mins * STEP
        rets = [c / closes[end - span] - 1 for end, c in closes.items()
                if keep(end - STEP) and (end - span) in closes and closes[end - span]]
        out[mins] = ((_quantile(rets, LOW_QUANTILE), _quantile(rets, STRONG_QUANTILE))
                     if len(rets) >= MIN_SAMPLE else None)
    return out


def judge(bars: list[dict], price: float, price_at: datetime, now: datetime, source: str, keep,
          limits: dict | None = None, from_high: bool = True) -> list[dict]:
    """The alarms on ``price`` (``source``'s live price at ``price_at``) against ``bars`` (that source's
    1-minute bars, oldest first); an alert's time is the minute of the price. ``keep`` takes the minutes
    the source is judged in; ``limits`` (thresholds) may be passed in already worked out."""
    if not price or now - price_at > timedelta(seconds=FRESH_SEC) or not keep(price_at):
        return []
    closes = _closes(bars, now)
    limits = limits if limits is not None else thresholds(closes, keep)
    at = _minute(price_at)
    extra = {"source": source, "delay_sec": max(0, round((now - price_at).total_seconds())),
             "quantile": LOW_QUANTILE}
    out = []
    for mins, rule in WINDOWS:
        ref, limit = closes.get(at - mins * STEP), limits.get(mins)
        if not ref or limit is None:
            continue
        ret = price / ref - 1
        if ret < 0 and ret <= limit[0]:
            out.append(nq_watch._alert(rule, at, ret, limit[0], "strong" if ret <= limit[1] else "caution",
                                       window_min=mins, **extra))
            break                          # the 15-minute rule only when the 5 did not fire
    if from_high:
        start = nq_watch._session_start(at)
        session = [b for b in bars if _at(b) >= start and _at(b) + STEP <= now]
        if session:
            high = max(price, *((b.get("high") or b["close"]) for b in session))
            off = price / high - 1
            if off * 100 <= -nq_watch.FROM_HIGH_PCT:
                out.append(nq_watch._alert("nq_from_high", at, off, -nq_watch.FROM_HIGH_PCT / 100, "caution",
                                           high=high, source=source, delay_sec=extra["delay_sec"]))
    return out


class NqFast:
    """One pass a ``TICK_SEC``: QQQ in its hours when its price is live, else the Binance contract.

    ``qqq_price`` returns desk.live_bars.live_price's dict for QQQ; ``qqq_regular`` whether QQQ's
    market is in its regular session now (the broker calendar); ``estimator`` desk.estimate's."""

    def __init__(self, prices, estimator, qqq_price, qqq_regular, now=None) -> None:
        self.prices, self.estimator = prices, estimator
        self.qqq_price, self.qqq_regular = qqq_price, qqq_regular
        self._now = now or (lambda: datetime.now(UTC))
        self._minutes: list[dict] = []          # the Binance contract's 1-minute mark bars
        self._limits: TTLCache[tuple[str, str], dict] = TTLCache(4, 3600.0)
        self.last: dict | None = None           # the last pass: {at, source, price_at, delay_sec, alerts}
        self._down: set[str] = set()            # sources failing now: logged once when they go, once when back

    def _cached_limits(self, source: str, bars: list[dict], now: datetime, keep) -> dict:
        """The thresholds change with the history, not with each pass: worked out again each new hour."""
        key = (source, now.strftime("%Y-%m-%dT%H"))
        got = self._limits.get(key)
        if got is None:
            got = thresholds(_closes(bars, now), keep)
            if any(v is not None for v in got.values()):   # a thin answer is asked again next pass
                self._limits.put(key, got)
        return got

    def _state(self, source: str, ok: bool, why: str = "") -> None:
        if ok and source in self._down:
            self._down.discard(source)
            log.info("NQ fast pass: %s is back", source)
        elif not ok and source not in self._down:
            self._down.add(source)
            log.info("NQ fast pass: %s unavailable: %s", source, why)

    async def _binance_bars(self, now: datetime) -> list[dict]:
        since = (_at(self._minutes[-1]) if self._minutes else now - HISTORY)
        fresh = await self.estimator.minute_marks(nq_watch.TICKER, since)
        for b in fresh:
            b["_t"] = datetime.fromisoformat(b["at"]).astimezone(UTC)
        merged = {b["at"]: b for b in self._minutes}
        merged.update({b["at"]: b for b in fresh})
        cut = now - HISTORY
        self._minutes = [b for _, b in sorted(merged.items()) if _at(b) >= cut]
        return self._minutes

    async def _qqq(self, now: datetime) -> tuple[list[dict], float, datetime] | None:
        """QQQ's 1-minute bars and live price, when its regular session runs, the price is live and the
        bars can judge it (thresholds, and the closes 5 and 15 minutes back) - else the contract judges."""
        if not (qqq_hours(now) and await self.qqq_regular()):
            return None
        try:
            live = await self.qqq_price()
            at = datetime.fromisoformat(live["at"]).astimezone(UTC)
            if not live.get("live") or live.get("estimated") or now - at > timedelta(seconds=FRESH_SEC):
                self._state("qqq", False, f"price {live.get('source')} not live")
                return None
            bars = (await self.prices.candles(QQQ, "1m", QQQ_BARS))["bars"]
        except Exception as exc:  # noqa: BLE001 - QQQ not live now: the contract still is
            self._state("qqq", False, type(exc).__name__)
            return None
        limits = self._cached_limits("qqq", bars, now, qqq_hours)
        closes = _closes(bars, now)
        if any(v is None for v in limits.values()) or any(
                (_minute(at) - mins * STEP) not in closes for mins, _ in WINDOWS):
            self._state("qqq", False, "1-minute bars too thin or behind")
            return None
        self._state("qqq", True)
        return bars, float(live["price"]), at

    async def run_pass(self, now: datetime | None = None) -> tuple[str | None, list[dict]]:
        """(the source the drops were judged on or None, the alerts) - for desk.market_alerts' dispatch.
        The Binance minutes are read each pass: they judge outside QQQ's hours, the session high always,
        and confirm a QQQ drop."""
        now = now or self._now()
        if not nq_watch.cme_open(now):
            return None, []
        marks: list[dict] = []
        try:
            marks = await self._binance_bars(now)
            self._state("binance", True)
        except Exception as exc:  # noqa: BLE001 - Binance down: QQQ may still judge
            self._state("binance", False, type(exc).__name__)
        forming = [b for b in marks if b.get("close")]
        mark = (float(forming[-1]["close"]), min(now, _at(forming[-1]) + STEP)) if forming else None
        if mark is not None and now - mark[1] > timedelta(seconds=FRESH_SEC):
            mark = None                         # the minutes stopped coming: no price to judge or confirm
        qqq = await self._qqq(now)
        if qqq is not None:
            bars, price, at = qqq
            got = [a for a in self._judged("qqq", bars, price, at, now, qqq_hours, False)
                   if confirmed(a, marks, mark, now)]
            self.last["alerts"] = len(got)
            if mark is not None:
                got += [a for a in judge(marks, *mark, now, "binance", nq_watch.cme_open,
                                         self._cached_limits("binance", marks, now, nq_watch.cme_open), True)
                        if a["rule"] == "nq_from_high"]
            return "qqq", got
        if mark is None:
            return None, []
        return "binance", self._judged("binance", marks, *mark, now, nq_watch.cme_open, True)

    def _judged(self, source, bars, price, price_at, now, keep, from_high) -> list[dict]:
        limits = self._cached_limits(source, bars, now, keep)
        got = judge(bars, price, price_at, now, source, keep, limits, from_high)
        self.last = {"at": now.isoformat(timespec="seconds"), "source": source,
                     "price_at": price_at.isoformat(timespec="seconds"),
                     "delay_sec": max(0, round((now - price_at).total_seconds())), "alerts": len(got)}
        return got


def confirmed(alert: dict, marks: list[dict], mark: tuple[float, datetime] | None, now: datetime) -> bool:
    """A QQQ drop the Binance contract moved with: at least ``CONFIRM`` of it over the same minutes (one
    bad print fired a QQQ alarm NQ never saw - docs/nq-watch.md). Kept when the contract has no price."""
    if mark is None or alert["rule"] not in ("nq_drop", "nq_drop15"):
        return True
    ref = _closes(marks, now).get(datetime.fromisoformat(alert["t"]).astimezone(UTC) - alert["window_min"] * STEP)
    if not ref:
        return True
    return (mark[0] / ref - 1) * 100 <= CONFIRM * alert["value"]
