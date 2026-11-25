import type { ChartReadRef } from "./types";
import type { DrawingAnchor, DrawingKind } from "./chartDrawingModel";
import type { DrawingCandle, DrawingPractice } from "./components/chartDrawingPractice";

/** #977 GET /api/chart-read 의 선택 필드 trend_follow — 일봉 추세추종 근거(관찰이지 매매 신호가 아니다). */
export type TrendFollowReason = "missing_daily_source" | "product_mismatch" | "insufficient_bars" | "trend_follow_error";
export type TrendState = "down" | "sideways" | "up";
export type TrendStage = "first_rebound" | "awaiting_confirmation" | "breakout_confirmed" | "failed_breakout" | "none";
export type TrendSignal = "first_up_bar" | "support_reclaim" | "wick_touch" | "close_above" | "close_back_below";
export type EvidenceKind = "quarter_level" | "channel_quarter" | "fib_extension" | "divergence" | "stochastic" | "overlap" | "lead";
export type EvidenceStatus = "confirmed" | "pending" | "insufficient";
export type LeadState = "led" | "same" | "lagged" | "waiting" | "not_comparable";

export interface TrendKeyPrice {
  price: number;
  label: string;
  distance_pct: number;
  ref?: ChartReadRef;
}

export interface TrendCondition {
  id: string;
  kind: "confirm" | "invalidate";
  text: string;
  price: number | null;
  met: boolean | null;
}

/** 피벗 시각 · 가격 — divergence 는 그 봉의 rsi, stochastic 은 그 봉의 %K(k)가 붙을 수 있다 */
export interface TrendAnchor { at: string; price: number; rsi?: number; k?: number }

export interface TrendEvidence {
  id: string;
  kind: EvidenceKind;
  status: EvidenceStatus;
  label: string;
  detail: string;
  prices: number[];
  anchors: TrendAnchor[];
  confirmed_at: string | null;
  meta?: Record<string, unknown>;
}

export interface TrendFollowObservation {
  ticker: string;
  timeframe: "1d";
  status: "confirmed" | "in_progress" | "unavailable";
  reason: TrendFollowReason | null;
  instrument: {
    kind: "etf" | "cash_index" | "continuous_future" | "perpetual" | "stock" | "index" | "spot";
    unit: "USD" | "KRW" | "index_points" | "USDT";
    symbol: string;
    provider: string | null;
    session?: "us_regular" | "krx_regular" | "cme_globex" | "24h" | string;
    rollover: string | null;
    note: string | null;
  } | null;
  observed_at: string;
  /** 마지막 확정 일봉 시각 — 진행 중 봉은 판정에서 뺀다 */
  as_of: string | null;
  last_bar: { at: string; open: number; high: number; low: number; close: number } | null;
  trend: { state: TrendState; basis: string } | null;
  stage: { state: TrendStage; signal: TrendSignal | null; confirmed_at: string | null; basis: string } | null;
  key_prices: { hold: TrendKeyPrice | null; break: TrendKeyPrice | null };
  conditions: TrendCondition[];
  evidence: TrendEvidence[];
  /** 고정 안내 문구(판정이 없어도 온다) */
  note?: string;
  lead?: {
    benchmark: string;
    /** 기준(코스피)의 돌파 봉 시각 */
    benchmark_at?: string | null;
    condition: string;
    items: { ticker: string; state: LeadState; at: string | null }[];
  } | null;
}

export const TREND_KO: Record<TrendState, string> = { down: "하락 추세", sideways: "횡보", up: "상승 추세" };
export const STAGE_KO: Record<TrendStage, string> = {
  first_rebound: "첫 반등",
  awaiting_confirmation: "확인 대기",
  breakout_confirmed: "돌파 확인",
  failed_breakout: "재이탈",
  none: "뚜렷한 진행 없음",
};
const SIGNAL_KO: Record<TrendSignal, string> = {
  first_up_bar: "하락 뒤 첫 양봉만 나왔어요 — 아직 반등 하나예요.",
  support_reclaim: "지지 아래로 내려갔다가 종가로 다시 올라섰어요.",
  wick_touch: "저항에 꼬리만 닿았고 종가는 아래에서 끝났어요.",
  close_above: "종가가 저항 위에서 마감했어요.",
  close_back_below: "넘었던 저항 아래로 종가가 다시 내려왔어요.",
};
const REASON_KO: Record<TrendFollowReason, string> = {
  missing_daily_source: "일봉 원자료를 받지 못해 추세를 판정하지 않았어요.",
  product_mismatch: "차트 상품과 판정 상품이 달라 추세를 판정하지 않았어요.",
  insufficient_bars: "확정된 일봉이 모자라 추세를 판정하지 않았어요.",
  trend_follow_error: "추세 근거를 계산하지 못했어요.",
};
export const EVIDENCE_KO: Record<EvidenceKind, string> = {
  quarter_level: "수평 구간 0.25",
  channel_quarter: "채널 폭 0.25 보조선",
  fib_extension: "피보나치 확장",
  divergence: "다이버전스",
  stochastic: "스토캐스틱",
  overlap: "저항 겹침",
  lead: "선행 돌파",
};
export const EVIDENCE_STATUS_KO: Record<EvidenceStatus, string> = { confirmed: "확인됨", pending: "진행 중", insufficient: "자료 부족" };
export const LEAD_KO: Record<LeadState, string> = {
  led: "기준보다 먼저 돌파",
  same: "기준과 같은 날 돌파",
  lagged: "기준보다 늦게 돌파",
  waiting: "아직 돌파 전 · 확인 대기",
  not_comparable: "비교 불가",
};
export const TREND_NOTE = "상태 하나로 바닥이나 사고팔 때를 정하지 않아요 — 확인 조건과 무효화 조건을 같이 봐요.";

