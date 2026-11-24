"""Channel and trendline signals on watched tickers and timeframes (#613), sent through desk.notify.

The lines are desk.channels' (the same ``pick`` /api/lines draws and desk.lines_bt measured), found on
the finished bars only. Just after a bar of a watched ticker · timeframe closes, that bar is judged:

- **touch**: its low reaches a line below the price (a channel's lower line, a rising support) without
  closing through it, the bar before not near it - or its high a line above (upper line, falling
  resistance). "Arrived at the line", not "sitting on it".
- **break**: its close is the first beyond a trendline (desk.channels' ``broken_at``) or a channel.
- **retest**: a trendline broken within HORIZON bars is reached again from the broken side and the bar
  closes on that side still.
- **long** (daily bars only, #679): a long line of months (desk.long_lines) is reached - the bar's low
  arrives at a long support (high at a long resistance) and its close holds: "장기 지지 시험" - or its
  second close beyond it confirms the break. Read once the daily bar has ended.

What goes out is gated: the watch is off until the owner turns it on (``line_watch``, default off);
the same line · signal goes once a KST day; a ticker · timeframe · kind of signal waits COOLDOWN_MIN
(or COOLDOWN_BARS bars) before the next; at most DAILY_MAX a day, PER_TICKER_MAX of them per ticker.
Every signal sent is kept in ``line_signals``. The text says what happened in plain words, the line ·
the price · the past rate (desk.lines_bt), that short bars often mislead, and that it is no advice.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import blake2s
from urllib.parse import quote

from sqlalchemy import and_, desc, insert, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from . import channels, line_watch_bars, lines_bt, long_lines, long_lines_bt, notify
from .db import aware, line_signals, line_watch, now

log = logging.getLogger(__name__)

KIND = "line_signal"
TIMEFRAMES = line_watch_bars.TIMEFRAMES
SHORT = {"5m", "10m", "15m", "60m", "120m"}              # bars short enough to warn about
EVENTS = ("touch", "break", "retest", "long")
SIDES = ("both", "support", "resistance")
_RESISTANCE = {"touch_resistance", "break_up", "retest_up", "long_resistance", "long_break_up"}
_TF_KO = {**channels.TF_KO, "10m": "10분봉", "1w": "주봉"}
_SWING_K = {**channels.SWING_K, "10m": 3, "1w": 3}
_HORIZON = {**channels.HORIZON, "10m": 8, "1w": 5}
MAX_TICKERS = 10
COOLDOWN_MIN = 30
COOLDOWN_BARS = 2
DAILY_MAX = 12
PER_TICKER_MAX = 6
GRACE_SEC = 20             # after a bar's end before it is read (the source's last bar settles)
FETCH_SLACK_SEC = 5        # ``fetched_at`` is when the read ended (in whole seconds): it began a little before
IDLE_SEC = 15 * 60         # the longest wait when no bar is trading (a closed market)
TICK_SEC = 30
PASS_MAX = 20              # market data reads in one pass; the rest wait for the next (a 4-hour mark makes all due)
DEFAULT = {"enabled": False, "tickers": ["NQ=F"], "timeframes": ["60m"],
           "events": {e: True for e in EVENTS}, "rules": None}
NO_ADVICE = "※ 매수 · 매도 추천이 아닙니다"
# #613 a line's edge is thin (#679: long supports 40% against 35-36%): every line signal is "참고" - the
# title says it and it goes out under tone_neutral, so a device or channel taking warnings only skips it
REFERENCE = "참고"
TONE = "neutral"

_ICON = {"touch_support": "🟢 지지", "touch_resistance": "🔵 저항", "break_down": "🔻 하향 이탈",
         "break_up": "🔺 상향 이탈", "retest_down": "↩️ 되돌림", "retest_up": "↩️ 되돌림",
         "long_support": "🟢 장기 지지 시험", "long_resistance": "🔵 장기 저항 시험",
         "long_break_down": "🔻 장기 지지 이탈", "long_break_up": "🔺 장기 저항 돌파"}
_SAY = {
    "touch_support": "가격이 지지 구간({line})에 닿았어요 → 하락이 멈추는지 지켜볼 자리",
    "touch_resistance": "가격이 저항 구간({line})에 닿았어요 → 상승이 막히는지 지켜볼 자리",
    "break_down": "종가가 {line} 아래로 넘었어요 → 다시 선 위로 돌아오는지 지켜볼 자리",
    "break_up": "종가가 {line} 위로 넘었어요 → 다시 선 아래로 돌아오는지 지켜볼 자리",
    "retest_down": "아래로 이탈한 {line}에 다시 닿았지만 넘지 못했어요 → 이탈이 굳는지 지켜볼 자리",
    "retest_up": "위로 이탈한 {line}에 다시 닿았지만 넘지 못했어요 → 이탈이 굳는지 지켜볼 자리",
    "long_support": "몇 달 이어 온 {line}을 시험 중이에요 → 버티는지 지켜볼 자리",
    "long_resistance": "몇 달 이어 온 {line}을 시험 중이에요 → 막히는지 지켜볼 자리",
    "long_break_down": "종가가 이틀째 {line} 아래예요 → 다시 선 위로 돌아오는지 지켜볼 자리",
    "long_break_up": "종가가 이틀째 {line} 위예요 → 다시 선 아래로 돌아오는지 지켜볼 자리",
}
_DIR_KO = {"up": "상승", "down": "하락", "flat": "횡보"}


def _bar_end(bar: dict, tf: str) -> datetime:
    """When a bar ends by its length (for a folded 2h / 4h bar, an estimate - see _finished_end)."""
    return datetime.fromisoformat(bar["at"]) + timedelta(minutes=line_watch_bars.MINUTES[tf])


def _finished_end(bars: list[dict], i: int, tf: str) -> datetime | None:
    """When ``bars[i]`` ended; None while it may still trade. desk.prices folds 2h / 4h bars from 60m
    ones by the exchange's day, so around a session break a fold runs past its length: it has ended
    only once the next one has begun."""
    if tf in channels.GROUPED:
        return datetime.fromisoformat(bars[i + 1]["at"]) if i + 1 < len(bars) else None
    return _bar_end(bars[i], tf)


def detect(bars: list[dict], tf: str, now: datetime | None = None, *, confirmed: bool = False) -> list[dict]:
    """The signals of the last finished bar of ``bars`` (oldest first; a last bar still trading at
    ``now`` is left out)."""
    now = now or datetime.now(UTC)
    usable = [b for b in bars if b["low"] > 0 and b["close"] > 0 and b["high"] >= b["low"]]
    if usable and not confirmed and (tf in channels.GROUPED or _bar_end(usable[-1], tf) > now):
        usable = usable[:-1]
    k = _SWING_K[tf]
    a = channels.atr(usable)
    if len(usable) < channels.MIN_SPAN + 4 * k + 1 or not a:
        return []
    c = len(usable) - 1
    unit = a / usable[c]["close"]
    touch, brk = channels.TOUCH_ATR * unit, channels.BREAK_ATR * unit
    lo = [math.log(b["low"]) for b in usable]
    hi = [math.log(b["high"]) for b in usable]
    cl = [math.log(b["close"]) for b in usable]
    found_channels, found_lines = channels.pick(usable, tf, unit, a, swing_k=k)
    out: list[dict] = []

    def add(rule, role, line_id, name, f):
        out.append({"event": rule.split("_")[0], "rule": rule, "role": role, "line_id": line_id, "line": name,
                    "at": usable[c]["at"], "price": usable[c]["close"], "line_price": math.exp(f(c))})

    def touched_from_above(f):
        return lo[c] <= f(c) + touch and cl[c] >= f(c) - brk and lo[c - 1] > f(c - 1) + touch

    def touched_from_below(f):
        return hi[c] >= f(c) - touch and cl[c] <= f(c) + brk and hi[c - 1] < f(c - 1) - touch

    for ch in found_channels:
        cid = f"{tf}-channel-{usable[ch.i]['at']}-{usable[ch.j]['at']}"
        word = _DIR_KO[channels._direction(ch, len(usable))]
        for f, side, ko in ((ch.lower, "lower", "하단"), (ch.upper, "upper", "상단")):
            name = f"{word} 채널 {ko}"
            if side == "lower" and touched_from_above(f):
                add("touch_support", "channel_lower", cid, name, f)
            elif side == "upper" and touched_from_below(f):
                add("touch_resistance", "channel_upper", cid, name, f)
        if cl[c] < ch.lower(c) - brk and cl[c - 1] >= ch.lower(c - 1) - brk:
            add("break_down", "channel_break", cid, f"{word} 채널 하단", ch.lower)
        elif cl[c] > ch.upper(c) + brk and cl[c - 1] <= ch.upper(c - 1) + brk:
            add("break_up", "channel_break", cid, f"{word} 채널 상단", ch.upper)
    for t in found_lines:
        tid = f"{tf}-trend-{t.kind}-{usable[t.i]['at']}-{usable[t.j]['at']}"
        support = t.kind == "support"
        name = "상승 추세선" if support else "하락 추세선"
        if t.broken_at is None:
            if support and touched_from_above(t.at):
                add("touch_support", "trend_support", tid, name, t.at)
            elif not support and touched_from_below(t.at):
                add("touch_resistance", "trend_resistance", tid, name, t.at)
        elif t.broken_at == c:
            add("break_down" if support else "break_up", "trend_break", tid, name, t.at)
        elif c - t.broken_at <= _HORIZON[tf]:    # back at the line from the broken side, arriving
            if support and hi[c] >= t.at(c) - touch and cl[c] < t.at(c) and hi[c - 1] < t.at(c - 1) - touch:
                add("retest_down", "trend_retest", tid, name, t.at)
            elif not support and lo[c] <= t.at(c) + touch and cl[c] > t.at(c) and lo[c - 1] > t.at(c - 1) + touch:
                add("retest_up", "trend_retest", tid, name, t.at)
    if tf == "1d":
        _long(usable, add, lo, hi, cl)
    return out


def _long(usable: list[dict], add, lo: list[float], hi: list[float], cl: list[float]) -> None:
    """#679 the long lines' signals on the last finished daily bar (``add`` appends to detect's list)."""
    a = long_lines.atr(usable)
    if not a:
        return
    c = len(usable) - 1
    unit = a / usable[c]["close"]
    touch, brk = long_lines.TOUCH_ATR * unit, long_lines.BREAK_ATR * unit
    for ln in long_lines.find(usable, "1d", unit):
        lid = f"1d-long-{ln.kind}-{usable[ln.i]['at']}-{usable[ln.j]['at']}"
        support = ln.kind == "support"
        since = datetime.fromisoformat(usable[ln.touches[0]]["at"]).astimezone(long_lines.KST)  # a KR day: KST
        name = f"{since.month}월 {since.day}일부터의 장기 {'상승 지지선' if support else '하락 저항선'}"
        if ln.broken_at is None:
            if support and lo[c] <= ln.at(c) + touch and cl[c] >= ln.at(c) - brk and lo[c - 1] > ln.at(c - 1) + touch:
                add("long_support", "long_support", lid, name, ln.at)
            elif not support and hi[c] >= ln.at(c) - touch and cl[c] <= ln.at(c) + brk and                     hi[c - 1] < ln.at(c - 1) - touch:
                add("long_resistance", "long_resistance", lid, name, ln.at)
        elif ln.run == long_lines.CONFIRM_BARS:        # this close confirmed it
            add("long_break_down" if support else "long_break_up", "long_break", lid, name, ln.at)


