"""Which way an alert reads (#641): good news, for reference, caution or a warning - beside its size.

The flow alerts' ``severity`` (info · caution · strong) says how big a move is; a foreign buy turn and a
foreign sell turn are both "caution" by size. ``tone`` says what it means for the index, read on the
main positions (the foreign and the institution - the individual mostly takes the other side, so their
moves are ``neutral``):

- ``positive`` 호재: a buy turn, buying that grows, selling that shrinks, a sell-then-buy opening reversal.
- ``caution`` 주의: selling that grows, buying that shrinks; NQ=F off its session high.
- ``warning`` 경고: a sell turn or a buy-then-sell opening reversal (buying turned to selling), strong
  selling growth, an NQ=F slump.
- #699 a flow warning (``flow_warn``: the individual alone against the main positions' selling, or their
  selling past its floor) is ``caution``, ``warning`` when strong.
- ``neutral`` 참고: the individual, and the other small (``info``) moves. A shrink is always ``info`` by
  size (desk.flow_shift), yet its direction is the news: it reads by its rule, not as small.

It is worked out from what each alert already stores (kind, rule, investor, severity), so the alerts of
any day get it - nothing new is stored. The pushed text starts with the tone's mark and ends with one
line of reading; the channels (and web push) take ``tone_<tone>`` as one more kind, so a channel can turn
a tone off (a kind it never stored counts as on).
"""

from __future__ import annotations

import re

TONES = ("positive", "neutral", "caution", "warning")
MARK = {"positive": "🟢 호재", "neutral": "⚪ 참고", "caution": "🟠 주의", "warning": "🔴 경고"}
KINDS = tuple(f"tone_{t}" for t in TONES)
WHO_KO = {"foreign": "외인", "institution": "기관", "individual": "개인"}

POSITIVE = {"buy_turn", "buy_grow", "sell_shrink", "open_sell_reversal"}
WARNING = {"sell_turn", "open_buy_reversal"}
HOLDING = {"hold_stop": "warning", "hold_near_stop": "caution", "hold_trend_down": "caution",
           "hold_take_profit": "positive"}
HOLDING_READING = {"hold_stop": "손절선을 넘었음 — 미리 정한 대로 정리를 검토(지더라도 작게)",
                   "hold_near_stop": "손절선이 가까움 — 이탈하면 바로 알림",
                   "hold_trend_down": "일봉 하락 추세 — 비중 줄이기를 검토",
                   "hold_take_profit": "박스권 위 끝 — 일부 익절을 검토(박스권은 위에서 팔고 아래에서 버팀)"}
_DOT = re.compile(r"^[🔴🔵]\s*")


def tone(alert: dict) -> str:
    """The tone of an alert in desk.market_alerts' shape (kind, rule, who, severity); a foreign turn of
    desk.flow_alerts reads as kind ``flow_turn`` with its kind as the rule."""
    kind, rule, severity = alert.get("kind"), alert.get("rule"), alert.get("severity")
    if kind == "holding_alert":          # #694 🔴 손절 · 🟠 주의 · 🟢 익절 고려
        return HOLDING.get(rule, "caution")
    if kind == "nq_alert":
        return "caution" if rule == "nq_from_high" and severity != "strong" else "warning"
    if kind == "flow_warn":
        return "warning" if severity == "strong" else "caution"
    if alert.get("who") == "individual":
        return "neutral"
    if rule == "sell_shrink":
        return "positive"
    if rule == "buy_shrink":
        return "caution"
    if severity == "info":
        return "neutral"
    if rule in POSITIVE:
        return "positive"
    if rule in WARNING:
        return "warning"
    if rule == "sell_grow":
        return "warning" if severity == "strong" else "caution"
    return "neutral"


def reading(alert: dict, t: str | None = None) -> str:
    """One line of what it means, e.g. "외인 매수 유입 — 지수에 우호적"."""
    t = t or tone(alert)
    rule, who = alert.get("rule"), WHO_KO.get(alert.get("who") or "", "")
    if alert.get("kind") == "holding_alert":
        return HOLDING_READING.get(alert.get("rule"), "보유 종목 확인")
    if alert.get("kind") == "nq_alert":
        return ("나스닥 선물이 장중 고점에서 밀림 — 미국 기술주 약세 쪽" if t == "caution"
                else "나스닥 선물 급락 — 국장 시초가 · 반도체에 부담")
    if alert.get("kind") == "flow_warn":
        if rule == "retail_alone":
            return "개인 혼자 받는 장 — 외인 · 기관이 빠져나가는 중, 하락 압력 경계"
        if rule == "heavy_sell_combined":
            return "외인 · 기관이 현물 · 선물로 함께 던지는 중 — 하락 압력 경계"
        return f"{who} 매도가 과함 — 하락 압력 경계"
    if t == "neutral":
        return ("개인 흐름 — 지수 방향과 반대로 읽히는 경우가 많아 참고만" if alert.get("who") == "individual"
                else "작은 변화 — 참고만")
    if t == "positive":
        return f"{who} 매도 압력 완화 — 지수에 우호적" if rule == "sell_shrink" else f"{who} 매수 유입 — 지수에 우호적"
    if t == "caution":
        return f"{who} 매수 약해짐 — 지수 탄력이 줄 수 있음" if rule == "buy_shrink" else f"{who} 매도 확대 — 지수에 부담"
    if rule == "sell_grow":
        return f"{who} 강한 매도 — 하락 압력 경계"
    return f"{who} 매수에서 매도로 돌아섬 — 하락 압력 경계"


def headline(alert: dict, text: str) -> str:
    """The pushed text: the tone's mark first (in place of the old red / blue dot) and the reading after."""
    t = tone(alert)
    return f"{MARK[t]} · {_DOT.sub('', text)}\n{reading(alert, t)}"


def kind_of(alert: dict) -> str:
    """The channel / web push kind of the alert's tone."""
    return f"tone_{tone(alert)}"
