import type { DeskEvent, Health, Holding, RegimeSnapshot, Run } from "./types";

// The API token (DESK_API_TOKEN) is kept per browser; the page asks for it on a 401.
const TOKEN_KEY = "desk.token";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage blocked: the token lives for this page only */
  }
}

export class Unauthorized extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (res.status === 401) throw new Unauthorized("unauthorized");
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((d: any) => d.msg).join(", ")
      : body.detail ?? res.statusText;
    throw new Error(detail);
  }
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
  health: () => call<Health>("/api/health"),
  config: () => call<{ analysts: string[]; provider: string; deep_model: string | null; quick_model: string | null; output_language: string }>("/api/config"),
  runs: () => call<Run[]>("/api/runs"),
  run: (id: string) => call<Run>(`/api/runs/${id}`),
  createRun: (body: NewRun) => call<Run>("/api/runs", { method: "POST", body: JSON.stringify(body) }),
  portfolio: () => call<{ broker: string; error?: string; holdings: Holding[] }>("/api/portfolio"),
  regime: () => call<RegimeSnapshot>("/api/regime"),
};

/** Follow a run's events. The server replays from the start, then streams live. */
export function followRun(id: string, onEvent: (e: DeskEvent) => void): () => void {
  const token = getToken();
  const source = new EventSource(`/api/runs/${id}/stream${token ? `?token=${encodeURIComponent(token)}` : ""}`);
  source.onmessage = (msg) => {
    const event: DeskEvent = JSON.parse(msg.data);
    onEvent(event);
    if (event.type === "done") source.close(); // otherwise EventSource reconnects forever
  };
  return () => source.close();
}