def _fmt(price: float) -> str:
    return f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"


def _rate_line(sig: dict, rates: dict) -> str:
    role = sig["role"]
    got = (rates.get("channel") or {}).get(role.split("_")[1]) if role.startswith("channel_") and role != "channel_break" \
        else rates.get(role) if role in ("trend_support", "trend_resistance") \
        else rates.get("trend_break") if role == "trend_break" else None
    if role.startswith("long_"):
        return long_lines_bt.rate_text(role)
    if role in ("channel_break", "trend_retest"):
        return "과거 맞은 비율: 이 사건은 따로 재지 않았어요"
    if not got or not got.get("n"):
        return "과거 맞은 비율: 이 선 · 시간봉은 아직 재지 않았어요"
    pct = (lambda x: "-" if x is None else f"{x * 100:.0f}%")
    if role == "trend_break":
        return (f"과거 막 이탈 뒤 {got['horizon_bars']}봉 안에 1 ATR 더 간 비율 {pct(got['follow_rate'])} "
                f"(같은 자리 아무 선 {pct(got['baseline_rate'])}, {got['n']}번)"
                f"{lines_bt.edge_note(got['follow_rate'], got['baseline_rate'])}")
    return (f"과거 닿은 뒤 {got['horizon_bars']}봉 안 반등 {pct(got['bounce_rate'])} · 이탈 {pct(got['break_rate'])} "
            f"(같은 자리 아무 선 반등 {pct(got['baseline_bounce_rate'])}, {got['n']}번)"
            f"{lines_bt.edge_note(got['bounce_rate'], got['baseline_bounce_rate'])}")


