"""#567: a Korean run's text that came back in Japanese or Chinese - judged, translated line by line
with its numbers and labels kept, or left and marked."""

import logging

import pytest

from desk import language, runner
from desk.consensus import ConsensusContext
from desk.consensus import build_prompt as consensus_prompt
from desk.consensus import parse as parse_consensus
from desk.events import StateDiffer
from desk.leverage_analyst import strip_extras
from desk.plan import parse_plan

JA = "最終判断はHoldで、KORUの方向性を急いで決めず、7.31付近では様子見です。"
ZH = "最终判断为观望，KORU 的方向尚不明确，建议在 7.31 附近等待。"
KO = "최종 판단은 Hold, KORU 는 7.31 부근에서 관망합니다. RSI 33.73 으로 과매도 근처, MACD 는 아직 음수."


@pytest.mark.parametrize(("text", "kind"), [
    (JA, "ja"),
    ("RSIは33.73で売られ過ぎ水準に近く、反発の余地がある。", "ja"),
    (ZH, "zh"),
    (KO, None),
    ("삼성전자(005930) 는 ソニー 와 경쟁하며 20일선 위에서 추세를 유지합니다. 거래량은 평균 대비 1.3배.", None),
    ("前年比 실적이 개선되었고 영업이익률이 상승했다. 수주 잔고도 늘었다.", None),
    ("**Rating**: Hold\n**Entry Price**: 7.31 / **Stop Loss**: 6.86", None),
    ("NVDA long above 182.5, stop 171. RSI 61, MACD golden cross.", None),
    ("", None), (None, None),
])
def test_foreign_tells_japanese_and_chinese_from_korean(text, kind):
    assert language.foreign(text) == kind


def test_a_translation_must_keep_numbers_labels_and_rating_words():
    ja = "**Executive Summary**: 最終判断はHoldで、7.31付近で様子見、損切り6.86。"
    assert language.kept(ja, "**Executive Summary**: 최종 판단은 Hold, 7.31 부근 관망, 손절 6.86.")
    assert not language.kept(ja, "**Executive Summary**: 최종 판단은 관망, 7.31 부근 관망, 손절 6.86.")   # Hold
    assert not language.kept(ja, "**Executive Summary**: 최종 판단은 Hold, 7.3 부근 관망, 손절 6.86.")    # 7.31
    assert not language.kept(ja, "**요약**: 최종 판단은 Hold, 7.31 부근 관망, 손절 6.86.")                 # the label
    assert language.kept("目標は１，２３４．５", "목표는 1,234.5")   # full-width digits and separators


def test_the_prompts_spell_out_hangul_only():
    assert language.instruction_name("Korean").startswith("Korean (한국어: 한글로만")
    assert "never Japanese or Chinese" in language.instruction_name("Korean")
    assert language.instruction_name("English") == "English" and language.instruction_name(None) is None
    assert language.korean(language.KOREAN_NAME)   # the strengthened name is still a Korean run
    prompt = consensus_prompt(ConsensusContext("KORU", "EWY", {"final_trade_decision": "x"}, {}, language="Korean"))
    assert "never Japanese or Chinese" in prompt and "일본어 · 중국어 금지" in prompt
    assert "Write the text in English." in consensus_prompt(ConsensusContext("KORU", "EWY", {}, {}))


def test_target_falls_back_to_the_setting(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "Korean")
    assert language.target(None) == "Korean" and language.target("English") == "English"
    monkeypatch.delenv("TRADINGAGENTS_OUTPUT_LANGUAGE")
    assert language.target(None) == "English"


DECISION_JA = (
    "**Rating**: Hold\n\n"
    "**Executive Summary**: 最終判断はHoldで、KORUの方向性を急いで決めず様子見。\n"
    "Entry Price: 7.31 / Stop Loss: 6.86 / Price Target: 7.42\n"
    "Win Probability: 48% / Long Trigger: 7.42 / Short Trigger: 6.86\n\n"
    "**Investment Thesis**: RSIは33.73で売られ過ぎ水準に近い。\n"
    "Checklist: trend=short, location=long, momentum=neutral, volume=neutral, catalyst=neutral, regime=short\n\n"
    "**Time Horizon**: 1-2週間"
)
KO_FOR = {
    "**Executive Summary**: 最終判断はHoldで、KORUの方向性を急いで決めず様子見。":
        "**Executive Summary**: 최종 판단은 Hold, KORU 방향을 서두르지 않고 관망.",
    "**Investment Thesis**: RSIは33.73で売られ過ぎ水準に近い。": "**Investment Thesis**: RSI 33.73 으로 과매도 수준에 가깝다.",
    "**Time Horizon**: 1-2週間": "**Time Horizon**: 1-2주",
}


