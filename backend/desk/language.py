"""#567 Korean runs that came back in Japanese or Chinese: a firmer instruction going in, and on the
way out a check of the text the owner reads (the decision, the leverage analyst's call, the closing
summary) with a short translation of only the lines that are not Korean.

The labelled lines are the program's (``**Rating**: Hold``, ``Entry Price: 7.31``): a translated line
must keep every number, English label and rating word it had, and the decision must parse to the same
plan - otherwise the original stays and the run is marked ``language_mismatch``. Live chat turns are
only logged (``StateDiffer``); they are not translated.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

TRANSLATE_TIMEOUT_SEC = 60.0
BUDGET_SEC = 120.0        # no new translation call starts after this long (one fix: a run's fields together)
BATCH_CHARS = 6000        # one translation call carries at most this much text
MAX_CHARS = 30000         # more foreign text than this is not translated (marked instead)
MIN_LETTERS = 8           # fewer CJK letters than this cannot be told apart

_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣]")
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")

NAMES = {"ja": "일본어", "zh": "중국어"}

# translate(lines, language) -> the same number of lines in that language
Translate = Callable[[list[str], str], list[str]]


def korean(language: str | None) -> bool:
    lang = (language or "").strip().lower()
    return lang.startswith(("korean", "한국어")) or lang in ("ko", "ko-kr")


def target(language: str | None) -> str:
    """The run's output language: the request's, else the TRADINGAGENTS_OUTPUT_LANGUAGE setting."""
    return language or os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE") or "English"


KOREAN_NAME = ("Korean (한국어: 한글로만 쓴다. 일본어 · 중국어 문장은 쓰지 않는다. 종목명 · 지표명 · 숫자와 영어 라벨 줄은 "
               "그대로 둔다 - Hangul only, never Japanese or Chinese; keep tickers, indicator names, numbers and the "
               "English labelled lines as they are)")


def instruction_name(language: str | None) -> str | None:
    """The output_language handed to TradingAgents: its prompt says "Write your entire response in
    <this>", so a Korean run gets the rule spelled out in Korean and English inside the name."""
    return KOREAN_NAME if korean(language) else language


def directive(language: str | None) -> str:
    """One prompt line for the desk's own model calls (closing summary, leverage analyst)."""
    if not korean(language):
        return f"Write the text in {language or 'English'}."
    return ("Write the text in Korean (Hangul) only - never Japanese or Chinese; keep tickers, indicator names "
            "and numbers as they are. 글은 한국어(한글)로만 쓴다 - 일본어 · 중국어 금지, 종목명 · 지표명 · 숫자는 그대로.")


def _counts(text: str) -> tuple[int, int, int]:
    return len(_HANGUL.findall(text)), len(_KANA.findall(text)), len(_HAN.findall(text))


def _whole(text: str) -> str | None:
    hangul, kana, han = _counts(text)
    letters = hangul + kana + han
    if letters < MIN_LETTERS:
        return None
    if kana >= 5 and kana >= 0.1 * letters:
        return "ja"
    if han >= MIN_LETTERS and han >= 0.5 * letters:
        return "zh"
    return None


def _strong(line: str) -> str | None:
    """One line that is plainly Japanese or Chinese, whatever the text around it is."""
    hangul, kana, han = _counts(line)
    letters = hangul + kana + han
    if kana >= 5 and kana >= 0.3 * letters:
        return "ja"
    if han >= MIN_LETTERS and han >= 0.7 * letters:
        return "zh"
    return None


def foreign(text: str | None) -> str | None:
    """'ja' when the text is Japanese (kana), 'zh' when it is Chinese (Han characters without Hangul
    or kana) - as a whole, or in any one line (a Korean report with one Japanese summary line);
    None for Korean, English, a Korean text with a quoted name or a hanja word, or too little to tell."""
    text = text or ""
    if kind := _whole(text):
        return kind
    return next((kind for line in text.split("\n") if (kind := _strong(line))), None)


def _foreign_line(line: str) -> bool:
    """A line to translate once its text is foreign: any kana, or Han characters without Hangul."""
    hangul, kana, han = _counts(line)
    letters = hangul + kana + han
    return bool(letters) and ((kana and kana >= 0.1 * letters) or (han >= 2 and not hangul))


_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# ASCII-letter edges, not \b: in "Holdで" the kana is a word character too
_LABEL = re.compile(r"\*\*[A-Za-z][A-Za-z /&()-]*\*\*\s*:|(?<![A-Za-z])(?:Exit by|[A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)*)\s*:")
_RATING = re.compile(r"(?<![A-Za-z])(?:buy|overweight|hold|underweight|sell)(?![A-Za-z])", re.IGNORECASE)


