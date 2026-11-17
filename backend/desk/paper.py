"""#527 the paper account: the desk's scored plans traded with won on paper. No orders, no LLM.

The price side is not judged again here: every plan's fill, exit and R come from ``runs.outcome``
(desk.outcome.judge, #64) as the plan scorer wrote them, so a trade's ``r`` is that run's ``outcome.r``
and a run is counted once. On top of it this module only adds money:

- size: ``risk`` - the shares that lose ``risk_pct`` of the realized balance at the stop; or ``weight`` -
  ``weight_pct`` of it. Whole shares; capped by the cash left (skipped only when not even one share fits).
- costs: a commission each side (Korean / US rates), the Korean sell tax, and slippage against us on both
  fills (in the fill price).
- US prices in won at the fill day's ``KRW=X`` daily close (the last one within ``FX_LOOKBACK`` days).
- order: events in time; at the same moment exits first (they free cash), then entries by plan time and
  run id. Skipped with a reason: short off, the open-position limit, no cash, under one share, no rate.
- open trades are marked at the scorer's last close (``outcome.r`` of an ``open`` plan); ``both`` (a bar
  touching stop and target) is taken at the stop, as the hit rate counts it as a miss.

Everything is recomputed from the start whenever the settings or the scores change: deterministic for the
same runs, settings and rates.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Engine

from desk.db import now as db_now
from desk.db import paper_settings as settings_t
from desk.db import runs as runs_t

log = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
FX_TICKER = "KRW=X"
FX_BARS = 1500                # about six years of daily rates
FX_TTL_SEC = 6 * 3600
FX_LOOKBACK = 5               # days back for a rate when the fill day has none (a holiday)
FILLED = ("target", "stop", "both", "expired", "open")
CLOSED = ("target", "stop", "both", "expired")

DEFAULTS: dict = {
    "start_capital": 10_000_000, "start_date": None, "sizing": "risk", "risk_pct": 1.0, "weight_pct": 10.0,
    "max_positions": 5, "fee_kr_pct": 0.015, "fee_us_pct": 0.1, "tax_kr_pct": 0.18, "slippage_pct": 0.05,
    "allow_short": True,
}
RANGES: dict[str, tuple[float, float]] = {
    "start_capital": (100_000, 100_000_000_000), "risk_pct": (0.1, 5.0), "weight_pct": (1.0, 100.0),
    "max_positions": (1, 50), "fee_kr_pct": (0.0, 1.0), "fee_us_pct": (0.0, 1.0), "tax_kr_pct": (0.0, 1.0),
    "slippage_pct": (0.0, 2.0),
}
SKIP_TEXT = {
    "no_fill": "진입가에 닿지 않아 사지 않음",
    "no_cash": "모의 잔고가 모자라 건너뜀",
    "max_positions": "동시 보유 한도라 건너뜀",
    "short_off": "숏을 꺼 두어 건너뜀",
    "too_small": "1주도 살 수 없는 크기라 건너뜀",
    "no_fx": "그날 원/달러 환율이 없어 건너뜀",
    "unsupported": "원화 · 달러가 아닌 종목이라 건너뜀",
    "unscorable": "채점할 수 없는 계획이라 건너뜀",
}


# ---------------------------------------------------------------- settings

def validate(patch: dict, base: dict) -> dict:
    """``base`` with ``patch`` applied, checked: ValueError names the first bad field."""
    out = {k: base.get(k, v) for k, v in DEFAULTS.items()}
    for key, value in patch.items():
        if key not in DEFAULTS:
            continue                                   # version, updated_at and unknown keys are ignored
        if key == "start_date":
            if value not in (None, ""):
                try:
                    value = date.fromisoformat(str(value)).isoformat()
                except ValueError:
                    raise ValueError("start_date: YYYY-MM-DD or null") from None
            else:
                value = None
        elif key == "sizing":
            if value not in ("risk", "weight"):
                raise ValueError("sizing: risk or weight")
        elif key == "allow_short":
            if not isinstance(value, bool):
                raise ValueError("allow_short: true or false")
        else:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise ValueError(f"{key}: a number")
            lo, hi = RANGES[key]
            if not lo <= value <= hi:
                raise ValueError(f"{key}: between {lo:g} and {hi:g}")
            value = int(value) if key in ("start_capital", "max_positions") else float(value)
        out[key] = value
    return out


# ---------------------------------------------------------------- the simulation (pure)

def _at(text: str) -> datetime:
    at = datetime.fromisoformat(text)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _kst_day(at: datetime) -> date:
    return at.astimezone(KST).date()


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def market_of(ticker: str) -> str | None:
    """KR (won) for .KS / .KQ, US (dollar) for a plain Yahoo ticker (BRK-B, BTC-USD), None for another
    exchange's suffix (.T, .HK), an index (^) or a rate / future (=X, =F)."""
    t = ticker.upper()
    if t.endswith((".KS", ".KQ")):
        return "KR"
    if "." in t or t.startswith("^") or "=" in t:
        return None
    return "US"