class Translator:
    """A fake quick model: a fixed table, with what it was asked kept."""

    def __init__(self, table=None, edit=None):
        self.table, self.edit, self.asked = KO_FOR if table is None else table, edit, []

    def __call__(self, lines, lang):
        self.asked.append((list(lines), lang))
        out = [self.table[line] for line in lines]
        return self.edit(out) if self.edit else out


def test_only_the_foreign_lines_are_translated_and_the_plan_reads_the_same():
    t = Translator()
    state = {"final_trade_decision": DECISION_JA, "trader_investment_plan": "**Action**: Hold\n\n**Entry Price**: 7.31"}
    fixed, report = language.fix_state(state, t)
    assert report == {"fixed": ["final_trade_decision"], "mismatch": [], "kind": "ja"}
    assert t.asked == [(list(KO_FOR), "Korean")]   # the labelled English lines are never sent
    assert language.foreign(fixed["final_trade_decision"]) is None
    before, after = parse_plan(state), parse_plan(fixed)
    assert after["executive_summary"].startswith("최종 판단은 Hold")
    for key in ("rating", "bias", "entry_price", "stop_loss", "price_target", "win_probability", "checklist",
                "long_trigger", "short_trigger", "reward_risk"):
        assert after[key] == before[key], key
    assert (after["entry_price"], after["stop_loss"], after["win_probability"]) == (7.31, 6.86, 0.48)
    assert fixed["trader_investment_plan"] == state["trader_investment_plan"]   # English: untouched


@pytest.mark.parametrize("translate", [
    None,
    Translator(edit=lambda out: [line.replace("33.73", "33.7") for line in out]),   # lost a number
    Translator(edit=lambda out: out[:-1]),                                            # one line short
    Translator(edit=lambda out: [next(iter(KO_FOR)), *out[1:]]),                       # still Japanese
    Translator(table={}),                                                             # the call fails
])
def test_a_translation_that_is_off_leaves_the_original_marked(translate):
    state = {"final_trade_decision": DECISION_JA}
    fixed, report = language.fix_state(state, translate)
    assert fixed == state
    assert report == {"fixed": [], "mismatch": ["final_trade_decision"], "kind": "ja"}


def test_one_japanese_line_in_a_long_korean_text_is_caught_and_only_it_is_translated():
    body = "\n".join(f"- {i}번째 근거: 20일선 위에서 거래량이 평균 대비 1.{i}배로 늘었고 외국인 순매수가 이어졌다." for i in range(30))
    text = f"**Rating**: Hold\n\n**Executive Summary**: 最終判断はHoldで、KORUの方向性を急いで決めず様子見。\n{body}"
    assert language.foreign(body) is None and language.foreign(text) == "ja"
    t = Translator()
    fixed, report = language.fix_state({"final_trade_decision": text}, t)
    assert report["fixed"] == ["final_trade_decision"] and len(t.asked[0][0]) == 1
    assert fixed["final_trade_decision"].endswith(body)
    # a Korean report's hanja table row is not a foreign text
    assert language.foreign("## 재무 요약\n이번 분기 실적은 시장 기대를 웃돌았고 이익률도 개선됐다.\n| 항목 | 값 |\n|---|---|\n"
                            "| 前年比 | +12% |\n| 營業利益率 | 31% |") is None


def test_no_translation_call_starts_after_the_budget():
    t = Translator()
    fixed, kind, status = language.fix_text(DECISION_JA, t, deadline=0.0)
    assert (fixed, kind, status) == (DECISION_JA, "ja", "mismatch") and t.asked == []


def test_kept_holds_the_numbers_in_order_and_the_labels_rating_words_in_any_case():
    assert not language.kept("Holder Exit: 55.2 (現在 58.1)", "Holder Exit: 58.1 (현재 55.2)")
    assert not language.kept("**Action**: buy — 押し目で", "**Action**: 매수 — 눌림에서")
    assert not language.kept("Exit by: 金曜の引けまで", "청산 기한: 금요일 마감까지")
    assert language.kept("Exit by: 金曜の引けまで", "Exit by: 금요일 마감까지")


def test_the_trader_action_verb_must_stay():
    state = {"trader_investment_plan": "**Action**: buy — 押し目で分割\n\n**Entry Price**: 7.31"}
    assert language.same_plan(state, "trader_investment_plan", "**Action**: buy — 눌림에서 분할\n\n**Entry Price**: 7.31")
    assert not language.same_plan(state, "trader_investment_plan", "**Action**: 매수 — 눌림에서\n\n**Entry Price**: 7.31")


