import type { PlaybookSide } from "./types";

export type PlaybookCondition = PlaybookSide["conditions"][number];

const CONDITION: Record<string, string> = {
  in_zone: "구역 안", rejection_wick: "아래꼬리", rsi_divergence: "RSI 다이버전스", higher_low: "저점 높임", reclaim: "구역 되찾기",
  volume_dry: "거래량 마름", climax_recent: "투매 직후", tape_hh_hl: "체결 고점 · 저점 높임",
  at_resistance: "저항 도달", rejection_bar: "윗꼬리 · 음봉", lower_high: "고점 낮춤", below_vwap: "VWAP 아래",
  below_ma20: "20선 아래", volume_on_break: "이탈 거래량", no_new_entry_late: "장 막판 신규 금지",
  event_clear: "경제 일정 통과",
  vs_oversold: "과매도", vs_depth: "낙폭", vs_base: "바닥 구조", vs_fear: "시장 공포", vs_flows: "수급 · 뉴스",
};

export function conditionLabel(condition: PlaybookCondition): string {
  return CONDITION[condition.id] ?? condition.label ?? condition.id;
}

export function scenarioConfirmationRows(
  { intrabar = [], bar_close = [] }: { intrabar?: PlaybookCondition[]; bar_close?: PlaybookCondition[] },
) {
  return [{ label: "장중 확인", checks: intrabar }, { label: "봉 마감 확인", checks: bar_close }]
    .filter(({ checks }) => checks.length > 0)
    .map(({ label, checks }) => ({
      label,
      text: checks.map((check) => {
        const state = check.met == null ? "관찰" : check.met ? "충족" : "미충족";
        return `${conditionLabel(check)} (${state})`;
      }).join(" · "),
    }));
}