def fx_on(rates: dict[str, float], day: date) -> float | None:
    for back in range(FX_LOOKBACK + 1):
        got = rates.get((day - timedelta(days=back)).isoformat())
        if got:
            return got
    return None


def _plan_at(run: dict) -> str | None:
    return (run.get("outcome") or {}).get("plan_at") or ((run.get("plan") or {}).get("live_price") or {}).get("fetched_at")


def candidates(runs: list[dict], start_date: str | None) -> list[dict]:
    """The runs a paper trade can come from: a directional plan with entry, stop and target, scored or not
    (``invalid`` and plans before ``start_date`` are left out), newest plan last."""
    out = []
    for run in runs:
        plan = run.get("plan") or {}
        if plan.get("bias") not in ("long", "short") or None in (plan.get("entry_price"), plan.get("stop_loss"),
                                                                   plan.get("price_target")):
            continue
        status = (run.get("outcome") or {}).get("status")
        if status == "invalid":
            continue
        at = _plan_at(run) or (run["created_at"].isoformat() if isinstance(run.get("created_at"), datetime)
                               else run.get("created_at"))
        if not at:
            continue
        if start_date and _kst_day(_at(at)).isoformat() < start_date:
            continue
        out.append({**run, "_plan_at": _at(at)})
    return sorted(out, key=lambda r: (r["_plan_at"], r["id"]))


def _exit_price(plan: dict, outcome: dict, direction: int) -> float | None:
    entry, stop, target = plan["entry_price"], plan["stop_loss"], plan["price_target"]
    status, r = outcome.get("status"), outcome.get("r")
    if status == "target":
        return target
    if status == "both":
        return stop
    if r is None:
        return None
    return entry + direction * r * abs(entry - stop)


def simulate(runs: list[dict], settings: dict, rates: dict[str, float], now: datetime,
             name_of: Callable[[str], str | None] | None = None) -> dict:
    """The paper account over ``runs`` (rows of the runs table as dicts) with ``settings`` and the daily
    ``KRW=X`` closes ``rates`` (ISO date -> won per dollar). Pure: the same input gives the same output."""
    s = settings
    slip = s["slippage_pct"] / 100
    trades: list[dict] = []
    events: list[tuple] = []           # (time, 0 exit | 1 entry, plan time, run id, trade)
    for run in candidates(runs, s.get("start_date")):
        plan, outcome = run["plan"], run.get("outcome") or {}
        direction = 1 if plan["bias"] == "long" else -1
        ticker = run["ticker"]
        market = market_of(ticker)
        status = outcome.get("status")
        name = (run.get("options") or {}).get("name") or (name_of(ticker) if name_of else None)
        t = {"run_id": run["id"], "ticker": ticker, "name": name, "market": market or "US",
             "currency": "KRW" if market == "KR" else "USD", "direction": plan["bias"],
             "rating": plan.get("rating"), "horizon": plan.get("horizon") or (run.get("options") or {}).get("horizon"),
             "plan_at": _iso(run["_plan_at"]), "status": "waiting", "exit_reason": None, "skip": None,
             "plan": {"entry": plan["entry_price"], "stop": plan["stop_loss"], "target": plan["price_target"]},
             "entry": None, "exit": None, "qty": None, "fx": None, "amount_krw": None, "costs_krw": None,
             "pnl_krw": None, "pnl_pct": None, "r": outcome.get("r") if status in FILLED else None, "marked": False,
             "_dir": direction, "_outcome": outcome}
        trades.append(t)
        if status in (None, "waiting", "error"):
            continue                                            # not filled yet (or to be scored again)
        if status == "unfilled":
            _skip(t, "no_fill")
            continue
        if status not in FILLED:
            _skip(t, "unscorable")
            continue
        if market is None:
            _skip(t, "unsupported")
            continue
        events.append((_at(outcome["filled_at"]), 1, run["_plan_at"], run["id"], t))
        if status in CLOSED and outcome.get("exited_at"):
            events.append((_at(outcome["exited_at"]), 0, run["_plan_at"], run["id"], t))
    events.sort(key=lambda e: (e[0], e[1], e[2], e[3]))

    realized = float(s["start_capital"])
    held: dict[str, dict] = {}
    closes: list[tuple[datetime, float]] = []      # (exit time, pnl) of the closed trades, in order
    for at, kind, _plan_time, run_id, t in events:
        if kind == 1:
            _enter(t, at, s, rates, realized, held, slip)
        elif run_id in held:
            pnl = _close(t, at, s, rates, slip, mark=False)
            realized += pnl
            del held[run_id]
            closes.append((at, pnl))
    last_fx = rates[max(rates)] if rates else None
    unrealized = 0.0
    for t in held.values():                         # still open: marked at the scorer's last close
        if t["_outcome"].get("status") == "open" and t["r"] is not None:
            unrealized += _close(t, now, s, rates, slip, mark=True, fx_now=last_fx)
        else:                                       # closed in the plan record but its exit time is missing
            t["status"], t["marked"] = "open", True
    for t in trades:
        for k in ("_dir", "_outcome", "_entry_costs"):
            t.pop(k, None)
    return _report(trades, closes, realized, unrealized, held, s, now)


