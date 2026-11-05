export function price(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: v >= 1000 ? 0 : 2 });
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("ko-KR", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function duration(from: string | null, to: string | null): string {
  if (!from) return "";
  const ms = (to ? new Date(to) : new Date()).getTime() - new Date(from).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s >= 60 ? `${Math.floor(s / 60)}분 ${s % 60}초` : `${s}초`;
}

export const STATUS_LABEL: Record<string, string> = {
  queued: "대기",
  running: "분석 중",
  done: "완료",
  failed: "실패",
};

export type Currency = "KRW" | "USD";

/** 1,234,567원 / $1,234.56 — "—" when the amount is unknown. */
export function money(v: number | null | undefined, currency: Currency, signed = false): string {
  if (v === null || v === undefined) return "—";
  const digits = currency === "KRW" ? 0 : 2;
  const abs = Number(Math.abs(v).toFixed(digits)); // sign from the shown amount: no "-$0.00"
  const sign = abs === 0 ? "" : v < 0 ? "-" : signed ? "+" : "";
  return currency === "KRW"
    ? `${sign}${abs.toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원`
    : `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function percent(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** Korean convention: red up, blue down. */
export function tone(v: number | null | undefined): string {
  return v === null || v === undefined || v === 0 ? "" : v > 0 ? "up" : "down";
}
