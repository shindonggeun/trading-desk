/**
 * 판단 용어 한글 풀이.
 *
 * TradingAgents 는 보고서 본문을 한국어로 쓰지만, 구조화된 항목명(**Action**, **Entry Price** …)과
 * 등급 값(Buy · Overweight · Underweight …)은 영어로 고정한다(백엔드 plan.py 가 그 라벨을 읽는다).
 * 화면에서만 한글로 바꿔 보여 준다 — 원문은 DB 에 그대로 있다.
 */
import type { Bias } from "./types";

export interface Term {
  ko: string;
  desc: string;
}

/** 포트폴리오 매니저의 최종 등급(5단계). desc 는 레버리지 공격형 관점의 행동까지 적는다. */
export const RATING: Record<string, Term> = {
  Buy: { ko: "매수", desc: "상승 판단이 강합니다. 새로 사거나 크게 늘립니다. 공격형이면 레버리지 롱 진입 후보입니다." },
  Overweight: { ko: "비중 확대", desc: "상승 쪽이 우세합니다. 평소보다 더 담되 분할로 들어갑니다." },
  Hold: { ko: "보유 · 관망", desc: "방향이 뚜렷하지 않습니다. 지금 비중을 유지하고 제시된 가격에 닿을 때 움직입니다." },
  Underweight: { ko: "비중 축소", desc: "하락 쪽이 우세합니다. 보유분을 줄입니다. 공격형이면 인버스 · 숏 진입 후보입니다." },
  Sell: { ko: "매도", desc: "하락 판단이 강합니다. 정리하거나 숏 · 인버스로 대응합니다." },
  REVIEW: { ko: "판단 보류", desc: "등급을 읽지 못했습니다. 아래 대화 원문을 확인하세요." },
};

/** 트레이더의 행동(3단계). */
export const ACTION: Record<string, Term> = {
  Buy: { ko: "매수", desc: "롱(상승) 진입" },
  Hold: { ko: "관망", desc: "진입하지 않고 기다림" },
  Sell: { ko: "매도", desc: "정리 또는 숏(하락) 진입" },
};

export const BIAS: Record<Bias, Term> = {
  long: { ko: "롱", desc: "상승에 베팅" },
  short: { ko: "숏 · 인버스", desc: "하락에 베팅 (또는 비중 축소)" },
  flat: { ko: "관망", desc: "방향 없음" },
  review: { ko: "판단 보류", desc: "등급 확인 필요" },
};

export function term(table: Record<string, Term>, value: string | null | undefined): Term | null {
  if (!value) return null;
  const key = Object.keys(table).find((k) => value.trim().toLowerCase().startsWith(k.toLowerCase()));
  return key ? table[key] : null;
}

/** 에이전트 메시지 속 영어 항목명 → 한글. */
const LABEL: Record<string, string> = {
  Action: "행동",
  Reasoning: "근거",
  "Entry Price": "진입가",
  "Stop Loss": "손절가",
  "Position Sizing": "포지션 크기",
  Rating: "등급",
  "Executive Summary": "요약",
  "Investment Thesis": "투자 논리",
  "Price Target": "목표가",
  "Time Horizon": "보유 기간",
  Recommendation: "권고",
  Rationale: "근거",
  "Strategic Actions": "실행 계획",
};
const VALUE_TABLES: Record<string, Record<string, Term>> = { Action: ACTION, Rating: RATING, Recommendation: RATING };

/**
 * "**Action**: Sell" → "**행동**: Sell (매도 · 정리 또는 숏(하락) 진입)".
 * 마지막 "FINAL TRANSACTION PROPOSAL: **SELL**" 줄은 위 행동과 같은 말이라 "최종 제안: 매도" 로 줄인다.
 */
export function koreanizeLabels(markdown: string): string {
  return markdown
    .replace(/\*\*([A-Za-z][A-Za-z ]+?)\*\*(\s*:\s*)([^\n]*)/g, (whole, label: string, sep: string, rest: string) => {
      const ko = LABEL[label.trim()];
      if (!ko) return whole;
      const table = VALUE_TABLES[label.trim()];
      const t = table ? term(table, rest) : null;
      const value = t ? `${rest.trim()} (${t.ko} · ${t.desc})` : rest;
      return `**${ko}**${sep}${value}`;
    })
    .replace(/FINAL TRANSACTION PROPOSAL:\s*\*\*(BUY|HOLD|SELL)\*\*/g, (_w, a: string) => {
      const t = term(ACTION, a.charAt(0) + a.slice(1).toLowerCase());
      return `**최종 제안**: ${t ? t.ko : a}`;
    });
}