const PRODUCT: Record<string, string> = {
  SOXX: "반도체 ETF",
  SOXL: "반도체 3배 레버리지 ETF",
  SOXLUSDT: "SOXL 연동 USDT 무기한 계약",
  "SOXLUSDT.P": "SOXL 연동 USDT 무기한 계약",
  "NQ=F": "나스닥100",
  "^NDX": "나스닥100",
  "^KS11": "코스피",
  "000660.KS": "SK하이닉스",
  "005930.KS": "삼성전자",
};
const KIND_KO = {
  etf: "ETF", cash_index: "현물 지수", continuous_future: "연속 선물", perpetual: "무기한 계약", stock: "주식", index: "지수", spot: "24시간 시세",
};
const SESSION_KO: Record<string, string> = { us_regular: "미국 정규장", krx_regular: "한국 정규장", cme_globex: "CME Globex", "24h": "24시간" };
const ROLLOVER_KO: Record<string, string> = { yahoo_front_month: "야후 근월물 연결" };
const UNIT_KO = { USD: "USD", KRW: "원", index_points: "포인트", USDT: "USDT" };
const PROVIDER_KO: Record<string, string> = { yahoo: "Yahoo Finance", toss: "토스증권", binance: "Binance" };

export function productName(ticker: string): string {
  return PRODUCT[ticker.trim().toUpperCase()] ?? ticker;
}

/** 상품 라벨 — 같은 기초자산이라도 ETF · 무기한 계약 · 연속 선물은 라벨과 단위를 따로 보인다 */
export function trendProduct(o: Pick<TrendFollowObservation, "ticker" | "instrument">) {
  const { ticker, instrument } = o;
  const name = PRODUCT[ticker.trim().toUpperCase()] ?? null;
  if (!instrument) return { label: name ? `${ticker} · ${name}` : ticker, unit: null, source: null, note: null };
  const kind = KIND_KO[instrument.kind];
  return {
    label: [instrument.symbol || ticker, name, name?.includes(kind) ? null : kind].filter(Boolean).join(" · "),
    unit: UNIT_KO[instrument.unit],
    source: [instrument.provider && (PROVIDER_KO[instrument.provider] ?? instrument.provider),
      instrument.session && (SESSION_KO[instrument.session] ?? instrument.session), instrument.rollover && `롤오버 ${ROLLOVER_KO[instrument.rollover] ?? instrument.rollover}`]
      .filter(Boolean).join(" · "),
    note: instrument.note,
  };
}

const validPrice = (v: number | null | undefined): v is number => typeof v === "number" && Number.isFinite(v) && v > 0;
const NUM = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const PCT = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 1, minimumFractionDigits: 1, signDisplay: "exceptZero" });

export function formatTrendPrice(v: number | null | undefined, unit: string | null): string {
  return validPrice(v) ? NUM.format(v) + (unit ? " " + unit : "") : "미제공";
}
export function formatTrendDistance(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? (Math.abs(v) < 0.05 ? "0.0%" : PCT.format(v) + "%") : "미제공";
}

/** 머리 한 줄 — "일봉 하락 추세 · 첫 반등" / 판정하지 못하면 그 이유 */
export function trendPresentation(o: TrendFollowObservation) {
  if (o.status === "unavailable" || !o.trend || !o.stage) {
    return {
      line: "일봉 추세 판정 없음",
      reason: (o.reason && REASON_KO[o.reason]) ?? "추세 근거가 아직 없어요.",
      signal: null,
      pending: false,
    };
  }
  return {
    line: `일봉 ${TREND_KO[o.trend.state]} · ${STAGE_KO[o.stage.state]}`,
    reason: null,
    signal: o.stage.signal ? SIGNAL_KO[o.stage.signal] : null,
    pending: o.status === "in_progress",
  };
}