def _skip(t: dict, code: str) -> None:
    t["status"], t["skip"] = "skipped", {"code": code, "text": SKIP_TEXT[code]}


def _fee(t: dict, s: dict) -> float:
    return (s["fee_kr_pct"] if t["market"] == "KR" else s["fee_us_pct"]) / 100


def _enter(t: dict, at: datetime, s: dict, rates: dict, realized: float, held: dict, slip: float) -> None:
    direction = t["_dir"]
    if direction < 0 and not s["allow_short"]:
        return _skip(t, "short_off")
    if len(held) >= s["max_positions"]:
        return _skip(t, "max_positions")
    fx = 1.0
    if t["market"] == "US":
        fx = fx_on(rates, at.astimezone(UTC).date())
        if fx is None:
            return _skip(t, "no_fx")
    fill = t["plan"]["entry"] * (1 + direction * slip)
    fee = _fee(t, s)
    if s["sizing"] == "risk":
        per_share = abs(fill - t["plan"]["stop"]) * fx
        qty = math.floor(realized * s["risk_pct"] / 100 / per_share) if per_share > 0 else 0
    else:
        qty = math.floor(realized * s["weight_pct"] / 100 / (fill * fx))
    if qty < 1:
        return _skip(t, "too_small")
    cash = realized - sum(h["amount_krw"] + h["_entry_costs"] for h in held.values())
    afford = math.floor(cash / (fill * fx * (1 + fee))) if fill > 0 else 0
    qty = min(qty, afford)
    if qty < 1:
        return _skip(t, "no_cash")
    amount = qty * fill * fx
    entry_costs = amount * fee + (amount * s["tax_kr_pct"] / 100 if t["market"] == "KR" and direction < 0 else 0)
    t.update({"status": "open", "entry": {"at": _iso(at), "price": t["plan"]["entry"], "fill": round(fill, 6)},
              "qty": qty, "fx": {"entry": fx, "exit": None} if t["market"] == "US" else None,
              "amount_krw": round(amount), "_entry_costs": entry_costs})
    held[t["run_id"]] = t
    return None


def _close(t: dict, at: datetime, s: dict, rates: dict, slip: float, mark: bool, fx_now: float | None = None) -> float:
    """The trade's won result at its exit (or marked now): fills, both fees, the Korean sell tax."""
    direction = t["_dir"]
    price = _exit_price({"entry_price": t["plan"]["entry"], "stop_loss": t["plan"]["stop"],
                         "price_target": t["plan"]["target"]}, t["_outcome"], direction)
    fx_in = t["fx"]["entry"] if t["fx"] else 1.0
    fx_out = 1.0
    if t["market"] == "US":
        fx_out = (fx_now if mark else fx_on(rates, at.astimezone(UTC).date())) or fx_in
    fill = price * (1 - direction * slip)
    qty = t["qty"]
    out_amount = qty * fill * fx_out
    exit_costs = out_amount * _fee(t, s) + (out_amount * s["tax_kr_pct"] / 100
                                           if t["market"] == "KR" and direction > 0 else 0)
    costs = t.pop("_entry_costs", 0.0) + exit_costs
    gross = out_amount - t["amount_krw"] if direction > 0 else t["amount_krw"] - out_amount
    pnl = gross - costs
    t.update({"exit": {"at": _iso(at), "price": round(price, 6), "fill": round(fill, 6)},
              "costs_krw": round(costs), "pnl_krw": round(pnl),
              "pnl_pct": round(pnl / t["amount_krw"] * 100, 2) if t["amount_krw"] else None})
    if t["fx"]:
        t["fx"]["exit"] = fx_out
    if mark:
        t.update({"status": "open", "marked": True, "exit": None})
    else:
        t.update({"status": "closed", "exit_reason": t["_outcome"].get("status")})
    return pnl


