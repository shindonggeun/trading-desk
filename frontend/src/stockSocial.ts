export type StockSocialStatus = "available" | "partial" | "insufficient" | "stale" | "unavailable";

export interface StockSocialScore {
  status: StockSocialStatus;
  score: number | null;
  sample_count: number;
  reason: string | null;
  updated_at: string | null;
}

export interface StockSocialSource {
  id: string;
  status: StockSocialStatus;
  reason: string | null;
  updated_at: string | null;
  prior_http_status: number | null;
}

export interface StockSocialItem {
  kind: "news" | "filing";
  source: string;
  publisher: string;
  title: string;
  summary: string;
  url: string;
  at: string | null;
  filed_date: string | null;
  time_basis: "gdelt_seen" | "edgar_acceptance" | "edgar_filing_date";
  relevance: "direct" | "indirect";
  tags: string[];
  sentiment: "positive" | "negative" | "neutral" | null;
  reason: string | null;
  weight: number | null;
  contribution: number | null;
  stale: boolean;
}

export interface StockSocialResponse {
  ticker: string;
  days: number;
  generated_at: string;
  method: { version: string; baseline: number; min_samples: number; min_publishers: number };
  coverage: { requested_days: number; news_max_days: number };
  news: StockSocialScore & { publisher_count: number };
  community: StockSocialScore;
  sources: StockSocialSource[];
  items: StockSocialItem[];
}

export type LoadStockSocial = (ticker: string, signal?: AbortSignal) => Promise<StockSocialResponse>;