def _tokens(line: str) -> tuple[list[str], list[str]]:
    """What a translation may not change: the numbers in their order (a holder's exit stays the exit,
    the price now stays the price now), and the English labels and rating words."""
    s = unicodedata.normalize("NFKC", line).replace(",", "")
    words = [*(re.sub(r"\s+", " ", t) for t in _LABEL.findall(s)), *(w.lower() for w in _RATING.findall(s))]
    return _NUMBER.findall(s), sorted(words)


def kept(original: str, translated: str) -> bool:
    return _tokens(original) == _tokens(translated)


def _translate(items: list[str], translate: Translate, deadline: float | None = None) -> list[str] | None:
    """Translated items, in batches; None when any batch fails, comes back malformed or would start
    after the deadline (time.monotonic) - a slow model never keeps calling once the run moved on."""
    if sum(len(s) for s in items) > MAX_CHARS:
        log.warning("language: %d chars of foreign text, not translated", sum(len(s) for s in items))
        return None
    batches: list[list[str]] = []
    for item in items:
        if batches and sum(len(s) for s in batches[-1]) + len(item) <= BATCH_CHARS:
            batches[-1].append(item)
        else:
            batches.append([item])
    out: list[str] = []
    for batch in batches:
        if deadline is not None and time.monotonic() > deadline:
            log.warning("language: out of time, %d batch(es) not translated", len(batches))
            return None
        try:
            got = translate(batch, "Korean")
        except Exception as exc:  # noqa: BLE001 - the original stays, marked
            log.warning("language: translation failed: %s", type(exc).__name__)
            return None
        if not isinstance(got, list) or len(got) != len(batch) or not all(isinstance(s, str) for s in got):
            log.warning("language: translation came back malformed")
            return None
        out += [s.replace("\r", "").replace("\n", " ") for s in got]   # one line in, one line out
    return out


def fix_text(text: str, translate: Translate | None, same: Callable[[str, str], bool] | None = None,
             deadline: float | None = None) -> tuple[str, str | None, str]:
    """(text, kind, status): status ``ok`` (Korean already), ``fixed`` (the foreign lines translated)
    or ``mismatch`` (left as it was). ``same(original, fixed)`` is the caller's check that the program
    reads the fixed text the same way (the plan's levels)."""
    kind = foreign(text)
    if kind is None:
        return text, None, "ok"
    lines = text.split("\n")
    idx = [i for i, line in enumerate(lines) if _foreign_line(line)]
    if deadline is None:
        deadline = time.monotonic() + BUDGET_SEC
    got = _translate([lines[i] for i in idx], translate, deadline) if translate and idx else None
    if got is None:
        return text, kind, "mismatch"
    new = list(lines)
    for i, line in zip(idx, got, strict=True):
        if not kept(lines[i], line) or _strong(line):
            log.warning("language: a translated line lost a number or label, or is still not Korean")
            return text, kind, "mismatch"
        new[i] = line
    fixed = "\n".join(new)
    if foreign(fixed) is not None or (same is not None and not same(text, fixed)):
        return text, kind, "mismatch"
    return fixed, kind, "fixed"


# --- the decision: translated only when it parses to the same plan --------------------

STATE_FIELDS = ("final_trade_decision", "trader_investment_plan", "investment_plan")
# parse_plan fields that are the model's prose - they are meant to change
_PROSE = {"time_horizon", "position_sizing", "executive_summary", "levels_contradiction", "invalidation", "exit_by"}


def _first_word(value: Any) -> Any:
    """The trader's action is prose after its verb ('buy - on the dip'): the verb must stay."""
    m = re.match(r"\W*([A-Za-z]+)", value) if isinstance(value, str) else None
    return m.group(1).lower() if m else value


def _skeleton(value: Any) -> Any:
    """A parsed plan without its prose: numbers, ratings, sides and short English codes."""
    if isinstance(value, dict):
        return {k: _first_word(v) if k == "trader_action" else _skeleton(v)
                for k, v in value.items() if k not in _PROSE}
    if isinstance(value, list):
        return [_skeleton(v) for v in value]
    if isinstance(value, str):
        return value if value.isascii() and len(value) <= 24 else "<text>"
    return value


def same_plan(state: dict, key: str, fixed: str) -> bool:
    from . import playbook
    from .plan import parse_plan

    after = {**state, key: fixed}
    return (_skeleton(parse_plan(state)) == _skeleton(parse_plan(after))
            and _skeleton(playbook.parse(state.get("final_trade_decision") or ""))
            == _skeleton(playbook.parse(after.get("final_trade_decision") or "")))