def text(sig: dict, ticker: str, tf: str, rates: dict) -> tuple[str, str]:
    """(title, body) of a signal: what happened and what to watch / the line, the price, the past rate /
    the short-bar warning / no advice."""
    tf_ko = _TF_KO[tf]
    title = f"{_ICON[sig['rule']]} · {REFERENCE} | {ticker} {tf_ko} — {_SAY[sig['rule']].format(line=sig['line'])}"
    gap = (sig["price"] / sig["line_price"] - 1) * 100
    price_label = "관측가" if sig.get("closed_at") else "지금"
    lines = [f"선 {_fmt(sig['line_price'])} · {price_label} {_fmt(sig['price'])} ({gap:+.2f}%)", _rate_line(sig, rates)]
    if tf in SHORT:
        lines.append(f"⚠️ {tf_ko}은 짧은 봉이라 신호가 자주 틀릴 수 있어요")
    if sig.get("closed_at"):
        lines.append(f"확정 봉 마감 {sig['closed_at']} · 원천 {sig['source']} · 지연 여유 {sig['lag_minutes']}분")
        lines.append(f"마감 이후 확인 {sig['fetched_at']}")
        if tf in line_watch_bars.ALERT_CHART_FRAMES:
            lines.append(f"차트는 {_TF_KO[tf]}으로 열려요")
    lines.append(NO_ADVICE)
    return title, "\n".join(lines)


