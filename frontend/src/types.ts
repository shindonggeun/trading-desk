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
  /** #116: 롱(하따) · 숏(단타) 플레이북 — 예전 run 은 없다. 가격은 상품 기준 */
  playbooks?: Playbooks;
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
  /** #221 · #222 한눈에 요약(데스크 계산) — 예전 분석엔 없다 */
  summary?: PlanSummary | null;
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
  /** #273 최근 72시간 · 최신순 · 같은 사건 하나 · age_h = 몇 시간 전 */
  headlines: ({ title: string; url: string; domain: string | null; country: string | null; seen: string | null; age_h?: number } & Partial<NewsRead>)[];
  why: string;
  /** #273 3일 기사도 24시간 기사도 없음 — 화면이 접는다 */
  quiet?: boolean;
  /** #242 테마 한 줄 설명 */
  about_ko?: string | null;
  /** #242 읽은 헤드라인의 평균 긍정/부정 · 가장 큰 영향. 판정이 없으면 null */
  /** #273 line = 한 줄 결론(가장 영향 큰 최신 헤드라인), lean = 증시 쪽 방향 */
  digest?: { sentiment: NewsSentiment; impact: NewsImpact; read: number; line?: string; lean?: MacroLean } | null;
  source: "gdelt" | "google-news";
  fetched_at: string;
  relevance?: 0 | 1 | 2 | null;
  /** 6시간 넘은 값 */
  stale: boolean;
}

/** #242 desk/news_digest — 헤드라인을 모델이 읽은 값(예측이 아님) */
export type NewsImpact = "high" | "mid" | "low";
export interface NewsSentiment { pos: number; neg: number; neu: number }
export interface NewsRead {
  title_ko: string | null;
  sentiment: NewsSentiment | null;
  impact: NewsImpact | null;
  assets: string[];
  why_ko: string | null;
}

/** #244 GET /api/sector-news — 섹터별 기사 급증 이슈 */
export interface SectorIssue {
  id: string;
  label: string;
  sector: string;
  lang: "en" | "ko";
  tickers: string[];
  articles_24h: number;
  spike: number | null;
  tag: string | null;
  tag_ko: string | null;
  story_size: number;
  headlines: ({ title: string; url: string; domain: string | null; seen: string | null } & Partial<NewsRead>)[];
  digest?: { sentiment: NewsSentiment; impact: NewsImpact; read: number } | null;
  stale: boolean;
}
export interface SectorNewsView {
  sectors: { id: string; label: string; issues: SectorIssue[];
    topics: { id: string; label: string; articles_24h: number; spike: number | null; issue: boolean; stale: boolean }[] }[];
  refreshed_at: string | null;
  error: string | null;
  note: string;
}

export type MacroLean = "우호" | "부담" | "중립";

export interface MacroBrief {
  /** #242 헤드라인 판정 회차 */
  digest?: { refreshed_at: string | null; error: string | null; note: string };
  ticker: string | null;
  sources: ("gdelt" | "google-news")[];
  /** 마지막 성공 회차. 기동 직후 첫 회차 전엔 null + themes [] */
  refreshed_at: string | null;
  error: string | null;
  /** #273 헤드라인 창(시간) · 급증 기준 · 응답 시각 */
  window_h?: number;
  spike_basis?: "24h_vs_prior_6d_avg" | "vs_other_themes" | "mixed" | null;
  as_of?: string;
  themes: MacroTheme[];
}

export type HorizonId = "scalp" | "intraday" | "short_swing" | "swing";
export type Factor = "trend" | "location" | "momentum" | "volume" | "catalyst" | "regime";
export type Opinion = "buy" | "sell" | "hold" | "review";

/** desk/outcome.py judge 의 status. */
export type OutcomeStatus =
  | "target" | "stop" | "both" | "expired" | "unfilled" | "invalid" | "unscorable"
  | "open" | "waiting" | "error";

export type RegimeId = "crash" | "downtrend" | "range" | "uptrend" | "unknown";
export type PlaybookSetup = "dip_catch" | "range_low" | "pullback" | "fade" | "breakdown" | "value_swing" | "none";

