export type RunStatus = "queued" | "running" | "done" | "failed";
export type Bias = "long" | "short" | "flat" | "review";

export interface Plan {
  rating: string;
  bias: Bias;
  trader_action: string | null;
  entry_price: number | null;
  stop_loss: number | null;
  price_target: number | null;
  reward_risk: number | null;
  time_horizon: string | null;
  position_sizing: string | null;
  executive_summary: string | null;
  /** Where each level came from: the model's call or the ATR fill (backend desk/levels.py). */
  levels_source?: Partial<Record<"entry_price" | "stop_loss" | "price_target", "model" | "atr">>;
  atr?: number;
  last_close?: number;
  levels_error?: string;
  /** The live quote was too far from the close to trust (backend desk/levels.py). */
  levels_warning?: string;
  /** The quote at the end of a same-day analysis (backend desk/runner.py). */
  live_price?: { price: number; source: string; fetched_at: string };
  /** Daily/weekly levels the agents were given (backend desk/sr.py); the card reads its ATR. */
  support_resistance?: { atr?: number };
  /** The entry against that quote (backend desk/levels.py entry_vs_live). */
  entry_vs_live?: {
    live: number;
    gap: number;
    gap_pct: number;
    gap_atr: number | null;
    kind: "now" | "limit" | "stop";
    far: boolean;
    stop_passed: boolean;
    target_passed: boolean;
  };
}

export interface RunPage {
  items: Run[];
  total: number;
  page: number;
  size: number;
}

export interface Run {
  id: string;
  ticker: string;
  trade_date: string;
  status: RunStatus;
  options: {
    analysts: string[];
    use_portfolio: boolean;
    output_language: string | null;
    name?: string | null;
    profile?: string | null;
  };
  plan: Plan | null;
  error: string | null;
  report_path: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DeskEvent {
  run_id: string;
  seq: number;
  ts: string;
  type: "status" | "message" | "tool" | "plan" | "error" | "done";
  agent: string | null;
  content: string | null;
  data: any;
}

export interface Holding {
  symbol: string;
  yahoo_ticker: string;
  name: string;
  market: string;
  currency: string;
  quantity: number;
  average_price: number | null;
  last_price: number | null;
  profit_rate: number | null;
}

export interface RegimeSnapshot {
  enabled: boolean;
  error?: string;
  regimes?: { ts: string; market: string; label: string; score: number; flags: string[] }[];
  triggers?: { ts: string; symbol: string; kind: string; direction: string; level: string }[];
}

export interface DeskConfig {
  analysts: string[];
  provider: string;
  deep_model: string | null;
  quick_model: string | null;
  output_language: string;
  fake_runs: boolean;
  broker: string;
  profiles: { id: string; label: string }[];
  default_profile: string;
}

/** One search hit from /api/instruments: Toss' listing with the Yahoo ticker to analyse. */
export interface Instrument {
  symbol: string;
  ticker: string;
  name: string;
  market: string;
  security_type: string;
}

/** GET /api/estimate (backend desk/estimate.py): 장이 닫힌 동안 바이낸스 주식 무기한 선물로 낸 추정가. */
export type Estimate =
  | { ticker: string; supported: false }
  | {
      ticker: string;
      supported: true;
      estimate: number;
      /** 기준 종가 이후 계약 가격 변화, % */
      change_pct: number;
      last_close: number;
      last_close_date: string;
      close_at: string;
      contract: string;
      market_state: "open" | "closed";
      fetched_at: string;
    };