def _timeframes(values: list[str]) -> list[str]:
    tfs = [channels.TF_ALIASES.get(t, t) for t in values]
    if any(t not in TIMEFRAMES for t in tfs):
        raise ValueError(f"timeframes must be among {', '.join(TIMEFRAMES)}")
    return list(dict.fromkeys(tfs))


def _events(events: dict) -> dict:
    if set(events) - set(EVENTS):
        raise ValueError(f"events must be among {', '.join(EVENTS)}")
    return {e: bool(events.get(e, True)) for e in EVENTS}


def _clean_rules(rules: list[dict] | None) -> list[dict] | None:
    if rules is None:
        return None
    if len(rules) > MAX_TICKERS:
        raise ValueError(f"at most {MAX_TICKERS} rules")
    cleaned = []
    seen = set()
    for rule in rules:
        ticker = rule.get("ticker")
        if not ticker or ticker in seen:
            raise ValueError("each rule needs a unique ticker")
        seen.add(ticker)
        tfs = _timeframes(rule.get("timeframes") or [])
        side = rule.get("side", "both")
        if side not in SIDES:
            raise ValueError(f"side must be among {', '.join(SIDES)}")
        enabled = bool(rule.get("enabled"))
        if enabled and not tfs:
            raise ValueError("an enabled rule needs timeframes")
        if tfs and line_watch_bars.market_of(ticker) is None:
            raise ValueError(f"unsupported_market: {ticker}")
        cleaned.append({"ticker": ticker, "enabled": enabled, "timeframes": tfs,
                        "events": _events(rule.get("events") or {}), "side": side})
    return cleaned


