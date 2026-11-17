"""#493: an analysis rewritten for a beginner - a question, numbered sections with a term box each,
the steps to act in, a checklist and a closing line.

The structure and every number come from the desk (the run's plan: summary, levels, ladder, stop,
targets, support / resistance zones, and the fear gauges when the caller has them). ``rules()``
writes the whole explanation from those alone - it is also the fallback. ``build()`` hands the
text fields to the model (the ChatGPT plan the runs use, one call) to say them more plainly; a
sentence of the model's that carries a number the desk did not give is dropped, and a field left
empty keeps the desk's own wording. Not a forecast and not advice: conditions and what to do.

Stored as the run's ``explain`` event (reused; ``?refresh=1`` writes it again).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable

from .projection import _NUM, _norm, fmt

log = logging.getLogger(__name__)

VERSION = 1
MAX_SECTIONS = 5
_SENTENCE = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)\s*|(?<=요\.)\s*|\n+")

RATING_KO = {"Buy": "매수", "Overweight": "비중 확대", "Hold": "관망", "Underweight": "비중 축소", "Sell": "매도"}
TERMS = {
    "rating": "분석이 내린 한 줄 결론이에요. '비중 확대' 는 조금 더 담아도 된다는 뜻, '관망' 은 지금은 지켜보자는 뜻이에요.",
    "support": "주가가 내려오다 여러 번 멈추고 다시 오른 가격대예요. 사려는 사람이 몰려 받쳐 주는 자리라 '지지' 라고 불러요.",
    "resistance": "주가가 올라가다 여러 번 막힌 가격대예요. 팔려는 사람이 몰려 '저항' 이라고 불러요.",
    "stop": "생각이 틀렸다고 보고 손실을 받아들이며 파는 가격이에요. 사기 전에 미리 정해 둬요.",
    "fear": "시장이 얼마나 겁먹었는지 보여 주는 지표예요. 극단적 공포일수록 사람들이 팔고 싶어 해요.",
}


def _f(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def facts(run: dict, fear: list[dict] | None = None) -> dict:
    """The desk's numbers for one finished run (nothing computed here that the desk did not)."""
    plan = run.get("plan") or {}
    s = plan.get("summary") or {}
    price = _f(plan.get("live_price")) or _f(plan.get("last_close"))
    sr = plan.get("support_resistance") or {}
    ref = price or _f(sr.get("reference_price"))

    def zone(lv: dict) -> dict:
        z = lv.get("zone") or {}
        return {"price": _f(lv.get("price")), "low": _f(z.get("low")), "high": _f(z.get("high")),
                "touches": lv.get("touches"), "held": lv.get("held"), "flipped": bool(lv.get("flipped"))}

    levels = [lv for lv in sr.get("levels") or [] if _f(lv.get("price"))]
    supports = sorted((lv for lv in levels if lv.get("kind") == "support" and (ref is None or lv["price"] <= ref)),
                      key=lambda lv: -lv["price"])[:2]
    resistances = sorted((lv for lv in levels if lv.get("kind") == "resistance" and (ref is None or lv["price"] >= ref)),
                         key=lambda lv: lv["price"])[:2]
    ladder = [{"step": st.get("step"), "price": _f(st.get("price")), "when": st.get("when")}
              for st in s.get("ladder") or [] if _f(st.get("price"))][:3]
    targets = [_f(t.get("price")) for t in s.get("targets") or [] if _f(t.get("price"))] or (
        [_f(plan.get("price_target"))] if _f(plan.get("price_target")) else [])
    zone_s = s.get("zone") or {}
    return {
        "ticker": run.get("ticker"), "trade_date": run.get("trade_date"), "price": price,
        "rating": plan.get("rating"), "rating_ko": RATING_KO.get(plan.get("rating") or "", plan.get("rating")),
        "bias": plan.get("bias"), "side": s.get("side"), "verdict": s.get("text"), "why": s.get("why"),
        "entry": _f(plan.get("entry_price")), "stop": _f(s.get("stop")) or _f(plan.get("stop_loss")),
        "stop_pct": s.get("stop_pct"), "targets": targets[:2],
        "zone": {"low": _f(zone_s.get("low")), "high": _f(zone_s.get("high"))} if zone_s else None,
        "ladder": ladder, "valid_until": s.get("valid_until") or plan.get("valid_until"),
        "invalidation": plan.get("invalidation"),
        "supports": [zone(lv) for lv in supports], "resistances": [zone(lv) for lv in resistances],
        "fear": [{"name": x.get("name"), "value": x.get("value"), "band_ko": x.get("band_ko")}
                 for x in fear or [] if x.get("value") is not None],
    }


def _p(x: float | None) -> str:
    return fmt(x) if x else "—"


def _zone_text(z: dict) -> str:
    if z.get("low") and z.get("high"):
        return f"{_p(z['low'])} ~ {_p(z['high'])}"
    return _p(z.get("price"))


