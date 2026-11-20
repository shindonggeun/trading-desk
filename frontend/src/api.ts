import type { ChartRead, GapReport, LinesReport, LineTf } from "./types";
import type { DayCallHistory, DayCallMarket, DayCallStats, DayCallToday, MorningBrief, MorningBriefList } from "./types";
import type { EarningsPreview, FuturesCandles, FuturesInterval, FuturesQuote, FuturesSession, PaperEquity, PaperSettings, PaperSettingsPatch, PaperSummary, PaperTrades, RunExplain, TipsView } from "./types";
import type { BacktestReport, BacktestSummary, WeeklyHistoryRow, WeeklyMap, WeeklyMapError, CreditView, DeskConfig, EconCalendar, Favorite, Positioning, Sector, DeskEvent, BoardItem, Estimate, EstimatedCandles, FlowInterval, FuturesFlow, FlowMarket, Instrument, LivePlaybooks, MacroBrief, MarketFlow, PlanStats, Portfolio, RegimeSnapshot, Run, RunPage, SupportResistance, BottomZone, ReportDetail, ReportList, Halving, FundBrief, FundChanges, FundDetail, FearIndex, FuturesDaily, FuturesIntraday, IntradayBucket, IntradayFlow, MarketMonth, SectorNewsView, LongShortItem, Projection, StockProfile } from "./types";

// Auth is an HttpOnly session cookie (docs/security.md): scripts never see it, the
// browser sends it with every same-origin fetch and with EventSource.

export class Unauthorized extends Error {}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly retryAfter?: number) {
    super(message);
  }
}

// App registers this to drop back to the login screen when a session ends mid-use.
let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

/**
 * #537 같은 GET 은 한 번만 — 대시보드 구역 여럿이 같은 경로(설정 · 달력 · 시세판 …)를 동시에 부르면 요청 하나를 나눠 쓴다.
 * 천천히 바뀌는 경로는 짧게 기억해, 다른 화면에 갔다 돌아오면 다시 받지 않고 바로 보인다. 기억 시간은 그 구역의
 * 갱신 간격보다 짧게 잡아 주기 갱신이 늘 새 값을 받는다. 쓰기(POST · PUT · DELETE …)가 한 번이라도 있으면 전부 잊는다.
 */
const CACHE_TTL: [RegExp, number][] = [
  [/^\/api\/config$/, 300_000],
  [/^\/api\/market\/credit\b/, 300_000],       // 하루 한 번 올라온다(구역 갱신 30분)
  [/^\/api\/calendar\b/, 60_000],
  [/^\/api\/regime$/, 60_000],
  [/^\/api\/stats\/plans\b/, 60_000],
  [/^\/api\/market\/weekly\b/, 60_000],
  [/^\/api\/bottom-zone\b/, 60_000],
  [/^\/api\/batch\/bottom-zone\b/, 60_000],   // #705 대시보드 바닥권 칸(지수 + 즐겨찾기 한 번에)
  [/^\/api\/sector-news\b/, 60_000],            // 구역 갱신 10분
  [/^\/api\/macro\/brief$/, 30_000],
  // 모의 투자 요약은 설정 저장 뒤 서버가 다시 계산하는 동안 3초마다 다시 읽는다 — 기억하지 않는다
  [/^\/api\/market\/fear$/, 30_000],            // 구역 갱신 1분
];
const cached = new Map<string, { at: number; data: unknown }>();
const inflight = new Map<string, Promise<unknown>>();
/** 지울 때마다 늘린다 — 지우기 전에 떠난 GET 의 응답은 기억하지도, 뒤에 온 호출과 나누지도 않는다 */
let generation = 0;

function ttlFor(path: string): number {
  return CACHE_TTL.find(([re]) => re.test(path))?.[1] ?? 0;
}

/** 기억한 응답을 모두 잊는다(로그아웃 · 계정 바뀜 · 쓰기 뒤) */
export function clearApiCache() {
  generation++;
  cached.clear();
  inflight.clear();
}

// 응답 객체를 화면 여럿이 나눠 쓰면 한쪽이 고친 값이 다른 쪽에 비친다 — 나눠 줄 때는 복사본
const copy = <T,>(v: T): T => (v == null || typeof v !== "object" ? v : structuredClone(v));

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET" || init?.body != null) {
    clearApiCache();
    return request<T>(path, init);
  }
  const ttl = ttlFor(path);
  const hit = ttl ? cached.get(path) : undefined;
  if (hit && Date.now() - hit.at < ttl) return copy(hit.data as T);
  // 나눠 쓰는 요청은 경로로만 묶는다 — 한 호출자의 signal 로 끊으면 나머지도 실패하니 GET 에 signal 을 넘기면 묶지 않는다
  const share = !init?.signal;
  const running = share ? inflight.get(path) : undefined;
  if (running) return running.then((v) => copy(v as T));
  const gen = generation;
  const p = request<T>(path, init)
    .then((data) => {
      // 저장본은 따로 — 첫 호출자가 받은 객체를 고쳐도 기억한 값은 그대로
      if (ttl && gen === generation) cached.set(path, { at: Date.now(), data: copy(data) });
      return data;
    })
    .finally(() => {
      if (inflight.get(path) === p) inflight.delete(path);
    });
  if (share) inflight.set(path, p);
  return p;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && path !== "/api/auth/login") {
    clearApiCache();
    onUnauthorized();
    throw new Unauthorized("login required");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: any) => d.msg).join(", ")
      : body.detail ?? res.statusText;
    throw new ApiError(detail, res.status, body.retry_after);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export interface NewRun {
  ticker: string;
  name?: string;
  profile?: string;
  horizon?: string;
  trade_date?: string;
  analysts: string[];
  use_portfolio: boolean;
  output_language?: string;
  max_debate_rounds?: number;
}