def allowed_support(ticker: str | None = None) -> dict:
    return line_watch_bars.allowed_support(ticker)


def clean(body: dict) -> dict:
    """A watch setting as stored: known timeframes and events, tickers as given (the API checks them)."""
    tfs = _timeframes(body.get("timeframes") or [])
    tickers = list(dict.fromkeys(body.get("tickers") or []))
    if len(tickers) > MAX_TICKERS:
        raise ValueError(f"at most {MAX_TICKERS} tickers")
    rules = _clean_rules(body.get("rules"))
    if rules is None and any(tf not in channels.TIMEFRAMES for tf in tfs):
        for ticker in tickers:
            if line_watch_bars.market_of(ticker) is None:
                raise ValueError(f"unsupported_market: {ticker}")
    return {"enabled": bool(body.get("enabled")), "tickers": tickers, "timeframes": tfs,
            "events": _events(body.get("events") or {}), "rules": rules}


def _selected(sig: dict, events: dict, side: str) -> bool:
    if not events.get(sig["event"], False):
        return False
    return side == "both" or (sig["rule"] in _RESISTANCE) == (side == "resistance")


def admit(sig: dict, ticker: str, tf: str, today: list[dict], before: list[dict] = ()) -> bool:
    """Whether ``sig`` goes out given the KST day's signals sent so far (``before``: the day before's,
    for the cooldown across midnight only)."""
    if any(r["ticker"] == ticker and r["timeframe"] == tf and r["rule"] == sig["rule"] and r["line_id"] == sig["line_id"]
           for r in today):
        return False                                   # the same line, the same signal: once a day
    at = datetime.fromisoformat(sig["at"])
    gap = _cooldown(tf)
    if any(r["ticker"] == ticker and r["timeframe"] == tf and r["event"] == sig["event"]
           and abs(at - datetime.fromisoformat(r["at"])) < gap for r in [*before, *today]):
        return False
    if len(today) >= DAILY_MAX:
        return False
    return sum(1 for r in today if r["ticker"] == ticker) < PER_TICKER_MAX


def _cooldown(tf: str) -> timedelta:
    return timedelta(minutes=max(COOLDOWN_MIN, COOLDOWN_BARS * line_watch_bars.MINUTES[tf]))


def chart_url(ticker: str, tf: str, signal_id: int | None = None) -> str:
    """Where the alert opens: that ticker · timeframe's chart (the timeline's link, frontend lineSignals.chartHref)."""
    url = f"/#/charts?t={quote(ticker, safe='')}&iv={tf}"
    if tf in line_watch_bars.ALERT_CHART_FRAMES:
        url += f"&signal_tf={tf}"
        if signal_id is not None:
            url += f"&signal_id={signal_id}"
    return url


def kst_day(at: str) -> str:
    return datetime.fromisoformat(at).astimezone(channels.KST).date().isoformat()


@dataclass(frozen=True)
class SignalOwner:
    user_id: int
    generation: str

    @classmethod
    def legacy(cls, user_id: int, password_verifier: str) -> SignalOwner:
        # A salted verifier distinguishes accounts even if SQLite reuses IDs in the same second.
        version = blake2s(password_verifier.encode(), digest_size=12, person=b"lnsignal").hexdigest()
        return cls(user_id, f"legacy:{version}")


