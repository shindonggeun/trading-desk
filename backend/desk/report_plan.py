"""#681 the desk report's plan: one report a US trading day (after the US close) on a subject from a rotation
of semiconductor and leveraged names, a weekly wrap-up on Saturday and the week ahead on Sunday - each built
around where KOSPI and the Nasdaq-100 future stand against their nearest resistance and support.

This module holds the schedule (``slots`` / ``due``), the rotation (``topics`` / ``rank``) and the three
drafts (``daily`` / ``weekly`` / ``next_week``) in the shape desk.reports stores and the report page reads
(#495: sections with a term box and a chart spec, stages, a checklist, a closing line). Every number comes
from the desk; the model only rewords (desk.reports.write_draft drops a sentence with a number of its own).

Each section's ``chart`` names its ``symbol`` (and ``timeframe``), so a candle chart (#682) can be drawn
from the same spec later.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from desk import explain, market_calendar

# the rotation: semiconductors and leveraged funds, as the user follows them (DESK_REPORT_TOPICS overrides)
TOPICS = (("BTC-USD", "비트코인"), ("NVDA", "엔비디아"), ("^SOX", "필라델피아 반도체"), ("005930.KS", "삼성전자"),
          ("000660.KS", "SK하이닉스"), ("KORU", "KORU"), ("SOXL", "SOXL"), ("GDXU", "GDXU"))
# the two indexes every report starts from
INDEXES = (("^KS11", "코스피"), ("NQ=F", "나스닥100 선물"))
KOSPI, NQ = "^KS11", "NQ=F"
# which index a subject follows: Korean names and the Korea 3x fund the KOSPI, the rest the Nasdaq-100 future
LINKS = {"005930.KS": KOSPI, "000660.KS": KOSPI, "KORU": KOSPI}
RECENT_DAYS = 7                    # a subject written in this many days goes to the back of the rotation
LINK_DAYS = 60                     # the days the subject and its index are compared over
AFTER_US_CLOSE = timedelta(minutes=40)    # the daily report waits for the US close and the closing prints
WEEKLY_AT = time(10, 0)            # Saturday, KST: the week's wrap-up
NEXT_WEEK_AT = time(18, 0)         # Sunday, KST: the week ahead
STALE = timedelta(hours=20)        # a slot missed by more than this (the desk was down) is skipped, not written late
KIND_KO = {"daily": "오늘의 리포트", "weekly": "주간 정리", "next_week": "다음 주 관점"}
STATE_KO = {"converging": "수렴 중", "broke_up": "위로 깸", "broke_down": "아래로 깸"}
WEEKDAY_KO = market_calendar.WEEKDAY_KO


@dataclass(frozen=True)
class Slot:
    kind: str          # daily | weekly | next_week
    key: str           # "daily:2026-11-19" - stored with the report, so a slot is written once
    due: datetime      # UTC


def topics(raw: str | None) -> tuple[tuple[str, str], ...]:
    """DESK_REPORT_TOPICS ("SOXL,005930.KS:삼성전자,...") or the default rotation."""
    out = []
    for part in (raw or "").split(","):
        sym, _, name = part.strip().partition(":")
        if sym.strip():
            out.append((sym.strip(), name.strip() or sym.strip()))
    return tuple(out) or TOPICS


def link_of(symbol: str) -> str:
    return LINKS.get(symbol, KOSPI if symbol.endswith((".KS", ".KQ")) else NQ)


# ---------------------------------------------------------------- the schedule

def slots(now: datetime, back_days: int = 3, ahead_days: int = 8) -> list[Slot]:
    """The report slots around ``now`` (oldest first): each US trading day's close + AFTER_US_CLOSE, each
    Saturday WEEKLY_AT and each Sunday NEXT_WEEK_AT (KST)."""
    out = []
    today = now.astimezone(market_calendar.KST).date()
    for i in range(-back_days, ahead_days + 1):
        d = today + timedelta(days=i)
        if market_calendar.is_open("us", d):
            _, close = market_calendar.session_bounds("us", d)
            out.append(Slot("daily", f"daily:{d.isoformat()}", (close + AFTER_US_CLOSE).astimezone(UTC)))
        if d.weekday() == 5:
            out.append(Slot("weekly", f"weekly:{d.isoformat()}",
                            datetime.combine(d, WEEKLY_AT, market_calendar.KST).astimezone(UTC)))
        if d.weekday() == 6:
            out.append(Slot("next_week", f"next_week:{d.isoformat()}",
                            datetime.combine(d, NEXT_WEEK_AT, market_calendar.KST).astimezone(UTC)))
    return sorted(out, key=lambda s: s.due)


def due(now: datetime, done: set[str]) -> tuple[Slot | None, datetime | None]:
    """The slot to write now (the latest one due and not written, missed by less than STALE) and when the
    next one comes."""
    got = [s for s in slots(now) if s.key not in done]
    ready = [s for s in got if s.due <= now and now - s.due <= STALE]
    later = [s.due for s in got if s.due > now]
    return (ready[-1] if ready else None), (min(later) if later else None)


def kind_for(now: datetime) -> str:
    """A manual report's kind: the weekly wrap-up on Saturday, the week ahead on Sunday (KST), else daily."""
    return {5: "weekly", 6: "next_week"}.get(now.astimezone(market_calendar.KST).weekday(), "daily")