/** backend desk/playbook.py (#116) — plan.playbooks */
export interface Playbooks {
  version: number;
  computed_at: string;
  frame: {
    primary: string; daily: string; atr: number; atr_daily: number; reference_price: number; fallback: string | null;
    /** #149 실시간 응답에서 하위 봉 트리거를 읽은 봉 간격 */
    trigger_interval?: string;
  };
  regime: {
    id: RegimeId;
    features: Record<string, number | boolean | null | undefined>;
    evidence: { key: string; value: number | boolean | null; threshold: number | null; hit: boolean }[];
    flags: { oversold?: boolean; overextended?: boolean; climax_recent?: boolean };
  };
  bias_note: { side: "long" | "short" | "both" | "none"; text: string; source: "model" | "desk" } | null;
  long: PlaybookSide;
  short: PlaybookSide;
  /** #146 desk/risk_regime.py — 시장 투매 점수(분석 때 또는 분석 없는 실시간 계산). 없으면 판단 안 함 */
  risk_regime?: RiskRegime | null;
  /** #148 주요 경제 발표 앞뒤 창 안이면 그 일정 — 사다리 2 · 3차가 `held: "event"` */
  event_hold?: EconEvent | null;
}

export type RiskPhase = "selling" | "exhausting" | "calm";
/** #146 desk/risk_regime.score */
export interface RiskRegime {
  version: number;
  market: "US" | "KR";
  as_of: string | null;
  score: number;
  /** 최근 10거래일 최고 점수 */
  peak: number;
  phase: RiskPhase;
  label: string;
  parts: { id: string; score: number; text: string }[];
  signals: { id: string; text: string }[];
  missing: string[];
}

/** #148 desk/events_calendar.Event.to_dict */
export interface EconEvent {
  id: string;
  label: string;
  at: string;
  importance: number;
  source: string;
  at_et: string;
}
/** GET /api/events/calendar */
export interface EconCalendar {
  events: EconEvent[];
  blackout: EconEvent | null;
  refreshed_at: string | null;
  error: string | null;
}

/** 플레이북이 정한 진입 트리거(이탈 매도의 지지 이탈 등) */
export interface PlanTrigger { price: number; kind: "stop" | "limit"; text: string }
/** #149 desk/trigger.live — 실시간 플레이북이 하위 봉으로 읽은 진입 트리거(`lower_trigger`, #193) */
export interface LowerTrigger {
  state: "armed" | "triggered" | "invalid";
  price: number | null;
  invalidation: number | null;
  signal: "break" | "vwap_reclaim" | "absorption" | "higher_low" | null;
  signal_at: string | null;
  touched_at: string | null;
  filled_at: string | null;
  rr_t1: number | null;
  /** 구역 지정가 대신 트리거를 기다리라는 데스크 권장 */
  preferred: boolean;
}

export interface PlaybookSide {
  setup: PlaybookSetup;
  reason: string | null;
  size_hint: "half" | "normal";
  flat_by_close: boolean;
  zone: {
    low: number; high: number; mid: number; score: number;
    members: { price: number; source: "sr" | "vp" | "fib" | "trend" | "ma" | "vwap" | "atr"; basis: string; weight: number }[];
  } | null;
  /** held: #148 경제 일정 창이 지날 때까지 보류된 차수 */
  entries: { step: 1 | 2 | 3; price: number; share: number; kind: "limit" | "stop"; after?: string[]; held?: "event" }[];
  /** 플레이북이 정한 진입 트리거(이탈 매도 등). #193 전 서버는 실시간 응답에서 하위 봉 트리거를 여기에 실었다 */
  trigger?: PlanTrigger | LowerTrigger | null;
  /** #146 시장 투매 점수가 걸러 낸 것: selling → 사다리 1차만(held = 뺀 차수), exhausting → 숏을 쉼(setup = 원래 셋업) */
  gate?: { phase: RiskPhase; text: string; held: { step: number; price: number; share: number }[]; setup?: PlaybookSetup } | null;
  /** #147 데스크 실적으로 보정한 T1 도달 확률 · 기대 R · 표본 수(표본 부족이면 확률 · 기대 R 은 null) */
  p_t1?: number | null;
  exp_r?: number | null;
  n?: number;
  calibration?: PlaybookCalibration | null;
  /** #167 과매도 스윙(value_swing) 만: 승부 타점 · 위험 예산 · 근거 묶음 · 바꿔 낸 원래 셋업 */
  conviction?: ValueSwingConviction;
  risk_budget?: { kind: "normal" | "conviction"; account_pct: number; position_pct: number };
  evidence?: Record<string, { met: boolean | null; checks: { id: string; met: boolean | null; value: number | string | boolean | null }[] }>;
  replaced?: PlaybookSetup | null;
  avg_entry: number | null;
  stop: number | null;
  risk: number | null;
  /** t3: #167 과매도 스윙만 — T2 뒤 trail_atr 배 ATR 추적 손절 */
  targets: { role: "t1" | "t2" | "t3"; price: number; basis: string; source: "desk" | "model" | "atr"; rr: number | null; action: "take_half" | "take_rest" | "trail"; then_stop?: number; trail_atr?: number }[];
  time_stop: { bars: number; interval: string; required: boolean; rule: string } | null;
  /** #239 청산 규칙 — 본주 스윙은 T1 뒤 ATR 추적 손절(백테스트 채택), 나머지는 T1 절반 → T2. 옛 계획엔 없다 */
  exit?: { rule: "trail_atr" | "fixed"; text: string; trail_atr?: number; distance?: number | null } | null;
  conditions: { id: string; met: boolean | null; how: "computed" | "watch"; value?: number | string | boolean | null }[];
  score: number;
  source?: Record<string, "desk" | "model" | "converted">;
  model?: { raw?: string; confirm_text?: string; ignored?: Record<string, unknown> } | null;
}