def _signals_owned_by(owner: SignalOwner):
    return and_(line_signals.c.signal_owner_id == owner.user_id,
                line_signals.c.signal_owner_generation == owner.generation)


class LineWatch:
    """The watch setting (one row of line_watch) and the signals sent (line_signals). A signal is kept
    before it is sent and never sent again: at most once, even if the process stops in between."""

    def __init__(self, engine: Engine, clock=lambda: datetime.now(UTC), *, send_notice=None, owner_snapshot=None) -> None:
        self.engine = engine
        self.send_notice = send_notice
        self.owner_snapshot = owner_snapshot
        self.clock = clock
        self._due: dict[tuple[str, str], datetime] = {}

    def settings(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(line_watch.c.body).where(line_watch.c.id == 1)).first()
        got = {**DEFAULT, **(row[0] if row else {})}
        # an event added later (#679 long) reads as its default, not off, for a setting stored before it
        return {**got, "events": {**DEFAULT["events"], **(got.get("events") or {})}}

    def save(self, body: dict) -> dict:
        """Write the setting: the fields ``body`` leaves out keep their value."""
        cur = self.settings()
        value = clean({**cur, **body, "events": {**cur["events"], **(body.get("events") or {})}})
        if (value["enabled"] and value["rules"] is None
                and not (value["tickers"] and value["timeframes"])):
            raise ValueError("a watch that is on needs tickers and timeframes")
        with self.engine.begin() as conn:
            hit = conn.execute(update(line_watch).where(line_watch.c.id == 1).values(body=value, updated_at=now()))
            if not hit.rowcount:
                conn.execute(insert(line_watch).values(id=1, body=value, updated_at=now()))
        self._due.clear()
        return self.settings()

    def recent(self, limit: int = 20, *, owner: SignalOwner | None = None) -> list[dict]:
        stmt = select(line_signals).order_by(desc(line_signals.c.id)).limit(limit)
        if owner is not None:
            allowed = _signals_owned_by(owner)
            if owner.generation.startswith("legacy:"):
                # Legacy timelines retain older ordinary frames. An unowned row never opens an exact alert price.
                allowed = or_(allowed, and_(line_signals.c.signal_owner_id.is_(None),
                                           line_signals.c.signal_owner_generation.is_(None),
                                           line_signals.c.timeframe.not_in(line_watch_bars.ALERT_CHART_FRAMES)))
            stmt = stmt.where(allowed)
        with self.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [{"trade_date": r["trade_date"], "ticker": r["ticker"], "timeframe": r["timeframe"],
                 "event": r["event"], "rule": r["rule"], "line_id": r["line_id"], "at": r["at"],
                 "price": r["price"], "line_price": r["line_price"], "text": r["text"],
                 "recorded_at": aware(r["created_at"]).astimezone(UTC).isoformat(),
                 **({"signal_id": r["id"]} if owner is not None
                    and r["timeframe"] in line_watch_bars.ALERT_CHART_FRAMES else {})} for r in rows]

    def chart_signal(self, signal_id: int, owner: SignalOwner) -> dict | None:
        stmt = select(line_signals.c.ticker, line_signals.c.timeframe, line_signals.c.line_price, line_signals.c.at).where(
            line_signals.c.id == signal_id, _signals_owned_by(owner),
            line_signals.c.timeframe.in_(line_watch_bars.ALERT_CHART_FRAMES),
        )
        with self.engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return dict(row) if row is not None else None

    def _day(self, conn, day: str) -> list[dict]:
        rows = conn.execute(select(line_signals).where(line_signals.c.trade_date == day)).mappings().all()
        return [dict(r) for r in rows]

    def _earlier(self, conn, day: str, tf: str) -> list[dict]:
        gap_days = math.ceil(_cooldown(tf) / timedelta(days=1))
        first = (datetime.fromisoformat(day) - timedelta(days=gap_days)).date().isoformat()
        rows = conn.execute(select(line_signals).where(line_signals.c.trade_date >= first,
                                                       line_signals.c.trade_date < day)).mappings().all()
        return [dict(r) for r in rows]

    def due(self, ticker: str, tf: str, at: datetime | None = None) -> bool:
        return (at or self.clock()) >= self._due.get((ticker, tf), datetime.min.replace(tzinfo=UTC))

    def _schedule(self, ticker: str, tf: str, bars: list[dict], at: datetime, seen: datetime) -> None:
        """Next read: just after the bar now trading ends. A copy of the bars taken before the last bar
        ended (a cache another reader filled) is read again on the next pass; with no bar trading (a
        closed market), after IDLE_SEC."""
        end = _bar_end(bars[-1], tf) if bars else None
        if end is not None and end > at:
            nxt = end + timedelta(seconds=GRACE_SEC)
        elif end is not None and end > seen:
            nxt = at + timedelta(seconds=TICK_SEC)
        else:
            nxt = at + timedelta(seconds=IDLE_SEC)
        self._due[(ticker, tf)] = nxt

    def scan(self, ticker: str, tf: str, bars: list[dict], pusher=None, cfg: dict | None = None,
             fetched_at: str | None = None, *, owner: SignalOwner | None = None) -> list[dict]:
        """Judge the last bar of ``bars`` finished when they were fetched (``fetched_at``: a cached copy
        may predate a bar's end); send and keep the signals that get through. A bar that ended long ago
        (a market closed, the server down) is not news: nothing goes out for it."""
        at = self.clock()
        seen = min(at, datetime.fromisoformat(fetched_at) - timedelta(seconds=FETCH_SLACK_SEC)) if fetched_at else at
        cfg = cfg or self.settings()
        self._schedule(ticker, tf, bars, at, seen)
        found = [s for s in detect(bars, tf, seen) if cfg["events"].get(s["event"], False)]
        if not found:
            return []
        index = next(i for i in range(len(bars) - 1, -1, -1) if bars[i]["at"] == found[0]["at"])
        end = _finished_end(bars, index, tf)
        if end is None or at - end > timedelta(minutes=max(2 * channels.MINUTES[tf], 10)):
            return []
        return self._deliver(ticker, tf, found, pusher, owner=owner)

    def _scan_confirmed(self, ticker: str, tf: str, rules: dict, sample: line_watch_bars.Sample,
                        end: datetime, pusher, *, owner: SignalOwner | None = None) -> list[dict]:
        found = [{**signal, "closed_at": end.isoformat(), "source": sample.source,
                  "fetched_at": sample.fetched_at.isoformat(), "lag_minutes": sample.lag_minutes}
                 for signal in detect(sample.bars, tf, confirmed=True)
                 if _selected(signal, rules["events"], rules["side"])]
        return self._deliver(ticker, tf, found, pusher, owner=owner)

    def _deliver(self, ticker: str, tf: str, found: list[dict], pusher, *,
                 owner: SignalOwner | None = None) -> list[dict]:
        if not found:
            return []
        rates = lines_bt.rates(ticker, tf)
        sent = []
        order = {"long": 0, "break": 1, "retest": 2, "touch": 3}
        for sig in sorted(found, key=lambda s: order[s["event"]]):
            if self.owner_snapshot is not None and (owner is None or owner != self.owner_snapshot()):
                break
            title, body = text(sig, ticker, tf, rates)
            day = kst_day(sig.get("closed_at") or sig["at"])
            try:
                with self.engine.begin() as conn:
                    if not admit(sig, ticker, tf, self._day(conn, day), self._earlier(conn, day, tf)):
                        continue
                    inserted = conn.execute(insert(line_signals).values(
                        trade_date=day, ticker=ticker, timeframe=tf, event=sig["event"], rule=sig["rule"],
                        line_id=sig["line_id"][:160], at=sig["at"], price=sig["price"], line_price=sig["line_price"],
                        signal_owner_id=owner.user_id if owner else None,
                        signal_owner_generation=owner.generation if owner else None,
                        text=f"{title}\n{body}"[:600], created_at=now()))
                    signal_id = inserted.inserted_primary_key[0]
            except IntegrityError:                     # another pass kept it first
                continue
            payload = {"title": title, "body": body, "tag": f"line-{ticker}-{tf}",
                       "url": chart_url(ticker, tf, signal_id if owner else None), "tone": TONE}
            if "closed_at" in sig:
                payload.update({key: sig[key] for key in ("closed_at", "source", "fetched_at", "lag_minutes")})
            recipient = {"owner": owner} if self.owner_snapshot is not None else {}
            (self.send_notice or notify.send)(pusher, KIND, f"tone_{TONE}", payload=payload, **recipient)
            sent.append({**sig, "title": title, "body": body})
        return sent


