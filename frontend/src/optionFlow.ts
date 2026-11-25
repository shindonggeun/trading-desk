import { krwShort } from "./format";
import type { FuturesFlow } from "./types";

/**
 * #1008 시장 수급 선물 카드의 코스피200 콜 · 풋 옵션 투자자별 순매수(#1007 GET /api/market/futures 의 options).
 * 풋 − 콜 순매수 금액은 숫자와 쉬운 말만 — 옵션 매도도 순매수 음수로 보이므로 매매 신호로 단정하지 않는다.
 */

/** 외국인 풋 − 콜 순매수 금액(백만 원)을 한 줄로: 값과 어느 쪽이 더 많은지(단정하지 않는 말). 색은 입히지 않는다 —
 *  +(풋 우세)가 수급 색 규칙의 빨강(순매수)과 뜻이 엇갈리므로 */
export function putCallLine(mn: number | null | undefined): { value: string; lean: string } | null {
  if (mn == null || !Number.isFinite(mn)) return null;
  const value = krwShort(mn * 1_000_000);
  if (Math.abs(mn) < 100) return { value, lean: "콜 · 풋이 비슷합니다" };        // 1억 원 미만
  return mn > 0
    ? { value, lean: "풋 쪽 순매수가 더 많아요(하락 대비일 수 있음)" }
    : { value, lean: "콜 쪽 순매수가 더 많아요(상승 쪽일 수 있음)" };
}

/** 기준 시각 — 이번 응답의 값인지, 원천이 실패해 마지막 정상값을 보이는지 */
export function optionsAsOf(d: Pick<FuturesFlow, "fetched_at" | "options" | "options_error">): { text: string; stale: boolean } | null {
  const o = d.options;
  if (!o) return null;
  const hhmm = o.as_of.slice(11, 16);
  const stale = o.as_of !== d.fetched_at;
  return { text: stale ? `${hhmm} 마지막 정상값` : `${hhmm} 기준`, stale };
}

export const PUT_CALL_TIP =
  "풋 순매수 − 콜 순매수 금액입니다. 풋을 사면 하락 대비, 콜을 사면 상승 쪽 베팅인 경우가 많습니다. " +
  "다만 옵션을 팔아도 순매수가 음수로 보이므로(풋 매도는 상승 · 횡보 쪽), 선물 · 현물 수급과 함께 보세요.";
