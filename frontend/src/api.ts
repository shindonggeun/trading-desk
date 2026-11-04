import type { DeskConfig, DeskEvent, Holding, RegimeSnapshot, Run } from "./types";

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
  trade_date?: string;
  analysts: string[];
  use_portfolio: boolean;
  output_language?: string;
  max_debate_rounds?: number;
}

export const api = {
  login: (username: string, password: string) =>
    call<void>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => call<void>("/api/auth/logout", { method: "POST" }),
  me: () => call<{ username: string }>("/api/auth/me"),
  config: () => call<DeskConfig>("/api/config"),
  runs: () => call<Run[]>("/api/runs"),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  createRun: (body: NewRun) => call<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  portfolio: () => call<{ broker: string; error?: string; holdings: Holding[] }>("/api/portfolio"),
  regime: () => call<RegimeSnapshot>("/api/regime"),
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
