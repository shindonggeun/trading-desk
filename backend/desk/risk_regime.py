"""Market-wide selling pressure (#146): is the whole market still dumping, or has the dump run out?

A dip catch lives or dies on that, not on the ticker's own drawdown. Free, key-less Yahoo daily
bars only: the VIX and its term structure (^VIX9D · ^VIX3M), the Nasdaq-100 and the semiconductor
index (the KOSPI for Korean tickers), the dollar, the 10-year yield (and the won for Korea).

- ``score``: a 0~100 selling score from what is there now (each indicator is optional - one that
  failed to load is listed in ``missing`` and the rest still score), the phase and its evidence.
- ``apply``: the rule on a finished ``desk.playbook.build`` - while the selling goes on only the
  first step of the dip-catch ladder stays (steps 2 · 3 wait for an exhaustion signal); once the
  selling shows exhaustion, the short sits out (a short into the capitulation's end is the trade
  that gives back the most).

Pure functions apart from ``fetch`` / ``Cache`` (Yahoo, run in a worker thread; ``desk.api`` builds
one Cache on the app's price source)."""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

VERSION = 1
US = ("^VIX", "^VIX9D", "^VIX3M", "^NDX", "^SOX", "DX-Y.NYB", "^TNX")
KR = (*US, "^KS11", "KRW=X")
# Yahoo stamps a daily bar at local midnight of the market it trades in: the KOSPI at Seoul's
# (15:00 UTC the day before), the won at London's (23:00 UTC the day before in summer), the rest
# at New York's. A bar's date is read in that zone.
_ZONES = {"^KS11": ZoneInfo("Asia/Seoul"), "KRW=X": ZoneInfo("Europe/London")}
_NEW_YORK = ZoneInfo("America/New_York")
# A Korean plan sees these on its own date. The rest only up to the day before: the US session
# closes after Seoul's, and the won's London-day bar runs on past Seoul's close too.
SAME_DAY_KR = ("^KS11",)
HISTORY_BARS = 90        # daily bars per indicator: 20-day bases plus the 10-day look-back
MIN_BARS = 21
LOOKBACK = 10            # sessions an exhaustion signal looks back over (and the recent peak score)

SELLING = 55             # the score at or above this = the selling goes on
STRESS = 50              # a recent score at or above this = there was a dump for a signal to end
EXHAUST_SIGNALS = 2      # this many exhaustion signals after a dump = its end
VIX_PEAK = 25.0          # a VIX turn needs a peak at least this high ...
VIX_SPIKE = 1.3          # ... and this many times the median before it
VIX_OFF_PEAK = 0.85      # ... and the VIX back under this share of the peak
GAP_DOWN = 0.01          # an index gap of at least 1% under the prior close
STREAK_END = 4           # down closes in a row before an up close for "the streak broke"

# How much each part counts in the score: fear and the index tape first, the macro drivers lighter.
WEIGHTS = {"vix": 1.5, "term": 1.5, "index": 1.5, "streak": 1.0, "dollar": 0.5, "rates": 0.5, "krw": 0.5}
PHASE_LABEL = {"selling": "투매 진행", "exhausting": "투매 끝물", "calm": "평상", "unknown": "판단 불가"}

Series = dict[str, list[dict]]   # symbol -> daily bars {"day", "open", "high", "low", "close"}, oldest first


def is_korean(ticker: str) -> bool:
    return ticker.upper().endswith((".KS", ".KQ"))


def symbols_for(ticker: str) -> tuple[str, ...]:
    return KR if is_korean(ticker) else US


def symbols_for_all(tickers: list[str]) -> tuple[str, ...]:
    return KR if any(is_korean(t) for t in tickers) else US


def _day(at: str, symbol: str) -> str:
    stamp = datetime.fromisoformat(at)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(_ZONES.get(symbol, _NEW_YORK)).date().isoformat()


def fetch(symbols: tuple[str, ...], candles: Callable | None = None, count: int = HISTORY_BARS) -> Series:
    """Daily bars per symbol; one that fails or comes back empty is left out (``score`` lists it)."""
    if candles is None:
        from .prices import yahoo_candles as candles
    out: Series = {}
    for symbol in symbols:
        try:
            bars = candles(symbol, "1d", count)
        except Exception as exc:  # noqa: BLE001 - one indicator down leaves the others
            log.info("risk regime: %s failed: %s", symbol, exc)
            continue
        rows = [{"day": _day(b.at, symbol), "open": b.open, "high": b.high, "low": b.low, "close": b.close}
                for b in bars if b.close]
        if rows:
            out[symbol] = rows
    return out


