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
  levels_source?: Partial<Record<"entry_price" | "stop_loss" | "price_target", "model" | "atr" | "converted">>;
  /** A leveraged product's levels on its underlying (backend desk/underlying.py); the same keys as the plan. Drawn by #68. */
  underlying?: Plan & { ticker: string; leverage: number; proxy: boolean };
  /** What TradingAgents analysed: the underlying for a leveraged product (#82). */
  analysis_subject?: string;
  /** The leverage analyst failed; the plan is the underlying's call (flipped for inverse) with converted levels. */
  leverage_analyst_error?: string;
  atr?: number;
  last_close?: number;
  levels_error?: string;
  /** The live quote was too far from the close to trust (backend desk/levels.py). */
  levels_warning?: string;
  /** The quote at the end of a same-day analysis (backend desk/runner.py). */
  live_price?: { price: number; source: string; fetched_at: string };
  /** Daily/weekly levels the agents were given (backend desk/sr.py); the card reads its ATR. */
  support_resistance?: Partial<SupportResistance>;
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

/** 지지 · 저항 한 구역(backend desk/sr.py compute). */
export interface SrLevel {
  price: number;
  kind: "support" | "resistance";
  timeframe: "1d" | "1w";
  strength: number;
  /** 근거 — 예: "weekly swing low ×3 · 52w low" */
  basis: string;
}

/** GET /api/levels, 그리고 분석 때 에이전트가 받은 것(plan.support_resistance). */
export interface SupportResistance {
  ticker: string;
  reference_price: number;
  reference: "live" | "close";
  atr: number | null;
  as_of: string;
  levels: SrLevel[];
  computed_at: string;
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

/** One amount in both currencies; null when the USD/KRW rate (or a price) is not known. */
export interface Money {
  krw: number | null;
  usd: number | null;
}

/** GET /api/portfolio(/stream) row (backend desk/portfolio.py). last_price is the live quote. */
export interface PortfolioRow extends Holding {
  value: Money;
  cost: Money;
  pnl: Money;
  pnl_rate: number | null;
  price_source: string;
  price_at: string | null;
}

export interface Portfolio {
  broker: string;
  fx: { usd_krw: number; source: string; at: string } | null;
  total: { krw: number | null; usd: number | null; pnl_krw: number | null; pnl_usd: number | null; pnl_rate: number | null };
  groups: Record<string, PortfolioRow[]>;
  holdings: PortfolioRow[];
  updated_at: string;
  error: string | null;
}

export type FlowMarket = "KOSPI" | "KOSDAQ";
export type FlowInterval = "1d" | "1w" | "1mo" | "1y";
export type Investor = "individual" | "foreigner" | "institution" | "other_corporation";
export type InstitutionKind =
  | "financial_investment" | "insurance" | "trust" | "private_equity_fund" | "bank"
  | "other_financial_institution" | "pension_fund";

/** 원화 정수. net = buy − sell (음수면 순매도). */
export interface FlowSide {
  buy: number;
  sell: number;
  net: number;
}

export interface FlowRecord {
  /** 기간의 대표 일자 YYYY-MM-DD */
  date: string;
  updated_at: string | null;
  /** 기간이 오늘을 포함 — 뒤 거래일이 더 쌓인다 */
  in_progress: boolean;
  /** 오늘 장중 값 — 저녁 확정 전에 바뀔 수 있다 */
  provisional: boolean;
  investors: Partial<Record<Investor, FlowSide>>;
  /** 토스가 빠뜨리면 일부만 온다 */
  institution_breakdown: Partial<Record<InstitutionKind, FlowSide>>;
}

/** GET /api/market/flow (backend desk/market_flow.py, #76) — records 는 최신순. */
export interface MarketFlow {
  market: FlowMarket;
  interval: FlowInterval;
  source: string;
  fetched_at: string;
  supported: boolean;
  index: { price: number; at: string | null } | null;
  records: FlowRecord[];
  error: string | null;
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
