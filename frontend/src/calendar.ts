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

/** 한국 종목 코드는 .KS · .KQ 를 떼고(035420) */
export const shortTicker = (t: string) => t.replace(/\.(KS|KQ)$/i, "");

const isKrw = (r: CalEarning) => (r.currency ?? (r.market === "kr" ? "KRW" : "USD")) === "KRW";

/** 금액 축약: $94.3B · $812M / 7.8조원 · 3,200억원 */
export function money(v: number, krw: boolean): string {
  const a = Math.abs(v);
  if (krw) {
    if (a >= 1e12) return `${(v / 1e12).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}조원`;
    if (a >= 1e8) return `${Math.round(v / 1e8).toLocaleString("ko-KR")}억원`;
    return `${Math.round(v).toLocaleString("ko-KR")}원`;
  }
  if (a >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

const perShare = (v: number, krw: boolean) =>
  krw ? `${Math.round(v).toLocaleString("ko-KR")}원` : `$${v.toFixed(2)}`;

/** #309 실적 상세 칸 — 값이 없는 칸은 뺀다 */
export function earnDetail(r: CalEarning): string[] {
  const krw = isKrw(r);
  const out: string[] = [];
  if (r.fiscal_quarter) out.push(`${r.fiscal_quarter} 분기`);
  if (r.eps_estimate != null) {
    let s = `EPS 추정 ${perShare(r.eps_estimate, krw)}`;
    if (r.eps_low != null && r.eps_high != null && r.eps_low !== r.eps_high) s += `(${perShare(r.eps_low, krw)}~${perShare(r.eps_high, krw)})`;
    if (r.eps_year_ago != null && r.eps_year_ago > 0) {
      const yoy = (r.eps_estimate / r.eps_year_ago - 1) * 100;
      s += ` · 작년 같은 분기 대비 ${yoy > 0 ? "+" : ""}${yoy.toFixed(0)}%`;
    }
    out.push(s);
  }
  if (r.revenue_estimate != null) {
    let s = `예상 매출 ${money(r.revenue_estimate, krw)}`;
    if (r.revenue_low != null && r.revenue_high != null && r.revenue_low !== r.revenue_high) s += `(${money(r.revenue_low, krw)}~${money(r.revenue_high, krw)})`;
    out.push(s);
  }
  if (r.analysts) out.push(`애널리스트 ${r.analysts}명`);
  if (r.market_cap != null) out.push(`시총 ${money(r.market_cap, krw)}`);
  return out;
}

/**
 * #309 실적 수집 안내 — 서버(#308)는 사람용 한국어 error 를 준다. 그 전 서버의 원문 예외(…HTTPStatusError 등)는
 * 화면에 내지 않고 일반 문구로 바꾼다. warn = 자료는 있는데 일부 · 최신을 못 받음(노란 줄), 자료가 아예 없을 때만 빨강.
 */
export function earningsNotice(src: { refreshed_at: string | null; error: string | null; stale?: boolean }, rows: number): { text: string; red: boolean } | null {
  if (!src.error && !src.stale) return null;
  const raw = !src.error || /[A-Za-z]{4,}(Error|Exception)|failed|Traceback|\bfirst:/i.test(src.error);
  let when: string | null = null;
  if (src.refreshed_at && !Number.isNaN(Date.parse(src.refreshed_at))) {
    const k = new Date(Date.parse(src.refreshed_at) + 9 * 3600_000);   // KST
    when = `${k.getUTCMonth() + 1}/${k.getUTCDate()} ${String(k.getUTCHours()).padStart(2, "0")}:${String(k.getUTCMinutes()).padStart(2, "0")}`;
  }
  const text = raw
    ? `실적 일정 일부를 새로 받지 못했습니다${when ? ` — ${when} 자료를 보이는 중` : ""}`
    : `실적 일정: ${src.error}`;
  return { text, red: rows === 0 };
}

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
