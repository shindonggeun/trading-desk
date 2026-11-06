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
  /** #109: a move against a leveraged product is traded by buying its pair (SOXL → SOXS), in the pair's prices. */
  pair?: PairTrade;
  /** #109: where someone already holding the product reduces or exits, in the product's price. */
  holder_exit?: number | null;
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
  /** #63 매매 시간축(desk/horizons.py). 없으면(예전 run) 스윙. */
  horizon?: HorizonId;
  /** 진입이 유효한 마지막 시각(ISO). 스윙은 null. */
  valid_until?: string | null;
  /** 시간축 mandate 의 Entry 줄(#63) — 스윙엔 없다. */
  entry_type?: "now" | "limit" | "stop" | null;
  invalidation?: string | null;
  exit_by?: string | null;
  /** #64 판단 체크리스트 · 고확률 필터(desk/verdict.py). 예전 run 엔 없다. */
  win_probability?: number | null;
  checklist?: Partial<Record<Factor, "long" | "short" | "neutral">> | null;
  score?: { long: number; short: number } | null;
  long_trigger?: number | null;
  short_trigger?: number | null;
  opinion?: Opinion;
  /** #94 결론 정리(desk/consensus.py): 에이전트별 투표 · 양쪽 관점 · 토론 요지. 예전 run · 실패면 없다. */
  consensus?: Consensus;
  consensus_error?: string;
  /** #106 매매 지도(desk/trade_map.py): 진입 · 1차 / 2차 목표 · 지지 · 손절 — 숫자는 계산값. 예전 run 엔 없다. */
  trade_map?: TradeMap | null;
  filter?: {
    passed: boolean | null;
    reasons: string[];
    thresholds: { win_probability: number; reward_risk: number; agreeing: number };
  };
}

export type Stance = "long" | "short" | "hold";

export interface Consensus {
  /** 투표가 가리키는 종목 — 레버리지 상품이면 본주(SOXL → SOXX). */
  subject: string;
  /** fixed: 리서치 매니저 · 트레이더 · 포트폴리오 매니저처럼 등급 라벨에서 읽은 표. */
  votes: { agent: string; stance: Stance; reason: string; fixed: boolean }[];
  tally: Record<Stance, number>;
  /** 표가 가장 많은 쪽, 동률이면 null. */
  majority: Stance | null;
  long_case: string[];
  short_case: string[];
  debate: string;
  conclusion: string;
  /** 이미 보유한 사람에게: 버티기 · 약손절 · 손절. 레벨은 계획 · 지지 · 저항 중에서만 고른다. */
  holder?: {
    action: "hold" | "trim" | "exit";
    cut_level: number | null;
    trim_level: number | null;
    reason: string;
    /** "내 보유 종목 반영" 으로 돌렸고 이 종목을 갖고 있을 때만. */
    position: { quantity: number; average_price: number | null; pnl_pct: number | null } | null;
  } | null;
}

export interface VolumeProfile {
  interval: string | null;
  bars: number;
  low: number;
  high: number;
  bin_width: number;
  bins: { price: number; volume: number; share: number }[];
  /** 가장 많이 거래된 가격 */
  poc: number;
  /** 거래량 70% 가 몰린 구간 */
  value_area: { low: number; high: number };
  nodes: { price: number; kind: "thick" | "thin"; share: number }[];
}

export type TradeRole = "target2" | "target1" | "entry" | "guard1" | "guard2" | "stop";

export interface TradeStep {
  role: TradeRole;
  price: number;
  /** 대표 근거: 계획 값 · 지지 / 저항 구역 · 매물대 · ATR 계산 · (관망) 진입 조건 */
  source: "plan" | "sr" | "volume" | "atr" | "trigger";
  sources: string[];
  strength: number | null;
  basis: string | null;
  volume: "thick" | "thin" | null;
  action: "enter" | "take_partial" | "take_rest" | "hold_check" | "reduce" | "exit";
  share?: number;
  /** 1차 목표가 두꺼운 벽(강도 높음 · 매물대 두꺼움) — 더 많이 던다 */
  heavy?: boolean;
  /** 1차와 2차 사이가 얇아 빨리 지나갈 수 있다 */
  gap_thin?: boolean | null;
  then_stop?: number;
  rr?: number | null;
  entry_type?: "now" | "limit" | "stop" | null;
}

export interface TradeMap {
  /** ignored: 방향과 반대편에 있어 지도에서 뺀 계획 값(롱인데 손절이 진입 위 등) */
  maps: { side: "long" | "short"; origin: "plan" | "trigger"; steps: TradeStep[]; ignored?: { stop_loss?: number; price_target?: number } }[];
  reference_price: number | null;
  atr: number;
  volume_profile: boolean;
  levels: boolean;
}

/** #115 거시 · 정책 뉴스 흐름(backend desk/macro_news.py, GET /api/macro/brief). */
export type MacroThemeId = "us_politics" | "fed" | "tariffs" | "ai_capex" | "chips" | "china" | "korea" | "geopolitics";

