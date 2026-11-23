export type UsOptionsMetric = "volume" | "oi";

export interface UsOptionsTiming {
  as_of: string | null;
  delay_sec: number | null;
}

export interface UsOptionsTotals extends UsOptionsTiming {
  calls: number | null;
  puts: number | null;
  put_call_ratio: number | null;
  status: "ok" | "zero_calls" | "partial" | "missing";
  trade_date: string | null;
}

export interface UsOptionsStrike {
  strike: number;
  call_volume: number | null;
  put_volume: number | null;
  call_oi: number | null;
  put_oi: number | null;
}

export interface UsOptionsRank {
  strike: number;
  count: number;
}

export interface UsOptionsResponse {
  ticker: string;
  status: "ok" | "unavailable" | "no_options" | "invalid_expiration" | "no_data";
  reason: string | null;
  source: string | null;
  expiration: string | null;
  expirations: string[];
  expirations_truncated: boolean;
  fetched_at: string | null;
  spot: UsOptionsTiming & { price: number | null };
  volume: UsOptionsTotals;
  oi: UsOptionsTotals;
  strikes: UsOptionsStrike[];
  top: Record<UsOptionsMetric, { calls: UsOptionsRank[]; puts: UsOptionsRank[] }>;
  range: { min: number | null; max: number | null; total_strikes: number; matching_strikes: number; limit: number; truncated: boolean };
  excluded_contracts: number;
}

export type LoadUsOptions = (ticker: string, expiration: string | null, signal: AbortSignal) => Promise<UsOptionsResponse>;