def strict_for(symbol: str, korean: bool, intraday: bool) -> bool:
    """Whether a plan on a day sees only the bars of the days before it. A daily plan is made on its
    session's close and sees that day's bar, but a Korean one not the bars that close after Seoul's
    (``SAME_DAY_KR``). An intraday plan is made during its session: that day's close is ahead of it."""
    return intraday or (korean and symbol not in SAME_DAY_KR)


def as_of(series: Series, day: str, *, korean: bool = False, intraday: bool = False) -> Series:
    """The bars an analysis on ``day`` had (``strict_for``)."""
    out = {}
    for symbol, bars in series.items():
        strict = strict_for(symbol, korean, intraday)
        kept = [b for b in bars if (b["day"] < day if strict else b["day"] <= day)]
        if kept:
            out[symbol] = kept
    return out


# ---------------------------------------------------------------- parts


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at ``lo``, 100 at ``hi``, clipped."""
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100))


def _closes(series: Series, symbol: str) -> list[float]:
    return [b["close"] for b in series.get(symbol) or []]


def _aligned(series: Series, a: str, b: str) -> list[tuple[str, float]]:
    """``a / b`` on the days both have, oldest first."""
    other = {x["day"]: x["close"] for x in series.get(b) or []}
    return [(x["day"], x["close"] / other[x["day"]]) for x in series.get(a) or [] if other.get(x["day"])]


def _change(closes: list[float], n: int) -> float | None:
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n and closes[-1 - n] else None


def _streak(closes: list[float]) -> int:
    """Down closes in a row up to the last bar."""
    n = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            n += 1
        else:
            break
    return n


def _indices(korean: bool) -> tuple[str, ...]:
    return ("^KS11", "^SOX") if korean else ("^NDX", "^SOX")


def main_index(series: Series, korean: bool) -> str | None:
    return next((s for s in _indices(korean) if len(series.get(s) or []) >= MIN_BARS), None)


def parts(series: Series, korean: bool = False) -> list[dict]:
    """Each available part's 0~100 and the evidence in Korean."""
    out = []
    vix = _closes(series, "^VIX")
    if len(vix) >= MIN_BARS:
        med = statistics.median(vix[-21:-1])
        ratio = vix[-1] / med if med else 1.0
        out.append({"id": "vix", "score": max(_ramp(ratio, 1.0, 1.6), _ramp(vix[-1], 20, 40)),
                    "text": f"VIX {vix[-1]:.1f} (20일 중앙값의 {ratio:.2f}배)"})
    term = []
    m3 = _aligned(series, "^VIX", "^VIX3M")
    if m3:
        term.append((_ramp(m3[-1][1], 0.9, 1.1), f"VIX/VIX3M {m3[-1][1]:.2f}" + (" 역전" if m3[-1][1] > 1 else "")))
    d9 = _aligned(series, "^VIX9D", "^VIX")
    if d9:
        term.append((_ramp(d9[-1][1], 0.95, 1.15), f"VIX9D/VIX {d9[-1][1]:.2f}" + (" 역전" if d9[-1][1] > 1 else "")))
    if term:
        out.append({"id": "term", "score": sum(s for s, _ in term) / len(term), "text": " · ".join(t for _, t in term)})
    index = []
    for symbol in _indices(korean):
        c = _closes(series, symbol)
        if len(c) < MIN_BARS:
            continue
        dd = c[-1] / max(c[-20:]) - 1
        gap = c[-1] / (sum(c[-20:]) / 20) - 1
        index.append(((_ramp(-dd, 0.02, 0.12) + _ramp(-gap, 0.0, 0.08)) / 2,
                      f"{symbol} 20일 고점 대비 {dd * 100:+.1f}% · 20일선 대비 {gap * 100:+.1f}%"))
    if index:
        out.append({"id": "index", "score": sum(s for s, _ in index) / len(index),
                    "text": " · ".join(t for _, t in index)})
    main = main_index(series, korean)
    if main:
        n = _streak(_closes(series, main))
        out.append({"id": "streak", "score": _ramp(n, 1, 5), "text": f"{main} {n}일 연속 하락"})
    for pid, symbol, lo, hi, fmt in (("dollar", "DX-Y.NYB", 0.005, 0.025, "달러 지수 5일 {:+.1f}%"),
                                     ("krw", "KRW=X", 0.005, 0.025, "원/달러 5일 {:+.1f}%")):
        if pid == "krw" and not korean:
            continue
        chg = _change(_closes(series, symbol), 5)
        if chg is not None:
            out.append({"id": pid, "score": _ramp(chg, lo, hi), "text": fmt.format(chg * 100)})
    tnx = _closes(series, "^TNX")
    if len(tnx) > 5:
        diff = tnx[-1] - tnx[-6]                 # ^TNX is quoted in percent: 0.3 = 30bp
        out.append({"id": "rates", "score": _ramp(diff, 0.1, 0.4), "text": f"미 10년물 5일 {diff * 100:+.0f}bp"})
    for p in out:
        p["score"] = round(p["score"])
    return out