/** #128 GET /api/playbooks — 지금 봉 · 현재가로 다시 계산한 상태 */
export type LivePlaybookState = "waiting" | "near" | "in_zone" | "confirmed" | "invalid";
export interface LivePlaybookSide extends PlaybookSide {
  state: LivePlaybookState | null;
  /** 이 상태가 처음 보인 시각(서버 기준, 최대 캐시 TTL 만큼 늦음) */
  state_since: string | null;
  /** 시세를 못 받아 마지막 종가로 계산 */
  stale: boolean;
  /** #149 하위 봉 트리거 — 구역 근처가 아니거나 하위 봉을 못 받으면 null(#193 에서 trigger 와 나눔) */
  lower_trigger?: LowerTrigger | null;
}
export interface LivePlaybooks extends Omit<Playbooks, "long" | "short"> {
  long: LivePlaybookSide;
  short: LivePlaybookSide;
  ticker: string;
  horizon: HorizonId;
  run: string | null;
  as_of: string;
  live: { price: number; source: string; fetched_at: string } | null;
  /** run: 분석의 레벨 그대로 · fresh: 새로 계산한 레벨(분석의 레벨이 아님) */
  levels_from: "run" | "fresh";
}

export type PlaybookOutcomeStatus =
  | "waiting" | "open" | "t1" | "t2" | "t1_be" | "stop" | "time_stop" | "flat" | "unfilled" | "invalid" | "unscorable" | "error";

export interface PlaybookOutcome {
  status: PlaybookOutcomeStatus;
  setup?: PlaybookSetup;
  took_t1?: boolean;
  /** #277 추적 청산 계획: 남은 절반이 추적 손절로 끝났다(상태는 t1_be) */
  trailed?: boolean;
  /** 보유 중(open)에 추적 손절이 걸려 있음 · 기한까지 추적 중이라 마지막 종가로 매김(상태 t1_be) */
  trailing?: boolean;
  marked?: boolean;
  score?: number;
  detail?: string;
  filled_shares?: number;
  avg_fill?: number | null;
  r?: number | null;
  exited_at?: string | null;
}

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
  /** #116 플레이북 채점 */
  playbooks?: { version: number; regime?: RegimeId; long?: PlaybookOutcome; short?: PlaybookOutcome };
  /** #116: 플레이북 채점 진행 상태 */
  sweep_status?: string;
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

/** backend desk/outcome.py _playbook_stats — hit_rate = (t1 + t2 + t1_be) ÷ 확정(+ stop · time_stop · flat),
 * avg_r 은 확정 건의 체결 비중을 반영한 R */