def rank(measured: list[dict], recent: dict[str, datetime], now: datetime) -> list[dict]:
    """The topics, the one that moved most first - a subject written in the last RECENT_DAYS goes behind
    every one that was not (the rotation), and behind the older ones among those."""
    def written(m: dict) -> bool:
        at = recent.get(m["symbol"])
        return at is not None and now - at <= timedelta(days=RECENT_DAYS)

    fresh = sorted((m for m in measured if not written(m)), key=lambda m: m["score"], reverse=True)
    again = sorted((m for m in measured if written(m)), key=lambda m: recent[m["symbol"]])
    return fresh + again


# ---------------------------------------------------------------- the desk's numbers beyond desk.reports.measure

def same_direction(a: list[dict], b: list[dict], days: int = LINK_DAYS) -> dict | None:
    """How often the subject and its index moved the same way, day by day (matched on the local day), over
    the last ``days`` common days: a plain measure of how closely one follows the other."""
    def moves(bars: list[dict]) -> dict[str, float]:
        out, prev = {}, None
        for x in bars:
            if prev and prev.get("close") and x.get("close"):
                out[x["day"]] = x["close"] / prev["close"] - 1
            prev = x
        return out

    ma, mb = moves(a), moves(b)
    common = sorted(set(ma) & set(mb))[-days:]
    pairs = [(ma[d], mb[d]) for d in common if ma[d] and mb[d]]
    if len(pairs) < 20:
        return None
    same = sum((x > 0) == (y > 0) for x, y in pairs)
    return {"days": len(pairs), "same_pct": round(100 * same / len(pairs))}


def pattern_of(daily: list[dict], atr: float | None) -> dict | None:
    """The daily converging pattern (desk.patterns, #297 · #664) of an index, in short."""
    from desk import patterns

    if not atr:
        return None
    try:
        got = patterns.find(daily, 3, atr, "1d")
    except (ValueError, ZeroDivisionError):
        return None
    if not got:
        return None
    p = got[0]
    return {"label": p["label"], "state": p["state"], "upper": p["upper"]["to"]["price"],
            "lower": p["lower"]["to"]["price"], "status": (p.get("breakout") or {}).get("label"),
            "reference": p.get("reference_label")}


def expiries(start: date, end: date) -> list[dict]:
    """The option expiries between ``start`` and ``end``: KOSPI 200 (the second Thursday, an open day) and the
    US monthly (the third Friday, the day before when it is a holiday)."""
    from desk import futures_oi

    out = []
    for y, m in {(start.year, start.month), (end.year, end.month)}:
        kr = futures_oi.expiry_of(y, m)
        first = date(y, m, 1)
        fri = first + timedelta(days=(4 - first.weekday()) % 7 + 14)
        while not market_calendar.is_open("us", fri):
            fri -= timedelta(days=1)
        out += [{"day": kr, "label": "코스피200 옵션 만기"}, {"day": fri, "label": "미국 월물 옵션 만기"}]
    return sorted((e for e in out if start <= e["day"] <= end), key=lambda e: e["day"])


# ---------------------------------------------------------------- drafts

def _p(x: float | None) -> str:
    if x is None:
        return "?"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _pct(x: float | None) -> str:
    return "?" if x is None else f"{x:+.1f}%"


def _zone(z: dict | None) -> str:
    return "없음" if not z else f"{_p(z['low'])} ~ {_p(z['high'])}"


def _day(d: date) -> str:
    return f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})"