def test_a_translation_that_moves_a_level_is_rejected():
    state = {"final_trade_decision": "**Rating**: Hold\n\n**Executive Summary**: 7.31で買い、6.86で損切り。"}
    # the numbers are all there, but now one is labelled - the plan would read an entry the model never set
    moved = "**Rating**: Hold\n\n**Executive Summary**: Entry Price: 7.31 매수, 6.86 손절."
    assert not language.same_plan(state, "final_trade_decision", moved)
    assert language.same_plan(state, "final_trade_decision",
                              "**Rating**: Hold\n\n**Executive Summary**: 7.31 에서 매수, 6.86 에서 손절.")


def test_a_leverage_call_is_checked_the_way_the_runner_reads_it():
    text = ("**Rating**: Buy\n\n**Entry Price**: 30.1\n\n**Stop Loss**: 28.2\n\n**Price Target**: 33.6\n\n"
            "**Executive Summary**: 本主の判断を3倍に移した。保有は短く。\n\nInverse Pair: none")
    same = language.plan_text_same(lambda t: parse_plan(
        {"final_trade_decision": strip_extras(t), "trader_investment_plan": strip_extras(t)}))
    table = {"**Executive Summary**: 本主の判断を3倍に移した。保有は短く。":
             "**Executive Summary**: 본주 판단을 3배로 옮겼다. 보유는 짧게."}
    fixed, kind, status = language.fix_text(text, Translator(table), same)
    assert (kind, status) == ("ja", "fixed") and "본주 판단을 3배로" in fixed
    assert parse_plan({"final_trade_decision": fixed})["entry_price"] == 30.1
    assert language.fix_text("**Rating**: Buy\n\n**Executive Summary**: 본주를 따른다.", None) == (
        "**Rating**: Buy\n\n**Executive Summary**: 본주를 따른다.", None, "ok")


STATE = {"investment_plan": "**Recommendation**: Hold", "final_trade_decision": "**Rating**: Hold"}


def _summary(conclusion=JA, long_case=("RSIは33.73で売られ過ぎに近い", "20일선 회복 시도"), reason="様子見が妥当"):
    import json

    return parse_consensus(json.dumps({
        "votes": [{"agent": "portfolio_manager", "stance": "hold", "reason": reason}],
        "long_case": list(long_case), "short_case": ["거래량 부족"], "debate": "보수형 의견으로 정리했다.",
        "conclusion": conclusion, "holder": {"action": "hold", "cut_level": None, "trim_level": None,
                                             "reason": "7.31 위라면 보유"},
    }, ensure_ascii=False), STATE, "EWY")


def test_the_closing_summary_has_only_its_foreign_sentences_translated():
    table = {JA: "최종 판단은 Hold, KORU 방향을 서두르지 않고 7.31 부근에서 관망합니다.",
             "RSIは33.73で売られ過ぎに近い": "RSI 33.73 으로 과매도에 가깝다", "様子見が妥当": "관망이 타당"}
    t = Translator(table)
    summary = _summary()
    fixed, kind, status = language.fix_summary(summary, t)
    assert (kind, status) == ("ja", "fixed")
    assert t.asked == [([JA, "RSIは33.73で売られ過ぎに近い", "様子見が妥当"], "Korean")]
    assert fixed["conclusion"].startswith("최종 판단은 Hold") and fixed["long_case"][1] == "20일선 회복 시도"
    assert fixed["votes"][-1]["reason"] == "관망이 타당" and fixed["votes"][-1]["stance"] == "hold"
    assert fixed["tally"] == summary["tally"] and summary["conclusion"] == JA   # the input is not changed
    assert language.fix_summary(_summary(conclusion=KO, long_case=["반등"], reason="관망"), None)[2] == "ok"
    left, _, status = language.fix_summary(summary, Translator(table, edit=lambda out: out[:1]))
    assert status == "mismatch" and left is summary


def test_prompt_and_parse_of_the_translation_call():
    prompt = language.build_prompt([JA, "**Time Horizon**: 1-2週間"], "Korean")
    assert "exactly 2 strings" in prompt and "Buy, Overweight, Hold, Underweight, Sell" in prompt and JA in prompt
    assert language.parse_lines('여기 있습니다: ["가", "나"] 끝', 2) == ["가", "나"]
    for bad in ("없음", '["가"]', '{"a": 1}', '[1, 2]'):
        with pytest.raises(ValueError):
            language.parse_lines(bad, 2)


