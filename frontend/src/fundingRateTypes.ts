export const FUNDING_SYMBOLS = [
  "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "SAMSUNGUSDT",
  "SKHYNIXUSDT", "NVDAUSDT", "AMDUSDT", "MUUSDT", "SOXXUSDT",
] as const;

export type FundingSymbol = (typeof FUNDING_SYMBOLS)[number];
export type FundingDays = 7 | 30;

export interface FundingSettlement {
  settled_at: string;
  rate: number;
  rate_type: "regular" | "special";
  mark_price: number | null;
}

export interface FundingDay {
  /** UTC 기준 날짜 (YYYY-MM-DD). */
  date: string;
  total_rate: number | null;
  settlement_count: number;
  status: "complete" | "missing" | "partial";
  average_7d: number | null;
}

export interface FundingRateResponse {
  symbol: FundingSymbol;
  days: FundingDays;
  underlying: {
    name: string;
    kind: "crypto" | "stock" | "etf";
    ticker: string;
  };
  contract_status: string | null;
  source: "binance";
  fetched_at: string | null;
  status: "available" | "stale" | "no_data" | "unsupported" | "inactive" | "source_error";
  reason: string | null;
  funding_interval_hours: number | null;
  latest_settlement: FundingSettlement | null;
  settlements: FundingSettlement[];
  daily: FundingDay[];
  current_day: (Omit<FundingDay, "status" | "average_7d"> & { status: "partial" }) | null;
  negative_streak_days: number;
  negative_streak_truncated: boolean;
}

export type LoadFundingRate = (symbol: FundingSymbol, days: FundingDays, signal: AbortSignal) => Promise<FundingRateResponse>;
