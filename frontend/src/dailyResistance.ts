/** #925 GET /api/chart-read 의 선택 필드 daily_reaction. */
export type DailyResistanceReason =
  | "session_open" | "awaiting_confirmation" | "session_unverified" | "missing_daily_source"
  | "source_unverified" | "confirmation_unverified" | "source_delayed" | "product_mismatch"
  | "invalid_bar" | "bar_missing" | "previous_bar_missing" | "resistance_unavailable"
  | "product_unsupported" | "reaction_error";

type DailyReactionKind =
  | "close_breakout" | "wick_rejection" | "strong_bullish_close"
  | "resistance_touch" | "resistance_near" | "none";

export interface DailyResistanceObservation {
  ticker: string;
  timeframe: "1d";
  status: "confirmed" | "in_progress" | "unavailable";
  reason: DailyResistanceReason | null;
  instrument: {
    kind: "etf" | "cash_index" | "continuous_future";
    unit: "USD" | "index_points";
  } | null;
  observed_at: string;
  fetched_at: string | null;
  source: string | null;
  session: {
    market: "us" | "cme";
    trade_date: string;
    opens_at: string;
    closes_at: string;
    confirm_after: string;
  } | null;
  bar: { at: string; open: number; high: number; low: number; close: number } | null;
  previous_close: { at: string; price: number } | null;
  resistance: {
    kind: "channel_upper" | "swing_high";
    price: number;
    basis_at: string;
    projected_at: string;
    ref: { kind: "channel"; edge: "upper"; id: string } | { kind: "swing"; side: "high"; at: string };
  } | null;
  metrics: {
    range: number;
    body_ratio: number | null;
    upper_wick_ratio: number | null;
    close_position: number | null;
    body_atr: number | null;
    close_distance_pct: number | null;
  } | null;
  reaction: {
    kind: DailyReactionKind;
    near: boolean;
    touched: boolean;
    wick_rejection: boolean;
    strong_bullish_close: boolean;
    close_breakout: boolean;
  } | null;
  volume: { ratio: number; hot: boolean } | null;
}

const PRODUCT_NAMES = new Map([
  ["SOXX", "반도체 ETF"],
  ["SOXL", "반도체 레버리지 ETF"],
  ["^NDX", "나스닥100 현물 지수"],
  ["NQ=F", "나스닥100 연속 선물"],
  ["SOXLUSDT", "SOXL 연동 USDT 무기한 계약"],
  ["SOXLUSDT.P", "SOXL 연동 USDT 무기한 계약"],
  ["SOXL-USDT", "SOXL 연동 USDT 무기한 계약"],
  ["NAS100", "NAS100 참고 CFD"],
  ["NAS100.CFD", "NAS100 참고 CFD"],
]);
const PRODUCT_KIND = { etf: "ETF", cash_index: "현물 지수", continuous_future: "연속 선물" };
const UNIT = { USD: "USD", index_points: "포인트" };

export function isDailyResistanceExample(ticker: string): boolean {
  return PRODUCT_NAMES.has(ticker.trim().toUpperCase());
}

export function dailyResistanceProduct(observation: Pick<DailyResistanceObservation, "ticker" | "instrument">) {
  const { ticker, instrument } = observation;
  const name = PRODUCT_NAMES.get(ticker.trim().toUpperCase()) ?? (instrument ? PRODUCT_KIND[instrument.kind] : null);
  return { name, unit: instrument ? UNIT[instrument.unit] : null };
}

const REACTION: Record<DailyReactionKind, string> = {
  close_breakout: "종가 돌파",
  wick_rejection: "윗꼬리 밀림",
  strong_bullish_close: "강한 양봉 마감",
  resistance_touch: "저항 접촉",
  resistance_near: "저항 근접",
  none: "뚜렷한 저항 반응 없음",
};

