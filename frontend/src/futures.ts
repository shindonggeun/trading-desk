import { ApiError } from "./api";
import type { FuturesQuote, FuturesSession } from "./types";

/** #444 코스피200 선물 주간 · 야간 시세 화면 · 카드가 같이 쓰는 말 · 형식. */

export const SESSION_KO: Record<FuturesSession, string> = { day: "주간", night: "야간" };
export const SESSION_HOURS: Record<FuturesSession, string> = { day: "08:45~15:45", night: "18:00~다음날 06:00" };

/** 장중 갱신 간격(서버 캐시 15초) */
export const LIVE_REFRESH_MS = 15_000;
/** 닫혀 있어도 이 간격으로 다시 묻는다 — 켜 둔 화면이 18:00 야간 · 08:45 주간 시작을 놓치지 않게 */
export const CLOSED_REFRESH_MS = 120_000;
/** 읽기에 실패하면(네트워크 · 502 · 재시작) 이만큼 뒤에 다시 */
export const RETRY_MS = 30_000;

/** 다음 읽기까지 — 세션이 끝나면(401) 다시 묻지 않는다(null) */
export function nextRefresh(q: Pick<FuturesQuote, "market" | "session"> | null, err?: unknown): number | null {
  if (err !== undefined) return err instanceof ApiError && err.status === 401 ? null : RETRY_MS;
  return q && isLive(q) ? LIVE_REFRESH_MS : CLOSED_REFRESH_MS;
}

/** 탭이 숨겨져 있으면 읽지 않고 다음 차례로 미룬다 */
export function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** 이 값의 세션이 지금 열려 있나 */
export function isLive(q: Pick<FuturesQuote, "market" | "session">): boolean {
  return q.market !== "closed" && q.market === q.session;
}

/** 세션 배지 글자: "주간 · 장중" / "야간 · 마감" */
export function sessionBadge(q: Pick<FuturesQuote, "market" | "session">): string {
  return `${SESSION_KO[q.session]} · ${isLive(q) ? "장중" : "마감"}`;
}

/** 계약 수 — 천 단위 쉼표, 없으면 "—" */
export function contracts(v: number | null | undefined, signed = false): string {
  if (v == null) return "—";
  return `${signed && v > 0 ? "+" : ""}${v.toLocaleString("ko-KR")}`;
}

/** ISO(+09:00) → "11.16 13:05" (KST 그대로) */
export function kstStamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const m = iso.match(/^\d{4}-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return m ? `${m[1]}.${m[2]} ${m[3]}:${m[4]}` : iso;
}

/** 봉 시작 ISO(+09:00) → 차트 시각(KST 를 UTC 처럼, 다른 차트와 같은 규칙 — chartFormat.crosshairTime) */
export function chartSec(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000) + 9 * 3600;
}
