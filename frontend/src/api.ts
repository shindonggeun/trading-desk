import type { DeskConfig, DeskEvent, Estimate, FlowInterval, FlowMarket, Instrument, MarketFlow, PlanStats, Portfolio, RegimeSnapshot, Run, RunPage, SupportResistance } from "./types";

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

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (res.status === 401 && path !== "/api/auth/login") {
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
export type CandleInterval = "1m" | "5m" | "15m" | "30m" | "60m" | "240m" | "1d" | "1w" | "1M" | "1y";

/** GET /api/candles — 봉은 오래된 것부터, at 은 봉 시작 시각(UTC ISO). */
export interface Candles {
  ticker: string;
  interval: string;
  source: string;
  fetched_at: string;
  /** rsi 는 indicators 에 rsi14 를 요청했을 때만(역사가 짧으면 앞쪽 null). */
  bars: { at: string; open: number; high: number; low: number; close: number; volume: number; rsi?: number | null }[];
  indicators?: string[];
  /** 야후 분봉 보관 한도(일). 분봉이 아니면 null. */
  history_limit_days?: number | null;
  /** 요청한 개수보다 적게 왔다. 안내는 history_limit_days 가 있을 때만 — 년봉은 늘 적게 온다. */
  truncated?: boolean;
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
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  createRun: (body: NewRun) => call<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  portfolio: () => call<Portfolio>("/api/portfolio"),
  regime: () => call<RegimeSnapshot>("/api/regime"),
  /** 계획 채점 통계(#64 desk/outcome.py) — 서버가 보관 기간까지로 자른다. */
  planStats: (days = 90) => call<PlanStats>(`/api/stats/plans?days=${days}`),
  /** 코스피 · 코스닥 투자자별 매매대금(#76). 장중 60초 · 장 밖 10분 서버 캐시. */
  marketFlow: (market: FlowMarket, interval: FlowInterval, count = 20) =>
    call<MarketFlow>(`/api/market/flow?market=${market}&interval=${interval}&count=${count}`),
  /** 매매 복기(백엔드 desk/review.py): 최근 days 일 체결을 그때의 분석 계획과 묶는다. 읽기 전용. */
  review: (days: number) => call<ReviewResponse>(`/api/review?days=${days}`),
  quote: (ticker: string) =>
    call<{ ticker: string; price: number; currency: string | null; source: string; fetched_at: string }>(
      `/api/quote?ticker=${encodeURIComponent(ticker)}`,
    ),
  estimate: (ticker: string) => call<Estimate>(`/api/estimate?ticker=${encodeURIComponent(ticker)}`),
  candles: (ticker: string, interval: CandleInterval, count: number, rsi = false) =>
    call<Candles>(
      `/api/candles?ticker=${encodeURIComponent(ticker)}&interval=${encodeURIComponent(interval)}&count=${count}${rsi ? "&indicators=rsi14" : ""}`,
    ),
  /** 지지 · 저항(backend desk/sr.py) — 지금 봉 기준, 서버가 몇 분 캐시. */
  levels: (ticker: string) => call<SupportResistance>(`/api/levels?ticker=${encodeURIComponent(ticker)}`),
  instruments: (q: string) => call<Instrument[]>(`/api/instruments?q=${encodeURIComponent(q)}`),
  instrumentsStatus: () =>
    call<{ source: string; count: number; updated_at: string | null; error: string | null }>("/api/instruments/status"),
};

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
