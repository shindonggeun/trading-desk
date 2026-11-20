"""Investor flow warnings (#699): the structure of the day's flow, beside desk.flow_shift's changes of pace.

Input is the day's snapshots (desk.futures_history.intraday: running totals, spot in 100 million KRW, the
KOSPI200 futures in contracts), read at the latest one of each spot market, from ``FROM_MIN`` after the
09:00 open to the closing auction (``UNTIL``):

- ``retail_alone`` (개미 홀로 받치기): the individual buys big while the foreign and the institution both sell,
  and their selling covers most of it (``ALONE_SHARE``) - the individual takes what the main positions
  dump. Strong at twice the floor, or on KOSPI with the foreign also selling futures (``FUTURES_SELL``).
- ``heavy_sell_foreign`` · ``heavy_sell_institution`` (과도한 매도): one main position's net selling past its
  floor; strong at twice it.
- ``heavy_sell_combined`` (KOSPI only): the foreign and the institution together, spot plus their KOSPI200
  futures in KRW (contracts x price x ``MULTIPLIER``) - a futures hedge bought against the spot shows here.

Floors (``FLOOR``) are absolute: the server keeps one-minute snapshots only since 2026-11-11, spot since the
13th afternoon - 6 days, too few for a same-time percentile (docs/flow-warn.md). From ``MIN_DAYS`` days of
history the same-time percentile is required on top (``PCT``: the individual's buying in the top 10 %, a
seller's selling in the bottom 5 % of the days at that minute), so a busy market raises the bar by itself.
Pure functions - recording, the once-a-day rule and the pushing are desk.market_alerts.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, time, timedelta

from . import flow_intraday
from .futures_flow import KST

MARKETS = ("kospi", "kosdaq")
OPEN = time(9, 0)
FROM_MIN = 30                       # the first half hour's figures are too small to say anything
UNTIL = time(15, 20)                # the closing auction and after: no rule reads them
SLACK_MIN = 5                       # a past day's value at a minute: its snapshot at most this much before
# floors in 100 million KRW (억): spot net buying of the individual / net selling of a main position
FLOOR = {("kospi", "retail"): 5000.0, ("kosdaq", "retail"): 1500.0,
         ("kospi", "foreign"): 10000.0, ("kospi", "institution"): 5000.0, ("kospi", "combined"): 15000.0,
         ("kosdaq", "foreign"): 2500.0, ("kosdaq", "institution"): 1000.0}
# the main positions' own selling for retail_alone: each at least this (억)
SIDE_FLOOR = {("kospi", "foreign"): 2000.0, ("kospi", "institution"): 1000.0,
              ("kosdaq", "foreign"): 500.0, ("kosdaq", "institution"): 200.0}
ALONE_SHARE = 0.7                   # foreign + institution selling at least this share of the individual's buying
STRONG_TIMES = 2.0
FUTURES_SELL = 500.0                # foreign futures net selling (contracts) that makes a KOSPI retail_alone strong
MULTIPLIER = 250_000                # KRW per KOSPI200 futures point
MIN_DAYS = 10
PCT = {"retail": 0.9, "sell": 0.05}
RULES = ("retail_alone", "heavy_sell_foreign", "heavy_sell_institution", "heavy_sell_combined")
WHO = {"retail_alone": "individual", "heavy_sell_foreign": "foreign", "heavy_sell_institution": "institution",
       "heavy_sell_combined": "all"}
RULE_KO = {"retail_alone": "개미 홀로 받치기", "heavy_sell_foreign": "외인 과도한 매도",
           "heavy_sell_institution": "기관 과도한 매도", "heavy_sell_combined": "외인 · 기관 합산 과도한 매도"}
MARKET_KO = {"kospi": "코스피", "kosdaq": "코스닥"}


def _clock(at: datetime) -> int:
    local = at.astimezone(KST)
    return local.hour * 60 + local.minute


def _latest(points: list[dict], market: str) -> tuple[datetime, dict] | None:
    s = flow_intraday.series(points, market)
    return s[-1] if s else None


def _futures(points: list[dict]) -> tuple[list[datetime], list[tuple[datetime, dict]]]:
    s = flow_intraday.series(points, "futures")
    return [t for t, _ in s], s


def _futures_at(futures: tuple[list[datetime], list[tuple[datetime, dict]]], at: datetime) -> dict | None:
    """The futures snapshot at or before ``at`` (within SLACK_MIN): foreign / institution contracts, price."""
    times, s = futures
    i = bisect_right(times, at) - 1
    if i < 0 or at - times[i] > timedelta(minutes=SLACK_MIN):
        return None
    return s[i][1]


def _combined(spot: dict, fut: dict | None) -> float | None:
    """KOSPI foreign + institution spot plus their futures in 억 (None without the spot pair)."""
    if spot.get("foreign") is None or spot.get("institution") is None:
        return None
    total = spot["foreign"] + spot["institution"]
    if fut and fut.get("price") and fut.get("foreign") is not None and fut.get("institution") is not None:
        total += (fut["foreign"] + fut["institution"]) * fut["price"] * MULTIPLIER / 1e8
    return total


def history(days: list[list[dict]]) -> dict:
    """Per (market, who) and minute of the day, the sorted values of the past ``days`` at that minute (each
    a day's snapshots) - ``who`` individual · foreign · institution · combined; ``n`` the days with spot."""
    out: dict[tuple[str, str, int], list[float]] = {}
    n = 0
    for points in days:
        seen = False
        futures = _futures(points)                  # once a day, not once a minute
        for market in MARKETS:
            s = flow_intraday.series(points, market)
            if not s:
                continue
            seen = True
            times = [t for t, _ in s]
            for minute in range(_clock(datetime.combine(s[0][0].date(), OPEN, KST)) + FROM_MIN,
                                _clock(datetime.combine(s[0][0].date(), UNTIL, KST))):
                at = datetime.combine(s[0][0].date(), time(minute // 60, minute % 60), KST)
                i = bisect_right(times, at) - 1
                if i < 0 or at - times[i] > timedelta(minutes=SLACK_MIN):
                    continue
                vals = s[i][1]
                for who in ("individual", "foreign", "institution"):
                    if vals.get(who) is not None:
                        out.setdefault((market, who, minute), []).append(float(vals[who]))
                if market == "kospi":
                    both = _combined(vals, _futures_at(futures, at))
                    if both is not None:
                        out.setdefault((market, "combined", minute), []).append(both)
        n += seen
    for sample in out.values():
        sample.sort()
    return {"n": n, "at": out}


def _share_at_or_below(sample: list[float], value: float) -> float:
    return bisect_right(sample, value) / len(sample)


def _past(hist: dict | None, market: str, who: str, minute: int) -> list[float] | None:
    if not hist or hist.get("n", 0) < MIN_DAYS:
        return None
    sample = (hist.get("at") or {}).get((market, who, minute)) or []
    return sample if len(sample) >= MIN_DAYS else None     # a minute some days lack (a half day): floors only


def _alert(rule: str, market: str, at: datetime, value: float, severity: str, **extra) -> dict:
    return {"kind": "flow_warn", "rule": rule, "market": market, "who": WHO[rule], "severity": severity,
            "t": at.isoformat(timespec="seconds"), "value": round(value, 2), "prev": None, **extra}


def judge(points: list[dict], hist: dict | None = None) -> list[dict]:
    """The warnings at the latest snapshot of each spot market."""
    out = []
    for market in MARKETS:
        got = _latest(points, market)
        if not got:
            continue
        at, vals = got
        minute = _clock(at)
        opened = datetime.combine(at.date(), OPEN, KST) + timedelta(minutes=FROM_MIN)
        if not opened <= at < datetime.combine(at.date(), UNTIL, KST):
            continue
        ind, fgn, ins = vals.get("individual"), vals.get("foreign"), vals.get("institution")
        if ind is None or fgn is None or ins is None:
            continue
        fut = _futures_at(_futures(points), at) if market == "kospi" else None
        fut_foreign = (fut or {}).get("foreign")
        common = {"individual": ind, "foreign": fgn, "institution": ins, "futures_foreign": fut_foreign,
                  "futures_institution": (fut or {}).get("institution")}

        past = _past(hist, market, "individual", minute)
        pct = None if past is None else _share_at_or_below(past, ind)
        floor = FLOOR[(market, "retail")]
        if (ind >= floor and fgn <= -SIDE_FLOOR[(market, "foreign")] and ins <= -SIDE_FLOOR[(market, "institution")]
                and -(fgn + ins) >= ALONE_SHARE * ind and (pct is None or pct >= PCT["retail"])):
            strong = ind >= STRONG_TIMES * floor or (fut_foreign is not None and fut_foreign <= -FUTURES_SELL)
            out.append(_alert("retail_alone", market, at, ind, "strong" if strong else "caution", pct=pct,
                              days=None if past is None else len(past), **common))

        sellers = [("heavy_sell_foreign", "foreign", fgn), ("heavy_sell_institution", "institution", ins)]
        if market == "kospi":
            sellers.append(("heavy_sell_combined", "combined", _combined(vals, fut)))
        for rule, who, value in sellers:
            if value is None:
                continue
            floor = FLOOR[(market, who)]
            past = _past(hist, market, who, minute)
            pct = None if past is None else _share_at_or_below(past, value)
            if value <= -floor and (pct is None or pct <= PCT["sell"]):
                severity = "strong" if value <= -STRONG_TIMES * floor else "caution"
                out.append(_alert(rule, market, at, value, severity, pct=pct,
                                  days=None if past is None else len(past), **common))
    return out


def _eok(v: float | None) -> str:
    """억 with 조 past 10,000: +1.25조 · −7,718억."""
    if v is None:
        return "?"
    sign = "+" if v >= 0 else "−"
    a = round(abs(v))                               # 9,999.6억 reads 1.00조, not 10,000억
    return f"{sign}{a / 10000:.2f}조" if a >= 10000 else f"{sign}{a:,}억"


def _contracts(v: float | None) -> str:
    return "?" if v is None else f"{v:+,.0f}".replace("-", "−") + "계약"


def text(alert: dict) -> str:
    """e.g. "개미 홀로 받치기 · 코스피 · 10:42 — 개인 +1.23조 vs 외인 −8,599억 · 기관 −4,926억, 외인 선물 −508계약"."""
    rule, market = alert["rule"], alert["market"]
    hhmm = datetime.fromisoformat(alert["t"]).astimezone(KST).strftime("%H:%M")
    head = f"{RULE_KO[rule]} · {MARKET_KO[market]} · {hhmm}"
    ind, fgn, ins = alert.get("individual"), alert.get("foreign"), alert.get("institution")
    if rule == "retail_alone":
        body = f"개인 {_eok(ind)} vs 외인 {_eok(fgn)} · 기관 {_eok(ins)}"
        if alert.get("futures_foreign") is not None and market == "kospi":
            body += f", 외인 선물 {_contracts(alert['futures_foreign'])}"
    elif rule == "heavy_sell_combined":
        if alert.get("futures_foreign") is None or alert.get("futures_institution") is None:
            body = f"외인 · 기관 현물 {_eok(alert['value'])} (외인 {_eok(fgn)} · 기관 {_eok(ins)}, 선물 값 없음)"
        else:
            body = f"외인 · 기관 현물 + 선물 {_eok(alert['value'])} (현물 외인 {_eok(fgn)} · 기관 {_eok(ins)}"
            body += f", 선물 외인 {_contracts(alert['futures_foreign'])} · 기관 {_contracts(alert['futures_institution'])})"
    else:
        body = f"{'외인' if rule == 'heavy_sell_foreign' else '기관'} 누적 {_eok(alert['value'])} (개인 {_eok(ind)})"
    if alert.get("pct") is not None:
        body += f" — {_rank(alert['pct'], alert.get('days'), rule == 'retail_alone')}"
    hedge = alert.get("futures_institution")
    if rule == "retail_alone" and market == "kospi" and hedge is not None and hedge > 0:
        body += f" · 기관은 선물 {_contracts(hedge)} 매수(헤지 · 방어)"
    return f"{head} — {body}"


def _rank(pct: float, days: int | None, buying: bool) -> str:
    """Where today stands among the past days at the same minute: "20일 같은 시각 중 최대" · "상위 5%"."""
    beyond = 1 - pct if buying else pct          # the share of past days further out than today
    if beyond <= 0:
        return f"{days}일 같은 시각 중 {'최대' if buying else '최대 매도'}"
    return f"{days}일 같은 시각 {'상위' if buying else '매도 상위'} {max(1, round(beyond * 100))}%"