/** 접힌 칸 제목에 붙는 짧은 한 줄(모바일 첫 화면) — 핵심 가격 두 개와 다음 확인 하나 */
export function trendSummaryLine(o: TrendFollowObservation): string {
  const { line } = trendPresentation(o);
  if (o.status === "unavailable") return "관찰: " + line;
  const { unit } = trendProduct(o);
  const hold = o.key_prices.hold;
  const brk = o.key_prices.break;
  const next = o.conditions.find((c) => c.kind === "confirm" && c.met !== true);
  // 접힌 한 줄에도 '관찰' 임을 붙인다 — 행동 계획처럼 읽히지 않게
  return "관찰: " + [line, hold && `지켜야 할 가격 ${formatTrendPrice(hold.price, unit)}`, brk && `넘어야 할 저항 ${formatTrendPrice(brk.price, unit)}`,
    next && `다음 확인 ${next.text}`].filter(Boolean).join(" · ");
}

export function conditionState(c: TrendCondition): { label: string; tone: "met" | "open" | "unknown" } {
  if (c.met === true) return { label: c.kind === "confirm" ? "충족" : "발생", tone: "met" };
  if (c.met === false) return { label: c.kind === "confirm" ? "아직" : "미발생", tone: "open" };
  return { label: "확인 전", tone: "unknown" };
}

/** 근거의 설정값을 한 줄로 — 계산 기준이 화면에서 보이도록 */
export function evidenceMeta(e: TrendEvidence): string | null {
  const m = e.meta ?? {};
  const has = (k: string) => m[k] !== undefined && m[k] !== null && m[k] !== "";
  const scale = has("scale") ? (m.scale === "log" ? "로그" : m.scale === "linear" ? "선형" : String(m.scale)) : null;
  const parts: (string | null)[] = (() => {
    switch (e.kind) {
      case "quarter_level":
        return [has("ratio") ? `비율 ${m.ratio}` : null, has("low") && has("high") ? `구간 ${NUM.format(Number(m.low))}~${NUM.format(Number(m.high))}` : null,
          scale, has("tolerance_pct") ? `허용 ${m.tolerance_pct}%` : null];
      case "channel_quarter":
        return [has("ratio") ? `채널 폭 ${m.ratio}` : null, has("width_pct") ? `폭 ${m.width_pct}%` : null, scale];
      case "fib_extension":
        return [has("ratios") ? `확장 ${String(m.ratios).split(",").join(" · ")}` : null,
          has("wave_low") && has("wave_high") ? `기준 파동 ${NUM.format(Number(m.wave_low))}→${NUM.format(Number(m.wave_high))}` : null, scale];
      case "divergence":
        return [has("indicator") ? String(m.indicator).toUpperCase().replace(/(\D+)(\d+)/, "$1 $2") : null,
          has("comparisons") ? `연속 비교 ${m.comparisons}회` : null, has("pivot_k") ? `피벗 양쪽 ${m.pivot_k}봉` : null];
      case "stochastic":
        return [has("k") ? `K ${m.k} · 평활 ${m.smooth ?? "—"} · D ${m.d ?? "—"}` : null,
          has("floor") ? `바닥 ${m.floor} → ${m.ceiling ?? 80}` : null,
          has("bars_to_80") ? `${m.bars_to_80}봉 만에 도달` : has("max_bars") ? `${m.max_bars}봉 안에 도달해야 확인` : null,
          has("pullback_max") ? `되밀림 ${m.pullback_max}${has("pullback_allowed") ? ` (허용 ${m.pullback_allowed})` : ""}` : null];
      case "overlap":
        return [has("distance_pct") ? `저항과 매물대 거리 ${formatTrendDistance(Number(m.distance_pct))}` : null,
          has("tolerance_pct") ? `허용 ${m.tolerance_pct}%` : null, m.falling === true || m.falling === "yes" ? "하락 저항" : null];
      default:
        return [];
    }
  })();
  const text = parts.filter(Boolean).join(" · ");
  return text || null;
}

/** 차트 강조 대상 — "stage"(최근 확정봉) · "hold" · "break" · 근거 id */
export type TrendFocusKey = string;
/** anchors 의 n 은 근거 안의 원래 순번(1부터) — 일부가 차트 밖이어도 번호가 밀리지 않는다 */
export type TrendFocus = { prices: { price: number; label: string }[]; anchors: (TrendAnchor & { n: number })[]; barAt: string | null };