def _stats(rows: list[dict]) -> dict:
    rs = [(-1.0 if t["exit_reason"] == "both" else t["r"]) for t in rows]
    rs = [r for r in rs if r is not None]
    return {"trades": len(rows), "wins": sum(1 for t in rows if t["pnl_krw"] > 0),
            "pnl": sum(t["pnl_krw"] for t in rows), "avg_r": round(sum(rs) / len(rs), 2) if rs else None}


def _by(rows: list[dict], key: str) -> list[dict]:
    keys = sorted({str(t[key]) for t in rows})
    return [{"key": k, **_stats([t for t in rows if str(t[key]) == k])} for k in keys]


def _report(trades: list[dict], closes: list[tuple[datetime, float]], realized: float, unrealized: float,
            held: dict, s: dict, now: datetime) -> dict:
    start = float(s["start_capital"])
    closed = [t for t in trades if t["status"] == "closed"]
    counts = {k: sum(1 for t in trades if t["status"] == k) for k in ("closed", "open", "waiting", "skipped")}
    wins = [t["pnl_krw"] for t in closed if t["pnl_krw"] > 0]
    losses = [t["pnl_krw"] for t in closed if t["pnl_krw"] <= 0]
    stats = _stats(closed)
    invested = sum(t["amount_krw"] for t in held.values())

    # the daily curve: realized balance at each KST day's end, today with the open trades marked
    first = min([_at(t["plan_at"]) for t in trades], default=now)
    day0 = date.fromisoformat(s["start_date"]) if s.get("start_date") else _kst_day(first)
    today = _kst_day(now)
    by_day: dict[date, float] = {}
    for at, pnl in closes:
        by_day[_kst_day(at)] = by_day.get(_kst_day(at), 0.0) + pnl
    points, balance, d = [], start, min(day0, today)
    while d <= today:
        balance += by_day.get(d, 0.0)
        points.append({"date": d.isoformat(), "balance": round(balance + (unrealized if d == today else 0))})
        d += timedelta(days=1)
    peak, peak_at, worst = start, points[0]["date"] if points else None, {"krw": 0, "pct": 0.0, "peak_at": None,
                                                                          "trough_at": None}
    for p in points:
        if p["balance"] > peak:
            peak, peak_at = p["balance"], p["date"]
        dd = p["balance"] - peak
        if dd < worst["krw"]:
            worst = {"krw": round(dd), "pct": round(dd / peak * 100, 2), "peak_at": peak_at, "trough_at": p["date"]}

    def bucket(day_of: Callable[[date], date]) -> list[dict]:
        sums: dict[date, float] = {}
        for at, pnl in closes:
            k = day_of(_kst_day(at))
            sums[k] = sums.get(k, 0.0) + pnl
        return [{"start": k.isoformat(), "pnl": round(v)} for k, v in sorted(sums.items())]

    month0 = today.replace(day=1)
    month_pnl = sum(p for at, p in closes if _kst_day(at) >= month0)
    before = next((p["balance"] for p in reversed(points) if date.fromisoformat(p["date"]) < month0), start)
    balance_now = realized + unrealized
    return {
        "summary": {
            "start_capital": int(start), "balance": round(balance_now), "cash": round(realized - invested),
            "invested": round(invested), "realized_pnl": round(realized - start), "unrealized_pnl": round(unrealized),
            "total_pnl": round(balance_now - start), "total_pnl_pct": round((balance_now - start) / start * 100, 2),
            "trades": counts, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(closed), 3) if closed else None,
            "avg_r": stats["avg_r"],
            "payoff": (round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 2)
                       if wins and losses and sum(losses) != 0 else None),
            "max_drawdown": worst,
            "this_month": {"start": month0.isoformat(), "pnl": round(month_pnl),
                           "pct": round(month_pnl / before * 100, 2) if before else None},
            "by_ticker": _by(closed, "ticker"), "by_direction": _by(closed, "direction"),
            "by_rating": _by(closed, "rating"),
        },
        "equity": {"start_capital": int(start), "points": points,
                   "weekly": bucket(lambda d: d - timedelta(days=d.weekday())),
                   "monthly": bucket(lambda d: d.replace(day=1))},
        "trades": sorted(trades, key=lambda t: (t["plan_at"], t["run_id"]), reverse=True),
        "counts": counts,
    }


# ---------------------------------------------------------------- storage, rates and the cached result