def fix_state(state: dict, translate: Translate | None) -> tuple[dict, dict]:
    """The final state with its foreign decision fields in Korean, and a report
    ``{"fixed": [field...], "mismatch": [field...], "kind": "ja"|"zh"|None}``."""
    out = dict(state)
    report: dict = {"fixed": [], "mismatch": [], "kind": None}
    deadline = time.monotonic() + BUDGET_SEC   # the fields share one budget
    for key in STATE_FIELDS:
        text = out.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        fixed, kind, status = fix_text(text, translate, lambda a, b, key=key: same_plan(out, key, b), deadline)
        if status == "ok":
            continue
        report["kind"] = report["kind"] or kind
        report[status].append(key)
        out[key] = fixed
    return out, report


def plan_text_same(parse: Callable[[str], dict]) -> Callable[[str, str], bool]:
    """``same`` for a text the caller parses on its own (the leverage analyst's call)."""
    return lambda a, b: _skeleton(parse(a)) == _skeleton(parse(b))


# --- the closing summary (#94): its sentences only -----------------------------------

def _summary_slots(summary: dict) -> list[tuple[Any, Any]]:
    slots: list[tuple[Any, Any]] = [(summary, "conclusion"), (summary, "debate")]
    for side in ("long_case", "short_case"):
        slots += [(summary.get(side), i) for i in range(len(summary.get(side) or []))]
    slots += [(vote, "reason") for vote in summary.get("votes") or [] if isinstance(vote, dict)]
    if isinstance(summary.get("holder"), dict):
        slots.append((summary["holder"], "reason"))
    return [(box, k) for box, k in slots if isinstance(box, (dict, list)) and isinstance(box[k], str)]


def fix_summary(summary: dict, translate: Translate | None) -> tuple[dict, str | None, str]:
    """(summary, kind, status) for the closing summary, like fix_text: only its foreign sentences
    are translated, each keeping its numbers; anything off leaves the whole summary as it was."""
    copy = json.loads(json.dumps(summary))
    slots = _summary_slots(copy)
    kind = foreign("\n".join(box[k] for box, k in slots))   # one sentence per line: each is judged too
    if kind is None:
        return summary, None, "ok"
    todo = [(box, k) for box, k in slots if _foreign_line(box[k])]
    got = _translate([box[k] for box, k in todo], translate, time.monotonic() + BUDGET_SEC) if translate and todo else None
    if got is None:
        return summary, kind, "mismatch"
    for (box, k), line in zip(todo, got, strict=True):
        if not kept(box[k], line) or _strong(line):
            return summary, kind, "mismatch"
        box[k] = line
    if foreign("\n".join(box[k] for box, k in _summary_slots(copy))) is not None:
        return summary, kind, "mismatch"
    return copy, kind, "fixed"


# --- the translation call -------------------------------------------------------------

def build_prompt(lines: list[str], language: str) -> str:
    return "\n".join([
        (f"Translate every item of the JSON array below into {language}. 각 항목을 한국어(한글)로 옮긴다 - "
         "일본어 · 중국어를 남기지 않는다."),
        "Rules:",
        f"- Answer with a JSON array of exactly {len(lines)} strings, in the same order, and nothing else.",
        "- Keep every number, price, percent, date and ticker exactly as written (same digits).",
        ("- Keep English labels such as **Rating**:, **Executive Summary**:, Entry Price:, Win Probability: and "
         "the rating words Buy, Overweight, Hold, Underweight, Sell exactly in English."),
        "- Keep markdown (**, -, #, |) as it is. Do not add, drop or explain anything. 종목명 · 지표명 · 숫자는 그대로.",
        "",
        json.dumps(lines, ensure_ascii=False),
    ])


def parse_lines(text: str, count: int) -> list[str]:
    start = (text or "").find("[")
    if start < 0:
        raise ValueError("no JSON array in the translation")
    data, _ = json.JSONDecoder().raw_decode(text, start)
    if not isinstance(data, list) or len(data) != count or not all(isinstance(s, str) for s in data):
        raise ValueError("the translation is not the same number of strings")
    return data


def llm_translator(lines: list[str], language: str) -> list[str]:
    """The TradingAgents provider and quick model. Blocking: run it in a thread."""
    from tradingagents.default_config import build_default_config
    from tradingagents.llm_clients import create_tier_client

    # one attempt: the SDK's retries would multiply the timeout past the runner's backstop
    llm = create_tier_client(build_default_config(), "quick", timeout=TRANSLATE_TIMEOUT_SEC, max_retries=0).get_llm()
    return parse_lines(llm.invoke(build_prompt(lines, language)).content, len(lines))
