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

/** 큰 원화 금액을 조 · 억 · 만 단위로("+1.25조", "-3,450억"). signed 면 + 도 붙인다. */
export function krwShort(v: number | null | undefined, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  // 경계는 반올림 뒤 기준 — 9,999.5억은 "10,000억"이 아니라 "1조"
  let body: string;
  if (abs >= 1e12 - 0.5e8) body = `${trimZeros((abs / 1e12).toFixed(abs >= 1e13 ? 1 : 2)).replace(/\B(?=(\d{3})+(?!\d))/g, ",")}조`;
  else if (abs >= 1e8 - 0.5e4) body = `${Math.round(abs / 1e8).toLocaleString("ko-KR")}억`;
  else if (abs >= 1e4 - 0.5) body = `${Math.round(abs / 1e4).toLocaleString("ko-KR")}만`;
  else body = `${Math.round(abs).toLocaleString("ko-KR")}원`;
  if (body === "0원") return body;
  const sign = v < 0 ? "-" : signed && v > 0 ? "+" : "";
  return sign + body;
}

function trimZeros(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** Korean convention: red up, blue down. */
export function tone(v: number | null | undefined): string {
  return v === null || v === undefined || v === 0 ? "" : v > 0 ? "up" : "down";
}

/**
 * #484 달러 금액을 한국어 단위로: 2_348_000_000 → "23억 4,800만 달러", 1.2e12 → "1조 2,000억 달러".
 * 만 단위에서 반올림하고 큰 두 단위만 쓴다(1만 달러 미만은 그대로).
 */
export function usdKo(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (Math.round(a) < 10_000) return `${sign}${Math.round(a).toLocaleString("ko-KR")} 달러`;
  const man = Math.round(a / 10_000); // 만 달러 단위
  const units: [number, string][] = [[100_000_000, "조"], [10_000, "억"], [1, "만"]];
  const parts: string[] = [];
  let rest = man;
  for (const [size, name] of units) {
    const n = Math.floor(rest / size);
    rest -= n * size;
    if (n > 0) parts.push(`${n.toLocaleString("ko-KR")}${name}`);
    if (parts.length === 2) break;
  }
  return `${sign}${parts.join(" ")} 달러`;
}