def test_a_japanese_chat_turn_in_a_korean_run_is_logged(caplog):
    differ = StateDiffer(language.KOREAN_NAME)
    with caplog.at_level(logging.WARNING, logger="desk.events"):
        events = differ.feed([], {"market_report": JA, "news_report": KO})
    assert [e["content"] for e in events] == [JA, KO]   # the turn itself is shown as written
    assert [r.getMessage() for r in caplog.records] == ["language: market_analyst wrote ja in a Korean run"]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="desk.events"):
        StateDiffer("English").feed([], {"market_report": JA})
    assert not caplog.records


# --- through a run --------------------------------------------------------------------------------

_KOREAN_DECISION = runner._fake_decision


def _japanese_decision(request):
    text = _KOREAN_DECISION(request)["final_trade_decision"]
    for ko, ja in (("182.5 부근 분할 진입, 171 손절, 목표 205.", "182.5付近で分割エントリー、171で損切り、目標205。"),
                   ("수주 모멘텀과 이익 성장, 밸류 부담은 비중으로 관리.", "受注モメンタムと利益成長、バリュエーション負担はウェイトで管理。"),
                   ("2-4주", "2-4週間")):
        assert ko in text
        text = text.replace(ko, ja)
    return {"final_trade_decision": text, "final_rating": "Overweight"}


RUN_KO_FOR = {
    "**Executive Summary**: 182.5付近で分割エントリー、171で損切り、目標205。": "**Executive Summary**: 182.5 부근 분할 진입, 171 손절, 목표 205.",
    "**Investment Thesis**: 受注モメンタムと利益成長、バリュエーション負担はウェイトで管理。":
        "**Investment Thesis**: 수주 모멘텀과 이익 성장, 밸류 부담은 비중으로 관리.",
    "**Time Horizon**: 2-4週間": "**Time Horizon**: 2-4주",
}


def _runs(db_url, monkeypatch, translator):
    """A Korean run, then the same run whose decision came back Japanese - one app, one database."""
    from tests.test_api import _client, _wait_done

    def one(client):
        rid = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03",
                                             "output_language": "Korean"}).json()["id"]
        run = _wait_done(client, rid)
        assert run["status"] == "done", run.get("error")
        return run, client.get(f"/api/runs/{rid}/events").json()

    with _client(db_url) as client:
        client.app.state.manager.translator = translator
        korean, _ = one(client)
        monkeypatch.setattr(runner, "_fake_decision", _japanese_decision)
        return korean, *one(client)


LEVEL_KEYS = ("rating", "bias", "entry_price", "stop_loss", "price_target", "win_probability", "checklist",
              "long_trigger", "short_trigger", "reward_risk")


def test_a_run_whose_decision_came_back_japanese_shows_it_in_korean(db_url, monkeypatch):
    korean, fixed, events = _runs(db_url, monkeypatch, Translator(RUN_KO_FOR))
    for key in LEVEL_KEYS:
        assert fixed["plan"][key] == korean["plan"][key], key
    assert fixed["plan"]["executive_summary"] == korean["plan"]["executive_summary"]
    assert fixed["plan"]["time_horizon"] == "2-4주"
    assert fixed["plan"]["language"] == {"fixed": ["decision"], "mismatch": []}
    assert "language_mismatch" not in fixed["plan"] and "language" not in korean["plan"]
    line = [e for e in events if (e.get("data") or {}).get("code") == "language_fixed"]
    assert len(line) == 1 and line[0]["content"].startswith("최종 결정 글이 일본어로 나와 한국어로 옮겼습니다")
    # the chat keeps the portfolio manager's turn as it was said
    said = [e["content"] for e in events if e["type"] == "message" and e["agent"] == "portfolio_manager"]
    assert "分割エントリー" in said[-1]


def test_a_run_whose_translation_fails_keeps_the_text_and_says_so(db_url, monkeypatch):
    korean, run, events = _runs(db_url, monkeypatch, None)
    plan = run["plan"]
    assert plan["language_mismatch"] == ["decision"] and plan["language"]["fixed"] == []
    assert "分割エントリー" in plan["executive_summary"] and plan["entry_price"] == korean["plan"]["entry_price"]
    line = [e for e in events if (e.get("data") or {}).get("code") == "language_mismatch"]
    assert len(line) == 1 and "원문 언어가 달라 그대로 둡니다" in line[0]["content"]
    assert line[0]["type"] == "status" and line[0]["agent"] == "system"
