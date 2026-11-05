import type { DeskConfig, Estimate, DeskEvent, Holding, Instrument, RegimeSnapshot, Run, RunPage } from "./types";

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
  portfolio: () => call<{ broker: string; error?: string; holdings: Holding[] }>("/api/portfolio"),
  regime: () => call<RegimeSnapshot>("/api/regime"),
  /** 매매 복기(백엔드 desk/review.py): 최근 days 일 체결을 그때의 분석 계획과 묶는다. 읽기 전용. */
  review: (days: number) => call<ReviewResponse>(`/api/review?days=${days}`),
  quote: (ticker: string) =>
    call<{ ticker: string; price: number; currency: string | null; source: string; fetched_at: string }>(
      `/api/quote?ticker=${encodeURIComponent(ticker)}`,
    ),
  estimate: (ticker: string) => call<Estimate>(`/api/estimate?ticker=${encodeURIComponent(ticker)}`),
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