/** 1단계 응답. OTP 를 켠 계정이면 세션 대신 challenge 가 온다(docs/security.md §6). */
export type LoginStep = { otp_required: true; challenge: string } | undefined;

export interface Me {
  username: string;
  otp_enabled: boolean;
  recovery_codes_left: number | null;
  /** 서버에 DESK_OTP_KEY 가 있는지 — 없으면 OTP 를 켤 수 없다. */
  otp_available: boolean;
}

/** 봉 주기(backend desk/prices.py INTERVALS). 대소문자 구분: 1m = 1분, 1M = 월봉. */
export type CandleInterval = "1m" | "5m" | "15m" | "30m" | "60m" | "120m" | "240m" | "1d" | "1w" | "1M" | "1y";

/** GET /api/candles — 봉은 오래된 것부터, at 은 봉 시작 시각(UTC ISO). */
export interface Candles {
  ticker: string;
  interval: string;
  source: string;
  fetched_at: string;
  /** rsi 는 indicators 에 rsi14 를 요청했을 때만(역사가 짧으면 앞쪽 null). stoch · bb 도 같다(#203). */
  bars: {
    at: string; open: number; high: number; low: number; close: number; volume: number; rsi?: number | null;
    stoch_k?: number | null; stoch_d?: number | null;
    bb20?: { mid: number; upper: number; lower: number } | null;
    bb50?: { mid: number; upper: number; lower: number } | null;
  }[];
  indicators?: string[];
  /** 야후 분봉 보관 한도(일). 분봉이 아니면 null. */
  history_limit_days?: number | null;
  /** 요청한 개수보다 적게 왔다. 안내는 history_limit_days 가 있을 때만 — 년봉은 늘 적게 온다. */
  truncated?: boolean;
  /** #559 일봉만: 마지막 봉의 장이 얼마나 지났는지(0~1). 끝난 봉 · 하루 장이 없는 시장은 null */
  session_elapsed?: number | null;
  /** #653 이 봉 원천이 얼마나 늦는지(초) — 야후 국장 · CME 선물은 10~20분. 보이기만 한다 */
  delay_sec?: number | null;
}

/** #653 GET /api/candles/live — 마지막 봉을 지금 값으로 이어 붙인 것. bar.at 은 봉 시작(UTC ISO).
 *  state: updated(마지막 봉이 움직임) · appended(새 봉) · price_only(봉은 그대로, 값만 — 체결 · 추정이 없거나 추정이 실제 봉 칸에 듦) */
export interface CandleLive {
  ticker: string;
  interval: string;
  bar: (Candles["bars"][number] & { estimated?: boolean }) | null;
  state: "updated" | "appended" | "price_only";
  price: number | null;
  /** kis · toss · yahoo · nxt · krx-overtime · binance-estimate */
  source: string;
  estimated: boolean;
  live?: boolean;
  session: "regular" | "pre" | "post" | "overnight" | "closed" | string;
  price_at: string | null;
  delay_sec: number | null;
  series_source?: string | null;
  fetched_at: string;
}

export type Alignment = "with_plan" | "against_plan" | "plan_flat" | "no_plan";

export interface ReviewRow {
  order_id: string;
  symbol: string;
  ticker: string;
  name: string | null;
  side: "BUY" | "SELL" | string;
  quantity: number;
  price: number;
  fee: number;
  currency: string;
  filled_at: string;
  plan: {
    run_id: string;
    trade_date: string;
    rating: string | null;
    bias: string | null;
    entry_price: number | null;
    stop_loss: number | null;
    price_target: number | null;
  } | null;
  alignment: Alignment;
  entry_gap_pct: number | null;
  outcome: { result: "target" | "stop" | "both" | "open"; on: string | null } | null;
  realized: {
    matched_quantity: number;
    unmatched_quantity: number;
    realized_pnl: number | null;
    realized_rate: number | null;
  } | null;
}

export interface ReviewResponse {
  supported: boolean;
  source: string;
  from?: string;
  to?: string;
  fills: ReviewRow[];
  summary: {
    fills: number;
    with_plan: number;
    against_plan: number;
    plan_flat: number;
    no_plan: number;
    outcomes: Record<"target" | "stop" | "both" | "open", number>;
    realized_pnl: Record<string, number>;
  } | null;
  errors: string[];
}

