export type RunStatus = "queued" | "running" | "done" | "failed";
export type Bias = "long" | "short" | "flat" | "review";

export interface Plan {
  rating: string;
  bias: Bias;
  trader_action: string | null;
  entry_price: number | null;
  stop_loss: number | null;
  price_target: number | null;
  /** #311 레벨 모양으로 본 방향: 손절 < 진입 < 목표 = long, 목표 < 진입 < 손절 = short, 모순 · 누락 = null(옛 run 도 읽을 때 계산) */
  levels_side?: "long" | "short" | null;
  /** #311 그 방향의 근거 한 줄(예 "트레이더 매도안의 숫자(숏 관점)"), 레벨이 없으면 null */
  levels_note?: string | null;
  /** #311 등급 bias(Hold = flat 포함)와 levels_side 가 다르다 */
  side_conflict?: boolean;
  /** #311 트리거가 이미 충족됐거나 롱 · 숏 기준이 엇갈릴 때의 한국어 경고 */
  trigger_warnings?: string[];
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
  /** #652 롱 · 숏마다 돌파 확인 진입과 지지(저항) 근처 진입 — 손절은 진입 근거가 깨지는 곳(최대 3 ATR) */
  scenarios?: TradeScenario[];
  regime?: string | null;
  /** 이 레짐에서 과거 어느 진입이 나았나 한 줄(표본 · 나쁘면 그대로) */
  regime_note?: string | null;
  /** #695 지도를 거스르는 읽기: 주봉 민스키형 '진입 조심' 단계, 방향을 거스르는 헤드앤숄더 */
  cautions?: { kind: string; timeframe?: string; label: string; text: string; stage?: string; state?: string; reference_only: boolean }[];
}

export interface TradeScenarioRecord {
  n: number;
  /** #671 겹치지 않는 거래 수(같은 종목 · 방향 · 진입에서 보유 기간이 겹치지 않는 것) — 없으면 n */
  n_indep?: number | null;
  win_rate: number;
  avg_r: number;
  median_r?: number;
  max_dd_r?: number;
}

export interface TradeScenario {
  side: "long" | "short";
  kind: "breakout" | "support";
  entry: number;
  entry_type: "now" | "limit" | "stop";
  stop: number;
  stop_atr: number;
  stop_pct: number;
  targets: { price: number; rr: number; basis: string | null }[];
  rr: number | null;
  low_rr: boolean;
  distance_pct: number | null;
  level: number | null;
  basis: string | null;
  strength?: number | null;
  record?: TradeScenarioRecord | null;
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
  /** how "edge": #285 학습 · 검증 모두 우위가 확인된 차트 조건(남은 스윙 롱), label 이 이름표 */
  conditions: { id: string; met: boolean | null; how: "computed" | "watch" | "edge"; value?: number | string | boolean | null; label?: string }[];
  /** #285 근거가 약해 걸러진 셋업(이때 setup 은 "none", reason 은 filtered.reason 과 같은 문장) */
  filtered?: {
    setup: PlaybookSetup;
    rule: string;
    reason: string;
    values?: { rel20?: number | null; ret20?: number | null; vol_ratio?: number | null; slope20?: number | null };
  } | null;
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
  /** #321 구역 하단 · 상단(스윙 꼬리 · 몸통) — 예전 run 은 없다 */
  zone?: [number, number];
  /** 과거 방문 수 · 그중 지켜진 수 · 비율 */
  touches?: number;
  held?: number;
  hold_rate?: number | null;
  flipped?: "resistance_to_support" | "support_to_resistance" | null;
  /** 지지마다 저가매수 구간(손절은 구역 하단 밖) */
  buy_zone?: BuyZone;
}

/** #321 desk/sr_zones.buy_zone */
export interface BuyZone {
  lo: number;
  hi: number;
  stop: number;
  target: number | null;
  rr: number | null;
  why: string[];
  hold_rate: number | null;
  n: number;
  ok: boolean;
  note: string | null;
}

/** #321 직전 저점(고점)을 깨고 구역에 들어왔다가 되돌아 마감한 봉 */
export interface SrEvent {
  kind: "sweep_reclaim";
  at: string;
  level: number;
  side: "support" | "resistance";
  low?: number;
  high?: number;
  close: number;
  through: boolean;
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
  /** #321 휩소(스윕 후 회복) — 예전 run 은 없다 */
  events?: SrEvent[];
  computed_at: string;
  /** 레벨을 계산한 봉 주기(#63 시간축) */
  intervals?: { primary: string; secondary: string };
  /** 추세선(#73) — 옛 분석 · 계산 실패면 없다 */
  trendlines?: Trendline[];
  fibonacci?: Fibonacci | null;
  /** #106 매물대(desk/volume_profile.py) — 거래량 없는 지수 · 예전 run 은 없다 */
  volume_profile?: VolumeProfile | null;
  /** #465 안 메운 갭(일봉 · 주봉, 가까운 순) — 예전 서버 · 저장된 run 레벨에는 없다 */
  gaps?: PriceGap[];
  gaps_error?: string | null;
  /** #465 계획 가격선마다 시나리오 · 근거 — run= 으로 부를 때만 */
  basis?: LineBasis[];
  /** #465 초보자용 해설 문장: 위 구역 → 지금 구역 → 아래 구역 → (가까운 갭) → 계획선마다 */
  explain?: string[];
  /** 매물대 계산이 실패한 이유(있으면 volume_profile 은 null) */
  volume_profile_error?: string;
  trend_error?: string;
  /** #297 삼각수렴 · 쐐기(주 · 보조 주기 각 최대 1개) — 예전 run 은 없다 */
  patterns?: ChartPattern[];
  /** #695 헤드앤숄더 · 역헤드앤숄더(주기마다 최대 1개씩) */
  hs?: HsPattern[];
  /** #695 주봉 민스키형 버블 단계(주봉이 있는 분석만) */
  bubble?: BubbleStage | null;
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
  // ── #530 여러 스윙의 선 · 선마다 시나리오(옛 run · 옛 서버에는 없다 — 모두 선택) ──
  /** 안정적인 키 `${timeframe}-${kind}-${anchor0 날짜}-${anchor1 날짜}` */
  id?: string;
  /** 스윙 크기: 짧은(k) · 중간(2k) · 긴(4k) */
  scale?: "short" | "mid" | "long";
  swing_k?: number;
  /** testing · broken 은 state 와 같고, intact 를 |distance_atr| 3배 안 near / 밖 far 로 나눈 것(#530 보완) */
  status?: "broken" | "testing" | "near" | "far";
  /** 선 − 지금가, 지금가 대비 %. + 면 선이 위(저항선이면 닿는 데 필요한 상승률) */
  distance_pct?: number;
  /** 그 선의 봉으로 N봉 뒤 선 값 · 그때 지금가 대비 % */
  ahead?: { bars: number; price: number; distance_pct: number }[];
  /** 지금 역할로 2개: 저항 역할 breakout · reject / 지지 역할 bounce · breakdown — 깨진 선은 역할이 뒤집힌다(깨진 지지선 → breakout · reject) */
  scenarios?: TrendScenario[];
  /** 쉬운 한국어 2~3줄(숫자는 모두 계산값) */
  summary?: string[];
}

