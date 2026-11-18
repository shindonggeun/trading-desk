import type { DayCallLabel, DayCallMarket } from "./types";

/** #573 오늘 방향(#572) — 단계 이름 · 기호 · 색(한국식: 롱 빨강 · 숏 파랑 · 약보합 회색). 색만으로 구분하지 않게 기호를 같이 쓴다. */
export const DAY_CALL_LABELS: DayCallLabel[] = ["strong_long", "long", "flat", "short", "strong_short"];

export const LABEL_KO: Record<DayCallLabel, string> = {
  strong_long: "강한 롱", long: "약한 롱", flat: "약보합", short: "약한 숏", strong_short: "강한 숏",
};
export const LABEL_MARK: Record<DayCallLabel, string> = {
  strong_long: "▲▲", long: "▲", flat: "■", short: "▼", strong_short: "▼▼",
};
export const MARKET_KO: Record<DayCallMarket, string> = { kr: "국장", us: "미장" };

/** 방향 묶음 — 적중(방향 맞춤)의 기준 */
export const sideOf = (l: DayCallLabel): "long" | "flat" | "short" => (l.endsWith("long") ? "long" : l === "flat" ? "flat" : "short");

export const pct = (p: number | null | undefined, digits = 0) => (p == null ? "—" : `${(p * 100).toFixed(digits)}%`);

/** "확률은 추정 — 지난 90일 적중률 N%" (적중률을 아직 모르면 그렇다고) */
export function estimateNote(hitRate: number | null | undefined, days = 90, n?: number): string {
  if (hitRate == null) return `확률은 추정이에요 — 지난 ${days}일 적중률은 아직 채점된 날이 없어요`;
  return `확률은 추정이에요 — 지난 ${days}일 적중률 ${pct(hitRate)}${n ? ` (${n}일)` : ""}`;
}

/** UTC ISO → "11/19 07:50" (한국 시각) */
export function kstStamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(Date.parse(iso) + 9 * 3600_000);
  if (Number.isNaN(t.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${t.getUTCMonth() + 1}/${t.getUTCDate()} ${p(t.getUTCHours())}:${p(t.getUTCMinutes())}`;
}