def _work(cfg: dict):
    if cfg["rules"] is None:
        fallback = {"events": cfg["events"], "side": "both"}
        for ticker in cfg["tickers"]:
            for tf in cfg["timeframes"]:
                yield ticker, tf, fallback, tf not in channels.TIMEFRAMES
        return
    for rule in cfg["rules"]:
        if rule["enabled"] and any(rule["events"].values()):
            for tf in rule["timeframes"]:
                yield rule["ticker"], tf, rule, True


async def run_pass(watch: LineWatch, prices, pusher) -> int:
    """Read at most PASS_MAX due frames. A missing/uncertain bar never falls back to an older signal."""
    owner = await asyncio.to_thread(watch.owner_snapshot) if watch.owner_snapshot is not None else None
    if watch.owner_snapshot is not None and owner is None:
        return 0
    cfg = await asyncio.to_thread(watch.settings)
    if not cfg["enabled"]:
        return 0
    sent = reads = 0
    for ticker, tf, rule, verified in _work(cfg):
        if reads >= PASS_MAX:
            break
        if not watch.due(ticker, tf):
            continue
        try:
            if not verified:
                reads += 1
                got = await prices.candles(ticker, tf, channels.BARS[tf])
                sent += len(await asyncio.to_thread(watch.scan, ticker, tf, got["bars"], pusher, cfg,
                                                    got.get("fetched_at"), owner=owner))
                continue
            market = line_watch_bars.market_of(ticker)
            if market is None:  # a stale stored rule rather than a verified request
                watch._due[(ticker, tf)] = watch.clock() + timedelta(seconds=IDLE_SEC)
                continue
            plan = line_watch_bars.plan(market, tf, watch.clock())
            watch._due[(ticker, tf)] = plan.next_at
            if plan.expected is None:
                log.debug("line watch %s %s: %s", market, tf, plan.reason)
                continue
            reads += 1
            interval, count = line_watch_bars.source_request(tf)
            got = await prices.candles(ticker, interval, count)
            at = watch.clock()
            sample = line_watch_bars.sample(market, tf, plan, got, at)
            if sample.reason is not None:
                log.debug("line watch %s %s: %s", market, tf, sample.reason)
                watch._due[(ticker, tf)] = min(plan.next_at, at + timedelta(seconds=TICK_SEC))
                continue
            sent += len(await asyncio.to_thread(watch._scan_confirmed, ticker, tf, rule, sample, plan.expected.end,
                                                pusher, owner=owner))
        except Exception as exc:  # noqa: BLE001 - one rule's failure leaves the others
            log.warning("line signals %s failed: %s", tf, type(exc).__name__)
            watch._due[(ticker, tf)] = watch.clock() + timedelta(seconds=IDLE_SEC)
    return sent