/** #530 추세선 하나의 시나리오 — 숫자는 서버 계산값(LLM 0) */
export interface TrendScenario {
  kind: "breakout" | "reject" | "bounce" | "breakdown";
  /** 조건 가격 — breakout · breakdown 은 선 ± 0.3 ATR, reject · bounce 는 선 값(#530 보완) */
  trigger: number;
  /** 돌파 · 이탈 때 필요한 거래량(20봉 평균). 거래량 없는 지수 · 막힘/받침은 null */
  volume_min: number | null;
  target: number | null;
  target_basis: string | null;
  /** 시나리오가 틀렸다고 보는 종가 */
  invalid: number | null;
  text: string;
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
/** #695 헤드앤숄더(천장) · 역헤드앤숄더(바닥) */
export interface HsPattern {
  kind: "head_shoulders" | "inverse_head_shoulders";
  label: string;
  timeframe: string;
  state: "forming" | "broken" | "retest" | "failed";
  state_label: string;
  expected: "up" | "down";
  points: Record<"left_shoulder" | "head" | "right_shoulder" | "neck_left" | "neck_right", { at: string; price: number }>;
  neckline: { from: { at: string; price: number }; to: { at: string; price: number }; slope_pct_per_bar: number };
  broke_at: string | null;
  target: number;
  height_pct: number;
  volume_right_lower: boolean | null;
  reference_only: boolean;
  reference_label: string | null;
  explain: string;
}

/** #695 주봉 민스키형 단계 — 숫자로만 판정, 과거 같은 단계 뒤 결과와 함께 */
export interface BubbleStage {
  stage: "quiet" | "awareness" | "mania" | "first_drop" | "bull_trap" | "fear" | "none";
  label: string;
  caution: boolean;
  numbers: Record<string, number | boolean | null>;
  reference_only: boolean;
  reference_label: string | null;
  explain: string;
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
  /** #629 꼭짓점 봉 시각(UTC ISO) — 봉이 모자라면 null */
  apex_at?: string | null;
  /** #629 흔히 방향이 나오는 구간(마지막 봉 뒤 bars_ahead 칸) — passed 면 이미 지났다 */
  breakout_window?: { from: string; to: string; bars_ahead: [number, number]; share: [number, number];
    basis: "measured" | "textbook"; passed: boolean } | null;
  /** #629 위 · 아래로 깼을 때의 목표, reach_pct 는 과거 도달 비율. #662 basis: measured_move(패턴 높이) ·
   *  recent_swing(시작 높이가 너무 넓어 최근 폭으로 — reach_pct null) · omitted(목표 없음 — up/down null, omitted 에 이유) */
  targets?: { up: number | null; down: number | null; height_pct: number | null; reach_pct: number | null;
    basis: "measured_move" | "recent_swing" | "omitted" | string; omitted?: string | null } | null;
  /** #629 어느 쪽 확률 — small_sample(n<30)이면 "표본 적음", verdict 가 edge 가 아니면 방향 근거 약함 */
  odds?: { up_pct: number; down_pct: number; none_pct: number | null; n: number; prior: unknown; small_sample: boolean;
    false_break_pct: number | null; verdict: string; edge_pct: number | null } | null;
  /** #629 쉬운 말 한 줄(끝이 "예측 아님.") */
  explain?: string;
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
  /** #437 run 이 끝날 때 고정한 1주 · 1개월 방향 전망 — 옛 run · 만들지 못한 run 은 null */
  outlook?: RunOutlook | null;
}

/** #437 방향 전망(GET /api/runs/{id} 의 outlook · outlook 이벤트 data). 모양은 PR #445 본문 기준. */
export type OutlookPeriodId = "1w" | "1m";
export type OutlookScenario = "bull" | "bear" | "range";
export interface OutlookLevel { price: number; why: string; strength: number | null }
export interface RunOutlook {
  version: 1;
  as_of: string;
  trade_date: string;
  timeframe: "1d";
  price: number | null;
  sigma: number | null;
  periods: Partial<Record<OutlookPeriodId, { bars: number; label: string; band68: [number, number]; band95: [number, number]; due: string }>>;
  paths: ProjectionPath[];
  first_touch: { up: number; down: number; none: number; bars_up: number | null; bars_down: number | null } | null;
  levels: { above: OutlookLevel[]; below: OutlookLevel[] };
  calibration: { coverage68_valid?: number; coverage95_valid?: number; n?: number } | null;
  decision: {
    rating: string | null;
    bias: "long" | "short" | "flat" | null;
    direction: "up" | "down" | "sideways";
    scenario: OutlookScenario | null;
    scenario_prob: number | null;
    likeliest: OutlookScenario | null;
    agrees: boolean | null;
    desk_direction: string | null;
    reason: string | null;
  };
  invalidation: number | null;
  note_ko: string | null;
  disclaimer: string;
}

export interface DeskEvent {
  run_id: string;
  seq: number;
  ts: string;
  type: "status" | "message" | "tool" | "plan" | "outlook" | "explain" | "error" | "done";
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
  /** #676 오늘 장중 값을 한투(거래소 잠정치)에서 받았으면 "kis" — 없으면 토스 */
  source?: "kis";
  /** #676 한투가 잠깐 실패해 직전 한투 값을 그대로 보이는 중(최대 5분) */
  held?: boolean;
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
  /** #673 오늘 값이 실제로 바뀐 것을 이 서버가 본 시각(그 직전 확인 시각, KST ISO) — 1d 오늘 기록만. */
  changed_at?: string | null;
  /** #673 pre: 08:00~09:00 장 시작 전 거래가 들어간 값 · regular: 정규장 · closed. */
  session?: "pre" | "regular" | "closed";
  /** #645 원천 시각(source_updated_at = max(토스 updatedAt, changed_at)) · 확인 시각(fetched_at). */
  freshness?: { fetched_at: string | null; source_updated_at: string | null; source_delay_sec: number | null;
                live: boolean; label: string | null };
}

export interface RegimeSnapshot {
  enabled: boolean;
  error?: string;
  regimes?: { ts: string; market: string; label: string; score: number; flags: string[] }[];
  triggers?: { ts: string; symbol: string; kind: string; direction: string; level: string }[];
  /** #409 semibot 이 연결됐으면 "semibot", 아니면 "desk"(서버) — 옛 응답엔 없다 */
  source?: "desk" | "semibot";
  /** #409 데스크 자체 국면: 시장 투매 · 위험 점수(#146)와 금리 · 달러 국면(#330) */
  desk?: DeskRegime;
}

export interface DeskRegime {
  markets: { market: "us" | "kr"; score: number | null; peak: number | null; phase: "selling" | "exhausting" | "calm" | "unknown" | null; label: string | null; as_of: string | null }[];
  rates: {
    rates: "hiking" | "cutting" | "hold" | null;
    yield10: "rising" | "falling" | "flat" | null;
    yield_spike: boolean | null;
    dollar: "strong" | "weak" | "flat" | null;
    sideways: boolean | null;
    as_of: string | null;
  } | null;
  error: string | null;
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
  commit?: string | null;
  /** #388 규칙 코드가 스냅샷을 만든 뒤 바뀌었나(null = 모름, 지문이 없던 예전 스냅샷) */
  stale?: boolean | null;
}
/** GET /api/backtest/reports/{horizon} — desk.backtest.Report */
export interface BacktestReport {
  version: number;
  horizon: HorizonId;
  tickers: string[];
  /** #555 시세를 받지 못해 빼고 돌린 종목(--allow-missing). 없으면 바스켓 전체 */
  missing?: string[];
  start: string | null;
  end: string | null;
  generated_at: string | null;
  /** #388 만든 develop 커밋 · 규칙 코드 지문 · 지금 코드와 지문이 다른가 */
  commit?: string | null;
  rules?: string;
  stale?: boolean | null;
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
  /** #286 기준일부터 최근 12개월(백테스트 + 운영 채점) — 옛 보정표엔 없다 */
  recent?: { n: number; p_t1: number | null; exp_r: number | null } | null;
  /** #286 최근 기대 R 이 장기와 뚜렷이 다름(표준오차 1.645배 · n 30 이상) */
  drift?: "weaker" | "stronger" | null;
  /** #286 drift 가 있을 때만: 위의 p_t1 · exp_r · n · p_range 는 최근 값으로 바뀌고 장기 값은 여기로 옮겨 온다 */
  long_term?: { n: number; p_t1: number | null; exp_r: number | null; p_range: [number, number] | null } | null;
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
  /** #508 rebound = 검정선 위 · 빨간선 아래(긴 하락 뒤 검정선이 빨간선보다 낮은 역배열),
   *  #521 reclaimed_red = 역배열인 채 두 선 모두 위(빨간선을 막 되찾은 초기 반등) */
  position: "above_black" | "reclaimed_red" | "between" | "rebound" | "below_red";
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
  /** #310 지금가 대비 % · 상태(now = 지금가 단계, below = 아래 대기, passed = 지금가가 이미 아래로 지나간 옛 단계) */
  from_now_pct?: number;
  status?: "now" | "below" | "passed";
}
export interface BottomZone {
  ticker: string;
  supported: boolean;
  error?: string;
  as_of?: string;
  /** 주봉 종가(판정 기준) */
  close?: number;
  /** #310 사다리 기준 지금가 · 출처 · 시각 */
  price_now?: number;
  price_source?: "kis" | "toss" | "yahoo" | "daily_close" | "weekly_close" | string;
  price_at?: string;
  /** 1차가 지금가보다 3% 넘게 아래면 그 거리(음수 %), 아니면 0 */
  first_step_gap_pct?: number;
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
  /** #386 같은 날 같은 회사로 합친 다른 상장 티커(보통주 · 우선주 등) — 화면에서만 채운다 */
  also?: string[];
  /** #308 상세 — 서버 배포 전엔 없다(모두 선택값), 없는 값은 null */
  eps_low?: number | null;
  eps_high?: number | null;
  eps_year_ago?: number | null;
  revenue_estimate?: number | null;
  revenue_low?: number | null;
  revenue_high?: number | null;
  analysts?: number | null;
  /** 예 "Oct/2026" */
  fiscal_quarter?: string | null;
  currency?: "USD" | "KRW" | string | null;
  refreshed_at?: string | null;
  /** 이번 회차에 못 받아 이전 행을 보여 주는 중 */
  stale?: boolean;
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
  sources: {
    economic: { refreshed_at: string | null; error: string | null };
    /** #308 error 는 사람용 한국어(문제 없으면 null), stale = 마지막 자료를 보이는 중 */
    earnings: { refreshed_at: string | null; error: string | null; stale?: boolean; failed_sources?: string[] };
  };
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
/** 네이버 분기 · 연간 실적(억원, 이익률 %) — consensus 면 추정치 */
export interface KrConsensusRow { period: string; consensus: boolean; revenue: number | null; op_income: number | null; net_income: number | null; op_margin: number | null }
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
  kr_consensus: { unit: string; quarter?: KrConsensusRow[]; annual?: KrConsensusRow[] } | null;
  sources: Record<string, { source: string; at: string }>;
  unavailable: { id: string; label: string; why: string }[];
  errors: string[];
  fetched_at: string;
  note: string;
  checkpoints?: { title_ko: string; why_ko: string | null; sentiment: string | null; impact: string | null; url: string; seen: string | null }[];
}
/** #296 GET /api/stock/profile (#295) — 종목 정보 카드 */
export interface ProfileStep {
  step: number;
  price: number;
  distance_pct: number | null;
  source: "level" | "volume";
}
export interface StockProfile {
  ticker: string;
  price: number | null;
  price_source: "live" | "close" | null;
  quote_source?: string | null;
  facts: {
    name: string | null;
    market_cap?: number | null;
    currency?: string | null;
    exchange?: string | null;
    first_trade_date?: string | null;
    first_trade_note?: string | null;
  } | null;
  recent: {
    days: { date: string; close: number; change_pct: number | null; highest_close: boolean; lowest_close: boolean }[];
    high_close: number | null;
    low_close: number | null;
  } | null;
  indicators: {
    week52?: { high: number | null; low: number | null; from_high_pct?: number | null; from_low_pct?: number | null; full?: boolean } | null;
    bollinger?: { upper: number; mid: number; lower: number; to_upper_pct: number | null; to_lower_pct: number | null } | null;
    moving_averages?: { days: number; value: number | null; vs_pct: number | null }[];
    rsi?: { value: number; zone: "overbought" | "neutral" | "oversold" } | null;
  } | null;
  levels: { supports: ProfileStep[]; resistances: ProfileStep[]; poc?: number | null; volume_bars?: number | null } | null;
  short: {
    available: boolean;
    reason?: string;
    shares?: number | null;
    percent_of_float?: number | null;
    days_to_cover?: number | null;
    as_of?: string | null;
  } | null;
  bars_source?: string | null;
  computed_at?: string;
  errors?: Record<string, string>;
}

/** #314 AI 채팅 — 대화 · 메시지 · 스트림 프레임 */
export interface ChatThread { id: string; title: string; created_at: string; updated_at: string }
/** #403 news_search 가 찾은 기사 — 제목 · 매체 · 게시 시각 · http(s) 링크(서버가 거른 것) */
export interface ChatLink { title: string; source: string | null; published: string | null; url: string }
export interface ChatTool { name: string; args: Record<string, unknown> | null; summary: string | null; links?: ChatLink[] }
export interface ChatMessageRow {
  id: string;
  role: "user" | "assistant";
  content: string;
  tools: ChatTool[];
  /** #404 답을 쓴 모델 이름(예전 답 · 내 말은 null) */
  model?: string | null;
  created_at: string;
}
/** #415 GET /api/chat/models (#414) — 채팅창에서 고를 수 있는 모델과 기본값 */
export interface ChatModels { default: string | null; items: { id: string; label: string }[] }
export type ChatEvent =
  | { type: "tool_call"; name: string; args: Record<string, unknown> | null }
  | { type: "tool_result"; name: string; summary: string | null; links?: ChatLink[] }
  | { type: "token"; text: string }
  | { type: "done"; message_id: string; user_message_id: string; model?: string | null }
  | { type: "error"; message: string };

/** #332 GET /api/tips (#330) — 금리 · 달러 국면과 국면별 매매 팁 */
export interface TipStats {
  n: number;
  median: number | null;
  win: number | null;
  all_median: number | null;
  all_win: number | null;
  vs_all: number | null;
  drawdown_median?: number | null;
  all_drawdown_median?: number | null;
}
export interface TipPeriod {
  episodes: number;
  regime_share: number | null;
  "20"?: TipStats;
  "60"?: TipStats;
}
export interface TipEvidence {
  proxy: string;
  metric: "return" | "decay";
  split: string;
  since: string | null;
  weak: boolean;
  train: TipPeriod;
  held_out: TipPeriod;
}
export interface Tip {
  id: string;
  title: string;
  condition: string;
  active: boolean;
  known: boolean;
  why: string;
  proxy: string;
  metric: "return" | "decay";
  applies_to: string[];
  evidence: TipEvidence | null;
  weak: boolean;
  exceptions: string[];
}
export interface TipsView {
  regime: {
    rates: "hiking" | "cutting" | "hold" | null;
    yield10: "rising" | "falling" | "flat" | null;
    yield_spike: boolean | null;
    dollar: "strong" | "weak" | "flat" | null;
    sideways: boolean | null;
    values: Record<string, number | null>;
  };
  tips: Tip[];
  as_of: string | null;
  evidence_as_of?: string | null;
  basis?: { horizons: number[]; worse_pp: number; decay_pp: number; min_days: number; rule: string } | null;
  /** #460 대세 국면 — ticker 없이 부를 때만 */
  trend?: TrendRow[];
  guide?: Partial<Record<TrendState, TrendGuide>>;
  trend_error?: string | null;
  /** #577 선물(NQ=F · ES=F · 코스피200)의 가장 가까운 열린 갭 한 줄 — 캐시에 있을 때만 */
  gaps?: TipGapLine[];
}

/** #577 GET /api/gaps — 세션 갭(전 세션 종가 → 새 세션 시가)의 메움 확률. 과거 빈도, 예측 아님 */
export interface GapOdds {
  n: number;
  p1: number | null; p3: number | null; p5: number | null; p20: number | null;
  median_sessions: number | null;
  filled_share: number | null;
  /** 표본 30 미만 */
  few: boolean;
}
export interface OpenGap {
  day: string;
  direction: "up" | "down";
  fill_price: number;
  open: number;
  gap_pct: number;
  atr_multiple: number;
  size: "small" | "mid" | "large";
  cause: "fomc" | "macro" | "earnings" | "weekend" | "other";
  trend: "up" | "down" | "mixed" | null;
  open_sessions: number;
  to_fill_pct: number;
  /** 같은 방향 · 크기의 갭이 이만큼 열려 있었을 때 그 뒤 메워진 비율 */
  odds: GapOdds;
  /** 레버리지 상품: 기초 갭이 메워질 때 이 상품의 대략 가격 · 등락 */
  product_fill_price?: number;
  product_move_pct?: number;
}
export interface GapReport {
  ticker: string;
  name: string | null;
  basis: { ticker: string; symbol: string; leverage: number | null; note: string | null };
  as_of: string;
  last_close: number;
  since: string;
  gaps_counted: number;
  open: OpenGap[];
  table: {
    all: GapOdds;
    direction: Record<"up" | "down", GapOdds>;
    size: Record<"small" | "mid" | "large", GapOdds>;
    cause: Record<OpenGap["cause"], GapOdds>;
    trend: Record<"up" | "down" | "mixed", GapOdds>;
    direction_size: Record<string, GapOdds>;
  };
  calendar_from: string | null;
}
export interface TipGapLine {
  ticker: string; name: string | null; text: string; direction: "up" | "down"; fill_price: number;
  p5: number | null; p20: number | null; n: number; few: boolean;
}
/** #577 진입 요약: 진입 방향에 맞서는 열린 갭(메워지면 이만큼 되밀릴 수 있다) */
export interface GapWarning {
  direction: "up" | "down"; fill_price: number; move_pct: number; product_fill_price: number | null;
  p5: number | null; p20: number | null; n: number; few: boolean; text: string;
}

/** #460 지수 대세 국면 · 초보용 지침 */
export type TrendState = "bull" | "bear" | "range" | "turning";
export interface TrendStat { median: number | null; win: number | null; p10: number | null; n: number; all_median: number | null; all_p10: number | null }
export interface TrendRow {
  market: "kospi" | "nasdaq100" | "sp500" | "sox";
  name: string;
  symbol: string;
  state: TrendState;
  state_ko: string;
  raw: TrendState;
  pending: { state: TrendState; state_ko: string; sessions: number; needs: number } | null;
  since: string;
  /** false 면 기록 시작 전부터 이어진 국면 — "기록 시작 이후 계속" */
  since_known: boolean;
  as_of: string;
  close: number;
  score: number;
  evidence: { id: string; value: number | null; vote: 1 | 0 | -1 | null; text: string }[];
  record?: { since: string; states: Partial<Record<TrendState, { "20": TrendStat | null; "60": TrendStat | null }>> } | null;
  record_note: string | null;
}
export interface TrendGuide {
  state: TrendState;
  state_ko: string;
  title: string;
  summary: string;
  size: string;
  stops: string;
  do: string[];
  dont: string[];
  prefer: string[];
  prefer_ko: string[];
  avoid: string[];
  avoid_ko: string[];
  basis: string;
}

/** #333 plan.summary 의 지금 할 일(#331) — 예전 run 에는 없다 */
export interface ActionTrigger {
  side: "long" | "short";
  price: number;
  confirm: "close" | "intraday";
  /** "일봉 종가" · "60분봉 마감" — 장중 터치가 아니라 그 봉의 마감 */
  confirm_label?: string;
  then: { entry: number | null; stop: number | null; t1: number | null; rr?: number | null };
}
export interface LadderStep {
  step: number;
  price: number;
  share: number | null;
  kind?: string;
  when: string;
  label?: string;
}
export interface PlanSummary {
  action?: { now: string; sentence: string; triggers: ActionTrigger[] };
  ladder_split?: { dip: LadderStep[]; confirm: LadderStep[] } | null;
  reference?: boolean;
  alt?: {
    entry: number | null; stop: number | null; target: number | null;
    entry_diff_pct: number | null; stop_diff_pct: number | null; target_diff_pct: number | null; text: string;
  } | null;
}

/** #312 지금가 기준 타점 하나 — 롱 또는 숏 쪽 계획(GET /api/entry-now 의 best · other) */
export interface EntryCandidate {
  side: "long" | "short";
  side_ko: string;
  setup: string;
  /** now = 지금 구역 안 · limit = 구역까지 기다림 · breakout = 돌파 · 이탈 확인 뒤 */
  kind: "now" | "limit" | "breakout";
  kind_ko: string;
  zone: [number | null, number | null];
  first: number;
  entries: { step: number; price: number; share: number | null; kind: string | null }[];
  avg_entry: number | null;
  stop: number;
  stop_pct: number | null;
  targets: { role: string | null; price: number; rr: number | null }[];
  from_now_pct: number | null;
  distance_atr: number | null;
  /** 지금가에서 같은 거리의 과거 체결률(0 ~ 1) */
  reach_prob: number | null;
  reach_n: number;
  p_t1: number | null;
  /** 체결된 거래의 기대 R(데스크 기록) */
  expected_r: number | null;
  /** 계획당 기대 R = reach_prob × expected_r */
  ev_r: number | null;
  n: number;
  thin: boolean;
  score: number | null;
  direction: string;
  state: string | null;
  why: string[];
}
export interface EntryNow {
  version: number;
  ticker: string;
  horizon: string;
  price_now: number | null;
  price_source: string | null;
  price_at: string | null;
  stale: boolean;
  atr: number | null;
  regime: string | null;
  /** 가장 나은 쪽 — 관망이어도 참고로 남는다(stand_aside 가 이유) */
  best: EntryCandidate | null;
  other: EntryCandidate | null;
  stand_aside: { reason: string } | null;
  bottom: { stage: string; stage_ko: string; score: number } | null;
  as_of: string | null;
  basis: string;
  /** #577 처음 보는 종목은 첫 응답에 없다(서버가 뒤에서 채운다) */
  gap_warning?: GapWarning | null;
}

// #360 오늘 장중 현물 · 선물 수급(#359 GET /api/market/flow/intraday). 값이 없으면 null.
export type IntradayBucket = 1 | 3 | 5 | 10 | 15 | 30 | 60;
export interface IntradaySpotBar {
  t: string; // 봉 끝 KST ISO(+09:00), 08:45 부터 bucket 격자. 스냅샷 없는 봉은 빠진다. 첫 봉 *_delta = 그 봉 누적값
  foreign: number | null; institution: number | null; individual: number | null; // 누적 억원
  foreign_delta: number | null; institution_delta: number | null; individual_delta: number | null; // 구간 억원
}
export interface IntradayFuturesBar {
  t: string;
  foreign_contracts: number | null; institution_contracts: number | null; individual_contracts: number | null; // 누적 계약
  foreign_delta: number | null; institution_delta: number | null; individual_delta: number | null;
  price: number | null;
}
export interface IntradayTurn {
  t: string; market: "futures" | "kospi" | "kosdaq"; who: "foreign"; kind: "buy_turn" | "sell_turn"; value: number | null;
}
export interface IntradayFlow {
  supported: boolean; // false 면 KIS 키 없음
  date: string; bucket_min: number; market_open: boolean;
  spot: { kospi: IntradaySpotBar[]; kosdaq: IntradaySpotBar[] };
  futures: IntradayFuturesBar[];
  turns: IntradayTurn[];
}

/** #443 · #444 코스피200 선물 근월물 주간 · 야간 시세 — GET /api/market/futures/quote */
export type FuturesSession = "day" | "night";
export interface FuturesQuote {
  /** false: KIS 키 없음 · 가짜 모드(아래 필드 대부분 없음) */
  supported: boolean;
  /** 한국어 한 줄. stale 일 때도 이유가 들어 있다 */
  error: string | null;
  /** true: 마지막으로 받은 값 */
  stale?: boolean;
  source?: "kis";
  /** 이 값의 세션 */
  session: FuturesSession;
  /** 지금 열린 세션(닫혔으면 closed) */
  market: FuturesSession | "closed";
  /** 그 세션을 연 날 YYYY-MM-DD(야간은 저녁 날짜) */
  session_date?: string;
  start?: string;
  end?: string;
  contract?: { code: string; name: string | null; expiry: string; days_left: number };
  price?: number;
  /** 기준가 대비, 부호 포함 */
  change?: number;
  change_pct?: number;
  /** 기준가 — 주간이면 전일 정산가, 야간이면 당일 주간 종가 */
  prev_close?: number;
  prev_close_basis?: "전일 정산가" | "주간 종가";
  open?: number;
  high?: number;
  low?: number;
  /** 그 세션 누적 거래량(계약) */
  volume?: number;
  /** 미결제약정(계약) */
  open_interest?: number;
  open_interest_change?: number;
  as_of?: string;
  /** #713 닫힌 세션에 한투가 빈 값을 줘서, 그 세션이 열려 있을 때 받은 마지막 값을 보이는 중 */
  held?: boolean;
  /** #713 그 세션 분봉의 마지막 종가로 대신한 값(등락 없음) · 그 봉 시각 */
  from_bars?: boolean;
  bar_at?: string;
  /** #713 세션 지정 없이 물으면: 이 세션이 지금 열렸나(sessions 안에서만) */
  state?: "open" | "closed";
  /** #713 세션 지정 없이 물으면: 대표 세션(진행 중, 없으면 가장 최근에 끝난 것)과 주간 · 야간 둘 다 */
  current?: FuturesSession;
  sessions?: Record<FuturesSession, FuturesQuote>;
}

export type FuturesInterval = "1m" | "5m" | "15m";

/** GET /api/market/futures/candles — bars 는 오래된 것부터, t = 봉 시작 ISO(+09:00) */
export interface FuturesCandles {
  supported: boolean;
  error: string | null;
  stale?: boolean;
  source?: "kis";
  session: FuturesSession;
  market: FuturesSession | "closed";
  session_date?: string;
  start?: string;
  end?: string;
  contract?: { code: string; expiry: string; days_left: number };
  interval?: FuturesInterval;
  bars: { t: string; o: number; h: number; l: number; c: number; v: number }[];
  as_of?: string;
}

// #463 공포지수 카드(#462 GET /api/market/fear). 항목은 늘 vix · cnn · vkospi · crypto 순서.
// band 는 서버가 방향을 맞춰 준다(VIX 는 클수록 공포) — 화면은 band 만 쓴다.
export type FearBand = "extreme_fear" | "fear" | "neutral" | "greed" | "extreme_greed";
export interface FearItem {
  key: "vix" | "cnn" | "vkospi" | "crypto";
  name: string;
  value: number | null;
  band: FearBand | null;
  band_ko: string | null;
  prev_day: number | null;
  prev_week: number | null; // vix 5거래일 전 · crypto 7일 전 · cnn previous_1_week
  source: string;
  as_of: string | null;
  error?: string;
}
export interface FearIndex { items: FearItem[]; error?: string }

// #465 /api/levels 의 갭 · 계획선 근거(#466 해설 패널 · 선 라벨)
export interface PriceGap {
  timeframe: "1d" | "1w";
  direction: "up" | "down"; // up = 아래 메움 목표, down = 위 메움 목표
  bottom: number; // 아직 안 메운 부분
  top: number;
  fill_price: number; // 여기 오면 메움
  formed_at: string; // 시장 자기 날짜 YYYY-MM-DD(주봉은 월요일)
  side: "above" | "below";
  distance_pct: number;
  text: string;
}
export interface LineAnchor {
  kind: "sr_zone" | "fib" | "trendline" | "gap";
  price: number;
  zone?: [number, number];
  distance: number;
  text: string;
}
export type PlanLineKey = "entry_price" | "stop_loss" | "price_target" | "long_trigger" | "short_trigger";
export interface LineBasis {
  line: PlanLineKey;
  label: string; // 진입 · 손절 · 목표 · 롱 조건 · 숏 · 축소 조건
  price: number;
  source: "plan";
  scenario: "long" | "short" | null;
  scenario_ko: string | null;
  hold_plan: boolean; // 관망 계획의 진입 · 손절 · 목표
  from_now_pct: number | null; // 지금가가 없으면 null
  anchors: LineAnchor[]; // [] = 어떤 레벨과도 안 맞음(모델이 고른 숫자)
  text: string;
}

// #484 유명 기관 13F 보유 공시(#483 /api/funds) — 1c 초안 계약
export type FundChange = "new" | "increase" | "decrease" | "sold" | "unchanged";
export interface FundLatest {
  quarter: string; // 2026Q3
  period_of_report: string; // 기준일(분기 끝)
  filed_at: string; // 공시일
  form: string; // 그 기관 자신의 공시 양식 — 13F-NT 면 보유는 다른 운용사의 13F-HR(reported_by)
  count: number | null; // 서버가 그 기관 최신 표를 아직 안 읽었으면 null
  total_value: number | null; // 달러
  summary: string | null; // 한 줄 요약(헤드라인)
  top_new: { ticker: string | null; name: string; value: number }[]; // 신규 매수 상위(최대 3, 평가액 큰 순)
}
export interface FundBrief {
  id: string;
  name: string; // 퍼싱스퀘어
  manager: string | null; // 빌 애크먼
  cik: string;
  latest: FundLatest | null;
  next_due: string | null; // 다음 공시 기한(분기 끝 + 45일)
  error: string | null;
}
export interface FundHolding {
  ticker: string | null;
  name: string;
  cusip: string;
  title: string | null; // COM · CL A …
  put_call: "PUT" | "CALL" | null;
  share_type: "SH" | "PRN"; // PRN = 채권(shares 가 원금 달러)
  shares: number;
  value: number; // 달러(옵션 줄은 명목가)
  weight: number | null; // %(옵션 줄은 null — 명목가라 비중에 넣지 않는다)
  change: FundChange | null; // 전 분기를 못 읽으면 null(그때 아래 셋도 null)
  change_shares: number | null; // 전 분기 대비(±)
  prev_shares: number | null;
  change_pct: number | null;
}
export interface FundDetail {
  id: string;
  name: string;
  manager: string | null;
  quarter: string;
  period_of_report: string;
  filed_at: string;
  form: string;
  prev_quarter: string | null;
  quarters: string[]; // 최근 먼저
  reported_by: { name: string; cik: string } | null; // 13F-NT: 다른 제출자가 보고
  total_value: number; // 주식 · 채권만(옵션 제외)
  options_value: number | null; // 옵션 명목가 합계
  count: number; // 전량 매도 · 옵션 제외 보유 수
  holdings: FundHolding[]; // 평가액 큰 순, 전량 매도는 맨 뒤
  summary: string;
  bullets: string[];
}
export interface FundChangeItem { fund: string; name: string; quarter: string; change: FundChange | null; change_shares: number | null; shares: number; weight: number | null }
export interface FundChanges { items: Record<string, FundChangeItem[]> }

/** #493 GET /api/runs/{id}/explain — 초보자 해설. 숫자는 데스크 계산값만, 문장은 AI(source "ai") 또는 규칙("rules") */
export interface ExplainChart { kind: "levels" | "zone" | "none"; levels: number[]; labels?: string[]; marks: number[] }
export interface RunExplain {
  version: number;
  hook: string;
  sections: { title: string; body: string; highlight: string; term: { name: string; explain: string }; chart: ExplainChart }[];
  stages: { label: string; low: number | null; high: number | null; why: string }[];
  checklist: { q: string; a: string }[];
  closing: string;
  source: "ai" | "rules";
  /** 분석이 돌고 있어 규칙 해설만 — 저장하지 않았다(다음에 다시 부르면 AI 로) */
  pending?: boolean;
  fallback?: string;
}


// #496 초보자 리포트(#495 /api/reports) — 01 계약(이슈 #495 댓글). 본문은 #493 RunExplain 과 같은 모양 + why · similar
export interface ReportSubject { symbol: string; name: string; kind: "index" | "favorite" | "topic" | "weekly" | "next_week" } // #681
export interface ReportSummary {
  id: string;
  created_at: string;
  title: string;
  summary: string; // 목록 · 대시보드 "오늘 볼 것" 한 줄
  subject: ReportSubject;
  source: "ai" | "rules";
}
export interface ReportList {
  items: ReportSummary[];
  generating: boolean; // 지금 만드는 중(동시 1개)
  next_due: string | null; // 다음 자동 생성 예정(꺼져 있으면 null)
  next_before: string | null; // 더 오래된 쪽 페이지 커서
}
export interface ReportSection {
  title: string;
  body: string;
  highlight: string;
  term: { name: string; explain: string };
  chart: ExplainChart;
  image: { url: string; alt: string } | null; // #497 설명 그림(차트 없는 섹션만, 서버에 그림 키가 있을 때) — url 은 세션 쿠키로 받는 /api 경로
}
export interface ReportDetail extends ReportSummary {
  /** 가격 기준 거래일(YYYY-MM-DD) — #495 계약 보충 */
  as_of?: string | null;
  why: { text: string; candidates: { symbol: string; name: string; score: number; reasons: string[] }[] } | null;
  hook: string;
  sections: ReportSection[];
  similar: { date: string; what: string; after: string }[];
  stages: { label: string; low: number | null; high: number | null; why: string }[];
  checklist: { q: string; a: string }[];
  closing: string;
  version: number;
}

/** #514 GET /api/crypto/halving — 비트코인 반감기 사이클 비교(날짜 UTC YYYY-MM-DD, 가격 USD 일봉 종가) */
export interface HalvingEvent { n: number; block: number; date: string; reward_after: number; estimated: boolean }
export interface HalvingCycle {
  halving: string;
  end: string;
  /** 이 사이클에서 가격이 있는 첫날 */
  data_from: string;
  /** 자료가 사이클 중간부터(2012 사이클) */
  partial: boolean;
  peak: { date: string; price: number; days_from_halving: number } | null;
  bottom: { date: string; price: number; days_from_halving: number; days_from_peak: number | null; drawdown_pct: number | null } | null;
  note: string | null;
}
export type HalvingRangeNow = "before" | "within" | "after";
export interface HalvingRange { min: number; max: number; cycles: string[] }
export interface HalvingSeries {
  halving: string;
  /** 반감기 날 종가가 없어 비율 기준을 그 사이클 첫 종가로 둔 날(2012 사이클) */
  base_date?: string;
  /** [일수, 비율] — 7일마다 */
  points: [number, number][];
}
export interface Halving {
  as_of: string;
  source: string;
  halvings: HalvingEvent[];
  cycles: HalvingCycle[];
  /** 지금 사이클 — 자료가 모자라면 null(서버) */
  current: {
    halving: string;
    days_since_halving: number;
    price: number;
    high: { date: string; price: number; days_from_halving: number };
    days_since_high: number;
    from_high_pct: number;
    /** 고점 뒤 지금까지 최저 — 바닥 확정 아님 */
    low_after_high: { date: string; price: number; days_from_halving: number; drawdown_pct: number } | null;
    from_low_pct: number | null;
  } | null;
  ranges: {
    /** 비교할 지난 사이클 값이 없으면 null(서버 _range([])) */
    bottom_days_from_halving: HalvingRange | null;
    bottom_days_from_peak: HalvingRange | null;
    bottom_drawdown_pct: HalvingRange | null;
    now: {
      days_since_halving: HalvingRangeNow | null;
      days_since_high: HalvingRangeNow | null;
      from_high_pct: "shallower" | "within" | "deeper" | null;
    };
  };
  series: { by_halving: HalvingSeries[]; by_peak: HalvingSeries[] };
  notes: string[];
}

/** #527 모의 투자(실제 주문 없음) — /api/paper/*. 금액은 원, 비율은 %(win_rate 만 0~1) */
export type PaperSizing = "risk" | "weight";
export interface PaperSettings {
  start_capital: number;
  start_date: string | null;
  sizing: PaperSizing;
  risk_pct: number;
  weight_pct: number;
  max_positions: number;
  fee_kr_pct: number;
  fee_us_pct: number;
  tax_kr_pct: number;
  slippage_pct: number;
  allow_short: boolean;
  version: number;
  updated_at: string | null;
}
export type PaperSettingsPatch = Partial<Omit<PaperSettings, "version" | "updated_at">>;
export interface PaperGroup { key: string; trades: number; wins: number; pnl: number; avg_r: number | null }
export interface PaperSummary {
  as_of: string;
  scored_at: string | null;
  settings_version: number;
  start_capital: number;
  balance: number;
  cash: number;
  invested: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  total_pnl_pct: number;
  trades: { closed: number; open: number; waiting: number; skipped: number };
  wins: number;
  losses: number;
  win_rate: number | null;
  avg_r: number | null;
  payoff: number | null;
  max_drawdown: { krw: number; pct: number; peak_at: string | null; trough_at: string | null } | null;
  this_month: { start: string; pnl: number; pct: number | null };
  by_ticker: PaperGroup[];
  by_direction: PaperGroup[];
  by_rating: PaperGroup[];
}
export interface PaperEquity {
  settings_version: number;
  start_capital: number;
  points: { date: string; balance: number }[];
  weekly: { start: string; pnl: number }[];
  monthly: { start: string; pnl: number }[];
}
export type PaperStatus = "closed" | "open" | "waiting" | "skipped";
export interface PaperTrade {
  run_id: string;
  ticker: string;
  name: string | null;
  market: "KR" | "US" | null;          // null: 채점은 됐지만 모의로 따라가지 못한 종목(unsupported)
  currency: "KRW" | "USD" | null;
  direction: "long" | "short";
  rating: string | null;
  horizon: string | null;
  plan_at: string;
  status: PaperStatus;
  exit_reason: "target" | "stop" | "both" | "expired" | null;
  skip: { code: string; text: string } | null;
  plan: { entry: number | null; stop: number | null; target: number | null };
  entry: { at: string; price: number; fill: number } | null;
  exit: { at: string; price: number; fill: number } | null;
  qty: number | null;
  fx: { entry: number | null; exit: number | null } | null;
  amount_krw: number | null;
  costs_krw: number | null;
  pnl_krw: number | null;
  pnl_pct: number | null;
  r: number | null;
  marked: boolean;
}
export interface PaperTrades {
  settings_version: number;
  counts: { closed: number; open: number; waiting: number; skipped: number };
  items: PaperTrade[];
}


// #573 아침 시황(#571) — 계약: #571 issuecomment-6966263436. 시각은 UTC ISO, date 는 한국 날짜
export type BriefSource = "ai" | "rules";
export type BriefTrigger = "auto" | "manual";
export interface MorningBrief {
  id: string;
  date: string;
  created_at: string;
  trigger: BriefTrigger;
  source: BriefSource;
  model: string | null;
  title: string;
  numbers: string[];
  kr_flow: string | null;
  done: string[];
  pending: string[];
  schedule: string[];
  good: string[];
  bad: string[];
  closing: string | null;
  day_call: string | null;
  missing: string[];
  fallback?: string | null;
  version: number;
  generating: boolean;
  next_due: string | null;
}
export interface MorningBriefItem { id: string; date: string; created_at: string; title: string; source: BriefSource; trigger: BriefTrigger }
export interface MorningBriefList { items: MorningBriefItem[]; generating: boolean }

// #573 오늘 방향(#572) — 계약: #572 issuecomment-6966479748 + 보완 issuecomment-6967245692(판정 두 개 나란히).
// 확률은 % (소수 1자리, 다섯 단계 합 100), 비율은 0~1
export type DayCallMarket = "kr" | "us";
export type DayCallKind = "prev_close" | "open";
export type DayCallStage = "strong_long" | "weak_long" | "flat" | "weak_short" | "strong_short";
export type DayCallProbs = Record<DayCallStage, number>;
export interface DayCallReason {
  key: string;
  label: string;
  value: string;
  effect: "long" | "short" | "volatile" | "calm";
  weight: number;
  text: string;
}
export interface DayCallOutcome {
  stage: DayCallStage;
  label: string;
  open: number;
  close: number;
  return_pct: number;
  ret_atr: number;
  hit_direction: boolean | null;   // 보류(hold)였던 판정은 null
  hit_stage: boolean;
  brier: number;
  scored_at: string;
  unscorable?: false;
}
/** 채점할 수 없었던 판정(지수 자료가 2일 넘게 없음) — stats 에서 빠진다 */
export interface DayCallUnscorable { unscorable: true; scored_at: string }
/** 그 판정의 적중 기록 — 실시간 판정이 30개 미만이면 검증 구간(backtest) 기준 */
export interface DayCallRecord {
  n: number;
  direction_hit_rate: number | null;
  always_long: number | null;
  always_short: number | null;
  brier_skill: number | null;
  basis: "backtest" | "live";
}
export interface DayCallEntry {
  call: DayCallKind;
  call_label: string;
  measured_from: DayCallKind;
  fallback?: boolean;              // 전일 종가 대비에서 야간 선물 없이 낸 판정
  round: number;
  round_label: string;
  called_at: string;
  cutoff_at?: string;
  stage: DayCallStage;
  label: string;
  direction: "long" | "short";
  verdict: "long" | "short" | "hold";   // hold 는 시가 대비에만(롱 · 숏 확률 차 10%p 미만)
  probs: DayCallProbs;
  edges_pct?: { flat: number; strong: number };
  reasons?: DayCallReason[];
  context?: { key: string; label: string; value: string; in_model: boolean }[];
  model_version?: string;
  source: "live" | "backfill";
  outcome: DayCallOutcome | DayCallUnscorable | null;
  record?: DayCallRecord;
}
export interface DayCallToday {
  market: DayCallMarket;
  date: string;          // 돌려준 판정의 세션 날짜(그 시장의 가장 최근 판정 날, 현지 오늘 이하)
  today?: boolean;       // false 면 지난 세션 판정(예: KST 아침의 미장)
  stages: { key: DayCallStage; label: string }[];
  calls: Partial<Record<DayCallKind, DayCallEntry | null>>;
  rounds: Partial<Record<DayCallKind, DayCallEntry[]>>;
  note?: string[];
}
export interface DayCallHistoryItem extends DayCallEntry { date: string }
export interface DayCallHistory { market: DayCallMarket; days: number; items: DayCallHistoryItem[] }
export interface DayCallRates { n?: number; direction_hit_rate?: number | null; stage_hit_rate?: number | null; brier?: number | null }
export interface DayCallRoundRates extends DayCallRates { live_n?: number; live?: DayCallRates; backfill?: DayCallRates }
export interface DayCallCallStats {
  n: number;
  missed: number;
  direction_hit_rate: number | null;
  stage_hit_rate: number | null;
  brier: number | null;
  baselines: {
    always_long?: { direction_hit_rate: number | null };
    always_short?: { direction_hit_rate: number | null };
    follow_yesterday?: { direction_hit_rate: number | null };
    climatology?: { brier: number | null };
  };
  per_stage: { stage: DayCallStage; label: string; called: number; called_hit: number; actual: number }[];
  calibration: { lo: number; hi: number; n: number; mean_prob: number | null; actual_rate: number | null }[];
  /** #606 회차별 — 그 안에서 실제 판정(live) · 검증 구간(backfill)을 나눈 것도 같이(카드의 적중 기록은 이 회차 하나다) */
  by_round: Record<string, DayCallRoundRates>;
  by_source: Record<string, DayCallRates>;
  hold_rate?: number | null;                                   // 시가 대비만
  called?: { n: number; direction_hit_rate: number | null };   // 시가 대비 — 보류가 아닌 날만
}
export interface DayCallStats {
  market: DayCallMarket;
  days: number;
  calls: Partial<Record<DayCallKind, DayCallCallStats>>;
  note?: string[];
  /** 검증 구간을 만든 모델 — holdout_from 이후가 학습에 안 쓴 날, stale 이면 지금 코드와 모델이 어긋남 */
  backtest?: { model_version: string | null; holdout_from: string | null; stale: boolean };
}

/** #612 평행 채널 · 짧은 추세선(#611 GET /api/lines, docs/lines.md). 시각은 UTC ISO, 선은 로그 가격 × 봉 순서 직선 */
export type LineTf = "5m" | "15m" | "60m" | "240m" | "1d";
export interface LinePoint { at: string; price: number }
export interface LineSegment { start: LinePoint; end: LinePoint }
/** 선에 닿은 뒤 HORIZON 봉 안에 1 ATR 반등 / 0.3 ATR 이탈 비율과 같은 기울기 · 거리의 아무 선(기준선) 반등 */
export interface LineRate { n: number; bounce_rate: number | null; break_rate: number | null; baseline_bounce_rate: number | null; horizon_bars: number }
/** 막 이탈한 추세선이 그 방향으로 1 ATR 더 간 비율과 기준선 */
export interface LineBreakRate { n: number; follow_rate: number | null; baseline_rate: number | null; horizon_bars: number }
export type ChannelState = "inside" | "near_upper" | "near_lower" | "above" | "below";
export interface ParallelChannel {
  id: string;
  timeframe: string;
  direction: "up" | "down" | "flat";
  base: "upper" | "lower";
  start_at: string;
  upper: LineSegment; lower: LineSegment; mid: LineSegment; outer_upper: LineSegment; outer_lower: LineSegment;
  slope_pct_per_bar: number;
  width_pct: number;
  width_atr: number;
  position: number;
  state: ChannelState;
  broke_at: string | null;
  touches: { upper: string[]; lower: string[] };
  body_breaks: number;
  score: number;
  explain: string;
  hit_rate: { lower: LineRate | null; upper: LineRate | null } | null;
}
export interface ShortTrendline {
  id: string;
  timeframe: string;
  kind: "support" | "resistance";
  anchors: LinePoint[];
  end: LinePoint;
  touches: string[];
  slope_pct_per_bar: number;
  state: "intact" | "testing" | "broken";
  broken_at: string | null;
  broken_price: number | null;
  fresh_break?: boolean;
  distance_atr: number | null;
  explain: string;
  hit_rate: LineRate | LineBreakRate | null;
}
/** #679 몇 달(일봉) · 몇 년(주봉)에 걸친 긴 추세선(desk/long_lines.py) — 짧은 추세선 필드 + 닿은 자리 · 거짓 이탈 */
export interface LongTrendline extends Omit<ShortTrendline, "hit_rate"> {
  long: true;
  /** 닿은 봉 시각 · 그때 선 가격(마지막이 오늘 봉이면 "오늘 닿음") */
  touch_points: LinePoint[];
  /** 종가가 3봉 이하로 선을 넘었다 돌아온 구간(꼬리는 세지 않는다) */
  false_breaks: { from: string; to: string; closes: number; extreme: number }[];
  /** holding 유지 · testing 시험 중 · broken 이탈 */
  status: "holding" | "testing" | "broken";
  distance_pct: number;
  /** 첫 닿음 시각 */
  since: string;
  span_bars: number;
  score: number;
}
export interface LineEvent { at: string; kind: "trend_break" | "channel_break"; line_id: string; text: string }
export interface LinesReport {
  ticker: string;
  interval: string;
  timeframe: string;
  source: string;
  live_bar?: boolean;
  as_of: string | null;
  reference_price: number | null;
  atr: number | null;
  channels: ParallelChannel[];
  trendlines: ShortTrendline[];
  /** #679 일봉 차트에서만: 일봉 · 주봉의 긴 지지 · 저항선(다른 주기는 []; 옛 서버는 없다) */
  long_lines?: LongTrendline[];
  events: LineEvent[];
  note: string;
  backtest: { generated_at: string; timeframes: Record<string, { from: string; to: string; steps: number }> } | null;
  /** DEV 에서 API 가 없을 때 봉으로 만든 가짜(화면 확인용) */
  fake?: boolean;
}

/** #631 · #672 GET /api/chart-read — 선이 차트에서 무엇인지(채널 · 짧은 추세선은 /api/lines 와 같은 id) */
export interface ChartReadRef {
  kind: "channel" | "trendline" | "pattern" | "swing" | "fib" | "gap" | "neckline";
  id?: string | null;
  /** 채널: upper · lower · mid · outer_upper · outer_lower, 삼각수렴: upper · lower */
  edge?: string;
  direction?: string;
  /** 추세선: support · resistance, 최근 고점 · 저점: high · low */
  side?: string;
  /** 방금 깨진 추세선 */
  broken?: boolean;
  pattern?: string | null;
  state?: string;
  ratio?: number;
  formed_at?: string;
  at?: string;
}

/** 가까운 위 · 아래 선(가까운 순, 4분의 1 ATR 안의 선은 하나로 — name 은 " · " 로 이어 붙임) */
export interface ChartReadLevel {
  price: number;
  name: string;
  distance_pct: number;
  refs?: ChartReadRef[];
}

export interface ChartReadFrame {
  timeframe: string;
  as_of?: string | null;
  price?: number | null;
  live_bar?: boolean;
  trend?: { state: "up" | "down" | "sideways"; basis?: string };
  levels?: { above: ChartReadLevel[]; below: ChartReadLevel[]; far?: { above: ChartReadLevel[]; below: ChartReadLevel[] } };
  volume?: { ratio: number; hot: boolean } | null;
  summary?: string;
  /** #695 */
  hs?: HsPattern[];
  bubble?: BubbleStage | null;
  /** 이 시간봉을 읽지 못했다(봉을 못 받음 등) */
  error?: string;
}

export interface ChartRead {
  ticker: string;
  generated_at: string;
  timeframes: ChartReadFrame[];
  /** 시간봉끼리 엇갈리는 곳 */
  conflicts: string[];
  summary: string;
  /** #695 주봉 민스키형 단계(주봉을 읽었을 때) */
  bubble?: BubbleStage | null;
  note: string;
}