const REASON: Record<DailyResistanceReason, { detail: string; next: string | null }> = {
  session_open: {
    detail: "거래 세션이 열려 있어 봉의 값이 달라질 수 있어요.",
    next: "세션 마감 뒤 확인 가능 시각에 받은 일봉으로 종가 반응을 판단해요.",
  },
  awaiting_confirmation: {
    detail: "세션이 끝났고 원천 일봉의 확인을 기다리고 있어요.",
    next: "확인 가능 시각 이후에 받은 일봉으로 다시 판단해요.",
  },
  session_unverified: {
    detail: "거래 세션의 마감 시각을 확인하지 못했어요.",
    next: "세션 마감 일정과 해당 거래일 일봉이 확인되면 판단할 수 있어요.",
  },
  missing_daily_source: {
    detail: "이 상품의 일봉을 받지 못했어요.",
    next: "같은 상품의 원천 일봉을 받으면 다시 확인할 수 있어요.",
  },
  source_unverified: {
    detail: "이 일봉의 원천을 확인하지 못했어요.",
    next: "확인 가능한 Yahoo Finance 일봉이 필요해요.",
  },
  confirmation_unverified: {
    detail: "원천 일봉을 받은 시각을 확인하지 못했어요.",
    next: "확인 가능 시각 이후 수신 기록이 있는 일봉이 필요해요.",
  },
  source_delayed: {
    detail: "확인 가능 시각보다 먼저 받은 일봉만 있어요.",
    next: "확인 가능 시각 이후에 받은 해당 거래일 일봉을 기다려요.",
  },
  product_mismatch: {
    detail: "받은 봉의 상품이나 주기가 이 관찰과 달라요.",
    next: "같은 상품의 일봉이 확인되면 판단할 수 있어요.",
  },
  invalid_bar: {
    detail: "받은 일봉의 날짜나 가격을 확인하지 못했어요.",
    next: "거래일에 맞는 유효한 시가·고가·저가·종가가 필요해요.",
  },
  bar_missing: {
    detail: "관찰할 거래일의 일봉이 빠져 있어요.",
    next: "해당 거래일의 일봉을 받으면 다시 확인할 수 있어요.",
  },
  previous_bar_missing: {
    detail: "비교에 필요한 직전 거래일 일봉이 빠져 있어요.",
    next: "관찰 봉과 직전 거래일의 확정 일봉이 모두 필요해요.",
  },
  resistance_unavailable: {
    detail: "직전까지의 일봉에서 비교할 저항을 찾지 못했어요.",
    next: "직전 확정 일봉에서 조건을 충족한 채널 상단이나 스윙 고점이 확인되면 비교할 수 있어요.",
  },
  product_unsupported: {
    detail: "이 상품의 일봉으로 마감 반응을 계산하지 않아요.",
    next: "지원 상품은 SOXX, SOXL, 나스닥100 현물 지수, 나스닥100 연속 선물이에요.",
  },
  reaction_error: {
    detail: "일봉 마감 반응을 계산하지 못했어요.",
    next: "일봉과 비교할 저항을 다시 받으면 확인할 수 있어요.",
  },
};

export function dailyResistancePresentation(observation: DailyResistanceObservation) {
  const { status, reason, reaction } = observation;
  const copy = reason ? REASON[reason] : undefined;
  if (status === "confirmed" && reaction) {
    return { state: "확정 일봉", closeLabel: "확정 종가", reaction: REACTION[reaction.kind], detail: null, next: null };
  }
  if (status === "in_progress") {
    return {
      state: reason === "awaiting_confirmation" ? "마감 후 확인 대기" : "장중 진행",
      closeLabel: "진행 봉 종가",
      reaction: null,
      detail: copy?.detail ?? "아직 이 봉의 종가 반응을 확정할 수 없어요.",
      next: copy?.next ?? "세션 마감 뒤 확인 가능 시각에 받은 일봉이 필요해요.",
    };
  }
  return {
    state: reason === "product_unsupported" ? "지원하지 않는 상품" : "판정 자료 부족",
    closeLabel: "관측 봉 종가",
    reaction: null,
    detail: copy?.detail ?? "일봉의 종가 반응을 확인하지 못했어요.",
    next: copy?.next ?? "확인 가능한 일봉과 비교 저항이 필요해요.",
  };
}

const PRICE = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const RATIO = new Intl.NumberFormat("ko-KR", { style: "percent", maximumFractionDigits: 1 });
const PERCENT = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
const SMALL_PERCENT = new Intl.NumberFormat("ko-KR", { maximumSignificantDigits: 3, signDisplay: "exceptZero" });
const validPrice = (value: number) => Number.isFinite(value) && value > 0;

export function formatDailyPrice(value: number | null | undefined, unit: string | null): string {
  return value != null && validPrice(value) && unit ? PRICE.format(value) + " " + unit : "미제공";
}

export function formatDailyRatio(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) && value >= 0 && value <= 1 ? RATIO.format(value) : "미제공";
}

export function formatDailyDistance(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "미제공";
  return (value !== 0 && Math.abs(value) < 0.01 ? SMALL_PERCENT : PERCENT).format(value) + "%";
}

export function dailyResistanceFocus(observation: DailyResistanceObservation) {
  const { bar, resistance, timeframe, instrument } = observation;
  if (timeframe !== "1d" || !instrument || !bar || !validPrice(bar.high) || !validPrice(bar.low) || bar.high < bar.low) return null;
  const at = Date.parse(bar.at);
  if (!Number.isFinite(at)) return null;
  // 날짜만 맞는 선이나 다른 봉에 투영된 선은 이 관찰의 저항으로 강조할 수 없다.
  return {
    barAt: bar.at,
    high: bar.high,
    low: bar.low,
    resistance: resistance && validPrice(resistance.price) && Date.parse(resistance.projected_at) === at
      ? { price: resistance.price, projectedAt: resistance.projected_at }
      : null,
  };
}

/** 같은 세션 날짜라는 이유만으로 관찰 봉을 다른 봉에 표시하지 않는다. */
export function findDailyResistanceCandle<T>(rows: readonly T[], barAt: string, at: (row: T) => string): T | undefined {
  const stamp = Date.parse(barAt);
  return Number.isFinite(stamp) ? rows.find(row => Date.parse(at(row)) === stamp) : undefined;
}
