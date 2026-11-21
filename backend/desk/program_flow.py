"""Program trading turns through the session (#743): KRX program trading net buy (desk.program_trade, kept in
the futures snapshots since #677) turning from selling to buying or back - the total and the non-arbitrage
(the arbitrage mostly follows the futures basis; the non-arbitrage is the baskets: the foreign's index buying).

- A turn is desk.flow_intraday.turns' dead band and hold on the running total: past ``band`` on the other
  side for ``HOLD`` snapshots with a figure in a row (a total wobbling around zero does not flip back and
  forth; a minute KIS missed neither breaks nor counts); the first side of the day is a start, not a turn.
  A minute Toss missed (no spot investors) still counts: the program figures are KIS' own.
- From ``FROM`` (09:05: at the bell every program total starts at 0 and the first minutes are small) until
  ``UNTIL`` (the closing auction). ``turns`` is what the chart marks (desk.flow_intraday.view) and ``judge``
  what the alerts send - the same band, the same hours; ``judge`` keeps those found within ``RECENT_MIN``.
- The band: ``DEFAULT`` until ``MIN_DAYS`` trading days of program figures are kept (표본 부족), then the
  ``QUANTILE`` of the absolute 30-minute changes over the ``HISTORY_DAYS`` before, never under half the
  default - once a day per process (``bands_for``; the collector's alerts and the api's chart read the same
  snapshots, so they agree), logged at INFO.
- The total and the non-arbitrage turning the same way within ``PAIR_MIN`` is one alert (the total's, saying
  both) - a day with little arbitrage would otherwise send the same news twice.
- Each alert carries the foreign spot running total of that market at the same moment (``prev``), so its text
  can say whether the program and the foreign go the same way.
- The alert's ``who`` is a short code (``ALERT_WHO``): ``market_alerts.who`` holds 16 characters.

Pure functions but ``bands_for``'s cache - the sending, the daily cap per rule and the push kind
``program_alert`` are desk.market_alerts.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from . import flow_intraday
from .futures_flow import KST

log = logging.getLogger(__name__)

MARKETS = ("kospi", "kosdaq")
WHO = ("program_total", "program_non_arbitrage")
ALERT_WHO = {"program_total": "prog_total", "program_non_arbitrage": "prog_nonarb"}
DEFAULT = {("kospi", "program_total"): 500.0, ("kospi", "program_non_arbitrage"): 500.0,
           ("kosdaq", "program_total"): 150.0, ("kosdaq", "program_non_arbitrage"): 150.0}   # 100 million KRW
HOLD = 3                          # snapshots with a figure past the band on the new side
FROM, UNTIL = time(9, 5), time(15, 20)
RECENT_MIN = 10
PAIR_MIN = 5
WINDOW_MIN = 30
QUANTILE = 0.5
MIN_DAYS = 10
HISTORY_DAYS = 20
KEEP_CACHED = 3
WHO_KO = {"prog_total": "프로그램 전체", "prog_nonarb": "프로그램 비차익"}
MARKET_KO = {"kospi": "코스피", "kosdaq": "코스닥"}
RULE_KO = {"buy_turn": "매수 전환", "sell_turn": "매도 전환"}

_cache: dict[str, dict] = {}


def bands(days: list[list[dict]]) -> dict:
    """Per (market, program class): the band and how many days it rests on (``{"band": {...}, "days": {...}}``)."""
    band, n_days = {}, {}
    for key, default in DEFAULT.items():
        sample, n = [], 0
        for points in days:
            s = [(at, v[key[1]]) for at, v in flow_intraday.series(points, key[0], need=(key[1],))]
            got = _changes(s)
            if got:
                n += 1
                sample += [abs(x) for x in got]
        n_days[key] = n
        if n < MIN_DAYS:
            band[key] = default
            continue
        sample.sort()
        band[key] = max(default / 2, sample[int(QUANTILE * (len(sample) - 1))])
    return {"band": band, "days": n_days}


def cached(day: date) -> bool:
    return day.isoformat() in _cache


def bands_for(history, day: date, days_points: list[list[dict]] | None = None) -> dict:
    """``bands`` over the HISTORY_DAYS trading days before ``day`` (desk.futures_history), once a day per process;
    ``days_points``: those days already read (desk.market_alerts reads them for its own thresholds)."""
    key = day.isoformat()
    if key in _cache:
        return _cache[key]
    if days_points is None:
        days = [d["date"] for d in history.daily(HISTORY_DAYS + 1) if d.get("date") and d["date"] < key]
        days_points = [history.intraday(date.fromisoformat(d)) for d in days[:HISTORY_DAYS]]
    try:
        got = bands(days_points)
    except Exception:            # the defaults then, not read again every minute
        log.exception("program bands failed - defaults for %s", key)
        got = {"band": dict(DEFAULT), "days": {}}
    log.info("program bands %s: %s", key, ", ".join(
        f"{m} {who.removeprefix('program_')} {got['band'][(m, who)]:,.0f}"
        f"({'default' if got['days'].get((m, who), 0) < MIN_DAYS else 'measured'}, {got['days'].get((m, who), 0)} days)"
        for m, who in DEFAULT))
    for old in sorted(_cache)[:-KEEP_CACHED + 1] if len(_cache) >= KEEP_CACHED else []:
        _cache.pop(old, None)
    _cache[key] = got
    return got


def _changes(s: list[tuple[datetime, float]]) -> list[float]:
    out, j = [], 0
    for at, v in s:
        while j < len(s) and s[j][0] <= at - timedelta(minutes=WINDOW_MIN):
            j += 1
        if j > 0 and at - s[j - 1][0] <= timedelta(minutes=WINDOW_MIN + 3):
            out.append(v - s[j - 1][1])
    return out


def _foreign_at(points: list[dict], market: str, at: datetime) -> float | None:
    got = None
    for t, vals in flow_intraday.series(points, market):
        if t > at:
            break
        if vals.get("foreign") is not None:
            got = vals["foreign"]
    return got


def turns(points: list[dict], band: dict | None = None) -> list[dict]:
    """The program turns of the day within the hours (FROM ~ UNTIL), oldest first - the chart's arrows."""
    band = band or DEFAULT
    out = []
    for market in MARKETS:
        for who in WHO:
            for t in flow_intraday.turns(points, market, who, band=band.get((market, who), DEFAULT[(market, who)]),
                                         hold=HOLD):
                if FROM <= datetime.fromisoformat(t["t"]).astimezone(KST).time() < UNTIL:
                    out.append(t)
    return sorted(out, key=lambda t: t["t"])