export const api = {
  /** #315 채팅처럼 이 파일 밖에서 같은 인증 · 오류 처리로 부를 때 */
  raw: <T>(path: string, init?: RequestInit) => call<T>(path, init),
  login: (username: string, password: string) =>
    call<LoginStep>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  /** 2단계: 6자리 코드 또는 복구 코드. 맞으면 세션 쿠키가 생긴다. */
  loginCode: (challenge: string, code: string) =>
    call<void>("/api/auth/login", { method: "POST", body: JSON.stringify({ challenge, code }) }),
  logout: () => call<void>("/api/auth/logout", { method: "POST" }),
  me: () => call<Me>("/api/auth/me"),
  otpSetup: (password: string) =>
    call<{ secret: string; uri: string }>("/api/auth/otp/setup", { method: "POST", body: JSON.stringify({ password }) }),
  otpEnable: (code: string) =>
    call<{ recovery_codes: string[] }>("/api/auth/otp/enable", { method: "POST", body: JSON.stringify({ code }) }),
  otpDisable: (password: string, code: string) =>
    call<void>("/api/auth/otp/disable", { method: "POST", body: JSON.stringify({ password, code }) }),
  config: () => call<DeskConfig>("/api/config"),
  runs: (page = 1, size = 50) => call<RunPage>(`/api/runs?page=${page}&size=${size}`),
  deleteRun: (id: string) => call<void>(`/api/runs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  run: (id: string) => call<Run>(`/api/runs/${encodeURIComponent(id)}`),
  // #528 모의 투자(#527) — 실제 주문 없음. 설정을 바꾸면 다음 읽기에서 settings_version 이 따라온다
  paperSettings: () => call<PaperSettings>("/api/paper/settings"),
  savePaperSettings: (patch: PaperSettingsPatch) =>
    call<PaperSettings>("/api/paper/settings", { method: "PUT", body: JSON.stringify(patch) }),
  paperSummary: () => call<PaperSummary>("/api/paper/summary"),
  paperEquity: () => call<PaperEquity>("/api/paper/equity"),
  paperTrades: (q: { status?: string; direction?: string; ticker?: string; limit?: number } = {}) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
    const qs = p.toString();
    return call<PaperTrades>(`/api/paper/trades${qs ? `?${qs}` : ""}`);
  },
  // #494 초보자 해설(#493) — 처음 한 번은 모델이 쓰는 동안 수십 초
  runExplain: (id: string, refresh = false) =>
    call<RunExplain>(`/api/runs/${encodeURIComponent(id)}/explain${refresh ? "?refresh=1" : ""}`),
  createRun: (body: NewRun) => call<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  portfolio: () => call<Portfolio>("/api/portfolio"),
  regime: () => call<RegimeSnapshot>("/api/regime"),
  /** #115 거시 · 정책 뉴스 흐름 — 서버가 30분마다 새로 모은다. */
  macroBrief: () => call<MacroBrief>("/api/macro/brief"),
  /** #293 실적 프리뷰 — 종목당 서버 캐시. */
  earningsPreview: (ticker: string) => call<EarningsPreview>(`/api/earnings/preview?ticker=${encodeURIComponent(ticker)}`),
  /** 계획 채점 통계(#64 desk/outcome.py) — 서버가 보관 기간까지로 자른다. */
  planStats: (days = 90) => call<PlanStats>(`/api/stats/plans?days=${days}`),
  /** #185 저장소에 커밋한 바스켓 백테스트 스냅샷 — 보기별 요약과 한 보기의 리포트 */
  backtestReports: () => call<BacktestSummary[]>("/api/backtest/reports"),
  backtestReport: (horizon: string) => call<BacktestReport>(`/api/backtest/reports/${encodeURIComponent(horizon)}`),
  /** #202 주간 시장 지도 — 주요 지수별(한 지수 실패는 그 지수만 error), symbol 로 하나만 */
  weeklyMaps: (symbol?: string) =>
    call<{ maps: (WeeklyMap | WeeklyMapError)[]; generated_at?: string }>(
      `/api/market/weekly${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  weeklyHistory: (symbol: string, weeks = 4) =>
    call<{ symbol: string; weeks: WeeklyHistoryRow[] }>(`/api/market/weekly/${encodeURIComponent(symbol)}/history?weeks=${weeks}`),
  /** #160: 코스피200 근월물 선물 현황(한국투자증권) */
  marketFutures: () => call<FuturesFlow>("/api/market/futures"),
  /** #463 공포지수 4종(VIX · CNN 공포탐욕 · VKOSPI · 암호화폐 공포탐욕), #462. */
  marketFear: () => call<FearIndex>("/api/market/fear"),
  /** #254 선물 수급 기록: 날마다 마감 스냅샷 · 그날 장중 스냅샷 */
  marketFuturesDaily: (days = 20) => call<FuturesDaily>(`/api/market/futures/daily?days=${days}`),
  marketFuturesIntraday: (date?: string) =>
    call<FuturesIntraday>(`/api/market/futures/intraday${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  /** 코스피 · 코스닥 투자자별 매매대금(#76). 장중 60초 · 장 밖 10분 서버 캐시. */
  marketFlow: (market: FlowMarket, interval: FlowInterval, count = 20) =>
    call<MarketFlow>(`/api/market/flow?market=${market}&interval=${interval}&count=${count}`),
  /** #360 오늘 장중 현물 · 선물 수급 분 단위 봉과 외국인 전향 지점(#359). */
  marketFlowIntraday: (bucket: IntradayBucket, date?: string) =>
    call<IntradayFlow>(`/api/market/flow/intraday?bucket=${bucket}${date ? `&date=${encodeURIComponent(date)}` : ""}`),
  /** #444 코스피200 선물 근월물 주간 · 야간 시세(#443) — session 을 빼면 지금 열린 세션, 닫혔으면 마지막 세션 */
  futuresQuote: (session?: FuturesSession) =>
    call<FuturesQuote>(`/api/market/futures/quote${session ? `?session=${session}` : ""}`),
  /** #444 그 세션의 분봉(오래된 것부터) */
  futuresCandles: (session?: FuturesSession, interval: FuturesInterval = "1m") =>
    call<FuturesCandles>(`/api/market/futures/candles?interval=${interval}${session ? `&session=${session}` : ""}`),
  /** 매매 복기(백엔드 desk/review.py): 최근 days 일 체결을 그때의 분석 계획과 묶는다. 읽기 전용. */
  review: (days: number) => call<ReviewResponse>(`/api/review?days=${days}`),
  quote: (ticker: string) =>
    call<{ ticker: string; price: number; currency: string | null; source: string; fetched_at: string }>(
      `/api/quote?ticker=${encodeURIComponent(ticker)}`,
    ),
  estimate: (ticker: string) => call<Estimate>(`/api/estimate?ticker=${encodeURIComponent(ticker)}`),
  /** #123 시세판: 여러 종목(최대 30)의 실시간 · 추정가 */
  board: (tickers: string[]) =>
    call<{ items: BoardItem[] }>(`/api/estimates?tickers=${encodeURIComponent(tickers.join(","))}`),
  /** #271 바이낸스 선물 롱/숏 비율(최대 30종목, 계약 없으면 contract: null) */
  longShort: (tickers: string[]) =>
    call<{ items: LongShortItem[] }>(`/api/longshort?tickers=${encodeURIComponent(tickers.join(","))}`),
  /** #156 섹터 탭(계정별). 처음 읽으면 기본 섹터가 생긴다 — 쓰기 전에 먼저 읽는다. */
  sectors: () => call<Sector[]>("/api/sectors"),
  createSector: (name: string, tickers: { ticker: string; name?: string | null }[] = []) =>
    call<Sector>("/api/sectors", { method: "POST", body: JSON.stringify({ name, tickers }) }),
  updateSector: (id: number, patch: { name?: string; tickers?: { ticker: string; name?: string | null }[] }) =>
    call<Sector>(`/api/sectors/${id}`, { method: "PUT", body: JSON.stringify(patch) }),
  reorderSectors: (ids: number[]) => call<Sector[]>("/api/sectors/order", { method: "PUT", body: JSON.stringify({ ids }) }),
  deleteSector: (id: number) => call<void>(`/api/sectors/${id}`, { method: "DELETE" }),
  /** #154 공매도 · 옵션 포지션 — 미국 종목만(나머지 supported: false) */
  positioning: (ticker: string) => call<Positioning>(`/api/positioning?ticker=${encodeURIComponent(ticker)}`),
  /** #240 장기 바닥권(인생 타점) */
  bottomZone: (ticker: string) => call<BottomZone>(`/api/bottom-zone?ticker=${encodeURIComponent(ticker)}`),
  positioningBatch: (tickers: string[]) =>
    call<{ items: Positioning[] }>(`/api/positioning/batch?tickers=${encodeURIComponent(tickers.join(","))}`),
  /** #155 빚투 · 예탁금(금융투자협회) */
  credit: (days = 90) => call<CreditView>(`/api/market/credit?days=${days}`),
  /** #134 즐겨찾기(계정별 · 순서대로). 추가 · 교체도 전체 목록을 돌려준다. */
  favorites: () => call<Favorite[]>("/api/favorites"),
  addFavorite: (ticker: string, name?: string) =>
    call<Favorite[]>("/api/favorites", { method: "POST", body: JSON.stringify({ ticker, name }) }),
  replaceFavorites: (items: { ticker: string; name?: string | null }[]) =>
    call<Favorite[]>("/api/favorites", { method: "PUT", body: JSON.stringify({ items }) }),
  removeFavorite: (ticker: string) => call<void>(`/api/favorites/${encodeURIComponent(ticker)}`, { method: "DELETE" }),
  /** #123 추정 봉 — 계약이 없으면 supported: false */
  estimateCandles: (ticker: string, interval: string) =>
    call<EstimatedCandles | { ticker: string; supported: false }>(
      `/api/estimate/candles?ticker=${encodeURIComponent(ticker)}&interval=${interval}`),
  /** indicators: true = RSI 만(예전 호출), 또는 ["rsi", "stoch", "bb"] 처럼 고른 지표(#206) */
  candles: (ticker: string, interval: CandleInterval, count: number, indicators: boolean | string[] = false) => {
    const wanted = indicators === true ? ["rsi14"] : indicators === false ? [] : indicators;
    return call<Candles>(
      `/api/candles?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(interval)}&count=${count}` +
        (wanted.length ? `&indicators=${encodeURIComponent(wanted.join(","))}` : ""),
    );
  },
  /** #631 · #632 차트만 읽기 — 시간봉(5분 ~ 일)별 흐름 · 가까운 선 · 쉬운 문장(규칙 기반, 가장 짧은 봉이 끝날 때까지 캐시) */
  chartRead: (ticker: string) => call<ChartRead>(`/api/chart-read?ticker=${encodeURIComponent(ticker)}`),
  /** #653 마지막 봉 · 현재가(서버 2초 캐시) — 차트가 보일 때만 몇 초마다 */
  candlesLive: (ticker: string, interval: CandleInterval) =>
    call<CandleLive>(`/api/candles/live?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(interval)}`),
  /** #203 RSI · 스토캐스틱 과열 · 침체 판정과 과거 극단 뒤 통계(레버리지는 기초자산 판정도) */
  oscillator: (ticker: string, interval: "1d" | "1w" = "1d") =>
    call<OscillatorView>(`/api/oscillator?ticker=${encodeURIComponent(ticker)}&interval=${interval}`),
  /** 지지 · 저항(backend desk/sr.py) — 지금 봉 기준, 서버가 몇 분 캐시. */
  /** #128: 분석(run)의 구역 · 사다리 · 손절 · 목표는 그대로, 조건 · 점수 · 상태만 지금 값. ticker · horizon 은 run 에서 */
  livePlaybooks: (run: string) => call<LivePlaybooks>(`/api/playbooks?run=${encodeURIComponent(run)}`),
  /** #317 분석 없이 종목만으로 — 지금 봉 · 현재가로 새로 계산한 롱 · 숏 플레이북 */
  playbooksFor: (ticker: string, horizon?: string) =>
    call<LivePlaybooks>(`/api/playbooks?ticker=${encodeURIComponent(ticker)}${horizon ? `&horizon=${encodeURIComponent(horizon)}` : ""}`),
  /** #148 다가오는 FOMC · 주요 BLS 발표(공식 일정, 서버가 백그라운드로 갱신)와 지금 열린 진입 보류 창 */
  /** #244 섹터 · 인물 이슈(기사 급증) — 헤드라인에 한글 판정 */
  sectorNews: (sector?: string) => call<SectorNewsView>(`/api/sector-news${sector ? `?sector=${encodeURIComponent(sector)}` : ""}`),
  econCalendar: (days = 14) => call<EconCalendar>(`/api/events/calendar?days=${days}`),
  /** #243 한 달의 휴장 · 발표(KST) · 만기 · 실적 — month "YYYY-MM", 없으면 이번 달 */
  marketMonth: (month?: string) => call<MarketMonth>(`/api/calendar${month ? `?month=${encodeURIComponent(month)}` : ""}`),
  /** run 을 주면(#465) 그 분석의 시간축으로 계획선 근거(basis) · 해설(explain)도 — horizon 은 넣지 않는다 */
  levels: (ticker: string, run?: string) =>
    call<SupportResistance>(`/api/levels?ticker=${encodeURIComponent(ticker)}${run ? `&run=${encodeURIComponent(run)}` : ""}`),
  /** #299 차트 예상도: 보정한 확률 원뿔 · 레벨 경로 · 설명 */
  projection: (ticker: string, horizon: string) =>
    call<Projection>(`/api/projection?ticker=${encodeURIComponent(ticker)}&horizon=${encodeURIComponent(horizon)}`),
  instruments: (q: string) => call<Instrument[]>(`/api/instruments?q=${encodeURIComponent(q)}`),
  instrumentsStatus: () =>
    call<{ source: string; count: number; updated_at: string | null; error: string | null }>("/api/instruments/status"),
  /** #296 종목 정보 카드(#295) */
  stockProfile: (ticker: string) => call<StockProfile>(`/api/stock/profile?ticker=${encodeURIComponent(ticker)}`),
  /** #484 유명 기관 13F(#483): 목록 · 한 기관의 분기 보유 */
  funds: () => call<{ funds: FundBrief[]; checked_at: string | null }>("/api/funds"),
  /** 즐겨찾기 배지: 종목별 각 기관 최신 분기 변화(최대 30개, 변화 없는 종목은 빠짐) */
  fundChanges: (tickers: string[]) => call<FundChanges>(`/api/funds/changes?tickers=${encodeURIComponent(tickers.slice(0, 30).join(","))}`),
  fund: (id: string, quarter?: string) =>
    call<FundDetail>(`/api/funds/${encodeURIComponent(id)}${quarter ? `?quarter=${encodeURIComponent(quarter)}` : ""}`),
  /** #496 초보자 리포트(#495) — 목록 · 본문 · 지금 하나 만들기(동시 1개) · 진행 상태 */
  reports: (before?: string) => call<ReportList>(`/api/reports?limit=20${before ? `&before=${encodeURIComponent(before)}` : ""}`),
  report: (id: string) => call<ReportDetail>(`/api/reports/${encodeURIComponent(id)}`),
  /** 202 { generating: true } · 이미 만드는 중이면 409 */
  createReport: () => call<{ generating: boolean }>("/api/reports", { method: "POST" }),
  /** #515 비트코인 반감기 사이클 비교(#514, 하루 캐시) */
  halving: () => call<Halving>("/api/crypto/halving"),
  /** #332 금리 · 달러 국면과 매매 팁(#330) */
  /** #577 세션 갭 메움 확률(서버 30분 캐시) */
  gaps: (ticker: string) => call<GapReport>(`/api/gaps?ticker=${encodeURIComponent(ticker)}`),
  /** #612 평행 채널 · 짧은 추세선(#611) — 5m · 15m · 60m · 240m · 1d */
  lines: (ticker: string, tf: LineTf) => call<LinesReport>(`/api/lines?ticker=${encodeURIComponent(ticker)}&tf=${tf}`),
  tips: (ticker?: string) => call<TipsView>(`/api/tips${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`),
  /** #362 웹 푸시(#361): 서버 공개키 · 이 기기 구독 · 시험 알림 */
  pushKey: () => call<{ enabled: boolean; public_key: string | null }>("/api/push/key"),
  pushSubscribe: (sub: PushSubscriptionJSON, device: string, kinds: PushKinds) =>
    call<{ endpoint: string; device: string; kinds: PushKinds }>("/api/push/subscriptions", {
      method: "POST", body: JSON.stringify({ endpoint: sub.endpoint, keys: sub.keys, device, kinds }),
    }),
  pushUnsubscribe: (endpoint: string) =>
    call<void>("/api/push/subscriptions", { method: "DELETE", body: JSON.stringify({ endpoint }) }),
  // #573 아침 시황(#571) — 아직 없으면 404
  morningBrief: (date?: string) =>
    call<MorningBrief>(`/api/briefs/morning${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  morningBriefs: (limit = 20) => call<MorningBriefList>(`/api/briefs/morning/list?limit=${limit}`),
  // #573 오늘 방향(#572) — 판정이 없으면 200 + call: null
  dayCall: (market: DayCallMarket) => call<DayCallToday>(`/api/day-call?market=${market}`),
  dayCallHistory: (market: DayCallMarket, days = 90) =>
    call<DayCallHistory>(`/api/day-call/history?market=${market}&days=${days}`),
  dayCallStats: (market: DayCallMarket, days = 90) =>
    call<DayCallStats>(`/api/day-call/stats?market=${market}&days=${days}`),
  pushTest: () => call<{ sent: number }>("/api/push/test", { method: "POST" }),
  /** #362 오늘(또는 date) 보낸 수급 전향 알림 — 앱 안 토스트가 폴링한다 */
  flowAlerts: (date?: string) =>
    call<{ date: string; items: FlowAlert[] }>(`/api/alerts/flow${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  /** #581 오늘(또는 date, KST) 알림 타임라인 — 수급 전향(#361) · 수급 흐름 변화 · NQ 이상 징후(#579), 오래된 것부터 */
  // #624 데이터 원천 상태(#623)
  status: () => call<DeskStatus>("/api/status"),
  alertsToday: (date?: string) =>
    call<MarketAlertsDay>(`/api/alerts/today${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  // #581 알림 채널(#580) — 텔레그램 · 카카오톡. 웹 푸시는 기기별 구독(/api/push/*)
  notifyChannels: () => call<NotifyChannels>("/api/notify/channels"),
  saveNotifyChannel: (id: NotifyChannelId, body: { enabled?: boolean; kinds?: Record<string, boolean> }) =>
    call<NotifyChannel>(`/api/notify/channels/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  testNotifyChannel: (id: NotifyChannelId) =>
    call<{ ok: boolean; error: string | null }>(`/api/notify/channels/${id}/test`, { method: "POST" }),
  kakaoConnect: () => call<{ url: string }>("/api/notify/kakao/connect", { method: "POST" }),
  kakaoDisconnect: () => call<void>("/api/notify/kakao", { method: "DELETE" }),
  /** #614 차트 선 알림(#613) 감시 설정 · 최근 보낸 선 알림 */
  lineWatch: () => call<LineWatch>("/api/notify/line-watch"),
  saveLineWatch: (body: Partial<Pick<LineWatch, "enabled" | "tickers" | "timeframes" | "events">>) =>
    call<LineWatch>("/api/notify/line-watch", { method: "PUT", body: JSON.stringify(body) }),
};

/** #705 GET /api/batch/{kind} — 종목마다 같은 경로를 부르던 화면이 한 번에(최대 30종목, 서버가 4개씩 같은 핸들러로) */
export type BatchKind = "bottom-zone" | "candles" | "levels" | "playbooks" | "projection" | "gaps" | "estimate-candles";
const BATCH_MAX = 30;
const batchFailed = (x: unknown): boolean =>
  x != null && typeof x === "object" && typeof (x as { error?: unknown }).error === "number"
  && Object.keys(x).every((k) => k === "error" || k === "detail");

/**
 * 종목 → 값(그 종목이 실패하면 null). 30개 넘으면 나눠 부른다. 일괄 경로가 없는 서버(배포 사이 404)면 single 로 하나씩.
 * 묶음 자체가 실패하면(네트워크 · 5xx) 모두 null — 화면은 종목마다 실패로 그린다.
 */
export async function batchGet<T>(kind: BatchKind, tickers: string[], params: Record<string, string | number> = {},
  single?: (ticker: string) => Promise<T>): Promise<Record<string, T | null>> {
  const names = [...new Set(tickers.filter(Boolean))];
  const out: Record<string, T | null> = {};
  if (!names.length) return out;
  const extra = Object.entries(params).map(([k, v]) => `&${k}=${encodeURIComponent(String(v))}`).join("");
  const parts: string[][] = [];
  for (let i = 0; i < names.length; i += BATCH_MAX) parts.push(names.slice(i, i + BATCH_MAX));
  await Promise.all(parts.map(async (part) => {
    try {
      const got = await call<{ kind: string; items: Record<string, unknown> }>(
        `/api/batch/${kind}?tickers=${encodeURIComponent(part.join(","))}${extra}`);
      for (const t of part) {
        const v = got.items?.[t];
        out[t] = v == null || batchFailed(v) ? null : (v as T);
      }
    } catch (e) {
      if (single && e instanceof ApiError && e.status === 404) {
        const each = await Promise.allSettled(part.map((t) => single(t)));
        part.forEach((t, i) => { const r = each[i]; out[t] = r.status === "fulfilled" ? r.value : null; });
      } else {
        for (const t of part) out[t] = null;
      }
    }
  }));
  return out;
}

/** #361 구독 알림 종류 — flow 는 전체 스위치, 나머지는 그 시장의 전향만(빠진 키는 서버가 켜짐으로) */
export interface PushKinds {
  flow: boolean;
  flow_futures: boolean;
  flow_kospi: boolean;
  flow_kosdaq: boolean;
  morning_brief?: boolean;   // #573 아침 시황(#571)
  day_call?: boolean;        // #573 오늘 방향(#572)
  flow_shift?: boolean;      // #581 수급 흐름 변화(#579)
  nq_alert?: boolean;        // #581 나스닥 선물 이상 징후(#579)
  line_signal?: boolean;     // #614 차트 선 닿음 · 이탈(#613)
  tone_positive?: boolean;   // #642 수급 알림의 뜻(#641) — 끈 tone 은 이 기기로 보내지 않는다
  tone_neutral?: boolean;
  tone_caution?: boolean;
  tone_warning?: boolean;
}

/** #581 알림 타임라인 한 건(GET /api/alerts/today) — flow_turn 은 #361 의 수급 전향 */
export interface MarketAlert {
  trade_date: string;
  kind: "flow_turn" | "flow_shift" | "nq_alert";
  rule: string;
  market: "kospi" | "kosdaq" | "futures" | "nq";
  who: "foreign" | "institution" | "individual" | "all";
  severity: "info" | "caution" | "strong";
  /** #642 지수에 어떤 뜻인지(#641) — 크기(severity)와 따로. 예전 서버 · 예전 알림에는 없다 */
  tone?: AlertTone;
  t: string;               // ISO (KST 오프셋)
  value: number | null;
  prev: number | null;
  text: string;            // 서버가 만든 한국어 한 줄
  pushed: boolean;         // 푸시로 나간 것(info 는 타임라인에만)
}
/** #641 호재 · 참고 · 주의 · 경고 */
export type AlertTone = "positive" | "neutral" | "caution" | "warning";
export interface MarketAlertsDay {
  date: string;
  items: MarketAlert[];
}

/** #581 알림 채널(#580) */
export type NotifyChannelId = "telegram" | "kakao";
export interface NotifyChannel {
  id: NotifyChannelId;
  label: string;
  configured: boolean;     // 서버 env 가 있는지
  connected: boolean;      // 텔레그램 = configured, 카카오 = OAuth 를 마쳤는지
  enabled: boolean;
  kinds: Record<string, boolean>;
  needs_reconnect: boolean;
  refresh_expires_at: string | null;
}
export interface NotifyChannels {
  kinds: string[];
  can_edit: boolean;
  channels: NotifyChannel[];
}

/** #361 수급 전향 한 건 */
export interface FlowAlert {
  trade_date: string;
  market: "futures" | "kospi" | "kosdaq";
  kind: "buy_turn" | "sell_turn";
  t: string;
  value: number | null;
  text: string;
}

/** Follow a run's events. The server replays from the start, then streams live. */
export function followRun(id: string, onEvent: (e: DeskEvent) => void): () => void {
  const source = new EventSource(`/api/runs/${id}/stream`);
  source.onmessage = (msg) => {
    const event: DeskEvent = JSON.parse(msg.data);
    onEvent(event);
    if (event.type === "done") source.close(); // otherwise EventSource reconnects forever
  };
  source.onerror = () => {
    // A 401 closes the stream for good; ask the API once so an ended session shows the login screen.
    if (source.readyState === EventSource.CLOSED) api.me().catch(() => {});
  };
  return () => source.close();
}

export type StreamState = "live" | "lost";

/** Live "내 투자" snapshots every few seconds (GET /api/portfolio/stream).
 * EventSource retries a dropped connection by itself, but any non-200 answer (a 502 while
 * the backend is redeployed, a 401 after logout) ends it for good. Then: ask the API once -
 * a 401 shows the login screen through call(); anything else opens a new stream after a
 * growing pause. ``onState`` lets the panel say it is reconnecting while it keeps the last numbers. */
export function followPortfolio(onSnapshot: (p: Portfolio) => void, onState: (s: StreamState) => void): () => void {
  let source: EventSource | null = null;
  let timer: number | undefined;
  let stopped = false;
  let delay = 3000;

  const retry = () => {
    if (stopped) return;
    timer = window.setTimeout(open, delay);
    delay = Math.min(delay * 2, 60000);
  };
  function open() {
    if (stopped) return;
    const es = new EventSource("/api/portfolio/stream");
    source = es;
    es.onopen = () => {
      delay = 3000;
      onState("live");
    };
    es.onmessage = (msg) => onSnapshot(JSON.parse(msg.data));
    es.onerror = () => {
      onState("lost");
      if (es.readyState !== EventSource.CLOSED) return; // the browser is retrying
      api.me().then(retry, (e) => {
        if (!(e instanceof Unauthorized)) retry();
      });
    };
  }
  open();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    source?.close();
  };
}

/** GET /api/live/stream 한 프레임(백엔드 desk/live.py): 장중이면 현재가 · 최근 1분봉, 장 마감이면 state=closed. */
export interface LiveFrame {
  state: "open" | "closed";
  ticker: string;
  quote?: { price: number; currency: string | null; source: string; fetched_at: string };
  bars?: Candles["bars"];
  source?: string;
  quote_error?: string;
  bars_error?: string;
}

/** 실시간 차트(#26)의 흐름. 끊기면 followPortfolio 처럼 API 에 한 번 묻고 점점 늦게 다시 연다. */
export function followLive(ticker: string, onFrame: (f: LiveFrame) => void, onState: (s: StreamState) => void): () => void {
  let source: EventSource | null = null;
  let timer: number | undefined;
  let stopped = false;
  let delay = 3000;

  const retry = () => {
    if (stopped) return;
    timer = window.setTimeout(open, delay);
    delay = Math.min(delay * 2, 60000);
  };
  function open() {
    if (stopped) return;
    const es = new EventSource(`/api/live/stream?ticker=${encodeURIComponent(ticker)}`);
    source = es;
    es.onopen = () => {
      delay = 3000;
      onState("live");
    };
    es.onmessage = (msg) => onFrame(JSON.parse(msg.data));
    es.onerror = () => {
      onState("lost");
      if (es.readyState !== EventSource.CLOSED) return;
      api.me().then(retry, (e) => {
        if (!(e instanceof Unauthorized)) retry();
      });
    };
  }
  open();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    source?.close();
  };
}

/** #203 · #206 GET /api/oscillator — RSI · 스토캐스틱 과열 · 침체 판정과 과거 극단 뒤 통계 */
export type OscKind = "rsi_hot" | "rsi_cold" | "stoch_hot" | "stoch_cold";
export interface OscEpisode {
  kind: OscKind;
  at: string;
  value: number;
  ret5?: number; ret10?: number; ret20?: number; max_drop?: number; max_rise?: number;
}
export interface OscStats {
  n: number; thin: boolean; up20?: number | null; ret5?: number | null; ret10?: number | null; ret20?: number | null;
  max_drop?: number | null; max_rise?: number | null;
}
export interface OscReading {
  ticker: string;
  interval: string;
  zone: "hot" | "cold" | "neutral" | null;
  by?: "rsi" | "stoch" | null;
  rsi?: number | null;
  stoch_k?: number | null;
  stoch_d?: number | null;
  divergence?: "bearish" | "bullish" | null;
  /** 한국어 근거 문장 */
  reason?: string;
  by_kind?: Partial<Record<OscKind, OscStats>>;
  recent?: OscEpisode[];
  episodes?: OscEpisode[];
  years?: number;
  bars?: number;
  error?: string;
  leverage?: number;
}
export interface OscillatorView {
  ticker: string;
  interval: "1d" | "1w";
  own: OscReading;
  underlying: OscReading | null;
}

/** #312 지금가 기준 최적 타점(롱 · 숏 중 계획당 기대 R 이 큰 쪽, 관망 이유) — #313 차트 "타점 보기" */
export function entryNow(ticker: string, horizon?: string): Promise<import("./types").EntryNow> {
  const q = new URLSearchParams({ ticker, ...(horizon ? { horizon } : {}) });
  return call(`/api/entry-now?${q}`);
}

/** #623 GET /api/status — 백그라운드 작업 · 원천마다 한 줄. 오류는 예외 종류 이름(또는 짧은 고정 문구)만 */
export interface StatusJob {
  name: string;
  label: string;
  source: string;
  parent: string | null;
  state: "fresh" | "stale" | "error" | "starting" | "running" | "stopped" | "off";
  runs: number;
  errors: number;
  last_run: string | null;
  last_ok: string | null;
  last_error: string | null;
  last_error_at: string | null;
  next_due: string | null;
  interval_sec: number | null;
}
export interface StatusCooldown { name: string; label: string; until: string | null; left_sec: number }
export interface DeskStatus { now: string; jobs: StatusJob[]; cooldowns: StatusCooldown[] }

/** #614 차트 선 알림(#613 GET · PUT /api/notify/line-watch) — 감시는 주인이 켜기 전까지 꺼짐 */
export type LineWatchEvent = "touch" | "break" | "retest" | "long";
export interface LineSignal {
  trade_date: string;        // KST 날짜
  ticker: string;
  timeframe: string;         // 5m · 15m · 60m · 120m · 240m
  event: LineWatchEvent;
  rule: string;
  line_id: string;
  at: string;                // 그 봉 시각(ISO)
  price: number;
  line_price: number;
  text: string;              // "제목\n본문"
}
export interface LineWatch {
  enabled: boolean;
  tickers: string[];
  timeframes: string[];
  events: Partial<Record<LineWatchEvent, boolean>>;
  can_edit: boolean;
  allowed: { timeframes: string[]; events: LineWatchEvent[]; max_tickers: number };
  limits: { cooldown_min: number; cooldown_bars: number; daily_max: number; per_ticker_max: number };
  recent: LineSignal[];
}