/** 근거 가격마다의 이름 — #977 의 prices 순서(docs/trend-follow.md)를 따른다 */
export function evidencePriceLabels(e: TrendEvidence): string[] {
  const n = e.prices.length;
  const fallback = Array.from({ length: n }, () => EVIDENCE_KO[e.kind]);
  if (e.kind === "quarter_level" && n === 2) return ["위 25% 선", "아래 25% 선"];
  if (e.kind === "channel_quarter" && n === 4) return ["채널 상단", "상단 25%", "하단 25%", "채널 하단"];
  if (e.kind === "overlap" && n === 2) return ["저항", "매물 구역"];
  if (e.kind === "fib_extension") {
    const ratios = String(e.meta?.ratios ?? "").split(",").filter(Boolean);
    if (ratios.length === n) return ratios.map((r) => `확장 ${r}`);
  }
  return fallback;
}

export function trendFocus(o: TrendFollowObservation, key: TrendFocusKey): TrendFocus | null {
  if (o.status === "unavailable") return null;
  const bar = o.last_bar && validPrice(o.last_bar.high) && validPrice(o.last_bar.low) ? o.last_bar : null;
  if (key === "stage") {
    if (!bar) return null;
    const prices = [o.key_prices.hold && { price: o.key_prices.hold.price, label: "지킬" },
      o.key_prices.break && { price: o.key_prices.break.price, label: "넘을" }]
      .filter((p): p is { price: number; label: string } => !!p && validPrice(p.price));
    return { prices, anchors: [], barAt: bar.at };
  }
  if (key === "hold" || key === "break") {
    const k = o.key_prices[key];
    return k && validPrice(k.price) ? { prices: [{ price: k.price, label: key === "hold" ? "지킬" : "넘을" }], anchors: [], barAt: null } : null;
  }
  const e = o.evidence.find((x) => x.id === key);
  if (!e) return null;
  const labels = evidencePriceLabels(e);
  const prices = e.prices.map((price, i) => ({ price, label: labels[i] })).filter((p) => validPrice(p.price));
  const anchors = e.anchors.map((a, i) => ({ ...a, n: i + 1 })).filter((a) => validPrice(a.price) && Number.isFinite(Date.parse(a.at)));
  return prices.length || anchors.length ? { prices, anchors, barAt: null } : null;
}

const PRACTICE_KIND: Partial<Record<EvidenceKind, DrawingKind>> = {
  quarter_level: "measure", fib_extension: "measure", channel_quarter: "channel", divergence: "trend", overlap: "horizontal",
};
const STAMP = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit" });

/** 같은 시각(epoch ms)으로 봉을 찾는 표 — 렌더마다 선형 탐색하지 않게 한 번 만든다 */
export function byStamp<T>(rows: readonly T[], at: (row: T) => string): (at: string) => T | undefined {
  const map = new Map<number, T>();
  for (const row of rows) {
    const t = Date.parse(at(row));
    if (Number.isFinite(t)) map.set(t, row);
  }
  return (value) => {
    const t = Date.parse(value);
    return Number.isFinite(t) ? map.get(t) : undefined;
  };
}

/**
 * 근거의 앵커를 기존 작도 연습(#927)의 단계로 — 앵커가 차트의 같은 시각 봉에 모두 있을 때만.
 * 날짜만 맞는 다른 봉(시간봉 차트 등)에는 연결하지 않는다.
 */
export function evidencePractice(e: TrendEvidence, find: (at: string) => DrawingCandle | undefined): DrawingPractice | null {
  const base = PRACTICE_KIND[e.kind];
  if (!base || e.status === "insufficient") return null;
  const want = base === "channel" ? (e.anchors.length >= 3 ? 3 : 2) : base === "horizontal" ? 1 : 2;
  const kind: DrawingKind = base === "channel" && want === 2 ? "trend" : base;
  const picked = (kind === "horizontal" || kind === "measure") ? e.anchors.slice(0, want) : e.anchors.slice(-want);
  if (picked.length < want) return null;
  const anchors: DrawingAnchor[] = [];
  for (const a of picked) {
    const candle = find(a.at);
    if (!candle || !validPrice(a.price)) return null;
    anchors.push({ time: candle.time, stamp: candle.stamp, price: a.price });
  }
  const steps = anchors.map((anchor, i) => ({
    anchor,
    instruction: `${EVIDENCE_KO[e.kind]} 앵커 ${i + 1} — ${STAMP.format(Date.parse(anchor.stamp))} 봉의 ${NUM.format(anchor.price)} 지점을 선택하세요.`,
  }));
  return { kind, steps, review: `${e.label} — 근거와 같은 앵커로 그렸어요. 판정은 확정봉 기준이에요.` };
}