def judge(points: list[dict], band: dict | None = None) -> list[dict]:
    """The program turns found lately at the latest snapshot of each market, as alerts."""
    found = turns(points, band)
    out = []
    for market in MARKETS:
        mine = [t for t in found if t["market"] == market]
        s = flow_intraday.series(points, market, need=flow_intraday.CLASSES + flow_intraday.PROGRAM)
        if not mine or not s:
            continue
        last = s[-1][0]
        totals = [t for t in mine if t["who"] == "program_total"]
        for turn in mine:
            t = datetime.fromisoformat(turn["t"])
            if last - t > timedelta(minutes=RECENT_MIN):
                continue
            paired = [x for x in totals if x["kind"] == turn["kind"]
                      and abs(datetime.fromisoformat(x["t"]) - t) <= timedelta(minutes=PAIR_MIN)]
            if turn["who"] == "program_non_arbitrage" and paired:
                continue                 # said with the total
            foreign = _foreign_at(points, market, t)
            alert = {"kind": "program_alert", "rule": turn["kind"], "market": market, "who": ALERT_WHO[turn["who"]],
                     "severity": "caution", "t": t.isoformat(timespec="seconds"), "value": round(turn["value"], 2),
                     "prev": None if foreign is None else round(foreign, 2)}
            if turn["who"] == "program_total" and any(
                    x["who"] == "program_non_arbitrage" and x["kind"] == turn["kind"]
                    and abs(datetime.fromisoformat(x["t"]) - t) <= timedelta(minutes=PAIR_MIN) for x in mine):
                alert["both"] = True
            out.append(alert)
    return out


def same_way(value: float | None, foreign: float | None) -> str | None:
    """"외인과 같은 방향" / "외인과 반대", or None without both."""
    if value is None or foreign is None or value == 0 or foreign == 0:
        return None
    return "외인과 같은 방향" if (value > 0) == (foreign > 0) else "외인과 반대"


def text(alert: dict) -> str:
    """e.g. "🔴 코스피 프로그램 전체 · 비차익 매수 전환 · 10:42 · 누적 +1,234억 · 외인 현물 +500억 · 외인과 같은 방향"."""
    dot = "🔵" if alert["rule"] == "sell_turn" else "🔴"
    hhmm = datetime.fromisoformat(alert["t"]).astimezone(KST).strftime("%H:%M")
    who = "프로그램 전체 · 비차익" if alert.get("both") else WHO_KO.get(alert["who"], "프로그램")
    head = f"{dot} {MARKET_KO.get(alert['market'], alert['market'])} {who} {RULE_KO[alert['rule']]} · {hhmm}"
    tail = f"누적 {_num(alert['value'])}억"
    if alert.get("prev") is not None:
        tail += f" · 외인 현물 {_num(alert['prev'])}억"
        way = same_way(alert["value"], alert["prev"])
        if way:
            tail += f" · {way}"
    return f"{head} · {tail}"


def _num(v: float | None) -> str:
    return "?" if v is None else f"{v:+,.0f}".replace("-", "−")
