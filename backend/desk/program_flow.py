"""Program trading turns through the session (#743): KRX program trading net buy (desk.program_trade, kept in
the futures snapshots since #677) turning from selling to buying or back - the total and the non-arbitrage
(the arbitrage mostly follows the futures basis; the non-arbitrage is the baskets: the foreign's index buying).

- A turn is desk.flow_intraday.turns' dead band and hold on the running total: past ``band`` on the other
  side for ``HOLD`` snapshots in a row (a total wobbling around zero does not flip back and forth); the
  first side of the day is a start, not a turn.
- From ``FROM`` (09:05: at the bell every program total starts at 0 and the first minutes are small) until
  ``UNTIL`` (the closing auction), found within ``RECENT_MIN`` (the snapshot loop and a replay alike).
- The band: ``DEFAULT`` until ``MIN_DAYS`` trading days of program figures are kept (표본 부족), then the
  ``QUANTILE`` of the absolute 30-minute changes over them, never under half the default.
- Each alert carries the foreign spot running total of that market at the same moment (``prev``), so its text
  can say whether the program and the foreign go the same way.

Pure functions - the sending, the daily cap per rule and the push kind ``program_alert`` are desk.market_alerts.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from . import flow_intraday
from .futures_flow import KST

MARKETS = ("kospi", "kosdaq")
WHO = ("program_total", "program_non_arbitrage")
DEFAULT = {("kospi", "program_total"): 500.0, ("kospi", "program_non_arbitrage"): 500.0,
           ("kosdaq", "program_total"): 150.0, ("kosdaq", "program_non_arbitrage"): 150.0}   # 100 million KRW
HOLD = 3                          # snapshots (minutes) past the band on the new side
FROM, UNTIL = time(9, 5), time(15, 20)
RECENT_MIN = 10
WINDOW_MIN = 30
QUANTILE = 0.5
MIN_DAYS = 10
WHO_KO = {"program_total": "프로그램 전체", "program_non_arbitrage": "프로그램 비차익",
          "program_arbitrage": "프로그램 차익"}
MARKET_KO = {"kospi": "코스피", "kosdaq": "코스닥"}
RULE_KO = {"buy_turn": "매수 전환", "sell_turn": "매도 전환"}


def bands(days: list[list[dict]]) -> dict:
    """Per (market, program class): the band and how many days it rests on (``{"band": {...}, "days": {...}}``)."""
    band, n_days = {}, {}
    for key, default in DEFAULT.items():
        sample, n = [], 0
        for points in days:
            s = [(at, v[key[1]]) for at, v in flow_intraday.series(points, key[0]) if v.get(key[1]) is not None]
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


def judge(points: list[dict], band: dict | None = None) -> list[dict]:
    """The program turns found lately at the latest snapshot of each market."""
    band = band or DEFAULT
    out = []
    for market in MARKETS:
        s = flow_intraday.series(points, market)
        if not s:
            continue
        last = s[-1][0]
        for who in WHO:
            for turn in flow_intraday.turns(points, market, who, band=band.get((market, who), DEFAULT[(market, who)]),
                                            hold=HOLD):
                t = datetime.fromisoformat(turn["t"])
                if last - t > timedelta(minutes=RECENT_MIN) or not (FROM <= t.astimezone(KST).time() < UNTIL):
                    continue
                foreign = _foreign_at(points, market, t)
                out.append({"kind": "program_alert", "rule": turn["kind"], "market": market, "who": who,
                            "severity": "caution", "t": t.isoformat(timespec="seconds"),
                            "value": round(turn["value"], 2), "prev": None if foreign is None else round(foreign, 2)})
    return out


def same_way(value: float | None, foreign: float | None) -> str | None:
    """"둘 다 매수" / "둘 다 매도" / "엇갈림", or None without both."""
    if value is None or foreign is None or value == 0 or foreign == 0:
        return None
    if (value > 0) == (foreign > 0):
        return "둘 다 매수" if value > 0 else "둘 다 매도"
    return "엇갈림"


def text(alert: dict) -> str:
    """e.g. "🔴 코스피 프로그램 비차익 매수 전환 · 10:42 · 누적 +1,234억 · 외인 현물 +500억 · 둘 다 매수"."""
    dot = "🔵" if alert["rule"] == "sell_turn" else "🔴"
    hhmm = datetime.fromisoformat(alert["t"]).astimezone(KST).strftime("%H:%M")
    head = f"{dot} {MARKET_KO[alert['market']]} {WHO_KO[alert['who']]} {RULE_KO[alert['rule']]} · {hhmm}"
    tail = f"누적 {_num(alert['value'])}억"
    if alert.get("prev") is not None:
        tail += f" · 외인 현물 {_num(alert['prev'])}억"
        way = same_way(alert["value"], alert["prev"])
        if way:
            tail += f" · {way}"
    return f"{head} · {tail}"


def _num(v: float | None) -> str:
    return "?" if v is None else f"{v:+,.0f}".replace("-", "−")