def _term(name: str, text: str) -> dict:
    return {"name": name, "explain": text}


def _chart(f: dict) -> dict:
    """A levels chart for one subject - named, so #682 can draw candles from the same spec."""
    res, sup = (f.get("resistances") or [None])[0], (f.get("supports") or [None])[0]
    levels, labels = [], []
    if res:
        levels.append(res["low"])
        labels.append("가까운 저항")
    if sup:
        levels.append(sup["high"])
        labels.append("가까운 지지")
    return {"kind": "levels", "symbol": f["symbol"], "timeframe": "1d", "levels": levels, "labels": labels,
            "marks": [f["close"]]}


def _where(f: dict) -> str:
    """One index's place: the close, the nearest resistance and support zones, the converging pattern."""
    res, sup = (f.get("resistances") or [None])[0], (f.get("supports") or [None])[0]
    text = f"{f['name']}은(는) {_p(f['close'])}(1주 {_pct(f['week_pct'])})이에요."
    if res and sup:
        text += f" 가장 가까운 저항은 {_zone(res)}, 지지는 {_zone(sup)}예요."
    elif res:
        text += f" 가장 가까운 저항은 {_zone(res)}이고, 아래로 가까운 지지 구역은 없어요."
    elif sup:
        text += f" 가장 가까운 지지는 {_zone(sup)}이고, 위로 가까운 저항 구역은 없어요."
    pat = f.get("pattern")
    if pat:
        status = pat["status"] or STATE_KO.get(pat["state"], pat["state"])
        text += (f" 일봉에서는 {pat['label']} 모양({status})으로 "
                 f"윗선 {_p(pat['upper'])} · 아랫선 {_p(pat['lower'])}이에요.")
    return text


def index_section(indexes: list[dict], first: str | None = None) -> dict:
    """① where KOSPI and the Nasdaq-100 future stand - ``first`` (the subject's index) leads."""
    order = sorted(indexes, key=lambda f: f["symbol"] != first)
    lead = order[0]
    res = (lead.get("resistances") or [None])[0]
    return {"title": "지수부터: 코스피 · 나스닥100 선물의 자리",
            "body": " ".join(_where(f) for f in order),
            "highlight": f"{lead['name']} 저항 {_zone(res)}" if res else f"{lead['name']} {_p(lead['close'])}",
            "term": _term("저항 · 지지", "가격이 오르다(내리다) 여러 번 멈췄던 가격대예요. 넘으면(깨지면) 다음 자리까지 열려요."),
            "chart": _chart(lead), "image": None}


def link_section(subject: dict, index: dict, link: dict | None) -> dict:
    """③ how the subject follows its index: the same-direction share, the index's line to watch."""
    res = (index.get("resistances") or [None])[0]
    sup = (index.get("supports") or [None])[0]
    follow = (f"최근 {link['days']}거래일 동안 {subject['name']}은(는) {index['name']}과(와) {link['same_pct']}% 의 날에 "
              f"같은 방향으로 움직였어요. " if link else "")
    body = (follow + f"그래서 {index['name']}이(가) 저항 {_zone(res)}을(를) 종가로 넘는지, 지지 {_zone(sup)}에서 버티는지가 "
            f"{subject['name']}에도 이어지기 쉬워요.")
    return {"title": f"{index['name']}과(와) 어떻게 이어지나", "body": body,
            "highlight": f"같은 방향 {link['same_pct']}%" if link else f"{index['name']} 저항 {_zone(res)}",
            "term": _term("같은 방향 비율", "두 가격이 하루 단위로 함께 오르거나 함께 내린 날의 비율이에요. 높을수록 지수를 따라가요."),
            # the index's levels are in the first section's chart: room here for an explainer picture (#497)
            "chart": {"kind": "none", "symbol": index["symbol"], "timeframe": "1d"}, "image": None}


def daily(base: dict, subject: dict, indexes: list[dict], link: dict | None) -> dict:
    """The day's report: ① the indexes ② the subject's levels and stages (desk.reports.rules) ③ the link."""
    index = next((f for f in indexes if f["symbol"] == link_of(subject["symbol"])), indexes[0] if indexes else None)
    sections = list(base["sections"])
    for s in sections:
        if isinstance(s.get("chart"), dict):
            s["chart"] = {**s["chart"], "symbol": subject["symbol"], "timeframe": "1d"}
    if index:
        sections = ([index_section(indexes, index["symbol"])] + sections[:explain.MAX_SECTIONS - 2]
                    + [link_section(subject, index, link)])
    return {**base, "kind": "daily", "sections": sections[:explain.MAX_SECTIONS]}


