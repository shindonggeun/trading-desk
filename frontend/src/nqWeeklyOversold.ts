/** #945의 NQ=F 주봉 관찰과 전체 규칙 저장 계약. */
export const NQ_PRICE_CONDITIONS = ["bollinger", "channel", "fib"] as const;
export type NqPriceCondition = typeof NQ_PRICE_CONDITIONS[number];
export type NqChannelName = "telegram";

export const NQ_PRICE_LABEL: Record<NqPriceCondition, string> = {
  bollinger: "볼린저 하단", channel: "평행 채널 바깥 하단", fib: "피보나치 2.618",
};
export const NQ_CHANNEL_LABEL: Record<NqChannelName, string> = { telegram: "텔레그램" };

export interface NqWeeklyOversoldSettings {
  enabled: boolean;
  rsi_limit: number;
  price_conditions: NqPriceCondition[];
  min_hits: number;
}

export type NqWeeklyOversoldSaved = { settings: NqWeeklyOversoldSettings; revision: number };

export interface NqOversoldWeek {
  /** 뉴욕 거래 주를 여는 월요일 날짜. */
  start: string;
  closed_at: string;
  confirmed_at: string;
  fetched_at: string | null;
  source: string | null;
}

type HitState = "hit" | "miss" | "unknown";
export type NqOversoldRsi = {
  value: number | null;
  limit: number;
  status: HitState;
  method: "wilder_rsi14";
  reason: string | null;
};
export type NqOversoldPriceEvidence = {
  value: number | null;
  actual_low: number;
  actual_close: number;
  tolerance: number | null;
  status: HitState;
  state: "near" | "normal" | "below" | "missing";
  method: string;
  anchor_weeks: string[];
  reason: string | null;
};

export type NqOversoldPoint = {
  week: NqOversoldWeek;
  status: "matched" | "not_matched" | "pending" | "unavailable";
  reason: string | null;
  evidence: { rsi?: NqOversoldRsi } & Partial<Record<NqPriceCondition, NqOversoldPriceEvidence>>;
  matched_conditions: NqPriceCondition[];
  min_hits: number;
};

export interface NqOversoldCurrent extends NqOversoldPoint {
  observed_at: string;
  method_version: string;
  rollover: "unknown";
  forming_week: { start: string; source_present: boolean; status: "pending" } | null;
}

type FollowUp = {
  status: "observed" | "unverified";
  through_week: string | null;
  return_pct: number | null;
  max_additional_decline_pct: number | null;
};
type NqHorizon = "4w" | "12w";

export interface NqOversoldMarker extends NqOversoldPoint {
  status: "matched";
  close: number;
  edge: boolean;
  prior_drawdown_pct: number | null;
  follow_up: Record<NqHorizon, FollowUp>;
}

type HorizonSummary = {
  weeks: number;
  sample_count: number;
  observed_count: number;
  unverified_count: number;
  negative_return_count: number;
  additional_decline_count: number;
  severe_additional_decline_count: number;
  median_return_pct: number | null;
  max_additional_decline_pct: number | null;
};

export type NqOversoldStudy = {
  status: "available" | "unavailable";
  reason: string | null;
  coverage: {
    source: string | null;
    first_week: string | null;
    last_week: string | null;
    confirmed_weeks: number;
    requested_weeks: number;
    first_evaluated_week: string | null;
    scanned_weeks: number;
    unverified_weeks: number;
    matched_weeks: number;
    edge_sample_count: number;
    severe_prior_drawdown_count: number;
  };
  severe_decline_threshold_pct: number;
  rollover: "unknown";
  horizons: Record<NqHorizon, HorizonSummary>;
};

export type NqOversoldChannel = {
  channel: NqChannelName;
  status: "ready" | "unavailable" | "disabled" | "needs_reconnect";
  needs_reconnect: boolean;
};
export type NqOversoldDelivery = {
  week: string;
  channel: NqChannelName;
  status: "pending" | "sending" | "sent" | "unknown" | "failed" | "expired" | "superseded"
    | "rate_limited" | "rate_limit_exhausted" | "unavailable" | "disabled" | "needs_reconnect";
  attempts: number;
  updated_at: string;
  retry_at: string | null;
};

export interface NqWeeklyOversoldView extends NqWeeklyOversoldSaved {
  method_version: string;
  can_edit: boolean;
  current: NqOversoldCurrent;
  markers: NqOversoldMarker[];
  study: NqOversoldStudy;
  channels: NqOversoldChannel[];
  deliveries: NqOversoldDelivery[];
}

export type NqOversoldDraft = {
  enabled: boolean;
  rsiLimit: string;
  priceConditions: NqPriceCondition[];
  minHits: string;
};

export function nqDraft(settings: NqWeeklyOversoldSettings): NqOversoldDraft {
  return {
    enabled: settings.enabled, rsiLimit: String(settings.rsi_limit),
    priceConditions: [...settings.price_conditions], minHits: String(settings.min_hits),
  };
}

export function parseNqDraft(draft: NqOversoldDraft): { settings: NqWeeklyOversoldSettings; error?: never } | { settings?: never; error: string } {
  const limit = draft.rsiLimit.trim() === "" ? NaN : Number(draft.rsiLimit);
  if (!Number.isFinite(limit) || limit < 0 || limit > 100) return { error: "RSI 상한은 0에서 100 사이로 입력해 주세요." };
  const conditions = NQ_PRICE_CONDITIONS.filter(key => draft.priceConditions.includes(key));
  if (!conditions.length || conditions.length !== draft.priceConditions.length) return { error: "가격 조건을 하나 이상 골라 주세요." };
  const minHits = draft.minHits.trim() === "" ? NaN : Number(draft.minHits);
  if (!Number.isInteger(minHits) || minHits < 1 || minHits > conditions.length) {
    return { error: "최소 충족 개수는 1개 이상, 선택한 가격 조건 수 이하여야 해요." };
  }
  return { settings: { enabled: draft.enabled, rsi_limit: limit, price_conditions: conditions, min_hits: minHits } };
}

export function confirmedNqMarkers(view: NqWeeklyOversoldView | null): NqOversoldMarker[] {
  if (!view) return [];
  const { current } = view;
  const formed = current.forming_week?.start;
  const observed = Date.parse(current.observed_at);
  return view.markers.filter(marker => {
    const confirmed = Date.parse(marker.week.confirmed_at);
    const fetched = marker.week.fetched_at ? Date.parse(marker.week.fetched_at) : NaN;
    return marker.status === "matched" && marker.week.start !== formed && confirmed <= fetched && fetched <= observed;
  });
}

export function nqChannelCopy(channel: NqOversoldChannel): string {
  if (channel.status === "needs_reconnect") return "재연결 필요";
  if (channel.status === "ready") return channel.needs_reconnect ? "수신 가능 · 재연결 예정" : "수신 가능";
  return channel.status === "disabled" ? "채널·알림 종류·중요 등급 중 일부 꺼짐" : "연결 또는 승인 수신처 미확인";
}
