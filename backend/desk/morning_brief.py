"""The morning market brief (#571): every Korean trading day at 07:00 KST, after the US close.

One short read of "what happened overnight and what to watch in Korea today", in the order the user's
example uses: a headline, the key numbers (US indexes and index futures, the fear gauges), yesterday's
Korean flow (foreign · institution · individual net buying and the index level; #605 the leveraged /
inverse ETFs' net assets and units and the KTB 3 / 10-year yields from KRX, with their date - KRX
publishes a day at 08:00 KST the next business day), the things that are done
and the ones still ahead, the schedule (releases, expiries, earnings, hand-kept items in
``data/calendar.json``), 🟢 what reads well / 🔴 what is easy to misread, and a closing line.

- ``collect`` gathers the inputs from the desk's own sources (each one may fail alone: it is then left
  out and named in ``missing``); ``bundle`` cuts them to the few numbers the brief may use.
- ``rules`` writes the brief from those numbers alone (the fallback, and the model's reference).
- ``write`` lets the model (the chat's provider, one call) reword it field by field; a sentence with a
  number the bundle does not hold is dropped (desk.explain.clean), and so is one that is not Korean
  (#567's check when it is there). Headlines are untrusted text: shown to the model, never a source of
  numbers.
- ``Briefs`` stores them (``morning_briefs``) and says when the next automatic one is due.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from desk import explain, krx_etp, market_calendar, runner
from desk.db import morning_briefs as briefs_t
from desk.db import now as db_now

log = logging.getLogger(__name__)

KST = market_calendar.KST
ET = market_calendar.ET
VERSION = 1
DEFAULT_AT = time(7, 0)
LATE_CUTOFF = time(11, 0)        # past this a missed brief waits for the next trading day (no "morning" at noon)
RETRY_SEC = 5 * 60               # the ChatGPT slot busy: try again
DESK_AFTER_SEC = 30 * 60         # still busy this long after the due time: the desk's own wording, on time
MANUAL_PER_DAY = 3               # POST .../run a Korean day (quota guard)
STALE_DAYS = 5                   # a daily bar older than this is no "last close"
LIST_MAX = 6                     # items per list field
SCHEDULE_DAYS = 7
CALENDAR_FILE = Path(__file__).parent / "data" / "calendar.json"

US_INDEXES = (("^GSPC", "S&P500"), ("^IXIC", "나스닥"), ("^DJI", "다우"), ("^SOX", "필라델피아 반도체"))
US_FUTURES = (("NQ=F", "나스닥100 선물"), ("ES=F", "S&P500 선물"))
KR_INDEXES = (("^KS11", "코스피"), ("^KQ11", "코스닥"))
FLOW_MARKETS = ("KOSPI", "KOSDAQ")
FLOW_NAMES = {"foreigner": "외국인", "institution": "기관", "individual": "개인"}
FIELDS = ("title", "numbers", "kr_flow", "done", "pending", "schedule", "good", "bad", "closing")
LISTS = ("numbers", "done", "pending", "schedule", "good", "bad")
TEXT_LIMIT = 300                 # characters of a model's text field read; kr_flow carries #605's KRX lines too
TEXT_LIMITS = {"kr_flow": 700}
WEEKDAY_KO = "월화수목금토일"


# ---------------------------------------------------------------- language (#567)

_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def foreign(text: str | None) -> str | None:
    """"ja" / "zh" when ``text`` is not Korean, else None: desk.language.foreign (#567) once it is
    merged; until then its whole-text rule (#583 thresholds)."""
    try:
        from desk import language  # type: ignore[attr-defined]
        return language.foreign(text)
    except ImportError:
        pass
    if not text:
        return None
    hangul, kana, han = len(_HANGUL.findall(text)), len(_KANA.findall(text)), len(_HAN.findall(text))
    letters = hangul + kana + han
    if letters < 8:                  # too little to tell (a quoted name, a hanja word)
        return None
    if kana >= 5 and kana >= 0.1 * letters:
        return "ja"
    return "zh" if han >= 8 and han >= 0.5 * letters else None


# ---------------------------------------------------------------- inputs


@dataclass
class Sources:
    """Where the inputs come from (desk.api wires the live ones; tests and scripted runs fake them).
    ``candles(symbol, interval, count)`` desk.prices; ``fear()`` desk.fear.Fear.get; ``flow(market)`` desk.market_flow
    (1d records); ``month("YYYY-MM")`` desk.market_calendar.month_view; ``macro()`` desk.macro_news brief;
    ``day_call()`` today's direction line (#572), None until it exists; ``krx()`` desk.krx_etp.summary (#605)."""
    candles: Callable[[str, str, int], list]
    fear: Callable[[], Awaitable[dict]] | None = None
    flow: Callable[[str], Awaitable[dict]] | None = None
    month: Callable[[str], dict] | None = None
    macro: Callable[[], dict] | None = None
    day_call: Callable[[], dict | None] | None = None
    extra: Callable[[], list[dict]] | None = None
    krx: Callable[[], dict | None] | None = None


def day_call_source() -> Callable[[], dict | None] | None:
    """#572's ``desk.day_call.today_call`` (today's direction for Korea and the US) once it is merged."""
    try:
        from desk import day_call  # type: ignore[attr-defined]
    except ImportError:
        return None
    return getattr(day_call, "today_call", None)


def hand_kept(path: Path = CALENDAR_FILE) -> list[dict]:
    """The schedule items kept by hand (``data/calendar.json``)."""
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("events") or [])
    except (OSError, ValueError, AttributeError):
        return []


async def collect(src: Sources, now: datetime | None = None) -> dict:
    """Every input, each fetched on its own; a failing one is None and named in ``missing``."""
    now = now or datetime.now(UTC)
    kst = now.astimezone(KST)
    raw: dict[str, Any] = {"now": now.isoformat(timespec="seconds"), "missing": []}

    async def guard(name: str, coro):
        try:
            return await coro
        except Exception as exc:  # noqa: BLE001 - one source down leaves the others
            log.info("morning brief: %s failed: %s", name, type(exc).__name__)
            raw["missing"].append(name)
            return None

    symbols = [s for s, _ in US_INDEXES + US_FUTURES + KR_INDEXES]
    bars = await asyncio.gather(*(guard(s, asyncio.to_thread(src.candles, s, "1d", 10)) for s in symbols))
    raw["bars"] = dict(zip(symbols, bars, strict=True))
    raw["fear"] = await guard("fear", src.fear()) if src.fear else None
    raw["flow"] = {m: (await guard(f"flow {m}", src.flow(m)) if src.flow else None) for m in FLOW_MARKETS}
    months = sorted({(kst - timedelta(days=5)).strftime("%Y-%m"), kst.strftime("%Y-%m"),
                     (kst + timedelta(days=SCHEDULE_DAYS)).strftime("%Y-%m")})
    raw["months"] = [m for m in [await guard(f"calendar {mo}", asyncio.to_thread(src.month, mo)) if src.month else None
                                 for mo in months] if m]
    raw["macro"] = await guard("macro", asyncio.to_thread(src.macro)) if src.macro else None
    raw["day_call"] = await guard("day_call", asyncio.to_thread(src.day_call)) if src.day_call else None
    raw["extra"] = (await guard("calendar.json", asyncio.to_thread(src.extra))) if src.extra else []
    raw["krx"] = await guard("krx", asyncio.to_thread(src.krx)) if src.krx else None
    return raw


def _pct(x: float) -> str:
    return f"{x:+.2f}%"


def _eok(won: float) -> str:
    return f"{round(won / 1e8):+,}억"


def _close_change(bars, today: date) -> dict | None:
    """The last daily close and its change on the one before; None when stale or too short."""
    rows = [b for b in bars or [] if getattr(b, "close", None)]
    if len(rows) < 2:
        return None
    at = datetime.fromisoformat(rows[-1].at)
    if (today - at.astimezone(KST).date()).days > STALE_DAYS:
        return None
    last, prev = rows[-1].close, rows[-2].close
    return {"close": explain.fmt(last), "change": _pct((last / prev - 1) * 100), "_move": last / prev - 1}


def _same_session(fut, index) -> list:
    """Futures bars up to the US index's last session: from 18:00 ET (07:00 KST in US summer time) Yahoo
    already has the new evening session as the last bar - near flat, and not the night the brief is about."""
    rows = [b for b in fut or [] if getattr(b, "close", None)]
    last = [b for b in index or [] if getattr(b, "close", None)]
    if not rows or not last:
        return rows
    end = datetime.fromisoformat(last[-1].at).astimezone(ET).date()
    return [b for b in rows if datetime.fromisoformat(b.at).astimezone(ET).date() <= end]


def _flow_of(got: dict | None) -> dict | None:
    """The last finished day's net buying by investor (억 원) and the index level."""
    if not got or got.get("error"):
        return None
    rec = next((r for r in got.get("records") or [] if not r.get("in_progress")), None)
    if rec is None:
        return None
    inv = rec.get("investors") or {}
    nets = {k: inv[k]["net"] for k in FLOW_NAMES if k in inv and inv[k].get("net") is not None}
    if not nets:
        return None
    return {"date": rec.get("date"), "net": {FLOW_NAMES[k]: _eok(v) for k, v in nets.items()},
            "_net": nets}


def _when_ko(e: dict) -> str:
    at = datetime.fromisoformat(e["at_kst"]) if e.get("at_kst") else None
    if at is None:
        return ""
    return f"{at.month}/{at.day}({WEEKDAY_KO[at.weekday()]}) {at.strftime('%H:%M')}"


def _events(raw: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """(done in the last 18 hours, ahead within SCHEDULE_DAYS) from the calendar and the hand-kept list."""
    events = [e for m in raw.get("months") or [] for d in m.get("days") or [] for e in d.get("events") or []]
    for x in raw.get("extra") or []:
        try:
            day = date.fromisoformat(x["date"])
            hm = time.fromisoformat(x["time_kst"]) if x.get("time_kst") else None
        except (KeyError, TypeError, ValueError):
            continue
        at = datetime.combine(day, hm or time(9, 0), KST)
        events.append({"label": str(x.get("label") or "")[:60], "market": x.get("market") or "kr", "importance": 2,
                       "at": at.astimezone(UTC).isoformat(), "at_kst": at.isoformat(), "kind": "manual",
                       "no_time": hm is None})
    seen, done, ahead = set(), [], []
    for e in sorted(events, key=lambda e: e.get("at") or ""):
        try:
            at = datetime.fromisoformat(e["at"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (e.get("label"), e.get("at"))
        if key in seen or not e.get("label"):
            continue
        seen.add(key)
        when = _when_ko(e)
        if e.get("no_time"):
            when = when.split(" ")[0]
        item = {"when": when, "label": e["label"], "market": e.get("market"), "importance": e.get("importance", 1),
                "_at": at.isoformat()}
        if now - timedelta(hours=18) <= at <= now:
            done.append(item)
        elif now < at <= now + timedelta(days=SCHEDULE_DAYS):
            ahead.append(item)
    # the market movers first when there are too many, then shown in time order
    keep = sorted(ahead, key=lambda i: -(i["importance"] or 0))[:LIST_MAX + 2]
    return done[:LIST_MAX], sorted(keep, key=lambda i: i["_at"])


def _earnings(raw: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """(reported on the last US day, due on the next one): name, ticker, timing and the EPS estimate.
    The calendar holds estimates, never the results - the brief must not say how a report came out."""
    days = {d["date"]: d for m in raw.get("months") or [] for d in m.get("days") or []}
    us_day = now.astimezone(ET).date()
    for _ in range(6):
        if days.get(us_day.isoformat(), {}).get("us", {}).get("open", us_day.weekday() < 5):
            break
        us_day -= timedelta(days=1)
    nxt = us_day + timedelta(days=1)
    for _ in range(6):
        if days.get(nxt.isoformat(), {}).get("us", {}).get("open", True):
            break
        nxt += timedelta(days=1)
    when = {"before": "장 전", "after": "장 후"}

    def rows(d: date) -> list[dict]:
        out = []
        for r in (days.get(d.isoformat()) or {}).get("earnings") or []:
            if (r.get("ticker") or "").endswith((".KS", ".KQ")):
                continue
            out.append({"name": r.get("name") or r.get("ticker"), "ticker": r.get("ticker"),
                        "when": when.get(r.get("when"), "시각 미정"),
                        "eps_estimate": None if r.get("eps_estimate") is None else f"{r['eps_estimate']:.2f}"})
        return out[:LIST_MAX]
    return rows(us_day), rows(nxt)


def _md(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}"


def _krx(raw: dict) -> dict:
    """#605 the brief's ETFs (desk.krx_etp.ETFS with ``brief``) and the KTB yields, as the words say them."""
    got = raw.get("krx") or {}
    etf, ktb = got.get("etf"), got.get("ktb")
    out: dict[str, Any] = {"etf": None, "ktb": None}
    items = [i for i in (etf or {}).get("items") or [] if i.get("brief")]
    if items:
        out["etf"] = {"date": _md(etf["date"]), "items": [{"name": i["name"], "text": krx_etp.etf_line(i)} for i in items]}
    if ktb and ktb.get("years"):
        out["ktb"] = {"date": _md(ktb["date"]), "text": krx_etp.ktb_line(ktb),
                      "years": {f"국고 {y}년": krx_etp.ktb_line({"years": {y: v}}) for y, v in ktb["years"].items()}}
    return out


def _themes(raw: dict) -> list[dict]:
    """The surging news themes (label and the first headline - untrusted text, no numbers taken from it)."""
    rows = [t for t in (raw.get("macro") or {}).get("themes") or [] if not t.get("quiet") and not t.get("stale")]
    rows.sort(key=lambda t: -(t.get("spike") or 0))
    return [{"label": t.get("label"), "headline": ((t.get("headlines") or [{}])[0].get("title") or "")[:160]}
            for t in rows[:4]]


def bundle(raw: dict, now: datetime | None = None) -> dict:
    """The numbers (and the few texts) the brief may use, from ``collect``'s answer."""
    now = now or datetime.fromisoformat(raw["now"])
    today = now.astimezone(KST).date()
    bars = raw.get("bars") or {}
    us = {name: c for s, name in US_INDEXES if (c := _close_change(bars.get(s), today))}
    fut = {name: c for s, name in US_FUTURES
           if (c := _close_change(_same_session(bars.get(s), bars.get("^GSPC")), today))}
    kr = {name: c for s, name in KR_INDEXES if (c := _close_change(bars.get(s), today))}
    flow = {m: f for m in FLOW_MARKETS if (f := _flow_of((raw.get("flow") or {}).get(m)))}
    fear = []
    for item in (raw.get("fear") or {}).get("items") or []:
        if item.get("value") is None or item.get("error"):
            continue
        fear.append({"name": item.get("name"), "value": explain.fmt(float(item["value"])),
                     "prev_day": None if item.get("prev_day") is None else explain.fmt(float(item["prev_day"])),
                     "band": item.get("band_ko") or item.get("band")})
    done, ahead = _events(raw, now)
    reported, due = _earnings(raw, now)
    call = raw.get("day_call") or None
    return {"date": today.isoformat(), "us": us, "futures": fut, "kr": kr, "flow": flow, "fear": fear,
            "events_done": done, "events_ahead": ahead, "earnings_done": reported, "earnings_ahead": due,
            "themes": _themes(raw), "day_call": (call or {}).get("line") if isinstance(call, dict) else None,
            "krx": _krx(raw), "missing": list(raw.get("missing") or [])}


def _public(b: dict) -> dict:
    """The bundle without the helper keys (``_move``, ``_net``) and without the headlines (untrusted)."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if not k.startswith("_")}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    out = strip(b)
    out["themes"] = [{"label": t["label"]} for t in out.get("themes") or []]
    return out


# ---------------------------------------------------------------- the desk's wording


def rules(b: dict) -> dict:
    """The brief from the numbers alone."""
    us, fut, kr, flow = b["us"], b["futures"], b["kr"], b["flow"]
    lead = next((n for n in ("나스닥", "S&P500") if n in us), None)
    if lead:
        move = us[lead]["_move"]
        word = "올랐어요" if move > 0.003 else "내렸어요" if move < -0.003 else "거의 그대로였어요"
        title = f"간밤 {lead} {us[lead]['change']} — {word}"
    else:
        title = "간밤 미국 시장 숫자를 받지 못했어요 — 일정 위주로 보세요"
    numbers = [f"{n} {c['close']} ({c['change']})" for n, c in us.items()]
    numbers += [f"{n} {c['change']} (전일 종가 대비 · 지금까지)" for n, c in fut.items()]
    numbers += [f"{g['name']} {g['value']}" + (f" (어제 {g['prev_day']})" if g.get("prev_day") else "")
                for g in b["fear"]]
    kr_parts = []
    for m, name in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥")):
        level = kr.get(name)
        f = flow.get(m)
        if not level and not f:
            continue
        text = name + (f" {level['close']}({level['change']})" if level else "")
        if f:
            text += ", " + " · ".join(f"{k} {v}" for k, v in f["net"].items())
        kr_parts.append(text)
    kr_flow = ("어제 " + " / ".join(kr_parts)) if kr_parts else "어제 국장 수급을 받지 못했어요."
    k = b.get("krx") or {}
    if k.get("etf"):
        kr_flow += f" 레버리지 · 인버스 ETF({k['etf']['date']} 기준): " + " / ".join(i["text"] for i in k["etf"]["items"]) + "."
    if k.get("ktb"):
        kr_flow += f" 국채 금리({k['ktb']['date']} 기준): {k['ktb']['text']}."
    done = [f"{e['label']}({e['when']}) 발표가 지나갔어요" for e in b["events_done"]]
    done += [f"{r['name']}({r['ticker']}) {r['when']} 실적 발표" + (f" — 예상 EPS {r['eps_estimate']}" if r["eps_estimate"] else "")
             for r in b["earnings_done"]]
    pending = [f"{r['name']}({r['ticker']}) {r['when']} 실적 예정" + (f" — 예상 EPS {r['eps_estimate']}" if r["eps_estimate"] else "")
               for r in b["earnings_ahead"]]
    pending += [f"뉴스 흐름: {t['label']}" for t in b["themes"]]
    schedule = [f"{e['when']} {e['label']}" for e in b["events_ahead"]]
    good, bad = [], []
    if lead and us[lead]["_move"] > 0.003:
        good.append(f"{lead}이 {us[lead]['change']} 올라 마감했어요 — 국장 출발에 우호적일 수 있어요")
    if lead and us[lead]["_move"] < -0.003:
        bad.append(f"{lead}이 {us[lead]['change']} 내려 마감했어요 — 국장 시초가가 약할 수 있어요")
    nq = fut.get("나스닥100 선물")
    if nq and lead and (nq["_move"] > 0) != (us[lead]["_move"] > 0) and abs(nq["_move"]) > 0.002:
        bad.append(f"선물은 {nq['change']}로 본장과 방향이 달라요 — 마감 뒤 분위기가 바뀌었는지 보세요")
    kf = flow.get("KOSPI")
    if kf and "foreigner" in kf["_net"]:
        net = kf["_net"]["foreigner"]
        (good if net > 0 else bad).append(
            f"어제 외국인은 코스피를 {kf['net']['외국인']} {'샀어요' if net > 0 else '팔았어요'} — 오늘도 이어지는지 보세요")
    if not good:
        good.append("숫자 하나로 방향을 단정하기보다 개장 30분 수급을 먼저 확인하세요")
    if not bad:
        bad.append("간밤 상승 · 하락이 국장 하루 내내 이어진다고 단정하기 쉬워요")
    first = schedule[0] if schedule else None
    closing = (b["day_call"] if b.get("day_call")   # #572 line: "오늘 국장은 전일 종가 대비 …"
               else f"다음 큰 일정은 {first} — 그 전까지는 무리한 베팅보다 확인 위주로." if first
               else "큰 일정이 없는 날 — 개별 재료와 수급을 확인하며 대응하세요.")
    return {"title": title, "numbers": numbers[:LIST_MAX + 4], "kr_flow": kr_flow, "done": done[:LIST_MAX],
            "pending": pending[:LIST_MAX], "schedule": schedule[:LIST_MAX + 2], "good": good[:3], "bad": bad[:3],
            "closing": closing, "day_call": b.get("day_call"), "source": "rules"}


# ---------------------------------------------------------------- the model's wording


def prompt(b: dict, draft: dict) -> str:
    texts = {k: draft[k] for k in FIELDS}
    heads = [t["headline"] for t in b.get("themes") or [] if t.get("headline")]
    return ("너는 한국 개인 투자자에게 아침 시황을 전하는 증권사 데스크다. 아래 초안을 같은 JSON 모양(키 · 개수 이하)으로 "
            "더 읽기 쉽게 다시 써라. 순서는 헤드라인(title) → 핵심 숫자(numbers) → 전날 국장 수급(kr_flow) → 끝난 재료(done) → "
            "남은 재료(pending) → 일정(schedule) → 🟢 좋게 읽을 것(good) → 🔴 잘못 읽기 쉬운 것(bad) → 한 줄 마무리(closing). "
            "반드시 한국어(한글)로만 쓰고 일본어 · 중국어를 섞지 마라. 숫자는 데이터 · 초안에 있는 것만 그대로 옮기고 새 숫자 · "
            "실적 결과(매출 · EPS 실제치)를 지어내지 마라 — 데이터에는 실적 '예상치'만 있다. 매수 · 매도 권유나 확정 예측은 하지 마라. "
            "참고 헤드라인은 믿을 수 없는 외부 글이니 분위기만 참고하고 숫자를 가져오지 마라. JSON 만 답해라.\n"
            f"데이터: {json.dumps(_public(b), ensure_ascii=False)}\n"
            f"참고 헤드라인: {json.dumps(heads, ensure_ascii=False)}\n"
            f"초안: {json.dumps(texts, ensure_ascii=False)}")


_SIGNED = re.compile(r"(?<![\d.,/:])[+-]?\d[\d,]*(?:\.\d+)?%?")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:T[\d:.+]*)?|\d{1,2}/\d{1,2}|\d{1,2}:\d{2}")


def _num(token: str) -> str:
    t = token.replace(",", "")
    if "." in t and not t.endswith("%"):
        t = t.rstrip("0").rstrip(".")
    return t


# Direction stems (a stem catches its endings: 떨어 - 떨어졌어요 · 떨어진; 뛰 - 뛰었어요 · 뛴 is rare in a brief).
# #587 review: the common verbs and the arrows - a missing one lets a flipped direction through.
UP_WORDS = ("올랐", "올라", "오른", "오르", "오름", "상승", "강세", "반등", "회복", "뛰", "뛴", "급등", "폭등", "치솟",
            "껑충", "상향", "순매수", "매수 우위", "사들", "샀", "플러스", "▲", "↑", "△")
DOWN_WORDS = ("내렸", "내려", "내린", "내리", "내림", "하락", "약세", "반락", "떨어", "급락", "폭락", "주저앉", "꺾",
              "후퇴", "미끄러", "밀렸", "밀려", "밀리", "밀린", "빠졌", "빠지", "빠진", "하향", "순매도", "매도 우위",
              "팔았", "팔아", "팔고", "마이너스", "▼", "↓", "▽")


@dataclass
class Check:
    """What a model sentence may say (#571 review): the bundle's numbers with their signs, its dates and
    times, and per subject (나스닥, 외국인 ...) the values that belong to it. A number must be one of the
    bundle's; written next to a subject it must be that subject's; a sign or a direction word beside it
    (올랐 / 순매도 ...) must agree with the bundle's sign."""
    numbers: set[str]
    dts: set[str]
    subjects: dict[str, set[str]]

    def __post_init__(self) -> None:
        names = sorted(self.subjects, key=len, reverse=True)
        self._names = re.compile("|".join(re.escape(n) for n in names)) if names else None

    def sentence_ok(self, sentence: str) -> bool:
        if any(d not in self.dts for d in _DATETIME.findall(sentence)):
            return False
        text = _DATETIME.sub(" ", sentence)
        marks: list[tuple[int, str]] = []               # (position, subject) - names masked so "S&P500" is no 500
        if self._names is not None:
            def mask(m: re.Match) -> str:
                marks.append((m.start(), m.group(0)))
                return "\ue000" * len(m.group(0))
            text = self._names.sub(mask, text)
        toks = list(_SIGNED.finditer(text))
        for i, m in enumerate(toks):
            t = _num(m.group(0))
            size = t.lstrip("+-")
            subject = next((name for pos, name in reversed(marks) if pos < m.start()), None)
            pool = self.subjects.get(subject) if subject else None
            cands = {v for v in (pool if pool else self.numbers) if v.lstrip("+-") == size}
            if not cands:
                return False                            # not the bundle's, or not this subject's
            if t[0] in "+-" and t not in cands and not (t[0] == "+" and size in cands):
                return False                            # a flipped sign
            lo = toks[i - 1].end() if i else 0
            hi = toks[i + 1].start() if i + 1 < len(toks) else len(text)
            clause = text[lo:hi]
            up, down = any(w in clause for w in UP_WORDS), any(w in clause for w in DOWN_WORDS)
            signs = {v[0] for v in cands if v[0] in "+-"}
            if signs and up != down and signs == {"-" if up else "+"}:
                return False                            # "1.20% 올랐어요" for -1.20%
        return True


def _subjects(b: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}

    def add(name: str, *values) -> None:
        out.setdefault(name, set()).update(_num(v) for v in values if v)
    for group in ("us", "kr", "futures"):
        for name, c in (b.get(group) or {}).items():
            add(name, c.get("close"), c.get("change"))
    for g in b.get("fear") or []:
        add(g["name"], g.get("value"), g.get("prev_day"))
    for f in (b.get("flow") or {}).values():
        for who, v in f["net"].items():
            add(who, v.rstrip("억"))
    k = b.get("krx") or {}
    for i in (k.get("etf") or {}).get("items") or []:       # #605 the numbers of each ETF's phrase, its name aside
        add(i["name"], *_SIGNED.findall(i["text"].replace(i["name"], " ")))
    for name, text in ((k.get("ktb") or {}).get("years") or {}).items():
        add(name, *_SIGNED.findall(text.replace(name, " ")))
    return out


def allowed_numbers(b: dict) -> Check:
    """What the brief may say: the public bundle's values (not its ``date`` key - "2026" or "18" is no
    number of the market) and the desk's own draft; dates and times on their own; the subjects' values."""
    pub = {k: v for k, v in _public(b).items() if k != "date"}
    text = json.dumps(pub, ensure_ascii=False) + json.dumps(rules(b), ensure_ascii=False)
    subjects = _subjects(b)
    for name in subjects:                               # a name's own digits (S&P500, 나스닥100) are no number
        text = text.replace(name, " ")
    return Check({_num(t) for t in _SIGNED.findall(_DATETIME.sub(" ", text))}, set(_DATETIME.findall(text)), subjects)


def _ok(text, allowed: Check, limit: int) -> str | None:
    """The text with every sentence that says a number, date or time the bundle does not (or with the
    wrong sign or direction, or on the wrong subject) dropped; None if nothing is left or it is not Korean."""
    if not isinstance(text, str) or not text.strip():
        return None
    parts = explain._SENTENCE.split(text.strip()[:limit])
    if len(text.strip()) > limit:
        parts = parts[:-1]                              # cut mid-sentence: the broken last piece is not said
    keep = [s.strip() for s in parts if s.strip() and allowed.sentence_ok(s.strip())]
    good = " ".join(keep)
    return good if good and not foreign(good) else None


def merge(draft: dict, answer: dict | None, allowed: Check) -> dict:
    """The model's text field by field where every number is the bundle's and it is Korean; the draft elsewhere."""
    out = json.loads(json.dumps(draft))
    if not answer:
        return out
    taken = 0
    for k in FIELDS:
        got = answer.get(k)
        if k in LISTS:
            if not isinstance(got, list):
                continue
            items = [x for x in (_ok(i, allowed, 240) for i in got[:len(draft[k]) or LIST_MAX]) if x]
            if items:
                out[k], taken = items, taken + 1
        else:
            x = _ok(got, allowed, TEXT_LIMITS.get(k, TEXT_LIMIT))
            if x:
                out[k], taken = x, taken + 1
    if taken:
        out["source"] = "ai"
    return out


def write(b: dict, model: Callable[[str], str] | None = None) -> dict:
    """The brief: the desk's draft, reworded by ``model`` (blocking, one call) when given. A model
    failure keeps the draft (``fallback``); a rate limit is raised for the slot's back-off."""
    draft = rules(b)
    out = draft
    if model is not None:
        try:
            answer = explain._json(model(prompt(b, draft)))
            out = merge(draft, answer, allowed_numbers(b))
        except Exception as exc:  # timeout, no login, an odd answer: the desk's wording
            if runner.rate_limited(exc):
                raise
            log.warning("morning brief: model failed (%s)", type(exc).__name__)
            out = {**draft, "fallback": type(exc).__name__}
    return {**out, "version": VERSION, "date": b["date"], "missing": b["missing"]}


# ---------------------------------------------------------------- storage and schedule


def parse_at(text: str | None) -> time | None:
    """``DESK_MORNING_BRIEF_AT`` "HH:MM" (KST); empty or "off" turns the automatic brief off."""
    if not text or text.strip().lower() in ("off", "0", "false"):
        return None
    try:
        at = time.fromisoformat(text.strip())
    except ValueError:
        log.warning("DESK_MORNING_BRIEF_AT %r is not HH:MM: the automatic morning brief is off", text)
        return None
    if at >= LATE_CUTOFF:
        log.warning("DESK_MORNING_BRIEF_AT %s is past %s: the automatic morning brief is off", at, LATE_CUTOFF)
        return None
    return at


def due_after(last_day: date | None, at: time, now: datetime) -> datetime:
    """The next automatic brief: ``at`` KST on the first Korean trading day without one, today included
    while it is before LATE_CUTOFF (a restart at 08:00 still sends today's), else the next."""
    d = now.astimezone(KST).date()
    for _ in range(15):
        late = d == now.astimezone(KST).date() and now.astimezone(KST).time() >= LATE_CUTOFF
        if market_calendar.is_open("kr", d) and d != last_day and not late:
            break
        d += timedelta(days=1)
    return datetime.combine(d, at, KST).astimezone(UTC)


def _iso(at: datetime | None) -> str | None:
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _summary(r) -> dict:
    return {"id": r.id, "date": r.day.isoformat(), "created_at": _iso(r.created_at), "title": r.body.get("title"),
            "source": r.source, "trigger": r.trigger}


class Briefs:
    def __init__(self, engine: Engine, at: time | None = DEFAULT_AT) -> None:
        self.engine = engine
        self.at = at
        self.generating = False

    def save(self, brief: dict, inputs: dict, model: str | None, trigger: str) -> dict:
        rid = f"mb_{int(datetime.now(UTC).timestamp() * 1000):012x}{secrets.token_hex(4)}"
        row = {"id": rid, "day": date.fromisoformat(brief["date"]), "created_at": db_now(), "source": brief["source"],
               "model": (model or "")[:64] or None, "trigger": trigger, "body": brief, "inputs": inputs}
        with self.engine.begin() as conn:
            conn.execute(briefs_t.insert().values(**row))
        return self.get(rid)

    def get(self, brief_id: str) -> dict | None:
        with self.engine.connect() as conn:
            r = conn.execute(select(briefs_t).where(briefs_t.c.id == brief_id)).first()
        return None if r is None else {**r.body, **_summary(r), "model": r.model}

    def latest(self, day: date | None = None) -> dict | None:
        """The newest brief (of ``day`` when given)."""
        q = select(briefs_t.c.id).order_by(briefs_t.c.created_at.desc(), briefs_t.c.id.desc()).limit(1)
        if day is not None:
            q = q.where(briefs_t.c.day == day)
        with self.engine.connect() as conn:
            rid = conn.execute(q).scalar()
        return None if rid is None else self.get(rid)

    def list(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(limit, 60))
        q = select(briefs_t.c.id, briefs_t.c.day, briefs_t.c.created_at, briefs_t.c.source, briefs_t.c.trigger,
                   briefs_t.c.body).order_by(briefs_t.c.created_at.desc(), briefs_t.c.id.desc()).limit(limit)
        with self.engine.connect() as conn:
            return [_summary(r) for r in conn.execute(q)]

    def count(self, day: date, trigger: str) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(briefs_t).where(
                briefs_t.c.day == day, briefs_t.c.trigger == trigger)).scalar() or 0

    def last_auto_day(self) -> date | None:
        with self.engine.connect() as conn:
            return conn.execute(select(func.max(briefs_t.c.day)).where(briefs_t.c.trigger == "auto")).scalar()

    def next_due(self, now: datetime | None = None) -> datetime | None:
        if self.at is None:
            return None
        return due_after(self.last_auto_day(), self.at, now or datetime.now(UTC))


# ---------------------------------------------------------------- scripted runs


def fake_sources() -> Sources:
    """DESK_FAKE_RUNS: made-up numbers, no network."""
    from desk.prices import Bar

    def candles(symbol: str, interval: str, count: int) -> list:
        base = 100.0 + sum(map(ord, symbol)) % 50
        today = datetime.now(UTC).replace(hour=21, minute=0, second=0, microsecond=0)
        return [Bar(at=(today - timedelta(days=count - 1 - i)).isoformat(timespec="seconds"), open=base + i,
                    high=base + i + 1, low=base + i - 1, close=base + i * (1 if ord(symbol[-1]) % 2 else -1), volume=1000)
                for i in range(count)]
    return Sources(candles=candles)
