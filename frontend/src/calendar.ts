import type { CalDay, CalEarning, CalEvent } from "./types";

/** #246 캘린더 화면 · 대시보드 "이번 주 일정"이 같이 쓰는 문구 · 판정 */

export const IMPORTANCE: Record<number, string> = { 2: "상", 1: "중" };
export const FLAG: Record<"kr" | "us", string> = { kr: "🇰🇷", us: "🇺🇸" };
export const WHEN: Record<CalEarning["when"], string> = { pre: "장 전", after: "장 후", unknown: "시간 미정" };

/** "22:30" → "22:30 (오후 10시 30분)" */
export function kstTime(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return hhmm;
  const half = h < 12 ? "오전" : "오후";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${hhmm} (${half} ${h12}시${m ? ` ${m}분` : ""})`;
}

/** 한국 날짜로 다음 날이면 "(한국 다음 날)" — 미국 일정은 미국 날짜에 둔다(#243) */
export const nextDay = (e: CalEvent) => (e.next_day_kst ? " (한국 다음 날)" : "");

export const eps = (v: number | null) => (v == null ? "" : ` · 예상 EPS ${v.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}`);

export const analyzeLink = (r: CalEarning) =>
  `#/?ticker=${encodeURIComponent(r.ticker)}${r.name ? `&name=${encodeURIComponent(r.name)}` : ""}`;

export function todayKst(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function addMonths(month: string, n: number): string {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + n, 1));
  return d.toISOString().slice(0, 7);
}

/** 그날의 휴장 · 조기 폐장 한 줄들(국 · 미 따로) */
export function closures(d: CalDay): { market: "kr" | "us"; text: string; early?: boolean }[] {
  const out: { market: "kr" | "us"; text: string; early?: boolean }[] = [];
  const weekend = d.weekday === "토" || d.weekday === "일";
  if (!weekend && d.kr.holiday) out.push({ market: "kr", text: `국장 휴장 · ${d.kr.holiday}` });
  if (!weekend && d.us.holiday) out.push({ market: "us", text: `미장 휴장 · ${d.us.holiday}` });
  if (d.us.early_close) out.push({ market: "us", text: `미장 조기 폐장(${d.us.early_close}) · 한국 ${d.us.close_kst} 마감`, early: true });
  return out;
}