export interface MacroTheme {
  id: MacroThemeId;
  label: string;
  articles_24h: number;
  /** RSS 100건이 꽉 찼다 — "100+" 로 */
  saturated?: boolean;
  spike: number | null;
  /** week = 평소 주간 대비(GDELT), themes = 다른 테마 대비(RSS) */
  spike_kind: "week" | "themes";
  /** -10 .. +10 */
  tone: number | null;
  /** headline-words = 헤드라인 단어로 센 대략값 */
  tone_kind: "gdelt" | "headline-words";
  headlines: { title: string; url: string; domain: string | null; country: string | null; seen: string | null }[];
  why: string;
  source: "gdelt" | "google-news";
  fetched_at: string;
  relevance?: 0 | 1 | 2 | null;
  /** 6시간 넘은 값 */
  stale: boolean;
}

export interface MacroBrief {
  ticker: string | null;
  sources: ("gdelt" | "google-news")[];
  /** 마지막 성공 회차. 기동 직후 첫 회차 전엔 null + themes [] */
  refreshed_at: string | null;
  error: string | null;
  themes: MacroTheme[];
}

export type HorizonId = "scalp" | "intraday" | "short_swing" | "swing";
export type Factor = "trend" | "location" | "momentum" | "volume" | "catalyst" | "regime";
export type Opinion = "buy" | "sell" | "hold" | "review";

/** desk/outcome.py judge 의 status. */
export type OutcomeStatus =
  | "target" | "stop" | "both" | "expired" | "unfilled" | "invalid" | "unscorable"
  | "open" | "waiting" | "error";

export interface Outcome {
  status: OutcomeStatus;
  detail?: string;
  entry_kind?: "now" | "limit" | "stop";
  filled_at?: string;
  exited_at?: string;
  /** 손절폭 대비 손익(R). 목표 = +손익비, 손절 = −1 근처 */
  r?: number | null;
  late?: boolean;
  partial_first_session?: boolean;
}

/** GET /api/stats/plans 의 한 묶음(desk/outcome.py _group). */
export interface StatsGroup {
  key?: string;
  plans: number;
  filled: number;
  target: number;
  stop: number;
  both: number;
  expired: number;
  unfilled: number;
  open: number;
  waiting: number;
  hit_rate: number | null;
  avg_probability: number | null;
  avg_probability_resolved: number | null;
  avg_r_at_expiry: number | null;
}

export interface PlanStats {
  days: number;
  from: string;
  to: string;
  thresholds: { win_probability: number; agreeing: number };
  rules_version: number;
  total: { plans: number; scored: number; waiting: number; errors: number; unscorable: number };
  by_horizon: (StatsGroup & { key: string })[];
  by_rating: (StatsGroup & { key: string })[];
  by_direction: (StatsGroup & { key: string })[];
  by_opinion: (StatsGroup & { key: string })[];
  by_analysts: (StatsGroup & { key: string })[];
  calibration: { bucket: string; plans: number; resolved: number; hits: number; hit_rate: number | null; avg_probability: number | null }[];
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

/** backend desk/leverage_analyst.parse_pair — always a buy of the pair (stop < entry < target). */
export interface PairTrade {
  ticker: string;
  leverage: number | null;
  entry_price: number;
  stop_loss: number;
  price_target: number;
  reward_risk: number | null;
  /** the pair's price the levels were checked against: live, or the last close up to the analysis date */
  price: number | null;
  price_source?: string | null;
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
  /** 레벨을 계산한 봉 주기(#63 시간축) */
  intervals?: { primary: string; secondary: string };
  /** 추세선(#73) — 옛 분석 · 계산 실패면 없다 */
  trendlines?: Trendline[];
  fibonacci?: Fibonacci | null;
  /** #106 매물대(desk/volume_profile.py) — 거래량 없는 지수 · 예전 run 은 없다 */
  volume_profile?: VolumeProfile | null;
  /** 매물대 계산이 실패한 이유(있으면 volume_profile 은 null) */
  volume_profile_error?: string;
  trend_error?: string;
}

export interface TrendPoint {
  at: string;
  price: number;
}

/** 로그 가격 × 봉 순서에서 직선(backend desk/trend.py). */
export interface Trendline {
  kind: "support" | "resistance";
  /** 그 선을 그은 봉 주기(1d · 1w · 5m …) */
  timeframe: string;
  anchors: TrendPoint[];
  /** 선의 지금 위치 */
  end: TrendPoint;
  touches: string[];
  slope_pct_per_bar: number;
  price_now: number;
  /** 지금 가격 − 선, 주 시간축 ATR 단위 */
  distance_atr: number;
  state: "intact" | "testing" | "broken";
}

export interface FibLevel {
  ratio: number;
  price: number;
  /** 겹치는 수평 지지 · 저항 구간 */
  confluence?: { price: number; strength: number; basis: string };
}

export interface Fibonacci {
  direction: "up" | "down";
  from: TrendPoint;
  to: TrendPoint;
  levels: FibLevel[];
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
    horizon?: HorizonId | null;
  };
  plan: Plan | null;
  error: string | null;
  report_path: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  /** #64 채점(서버가 1시간마다). 아직 안 봤으면 null. */
  outcome?: Outcome | null;
  outcome_status?: OutcomeStatus | null;
  scored_at?: string | null;
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
  horizons?: { id: HorizonId; label: string; holding: string }[];
  default_horizon?: HorizonId;
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