def _record(z: dict) -> str:
    if z.get("touches"):
        return f" 지금까지 {z['touches']}번 닿아 {z.get('held') or 0}번 버텼어요."
    return ""


def rules(f: dict) -> dict:
    """The whole explanation from the desk's numbers alone (the fallback, and the model's draft)."""
    t = f.get("ticker") or "이 종목"
    side = f.get("side") or ("long" if f.get("bias") == "long" else "short" if f.get("bias") == "short" else None)
    hook = {"long": f"{t}, 지금 사도 될까요? 어디서 사고 어디서 멈춰야 할까요?",
            "short": f"{t}, 더 내려갈까요? 지금 붙잡으면 안 되는 이유가 있을까요?"}.get(
        side, f"{t}, 지금은 왜 기다리라는 걸까요? 언제 움직이면 될까요?")
    sections = []
    verdict = f.get("verdict") or f"분석 결론은 '{f.get('rating_ko') or '—'}' 이에요."
    sections.append({
        "title": "한 줄 결론", "body": f"분석 결론은 '{f.get('rating_ko') or '—'}' 이에요. {verdict}",
        "highlight": verdict, "term": {"name": "결론(등급)", "explain": TERMS["rating"]},
        "chart": {"kind": "levels", "levels": [x for x in [f.get("entry"), f.get("stop"), *f.get("targets", [])] if x],
                  "marks": [f["price"]] if f.get("price") else []}})
    if f.get("supports"):
        z = f["supports"][0]
        flip = " 예전에는 위를 막던 자리가 이제 아래를 받치는 자리로 바뀌었어요." if z.get("flipped") else ""
        sections.append({
            "title": "받쳐 주는 자리", "body": f"가장 가까운 지지 구역은 {_zone_text(z)} 이에요.{_record(z)}{flip}",
            "highlight": f"{_zone_text(z)} 를 지키는지가 먼저예요.", "term": {"name": "지지", "explain": TERMS["support"]},
            "chart": {"kind": "zone", "levels": [x for x in (z.get("low"), z.get("high")) if x], "marks": []}})
    if f.get("resistances"):
        z = f["resistances"][0]
        sections.append({
            "title": "막히는 자리", "body": f"위로는 {_zone_text(z)} 에서 막힐 수 있어요.{_record(z)}",
            "highlight": f"{_zone_text(z)} 를 넘으면 흐름이 바뀌었다고 볼 수 있어요.",
            "term": {"name": "저항", "explain": TERMS["resistance"]},
            "chart": {"kind": "zone", "levels": [x for x in (z.get("low"), z.get("high")) if x], "marks": []}})
    if f.get("stop"):
        sections.append({
            "title": "틀렸다고 볼 자리", "body": f"{_p(f['stop'])} 아래로 내려가면 이 분석이 틀렸다고 보고 정리하는 자리예요."
                                         + (f" {f['invalidation']}" if f.get("invalidation") else ""),
            "highlight": f"손절 가격 {_p(f['stop'])} 은 사기 전에 정해 두세요.", "term": {"name": "손절", "explain": TERMS["stop"]},
            "chart": {"kind": "levels", "levels": [f["stop"]], "marks": []}})
    if f.get("fear"):
        line = " · ".join(f"{x['name']} {x['value']}({x['band_ko']})" for x in f["fear"][:3])
        sections.append({
            "title": "시장 분위기", "body": f"지금 시장의 공포지수는 {line} 이에요. 분위기가 나쁠 때는 더 작게, 더 천천히 들어가요.",
            "highlight": "분위기가 나쁘면 비중을 줄여요.", "term": {"name": "공포지수", "explain": TERMS["fear"]},
            "chart": {"kind": "none", "levels": [], "marks": []}})
    stages = []
    for i, st in enumerate(f.get("ladder") or [], 1):
        stages.append({"label": f"{i}단계", "low": st["price"], "high": st["price"], "why": st.get("when") or "이 가격에 닿으면 나눠서 담아요."})
    if not stages and f.get("zone") and f["zone"].get("low"):
        stages.append({"label": "1단계", "low": f["zone"]["low"], "high": f["zone"]["high"], "why": "이 구역 안에서 나눠서 담아요."})
    if f.get("stop"):
        stages.append({"label": "최대", "low": f["stop"], "high": f["stop"], "why": "여기 아래로는 더 담지 않고 정리해요."})
    checklist = [{"q": "손절 가격을 정했나요?", "a": f"{_p(f.get('stop'))} 아래면 정리해요." if f.get("stop") else "손절 가격을 먼저 정해요."}]
    if f.get("targets"):
        checklist.append({"q": "어디서 일부 팔까요?", "a": f"첫 목표 {_p(f['targets'][0])} 근처에서 일부를 팔아요."})
    if f.get("supports"):
        checklist.append({"q": "지지 구역을 지키고 있나요?", "a": f"{_zone_text(f['supports'][0])} 를 종가로 지키는지 봐요."})
    if f.get("valid_until"):
        checklist.append({"q": "이 분석은 언제까지 볼까요?", "a": f"{f['valid_until']} 까지예요. 그 뒤에는 다시 분석해요."})
    closing = "예측이 아니라 '이 가격이면 이렇게' 라는 조건과 대응이에요. 정한 가격을 지키는 것이 가장 중요해요."
    return {"version": VERSION, "hook": hook, "sections": sections[:MAX_SECTIONS], "stages": stages,
            "checklist": checklist, "closing": closing, "source": "rules"}