def _stages(indexes: list[dict]) -> list[dict]:
    out = []
    for f in indexes:
        res, sup = (f.get("resistances") or [None])[0], (f.get("supports") or [None])[0]
        if res:
            out.append({"label": f"{f['name']} 위", "low": res["low"], "high": res["high"],
                        "why": "종가로 이 구역을 넘으면 다음 저항까지 열려요."})
        if sup:
            out.append({"label": f"{f['name']} 아래", "low": sup["low"], "high": sup["high"],
                        "why": "이 구역이 깨지면 받쳐 준다는 생각을 바꿔요."})
    return out


def _calls_text(calls: dict | None) -> str | None:
    """The week's day-call record (desk.day_calls) in a sentence, or None without a scored call."""
    parts = []
    for market, name in (("kr", "코스피"), ("us", "미국 지수")):
        c = (calls or {}).get(market) or {}
        if c.get("n") and c.get("hit_pct") is not None:
            parts.append(f"{name} {c['n']}번 가운데 {c['hit_pct']}%")
    return ("이번 주 '오늘 방향' 예측은 " + ", ".join(parts) + " 맞았어요.") if parts else None


def weekly(indexes: list[dict], movers: list[dict], calls: dict | None, as_of: str) -> dict:
    """Saturday: the week's moves, the topics that moved most, the day-call record, the levels for next week."""
    by = {f["symbol"]: f for f in indexes}
    k, nq = by.get(KOSPI), by.get(NQ)
    ranked = sorted((m for m in movers if m.get("week_pct") is not None), key=lambda m: m["week_pct"], reverse=True)
    sections = [{
        "title": "이번 주 지수",
        "body": " ".join(f"{f['name']}은(는) 한 주 {_pct(f['week_pct'])} 움직여 {_p(f['close'])}에 마쳤어요." for f in indexes),
        "highlight": " · ".join(f"{f['name']} {_pct(f['week_pct'])}" for f in indexes),
        "term": _term("주간 등락", "지난주 마지막 종가에서 이번 주 마지막 종가까지 몇 % 움직였는지예요."),
        "chart": _chart(nq or indexes[0]), "image": None}]
    if ranked:
        top, bottom = ranked[:2], ranked[-2:] if len(ranked) > 2 else []
        body = "가장 강했던 쪽은 " + ", ".join(f"{m['name']} {_pct(m['week_pct'])}" for m in top) + "이에요."
        if bottom:
            body += " 가장 약했던 쪽은 " + ", ".join(f"{m['name']} {_pct(m['week_pct'])}" for m in bottom) + "이에요."
        sections.append({"title": "이번 주 순환 종목의 강약", "body": body,
                         "highlight": f"{top[0]['name']} {_pct(top[0]['week_pct'])}",
                         "term": _term("레버리지 ETF", "지수의 하루 등락을 2~3배로 따라가는 상품이에요. 한 주가 길어질수록 지수와 차이가 벌어져요."),
                         "chart": _chart(top[0]), "image": None})
    said = _calls_text(calls)
    if said:
        sections.append({"title": "오늘 방향 예측은 얼마나 맞았나", "body": said + " 맞힌 비율은 짧은 기간에는 크게 흔들려요.",
                         "highlight": said, "term": _term("오늘 방향", "데스크가 장 시작 전에 낸 그날 지수의 오름 · 내림 판단이에요."),
                         "chart": None, "image": None})
    sections.append({"title": "다음 주에 볼 자리", "body": " ".join(_where(f) for f in indexes),
                     "highlight": " · ".join(f"{f['name']} 저항 {_zone((f.get('resistances') or [None])[0])}" for f in indexes),
                     "term": _term("구역", "한 점이 아니라 폭이 있는 가격대예요. 그 안에서 종가가 어디서 끝나는지 봐요."),
                     "chart": _chart(k or indexes[0]), "image": None})
    title = "이번 주 정리 — " + " · ".join(f"{f['name']} {_pct(f['week_pct'])}" for f in indexes)
    return {"version": 1, "kind": "weekly", "title": title, "hook": title,
            "summary": " · ".join(f"{f['name']} 1주 {_pct(f['week_pct'])}" for f in indexes) + ".",
            "sections": sections[:explain.MAX_SECTIONS], "similar": [], "stages": _stages(indexes),
            "checklist": _checks(indexes),
            "closing": "숫자는 모두 데스크가 계산한 값이에요. 한 주를 돌아본 정리이고 예측이 아니에요.",
            "source": "rules", "as_of": as_of}


