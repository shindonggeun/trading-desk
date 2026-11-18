import type { GapOdds, GapReport, OpenGap } from "./types";

/**
 * #578 세션 갭 메움 확률(#577 GET /api/gaps)을 화면 글자로. 확률은 과거 빈도다 — "표본 적음"(30 미만)을 늘 함께 보인다.
 */
export const CAUSE_KO: Record<OpenGap["cause"], string> = {
  fomc: "FOMC", macro: "물가 · 고용 지표", earnings: "실적 시즌", weekend: "주말 · 휴장 뒤", other: "기타",
};
export const SIZE_KO: Record<OpenGap["size"], string> = { small: "작은 갭", mid: "중간 갭", large: "큰 갭" };

export function pct(p: number | null | undefined): string {
  return p == null ? "–" : `${Math.round(p * 100)}%`;
}

export function sessions(n: number | null | undefined): string {
  return n == null ? "–" : `${Number.isInteger(n) ? n : n.toFixed(1)}일`;
}

/** 차트 띠 이름표 — "5일 72% · 중앙 1.5일 · 표본 40" (표본 적음이면 붙인다) */
export function oddsLabel(o: GapOdds): string {
  return `5일 ${pct(o.p5)} · 중앙 ${sessions(o.median_sessions)} · 표본 ${o.n}${o.few ? "(적음)" : ""}`;
}

/** 시장 칸 칩 — 메움 가격까지 남은 거리. 레버리지 상품이면 그 상품 기준 */
export function chipText(g: OpenGap): string {
  const move = g.product_move_pct ?? g.to_fill_pct;
  return `갭 ${move > 0 ? "+" : ""}${move.toFixed(1)}% 열림`;
}

export function gapTitle(rep: GapReport, g: OpenGap): string {
  const where = g.product_fill_price != null ? `${g.fill_price}(이 상품 약 ${g.product_fill_price})` : String(g.fill_price);
  return `${g.day} ${g.direction === "up" ? "상승" : "하락"} 갭 ${g.gap_pct > 0 ? "+" : ""}${g.gap_pct}%(${CAUSE_KO[g.cause]}) — ` +
    `메움 ${where} 까지 ${g.to_fill_pct > 0 ? "+" : ""}${g.to_fill_pct}%, ${g.open_sessions}거래일째 열림 · ` +
    `비슷한 갭 ${oddsLabel(g.odds)} — ${rep.basis.symbol} 일봉 ${rep.gaps_counted}개 갭의 과거 빈도`;
}

/** 가장 가까운 열린 갭(시장 칸 칩) */
export function nearest(rep: GapReport | null | undefined): OpenGap | null {
  return rep?.open?.[0] ?? null;
}
