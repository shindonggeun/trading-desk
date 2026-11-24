export type ExtremeFearSymbol = "SOXL" | "KORU";
export type ExtremeFearHorizon = "5" | "20" | "60" | "252";
export type ExtremeFearConditionKey = "rsi" | "cnn" | "vix";
export type ExtremeFearVerdict = "matched" | "not_matched" | "unverified";
export type ExtremeFearStrategyKey = "triple" | "rsi_only" | "buy_hold" | "soxl_to_koru";

export interface ExtremeFearCondition {
  value: number | null;
  operator: "<=" | ">=";
  threshold: number;
  source: string;
  status: ExtremeFearVerdict | "missing" | "delayed";
  available_at: string | null;
  reason: string | null;
}

export interface ExtremeFearObservation {
  value: number;
  source: string | null;
  as_of: string | null;
  status: "unverified";
  reason: string;
}

export interface ExtremeFearMetrics {
  n: number;
  wins: number;
  losses: number;
  flat: number;
  win_rate: number | null;
  win_rate_ci95: [number, number] | null;
  median_return_pct: number | null;
  median_loss_pct: number | null;
  loss_quartiles_pct: [number, number, number] | null;
  worst_return_pct: number | null;
  worst_mae_pct: number | null;
  not_entered: number;
  immature: number;
  missing: number;
  overlap_skipped: number;
}

export interface ExtremeFearStrategy {
  signal_count: number;
  prelisting_sessions: number;
  unverified_sessions: number;
  episodes_suppressed: number;
  horizons: Record<ExtremeFearHorizon, ExtremeFearMetrics>;
  ex_post_252: { n: number; median_max_return_pct: number | null };
}

export type ExtremeFearOutcomeStatus = "complete" | "not_entered" | "immature" | "missing" | "overlap_skipped";

export interface ExtremeFearSignal {
  signal_date: string;
  available_at: string;
  entry_date: string | null;
  outcomes: Record<ExtremeFearHorizon, {
    status: ExtremeFearOutcomeStatus;
    exit_date: string | null;
    return_pct: number | null;
    mae_pct: number | null;
  }>;
  ex_post_252: { status: ExtremeFearOutcomeStatus; max_return_pct: number | null };
}

export interface ExtremeFearResponse {
  symbol: ExtremeFearSymbol;
  as_of: string;
  status: "available" | "unverified";
  reason?: string;
  cost_bps?: number;
  current: {
    date: string | null;
    matched_count: number;
    status: ExtremeFearVerdict;
    conditions: Record<ExtremeFearConditionKey, ExtremeFearCondition>;
  };
  observations: Partial<Record<"cnn" | "vix", ExtremeFearObservation>>;
  signal_history: ExtremeFearSignal[];
  strategies: Partial<Record<ExtremeFearStrategyKey, ExtremeFearStrategy>>;
  dataset: {
    kind: "approved";
    sha256: string;
    price_adjustment: string;
    start: string;
    end: string;
    sources: Record<"calendar" | "prices" | "cnn" | "vix", { name: string; version: string }>;
  } | null;
}

export function extremeFearChartHref(symbol: ExtremeFearSymbol, signalDate?: string): string {
  const params = new URLSearchParams({ interval: "1d" });
  if (signalDate) params.set("signalDate", signalDate);
  return `#/chart/${encodeURIComponent(symbol)}?${params}`;
}