def allowed_numbers(f: dict, draft: dict) -> set[str]:
    """Every number the desk gave (in the facts and in its own draft), normalised; step numbers 1-5."""
    text = json.dumps(f, ensure_ascii=False) + json.dumps(draft, ensure_ascii=False)
    out = {_norm(t) for t in _NUM.findall(text)}
    for x in re.findall(r"\d+(?:\.\d+)?", text):
        try:
            out.add(_norm(fmt(float(x))))
        except ValueError:
            pass
    return out | {str(i) for i in range(1, MAX_SECTIONS + 1)}


def clean(text, allowed: set[str], limit: int = 400) -> str | None:
    """The model's text with every sentence carrying a number the desk did not give dropped."""
    if not isinstance(text, str) or not text.strip():
        return None
    keep = []
    for sentence in _SENTENCE.split(text.strip()[:limit]):
        sentence = sentence.strip()
        if sentence and all(_norm(tok.lstrip("-")) in allowed for tok in _NUM.findall(sentence)):
            keep.append(sentence)
    return " ".join(keep) or None


def prompt(f: dict, draft: dict) -> str:
    texts = {"hook": draft["hook"], "sections": [{"title": s["title"], "body": s["body"], "highlight": s["highlight"],
                                                  "term": s["term"]["explain"]} for s in draft["sections"]],
             "stages": [s["why"] for s in draft["stages"]], "checklist": draft["checklist"], "closing": draft["closing"]}
    return ("너는 주식 초보에게 설명하는 친절한 해설가다. 아래 초안의 문장만 더 쉽고 짧게 다시 써라. "
            "JSON 모양(키 · 순서 · 개수)은 그대로, 값만 바꿔라. 숫자는 초안이나 데이터에 있는 것만 그대로 옮기고 새 숫자를 만들지 마라. "
            "매수 · 매도 권유나 확정 예측은 하지 말고 '~하면 ~할 수 있어요 / 확인하면 돼요' 처럼 조건과 대응으로 써라. "
            "전문 용어는 풀어서 써라. JSON 만 답해라.\n"
            f"데이터: {json.dumps(f, ensure_ascii=False)}\n초안: {json.dumps(texts, ensure_ascii=False)}")


def _json(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def merge(draft: dict, answer: dict | None, allowed: set[str]) -> dict:
    """The model's text field by field where it passes ``clean``; the desk's draft elsewhere."""
    if not answer:
        return draft
    out = json.loads(json.dumps(draft))
    taken = 0

    def pick(new, old):
        nonlocal taken
        good = clean(new, allowed)
        if good:
            taken += 1
            return good
        return old

    out["hook"] = pick(answer.get("hook"), out["hook"])
    out["closing"] = pick(answer.get("closing"), out["closing"])
    for s, a in zip(out["sections"], answer.get("sections") or [], strict=False):
        if isinstance(a, dict):
            s["title"] = pick(a.get("title"), s["title"])
            s["body"] = pick(a.get("body"), s["body"])
            s["highlight"] = pick(a.get("highlight"), s["highlight"])
            s["term"]["explain"] = pick(a.get("term"), s["term"]["explain"])
    for s, a in zip(out["stages"], answer.get("stages") or [], strict=False):
        s["why"] = pick(a, s["why"])
    for c, a in zip(out["checklist"], answer.get("checklist") or [], strict=False):
        if isinstance(a, dict):
            c["q"] = pick(a.get("q"), c["q"])
            c["a"] = pick(a.get("a"), c["a"])
    if taken:
        out["source"] = "ai"
    return out


def build(run: dict, fear: list[dict] | None = None, write: Callable[[str], str] | None = None) -> dict:
    """The explanation for a finished run: the desk's draft, reworded by ``write`` (blocking, one model
    call) when given. Any failure of the model leaves the draft."""
    f = facts(run, fear)
    draft = rules(f)
    if write is None:
        return draft
    try:
        answer = _json(write(prompt(f, draft)))
    except Exception as exc:  # noqa: BLE001 - rate limit, timeout, no login: the desk's own wording
        log.warning("explain for %s: model failed (%s)", run.get("id"), type(exc).__name__)
        return {**draft, "fallback": type(exc).__name__}
    return merge(draft, answer, allowed_numbers(f, draft))