def _total(ps: list[dict]) -> int | None:
    weight = sum(WEIGHTS[p["id"]] for p in ps)
    return round(sum(WEIGHTS[p["id"]] * p["score"] for p in ps) / weight) if weight else None


def signals(series: Series, korean: bool = False) -> list[dict]:
    """The exhaustion signals present now: the VIX turned from a spike, the term structure came
    back from an inversion, an index took back a gap down, a losing streak broke."""
    out = []
    vix = _closes(series, "^VIX")
    if len(vix) >= MIN_BARS + LOOKBACK:
        recent = vix[-LOOKBACK:]
        peak = max(recent[:-1])
        base = statistics.median(vix[-LOOKBACK - 20:-LOOKBACK])
        if peak >= VIX_PEAK and base and peak >= VIX_SPIKE * base and vix[-1] <= VIX_OFF_PEAK * peak:
            out.append({"id": "vix_turn", "text": f"VIX 고점 {peak:.1f} 뒤 {vix[-1]:.1f} 로 꺾임"})
    m3 = _aligned(series, "^VIX", "^VIX3M")
    if len(m3) >= 2:
        before = [r for _, r in m3[-LOOKBACK:-1]]
        if before and max(before) > 1.0 and m3[-1][1] < 1.0:
            out.append({"id": "term_normal", "text": f"VIX 기간 구조 역전 해소(VIX/VIX3M {max(before):.2f} → {m3[-1][1]:.2f})"})
    main = main_index(series, korean)
    if main:
        bars = series[main]
        for prev, bar in zip(bars[-4:-1], bars[-3:], strict=False):
            if bar["open"] <= prev["close"] * (1 - GAP_DOWN) and bar["close"] >= prev["close"]:
                out.append({"id": "gap_reclaim", "text": f"{main} 갭 하락 뒤 전일 종가 회복({bar['day']})"})
                break
        c = [b["close"] for b in bars]
        if len(c) > STREAK_END + 1 and c[-1] > c[-2] and _streak(c[:-1]) >= STREAK_END:
            out.append({"id": "streak_break", "text": f"{main} {_streak(c[:-1])}일 연속 하락 뒤 첫 반등"})
    return out


def score(series: Series, korean: bool = False) -> dict | None:
    """The selling score, its recent peak, the phase and the evidence. None with nothing to score."""
    ps = parts(series, korean)
    now = _total(ps)
    if now is None:
        return None
    main = main_index(series, korean) or next(iter(series), None)
    days = [b["day"] for b in series.get(main) or []]
    as_of_day = days[-1] if days else None
    # the score on each of the last sessions (the dump a signal would end may be days old)
    peak = now
    for day in days[-LOOKBACK:-1]:
        past = _total(parts({k: [b for b in v if b["day"] <= day] for k, v in series.items()}, korean))
        if past is not None:
            peak = max(peak, past)
    sig = signals(series, korean)
    if peak >= STRESS and len(sig) >= EXHAUST_SIGNALS:
        phase = "exhausting"
    elif now >= SELLING:
        phase = "selling"
    else:
        phase = "calm"
    return {"version": VERSION, "market": "KR" if korean else "US", "as_of": as_of_day,
            "score": now, "peak": peak, "phase": phase, "label": PHASE_LABEL[phase],
            "parts": ps, "signals": sig, "missing": [s for s in (KR if korean else US) if s not in series]}


# ---------------------------------------------------------------- the rule


def _first_step_only(side: dict, risk: dict) -> dict:
    """The ladder's first step alone; the rest held until an exhaustion signal."""
    from .playbook import _round

    first, held = side["entries"][0], side["entries"][1:]
    avg, stop = first["price"], side["stop"]
    one_r = abs(avg - stop) or 1e-9
    targets = []
    for t in side.get("targets") or []:
        t = {**t, "rr": round((t["price"] - avg) / one_r, 2)}
        if "then_stop" in t:
            t["then_stop"] = _round(avg)
        targets.append(t)
    text = (f"시장 투매 진행 중(점수 {risk['score']}) — 1차만, "
            f"{' · '.join(str(e['step']) for e in held)}차는 투매 끝물 신호가 날 때까지 보류")
    return {**side, "entries": [first], "avg_entry": _round(avg), "risk": _round(one_r), "targets": targets,
            "gate": {"phase": risk["phase"], "text": text, "held": held}}