def yahoo_rates() -> dict[str, float]:
    """Daily ``KRW=X`` closes by ISO date (UTC day of the bar)."""
    from desk import prices

    return {b.at[:10]: float(b.close) for b in prices.yahoo_candles(FX_TICKER, "1d", FX_BARS) if b.close}


class Paper:
    """The one paper account of the desk: its settings row and the result, recomputed when the settings or
    the plan scores change."""

    def __init__(self, engine: Engine, rates: Callable[[], dict[str, float]] = yahoo_rates,
                 name_of: Callable[[str], str | None] | None = None) -> None:
        self.engine, self.fetch_rates, self.name_of = engine, rates, name_of
        self.lock = threading.Lock()
        self._rates: dict[str, float] | None = None
        self._rates_at = 0.0
        self._cache: tuple[tuple, dict] | None = None

    # settings ----------------------------------------------------------
    def settings(self) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(settings_t).where(settings_t.c.id == 1)).first()
        if row is None:
            return {**DEFAULTS, "version": 0, "updated_at": None}
        body = validate(row.body or {}, DEFAULTS)
        at = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
        return {**body, "version": row.version, "updated_at": _iso(at)}

    def save(self, patch: dict) -> dict:
        """``patch`` applied to the stored settings (ValueError when a field is out of range)."""
        with self.lock, self.engine.begin() as conn:
            row = conn.execute(select(settings_t).where(settings_t.c.id == 1)).first()
            body = validate(patch, validate(row.body or {}, DEFAULTS) if row else DEFAULTS)
            at = db_now()
            if row is None:
                conn.execute(settings_t.insert().values(id=1, body=body, version=1, updated_at=at))
            else:
                conn.execute(settings_t.update().where(settings_t.c.id == 1)
                             .values(body=body, version=row.version + 1, updated_at=at))
        self._cache = None
        return self.settings()

    # rates ---------------------------------------------------------------
    def rates(self, need: bool) -> dict[str, float]:
        """The cached ``KRW=X`` closes; fetched only when a US trade needs them (a failure: empty, and
        those trades are skipped as ``no_fx`` until the next try)."""
        if not need:
            return self._rates or {}
        if self._rates is None or time.monotonic() - self._rates_at > FX_TTL_SEC:
            try:
                self._rates = self.fetch_rates()
            except Exception as exc:  # noqa: BLE001 - a rate failure skips the US trades, nothing else
                log.warning("paper: KRW=X rates not fetched (%s)", type(exc).__name__)
                self._rates = self._rates or {}
            self._rates_at = time.monotonic()
        return self._rates

    # the result ------------------------------------------------------------
    def _runs(self) -> list[dict]:
        cols = (runs_t.c.id, runs_t.c.ticker, runs_t.c.options, runs_t.c.plan, runs_t.c.outcome,
                runs_t.c.created_at, runs_t.c.scored_at)
        with self.engine.connect() as conn:
            rows = conn.execute(select(*cols).where(runs_t.c.status == "done")).mappings().all()
        return [dict(r) for r in rows]

    def result(self, now: datetime | None = None) -> dict:
        settings = self.settings()
        runs = self._runs()
        scored = max((r["scored_at"] for r in runs if r["scored_at"]), default=None)
        if scored is not None and scored.tzinfo is None:
            scored = scored.replace(tzinfo=UTC)
        need_fx = any(market_of(r["ticker"]) == "US" for r in runs)
        rates = self.rates(need_fx)
        now = now or datetime.now(UTC)
        key = (settings["version"], len(runs), scored, len(rates), _kst_day(now))
        with self.lock:
            if self._cache and self._cache[0] == key:
                return self._cache[1]
        got = simulate(runs, settings, rates, now, self.name_of)
        meta = {"as_of": _iso(now), "scored_at": _iso(scored) if scored else None,
                "settings_version": settings["version"]}
        out = {"meta": meta, **got}
        with self.lock:
            self._cache = (key, out)
        return out

    def summary(self) -> dict:
        r = self.result()
        return {**r["meta"], **r["summary"]}

    def equity(self) -> dict:
        r = self.result()
        return {"settings_version": r["meta"]["settings_version"], **r["equity"]}

    def trades(self, status: str | None = None, direction: str | None = None, ticker: str | None = None,
               limit: int = 200) -> dict:
        r = self.result()
        items = [t for t in r["trades"] if (not status or t["status"] == status)
                 and (not direction or t["direction"] == direction)
                 and (not ticker or t["ticker"].upper() == ticker.upper())]
        return {"settings_version": r["meta"]["settings_version"], "counts": r["counts"],
                "items": items[:max(1, min(limit, 1000))]}
