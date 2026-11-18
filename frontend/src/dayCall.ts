import type { DayCallEntry, DayCallKind, DayCallMarket, DayCallOutcome, DayCallStage, DayCallUnscorable } from "./types";

/** #573 오늘 방향(#572) — 단계 순서 · 기호 · 이름(한국식 색: 롱 빨강 · 숏 파랑 · 약보합 회색). 색만으로 구분하지 않게 기호를 같이 쓴다. */
export const DAY_CALL_STAGES: DayCallStage[] = ["strong_long", "weak_long", "flat", "weak_short", "strong_short"];

export const STAGE_KO: Record<DayCallStage, string> = {
  strong_long: "강한 롱", weak_long: "약한 롱", flat: "약보합", weak_short: "약한 숏", strong_short: "강한 숏",
};
export const STAGE_MARK: Record<DayCallStage, string> = {
  strong_long: "▲▲", weak_long: "▲", flat: "■", weak_short: "▼", strong_short: "▼▼",
};
export const MARKET_KO: Record<DayCallMarket, string> = { kr: "국장", us: "미장" };
export const MARKET_INDEX: Record<DayCallMarket, string> = { kr: "코스피", us: "S&P500" };

/** 0~1 비율 → "53%" */
export const rate = (r: number | null | undefined, digits = 0) => (r == null ? "—" : `${(r * 100).toFixed(digits)}%`);
/** 이미 % 인 값 → "34.2%" */
export const percent = (p: number | null | undefined, digits = 1) => (p == null ? "—" : `${p.toFixed(digits)}%`);

/** "확률은 추정 — 지난 90일 적중률 N%" (채점된 날이 없으면 그렇다고) */
export function estimateNote(hitRate: number | null | undefined, days = 90, n?: number): string {
  if (hitRate == null) return `확률은 추정이에요 — 지난 ${days}일 적중률은 아직 채점된 날이 없어요`;
  return `확률은 추정이에요 — 지난 ${days}일 적중률 ${rate(hitRate)}${n ? ` (${n}번 판정)` : ""}`;
}

/** UTC ISO → "11/19 07:30" (한국 시각) */
export function kstStamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const t = new Date(ms + 9 * 3600_000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getUTCMonth() + 1}/${t.getUTCDate()} ${p(t.getUTCHours())}:${p(t.getUTCMinutes())}`;
}

/** 두 판정 — 전일 종가 대비(야간 선물 포함, 기준선 대비 우위) 먼저 */
export const DAY_CALL_KINDS: DayCallKind[] = ["prev_close", "open"];
export const KIND_KO: Record<DayCallKind, string> = { prev_close: "전일 종가 대비", open: "시가 대비" };

/** 판정의 결론 — 롱 · 숏 · 판단 보류(기호 + 글자) */
export function verdictText(c: Pick<DayCallEntry, "verdict">): { mark: string; text: string; cls: string } {
  if (c.verdict === "hold") return { mark: "■", text: "판단 보류", cls: "daycall-hold" };
  return c.verdict === "long" ? { mark: "▲", text: "롱 우세", cls: "up" } : { mark: "▼", text: "숏 우세", cls: "down" };
}

/** 채점된 결과인가(채점 불가 · 채점 전이 아니면) */
export function scoredOutcome(o: DayCallOutcome | DayCallUnscorable | null | undefined): DayCallOutcome | null {
  return o && !o.unscorable ? o : null;
}