def apply(pb: dict | None, risk: dict | None) -> dict | None:
    """The phase's rule on both playbooks; the score rides along as ``risk_regime``. Idempotent."""
    if not pb or not risk:
        return pb
    from .playbook import _none_side

    out = {**pb, "risk_regime": risk}
    long_side, short_side = out.get("long") or {}, out.get("short") or {}
    if risk["phase"] == "selling" and long_side.get("setup") == "dip_catch" and len(long_side.get("entries") or []) > 1:
        out["long"] = _first_step_only(long_side, risk)
    if risk["phase"] == "exhausting" and short_side.get("setup") not in (None, "none"):
        why = " · ".join(s["text"] for s in risk["signals"])
        text = f"투매 끝물 신호({why}) — 바닥 반등에 숏은 쉬어 간다"
        out["short"] = {**_none_side(text, short_side.get("size_hint") or "half", True),
                        "gate": {"phase": risk["phase"], "text": text, "held": [], "setup": short_side["setup"]}}
    return out


def prompt_note(risk: dict | None) -> str | None:
    """The score and its evidence as an English block for the deciding agents."""
    if not risk:
        return None
    phase = {"selling": "SELLING UNDER WAY - only the first dip-catch step; steps 2-3 wait for an exhaustion signal",
             "exhausting": "SELLING EXHAUSTING - the dip-catch ladder runs as planned; no shorts into the turn",
             "calm": "no market-wide dump - the ladder and the short follow the ticker's own playbook"}[risk["phase"]]
    lines = [(f"Market selling pressure (desk-computed, {risk['market']} indices, as of {risk['as_of']}): "
              f"score {risk['score']}/100 (10-session peak {risk['peak']}) - {phase}.")]
    lines += [f"- {p['id']} {p['score']}: {p['text']}" for p in risk["parts"]]
    if risk["signals"]:
        lines.append("Exhaustion signals: " + "; ".join(s["text"] for s in risk["signals"]) + ".")
    if risk["missing"]:
        lines.append(f"Not available this time: {', '.join(risk['missing'])}.")
    lines.append("Size the dip-catch and the short with this; do not add ladder steps the desk held back.")
    return "\n".join(lines)


# ---------------------------------------------------------------- live


class Cache:
    """The score per market for a while (every run and every live refresh would refetch nine
    tickers otherwise). Thread-safe: read from worker threads. One thread fetches a market at a time
    (a caller that gave up waiting leaves its thread running; the next ones wait for it up to
    ``wait_sec``, not start another), and an empty or failed fetch is remembered for ``fail_ttl_sec``
    so a slow Yahoo is not asked again on every poll."""

    def __init__(self, ttl_sec: float = 900.0, fetcher: Callable[[tuple[str, ...]], Series] | None = None,
                 clock=time.monotonic, fail_ttl_sec: float = 120.0, wait_sec: float = 30.0) -> None:
        self.ttl = ttl_sec
        self.fail_ttl = fail_ttl_sec
        self.wait = wait_sec
        self._fetch = fetcher or fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._fetching = {"KR": threading.Lock(), "US": threading.Lock()}
        self._rows: dict[str, tuple[float, Series]] = {}

    def _hit(self, key: str) -> Series | None:
        with self._lock:
            hit = self._rows.get(key)
        if hit and self._clock() - hit[0] < (self.ttl if hit[1] else self.fail_ttl):
            return hit[1]
        return None

    def series(self, korean: bool) -> Series:
        key = "KR" if korean else "US"
        hit = self._hit(key)
        if hit is not None:
            return hit
        # a hung fetch does not pile up its waiters: they give up and score nothing this time
        if not self._fetching[key].acquire(timeout=self.wait):
            return {}
        try:
            hit = self._hit(key)                 # fetched while this one waited
            if hit is not None:
                return hit
            try:
                got = self._fetch(KR if korean else US)
            except Exception as exc:  # noqa: BLE001 - remembered as nothing, briefly
                log.warning("risk regime: %s fetch failed: %s", key, exc)
                got = {}
            with self._lock:
                self._rows[key] = (self._clock(), got)
            return got
        finally:
            self._fetching[key].release()

    def current(self, ticker: str, trade_date: str | None = None) -> dict | None:
        """The score an analysis of ``ticker`` on ``trade_date`` (today when None) sees. None when
        the date is older than the fetched bars or nothing loaded."""
        korean = is_korean(ticker)
        series = self.series(korean)
        if trade_date and trade_date < date.today().isoformat():
            series = as_of(series, trade_date, korean=korean)
            main = main_index(series, korean)
            if not main or len(series[main]) < MIN_BARS:
                return None
        return score(series, korean)