export interface PlaybookStatsGroup {
  key: string;
  plans: number;
  filled: number;
  t1: number;
  t2: number;
  t1_be: number;
  stop: number;
  time_stop: number;
  flat: number;
  unfilled: number;
  open: number;
  waiting: number;
  hit_rate: number | null;
  avg_r: number | null;
  avg_score: number | null;
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
  /** #116 플레이북 채점 — key "<long|short>:<setup>:<regime>". 예전 서버면 없다 */
  by_playbook?: PlaybookStatsGroup[];
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
  /** #297 삼각수렴 · 쐐기(주 · 보조 주기 각 최대 1개) — 예전 run 은 없다 */
  patterns?: ChartPattern[];
  patterns_error?: string;
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

/** #297 수렴 패턴의 한 선: 첫 터치 → 마지막 봉 */
export interface PatternSide {
  from: TrendPoint;
  to: TrendPoint;
  touches: string[];
  slope_pct_per_bar: number;
}
/** #297 desk.patterns_bt 의 측정 — 검증 구간이 무작위로 섞은 가격과 다른지가 판정이다 */
export interface PatternStats {
  n: number;
  up_pct: number | null;
  down_pct: number | null;
  expected_pct: number | null;
  false_break_pct: number | null;
  move_atr_avg: number | null;
  valid?: { n: number; expected_pct: number | null } | null;
  null?: { n: number; expected_pct: number | null } | null;
  edge_pct?: number | null;
  verdict?: "edge" | "no_edge" | "unstable" | "unmeasured";
}
export interface ChartPattern {
  kind: "symmetric" | "ascending" | "descending" | "rising_wedge" | "falling_wedge";
  label: string;
  timeframe: string;
  state: "converging" | "broke_up" | "broke_down";
  expected: "up" | "down" | "none";
  upper: PatternSide;
  lower: PatternSide;
  apex: { bars_ahead: number; price: number };
  width_pct: number;
  narrowed: number;
  stats: PatternStats | null;
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

/** #160 GET /api/market/futures — 코스피200 근월물(desk/futures_flow.py). 실패 · 키 없음도 200 + supported/error */
export interface FuturesInvestor { net_contracts: number | null; net_value_mn: number | null }
export interface FuturesFlow {
  source: string;
  fetched_at: string;
  front: { code: string; expiry: string; days_left: number };
  market_open: boolean;
  supported: boolean;
  error: string | null;
  price: {
    code: string; name: string; expiry: string; days_left: number;
    price: number | null; change: number | null; change_pct: number | null; volume: number | null;
    open_interest: number | null; open_interest_change: number | null;
    market_basis: number | null; theory_basis: number | null; theory_price: number | null; spot_index: number | null;
    structure: "contango" | "backwardation" | "flat" | null;
  } | null;
  investors: Partial<Record<"foreign" | "individual" | "institution", FuturesInvestor>> | null;
  investors_error: string | null;
  investors_unit: { value: string; qty: string };
  split: {
    spot_net: number | null; spot_date: string | null; futures_net_contracts: number | null;
    split: boolean; kind: "spot_buy_futures_sell" | "spot_sell_futures_buy" | null;
    reason: "spot_not_today" | "small" | "no_data" | null;
  } | null;
}
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

/** #123 시세판 한 줄(GET /api/estimates): 장중 실시간 · 장외 추정 · 추정 불가. */
export type BoardSession = "pre" | "regular" | "post" | "overnight" | "closed";
export type BoardQuoteSource = "kis" | "toss" | "nxt" | "krx-overtime" | "yahoo" | "binance-estimate";
export interface BoardItem {
  ticker: string;
  state: "live" | "estimate" | "unavailable";
  price: number | null;
  change_pct: number | null;
  close: number | null;
  close_date: string | null;
  /** 추정 출처: 그 종목 계약 · 대용(지수 ← ETF) · 레버리지 환산(기초 × 배수) */
  source: { kind: "binance_perp_mark" | "proxy" | "leveraged"; contract: string; basis: string | null; multiple: number } | null;
  /** #250 지금 가격의 출처 — kis · toss · nxt · yahoo · binance-estimate, 추정 불가면 null */
  quote_source?: BoardQuoteSource | string | null;
  /** #250 그 종목 시장의 지금 거래 구간(#238 미국 장외 pre · post · overnight 포함) */
  session?: BoardSession | string;
  /** #283 KRX 시간외(NXT 에 없는 종목) — closing = 시간외 종가(15:40~16:00), single = 시간외 단일가(16:00~18:00) */
  overtime?: "closing" | "single";
  /** #283 그 시간외 체결의 분(단일가는 10분마다) */
  traded_at?: string;
  fetched_at: string | null;
}

/** #123 추정 봉(GET /api/estimate/candles): 마지막 정규장 종가 이후. */
export interface EstimatedCandles {
  ticker: string;
  interval: string;
  close: number;
  close_date: string;
  close_at: string;
  market_state: "open" | "closed";
  method: string;
  basis: string | null;
  multiple: number;
  bars: { at: string; open: number; high: number; low: number; close: number; volume: number; estimated: true }[];
}

/** GET /api/estimate (backend desk/estimate.py): 장이 닫힌 동안 바이낸스 주식 무기한 선물로 낸 추정가. */
export type Estimate =
  | { ticker: string; supported: false }
  | {
      ticker: string;
      supported: true;
      estimate: number;
      /** 정규장 종가(last_close) 대비 추정가 등락, % */
      change_pct: number;
      last_close: number;
      last_close_date: string;
      close_at: string;
      contract: string;
      /** #209 nxt = 한국 종목 NXT 프리 · 애프터마켓 거래 중(estimate 는 NXT 마지막 체결가 그대로) */
      market_state: "open" | "closed" | "nxt";
      fetched_at: string;
      /** #204 계약 움직임을 얹은 기준 — 정규장 종가, 또는 한국 종목의 NXT 애프터마켓 마지막 체결 */
      anchor_source?: "close" | "nxt";
      anchor_price?: number | null;
      anchor_at?: string | null;
      /** 기준 시각 이후 계약 가격 변화, % */
      contract_move_pct?: number | null;
    };

/** #134 즐겨찾기 한 줄(GET /api/favorites) — position 0 이 맨 앞 */
export interface Favorite {
  ticker: string;
  name: string | null;
  position: number;
}

/** #156 섹터 탭(GET /api/sectors) — 계정별, position 0 이 맨 앞. 종목 이름은 없을 수 있다(null). */
export interface Sector {
  id: number;
  name: string;
  position: number;
  tickers: { ticker: string; name: string | null }[];
}

/** #154 공매도 · 옵션 포지션(GET /api/positioning) — 미국 상장 종목만, 나머지는 supported: false */
export interface PositioningWall {
  strike: number;
  oi: number;
  distance_pct: number;
}
export interface Positioning {
  ticker: string;
  supported: boolean;
  reason: string | null;
  short: {
    interest: { pct_float: number | null; days_to_cover: number | null; shares: number | null; change_pct: number | null; as_of: string | null } | null;
    days: number;
    volume_ratio?: number;
    volume_ratio_5d?: number;
    volume_ratio_20d?: number;
    as_of?: string;
    trend?: "rising" | "falling" | "steady";
    history?: { date: string; ratio: number }[];
    crowded: boolean;
  } | null;
  options: {
    spot: number;
    expiries: string[];
    put_call_oi: number | null;
    put_call_volume: number | null;
    near: { expiry: string; put_call_oi: number | null; put_call_volume: number | null };
    call_oi: number;
    put_oi: number;
    call_walls: PositioningWall[];
    put_walls: PositioningWall[];
    max_pain: { expiry: string; strike: number | null; distance_pct: number | null };
    implied_move: {
      expiry?: string; days?: number; iv_pct?: number; move_pct?: number; low?: number; high?: number;
      iv30_pct?: number; move_30d_pct?: number;
    } | null;
    lean: "put_heavy" | "call_heavy" | "balanced" | null;
    source: string;
  } | null;
  verdict: string | null;
  errors: string[] | null;
  sources: string[];
  fetched_at: string;
}

/** #155 빚투 · 예탁금(GET /api/market/credit, 금융투자협회) — 조원 · 억원 */
export interface CreditView {
  summary: {
    date: string;
    credit_trillion: number;
    credit_kospi_trillion: number;
    credit_kosdaq_trillion: number;
    deposits_trillion: number;
    ratio: number;
    ratio_change_1w: number | null;
    ratio_change_1m: number | null;
    credit_change_1w_pct: number | null;
    credit_change_1m_pct: number | null;
    deposits_change_1w_pct: number | null;
    deposits_change_1m_pct: number | null;
    ratio_percentile_1y: number;
    level: "hot" | "normal" | "cold";
    forced_sale_eok: number;
    forced_sale_pct: number | null;
    stress: boolean;
    verdict: string;
  } | null;
  series: { date: string; credit_trillion: number; deposits_trillion: number; ratio: number; forced_sale_eok: number; forced_sale_pct: number | null }[];
  source: string;
  refreshed_at: string | null;
  error: string | null;
}

/** #185 desk.backtest.aggregate — 한 묶음의 백테스트 성적 */
export interface BacktestAgg {
  plans: number;
  filled: number;
  settled: number;
  t2: number;
  t1_be: number;
  stop: number;
  time_stop: number;
  flat: number;
  open: number;
  unfilled: number;
  fill_rate: number | null;
  /** 체결된 것 중 손절보다 T1 에 먼저 닿은 비율 */
  t1_first_rate: number | null;
  median_r: number | null;
  /** 체결된 건당 비용 차감 기대 R */
  expectancy_r: number | null;
  /** 계획당(미체결 포함) 기대 R */
  expectancy_per_plan_r: number | null;
  mae_atr_median: number | null;
  mfe_atr_median: number | null;
}
/** GET /api/backtest/reports — 보기별 스냅샷 요약 */
export interface BacktestSummary {
  horizon: HorizonId;
  label: string;
  generated_at: string | null;
  start: string | null;
  end: string | null;
  tickers: string[];
  plans: number | null;
  filled: number | null;
  expectancy_r: number | null;
}
/** GET /api/backtest/reports/{horizon} — desk.backtest.Report */
export interface BacktestReport {
  version: number;
  horizon: HorizonId;
  tickers: string[];
  start: string | null;
  end: string | null;
  generated_at: string | null;
  costs: { bps_per_side: number };
  baseline: BacktestAgg;
  by_setup: Record<string, BacktestAgg>;
  by_regime: Record<string, BacktestAgg>;
  by_steps: Record<string, BacktestAgg>;
  by_ticker: Record<string, BacktestAgg>;
  by_direction?: Record<string, BacktestAgg>;
  by_risk?: Record<string, BacktestAgg>;
  crash_windows: Record<string, BacktestAgg>;
  split: { cut: string | null; embargo_days: number; train: BacktestAgg; valid: BacktestAgg };
  /** #146 규칙 켬 / 끔 — 키마다 묶음 성적, losing_streak 만 숫자 */
  risk_gate?: { on: Record<string, BacktestAgg | number>; off: Record<string, BacktestAgg | number> };
  /** #192 계획일 기준 누적 R(비용 차감) — 전체 · 방향:셋업별, 최대 400개 날짜로 줄임. max_dd 는 모든 날짜로 잰 최대 낙폭(R) */
  equity?: {
    basis: "plan_day";
    days: string[];
    series: Record<string, number[]>;
    max_dd: Record<string, number>;
    trades: Record<string, number>;
  };
  notes: string[];
}

/** #147 desk/calibration.Table.look — 셋업 · 국면 · 점수 구간의 데스크 실적(백테스트 + 운영 채점) */
export interface PlaybookCalibration {
  p_t1: number | null;
  exp_r: number | null;
  n: number;
  /** 어느 묶음에서 읽었나: 점수 구간 → 셋업 · 국면 → 셋업 전체(앞이 얇으면 뒤로) */
  level: "band" | "regime" | "setup" | null;
  /** 표본이 모자라 확률 · 기대 R 을 내지 않음 */
  thin: boolean;
  /** T1 확률의 90% 구간 */
  p_range: [number, number] | null;
  /** 그중 운영 채점 건수 */
  live_n: number;
  /** 표본이 충분한데 기대 R 90% 상한까지 음수 */
  negative: boolean;
}

/** #167 desk/value_swing.conviction — 0~100, 묶음 4개 이상 + 데스크 실적 양수면 승부(active) */
export interface ValueSwingConviction {
  score: number;
  active: boolean;
  /** 묶음별 충족 — oversold · depth · base · fear · flows · record(데스크 실적) */
  checklist: { id: string; met: boolean | null }[];
  /** 승부를 풀 조건 */
  release: string[];
}


/** #202 desk/weekly_map.build — 지수 하나의 주간 시장 지도(그 주까지의 주봉만으로 계산) */
export interface WeeklyMap {
  version: number;
  symbol: string;
  name: string;
  /** 야후에 그 지수가 없어 대신 쓴 심볼(코스피200 → 069500.KS, 나스닥 선물 → ^NDX) */
  source_symbol?: string;
  week: string;
  week_complete: boolean;
  close: number;
  lines: { black: WeeklyLine; red: WeeklyLine };
  position: "above_black" | "between" | "below_red";
  cross: "broke_black" | "reclaimed_black" | null;
  bollinger: { weeks: number; upper: number; middle: number; lower: number; lower_pct: number | null; upper_pct: number | null };
  range_52w: { high: number; low: number; from_high_pct: number | null };
  fibonacci: { ratio: number; price: number }[];
  supports: number[];
  resistances: number[];
  candle: { kind: "long_black" | "long_white" | "lower_wick" | "upper_wick" | "plain"; label: string; change_pct: number | null };
  scenario: { down: { price: number; pct: number | null }; up: { price: number; pct: number | null } };
  record: {
    black_break: { n: number; reached_red: number; rate: number | null; median_weeks: number | null; v_exceptions: string[]; pending: number; within_weeks: number };
    bb_lower_touch: { n: number; rebounded: number; rate: number | null; lower_after: string[]; pending: number; after_weeks: number };
  };
  text: { headline: string; lines: string[]; glossary: { term: string; text: string }[]; checklist: string[] };
}
export interface WeeklyLine { weeks: number; price: number; pct: number | null }
/** 지도를 못 만든 지수 — 그 카드만 "불러오지 못함" */
export interface WeeklyMapError { symbol: string; name: string; error: string }
/** GET /api/market/weekly/{symbol}/history — 지난 N주의 지도 요약, 최근 주부터 */
export interface WeeklyHistoryRow {
  week: string;
  week_complete: boolean;
  close: number;
  lines: WeeklyMap["lines"];
  position: WeeklyMap["position"];
  cross: WeeklyMap["cross"];
  candle: WeeklyMap["candle"];
  scenario: WeeklyMap["scenario"];
  headline: string;
}

/** #221 GET plan.summary — 결론 · 분할 매수 · 한 번에 매수 최적 타점 · 손절 · 목표 */
// #231 watch: 계획은 관망 — 데스크의 더 나은 쪽을 "들어간다면" 으로만
export type SummaryVerdict = "buy_now" | "wait_for_zone" | "short_now" | "wait_short" | "watch" | "stand_aside";
export interface PlanSummary {
  version: number;
  verdict: SummaryVerdict;
  text: string;
  why?: string | null;
  side: "long" | "short" | null;
  setup?: string;
  price?: number | null;
  rating?: string | null;
  valid_until?: string | null;
  zone?: { low: number | null; high: number | null };
  ladder?: { step: number; price: number; share: number | null; kind?: string; when: string }[];
  avg_entry?: number | null;
  single_entry?: { price: number; rr: number; weight: number; why: string[]; p_t1: number | null; thin: boolean; note?: string } | null;
  stop?: number | null;
  stop_pct?: number | null;
  targets?: { role: string; price: number; rr: number | null }[];
  distance_pct?: number | null;
  record?: { p_t1: number | null; exp_r: number | null; n: number | null; thin: boolean; negative: boolean } | null;
  risk_pct?: number | null;
  position_pct?: number | null;
  conviction?: boolean;
  time_stop?: { bars: number; interval: string } | null;
  notes?: string[];
}

/** #240/#241 장기 바닥권(인생 타점) — GET /api/bottom-zone */
export type BottomStage = "none" | "watch" | "approach" | "bottom";
export interface BottomEvidence {
  id: "drawdown" | "weekly_rsi" | "monthly_rsi" | "divergence" | "long_ma" | "support" | "capitulation";
  met: boolean | null;
  value: unknown;
  threshold: string;
  weight: number;
}
export interface BottomStep {
  step: number;
  price: number;
  weight_pct: number;
  from_close_pct: number;
}
export interface BottomZone {
  ticker: string;
  supported: boolean;
  error?: string;
  as_of?: string;
  close?: number;
  score?: number;
  stage?: BottomStage;
  stage_ko?: string;
  drawdown_pct?: number;
  high?: number;
  evidence?: BottomEvidence[];
  ladder?: BottomStep[];
  invalidation?: { monthly_close_below: number | null; text: string };
  targets?: { id: string; price: number | null; text: string }[];
  leveraged_note?: string | null;
}

/** #254 선물 수급 기록 한 점(스냅샷). 투자자 값은 그날 누적 순매수. */
export interface FuturesPoint {
  at: string;
  date: string;
  code: string;
  price: number | null;
  open_interest: number | null;
  market_basis: number | null;
  investors: Record<"foreign" | "individual" | "institution", FuturesInvestor>;
}
export interface FuturesDaily { supported: boolean; interval_min: number; days: FuturesPoint[] }
export interface FuturesIntraday { supported: boolean; date: string; interval_min: number; market_open: boolean; points: FuturesPoint[] }

/** #243 GET /api/calendar — 한 달의 국장 · 미장 일정(시각은 KST) */
export interface CalEvent {
  id: string;
  label: string;
  market: "kr" | "us";
  kind: "release" | "expiry";
  /** 2 = 시장 전체를 움직임(상), 1 = 눈여겨볼 것(중) */
  importance: number;
  source: string;
  /** 그 시장의 날짜(미국 일정은 미국 날짜) */
  date: string;
  next_day_kst: boolean;
  at: string;
  at_kst: string;
  time_kst: string;
  time_local: string;
}
export interface CalEarning {
  ticker: string;
  name: string | null;
  date: string;
  market: "kr" | "us";
  when: "pre" | "after" | "unknown";
  eps_estimate: number | null;
  market_cap: number | null;
  source: string;
  watched: boolean;
}
export interface CalDay {
  date: string;
  weekday: string;
  kr: { open: boolean; holiday: string | null };
  us: { open: boolean; holiday: string | null; early_close: string | null; close_kst: string | null };
  events: CalEvent[];
  earnings: CalEarning[];
}
export interface MarketMonth {
  month: string;
  days: CalDay[];
  sources: Record<"economic" | "earnings", { refreshed_at: string | null; error: string | null }>;
  notes: string[];
}

/** #271 바이낸스 선물 롱/숏 비율(공개 통계, 계약당 5분 갱신) */
export interface LongShortSplit {
  long_pct: number;
  short_pct: number;
  ratio: number | null;
  at: string | null;
}
export interface LongShortItem {
  ticker: string;
  contract: string | null;
  /** 자기 계약이 아니라 프록시 · 레버리지 기초자산 계약을 쓸 때 그 종목 */
  basis?: string | null;
  multiple?: number;
  accounts?: LongShortSplit | null;
  top_positions?: LongShortSplit | null;
  taker?: { buy_sell_ratio: number | null; buy_pct_1h: number | null; at: string | null } | null;
  change_24h?: { long_pct: number | null };
  source?: "binance";
  error?: string;
}

/** #299 차트 예상도(GET /api/projection) — 확률 범위 · 시나리오이지 예측이 아니다 */
export interface ProjectionPoint { bar: number; price: number; why: string }
export interface ProjectionPath { name: "bull" | "bear" | "range"; label: string; prob: number; points: ProjectionPoint[] }
export interface ProjectionCone { bar: number; p50: number; p16: number; p84: number; p2_5: number; p97_5: number }
export interface Projection {
  ticker: string;
  horizon: string;
  timeframe: string;
  steps: number;
  price: number | null;
  sigma: number | null;
  cone: ProjectionCone[];
  paths: ProjectionPath[];
  note_ko: string;
  note_source: "ai" | "desk";
  note_pending: boolean;
  fallback?: { from: string; reason: string } | null;
  calibration: { coverage68_valid?: number; coverage95_valid?: number; n?: number } | null;
}

/** #293 GET /api/earnings/preview — 실적 프리뷰(무료 출처만). 값은 모두 선택값이다. */
export interface EarningsEstimate { avg: number | null; low: number | null; high: number | null; analysts: number | null; year_ago: number | null; growth_pct: number | null; currency: string | null }
export interface EarningsHistoryRow { at: string; date: string; when: "pre" | "after" | "during" | null; estimate: number | null; actual: number | null; surprise_pct: number | null; beat: boolean | null }
export interface EarningsPreview {
  ticker: string;
  next: { date: string; when: "pre" | "after" | "during" | null; estimate: number | null; time_kst: string | null; days: number } | null;
  eps: Record<string, EarningsEstimate> | null;
  revenue: Record<string, EarningsEstimate> | null;
  history: EarningsHistoryRow[];
  implied_move: { expiry: string; strike: number; call: number; put: number; move_pct: number; days_after_report?: number | null } | null;
  past_moves: { moves: { date: string; session: string; move_pct: number }[]; avg_abs_pct: number; up: number; n: number } | null;
  targets: { mean: number | null; median: number | null; high: number | null; low: number | null; current: number | null; upside_pct: number | null } | null;
  ratings: { strong_buy: number; buy: number; hold: number; sell: number; strong_sell: number; total: number; buy_pct: number } | null;
  short_interest: Record<string, unknown> | null;
  kr_consensus: { unit: string; quarter?: Record<string, unknown>[]; annual?: Record<string, unknown>[] } | null;
  sources: Record<string, { source: string; at: string }>;
  unavailable: { id: string; label: string; why: string }[];
  errors: string[];
  fetched_at: string;
  note: string;
  checkpoints?: { title_ko: string; why_ko: string | null; sentiment: string | null; impact: string | null; url: string; seen: string | null }[];
}