def _checks(indexes: list[dict]) -> list[dict]:
    out = []
    for f in indexes:
        res = (f.get("resistances") or [None])[0]
        if res:
            out.append({"q": f"{f['name']}이(가) 저항을 넘었나요?", "a": f"{_zone(res)} 위에서 종가가 나와야 넘었다고 봐요."})
    out.append({"q": "일정 전후로 비중을 정했나요?", "a": "큰 발표 날은 흔들림이 커요. 미리 정한 자리에서만 움직여요."})
    return out[:5]


def next_week(indexes: list[dict], watch: list[dict], events: list[dict], as_of: str) -> dict:
    """Sunday: the week ahead - the scheduled events (KST), the indexes' levels, the topics' nearest levels."""
    when = "; ".join(f"{e['when']} {e['label']}" for e in events) if events else None
    sections = [{
        "title": "다음 주 일정",
        "body": (f"다음 주 큰 일정은 {when}이에요. 발표 전후로는 평소보다 크게 움직일 수 있어요." if when
                 else "다음 주에는 데스크가 아는 큰 일정이 없어요(실적 발표는 종목 화면에서 봐요)."),
        "highlight": events[0]["label"] if events else "큰 일정 없음",
        "term": _term("옵션 만기", "옵션 계약이 끝나는 날이에요. 큰 물량이 정리되며 지수가 흔들리기 쉬워요."),
        "chart": None, "image": None}]
    for f in indexes:
        sections.append({"title": f"{f['name']} 볼 자리", "body": _where(f),
                         "highlight": f"저항 {_zone((f.get('resistances') or [None])[0])} · 지지 {_zone((f.get('supports') or [None])[0])}",
                         "term": _term("저항 · 지지", "가격이 여러 번 멈췄던 가격대예요. 종가가 그 밖에서 끝나면 넘었다(깨졌다)고 봐요."),
                         "chart": _chart(f), "image": None})
    if watch:
        sections.append({"title": "같이 볼 종목",
                         "body": " ".join(f"{m['name']}은(는) {_p(m['close'])}, 저항 {_zone((m.get('resistances') or [None])[0])} · "
                                          f"지지 {_zone((m.get('supports') or [None])[0])}이에요." for m in watch),
                         "highlight": ", ".join(m["name"] for m in watch),
                         "term": _term("가까운 자리", "지금 가격에서 가장 가까운 저항 · 지지 구역이에요."),
                         "chart": _chart(watch[0]), "image": None})
    title = "다음 주 볼 것 — " + (events[0]["label"] + " · " if events else "") + "코스피 · 나스닥100 선물 자리"
    return {"version": 1, "kind": "next_week", "title": title, "hook": title,
            "summary": (f"일정: {when}. " if when else "") + " · ".join(
                f"{f['name']} 저항 {_zone((f.get('resistances') or [None])[0])}" for f in indexes) + ".",
            "sections": sections[:explain.MAX_SECTIONS], "similar": [], "stages": _stages(indexes),
            "checklist": _checks(indexes),
            "closing": "숫자는 모두 데스크가 계산한 값이에요. 다음 주를 미리 정리한 것이고 예측이 아니에요.",
            "source": "rules", "as_of": as_of}


def week_events(now: datetime, calendar_events: list[dict]) -> list[dict]:
    """The next Monday-Friday's events: the macro calendar's (``{"label", "at"}`` UTC) and the option expiries,
    as "11/25(화) 04:00" in KST."""
    today = now.astimezone(market_calendar.KST).date()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    friday = monday + timedelta(days=4)
    out = []
    for e in calendar_events:
        at = e["at"].astimezone(market_calendar.KST)
        if monday <= at.date() <= friday + timedelta(days=1):       # a Friday ET release lands on Saturday KST
            out.append((at, {"label": e["label"], "when": f"{_day(at.date())} {at:%H:%M}"}))
    for x in expiries(monday, friday):
        out.append((datetime.combine(x["day"], time(15, 20), market_calendar.KST),
                    {"label": x["label"], "when": _day(x["day"])}))
    return [e for _, e in sorted(out, key=lambda t: t[0])]
