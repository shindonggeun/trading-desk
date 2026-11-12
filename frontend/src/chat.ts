import { ApiError, Unauthorized, api } from "./api";
import type { ChatEvent, ChatMessageRow, ChatThread } from "./types";

/**
 * #315 AI 채팅(#314 /api/chat/*) — 대화 목록 · 메시지는 보통 JSON, 보내기는 POST 응답을 SSE 로 읽는다.
 * EventSource 는 POST 를 못 하므로 fetch + ReadableStream 으로 `event:` · `data:` 프레임을 직접 나눈다(쿠키 인증 그대로).
 */

export const chatApi = {
  threads: () => api.raw<{ items: ChatThread[] }>("/api/chat/threads"),
  create: (title?: string) => api.raw<ChatThread>("/api/chat/threads", { method: "POST", body: JSON.stringify(title ? { title } : {}) }),
  remove: (id: string) => api.raw<void>(`/api/chat/threads/${encodeURIComponent(id)}`, { method: "DELETE" }),
  messages: (id: string) =>
    api.raw<{ thread: ChatThread; items: ChatMessageRow[] }>(`/api/chat/threads/${encodeURIComponent(id)}/messages`),
};

/** 서버 오류를 사람이 읽을 한국어로 — 원문 예외 문자열은 그대로 내지 않는다 */
export function chatError(e: unknown): string {
  if (e instanceof DOMException && e.name === "AbortError") return "보내기를 멈췄습니다.";
  if (e instanceof Unauthorized) return "로그인이 끝났습니다. 다시 로그인하세요.";
  if (e instanceof ApiError) {
    if (e.status === 429) return e.message && /[가-힣]/.test(e.message) ? e.message : "모델 속도 제한 중입니다 — 잠시 뒤 다시 보내세요.";
    if (e.status === 409) return /[가-힣]/.test(e.message) ? e.message : "이미 답을 쓰는 중이거나 대화가 너무 깁니다. 새 대화를 여세요.";
    if (e.status === 404) return "대화를 찾을 수 없습니다(지워졌을 수 있음).";
    if (e.status === 422) return "질문은 1~2000자로 보내세요.";
    return `서버가 요청을 처리하지 못했습니다(${e.status}).`;
  }
  return "연결이 끊겼습니다. 대화를 다시 불러오면 저장된 데까지 보입니다.";
}

/** 질문을 보내고 프레임마다 onEvent — done · error 뒤 끝난다 */
export async function sendChat(thread: string, text: string, ticker: string | null, signal: AbortSignal,
  onEvent: (e: ChatEvent) => void): Promise<void> {
  const res = await fetch(`/api/chat/threads/${encodeURIComponent(thread)}/messages`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(ticker ? { text, ticker } : { text }),
    signal,
  });
  if (res.status === 401) throw new Unauthorized("login required");
  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(typeof body.detail === "string" ? body.detail : res.statusText, res.status, Number.isFinite(retry) ? retry : undefined);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value.replace(/\r\n/g, "\n");
    let cut: number;
    while ((cut = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      let type = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (!data.length) continue;
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(data.join("\n"));
      } catch {
        continue;
      }
      onEvent({ type, ...payload } as ChatEvent);
      if (type === "done" || type === "error") return;
    }
  }
}

/** 어디서든 옆 패널을 연다 — 차트 · 분석 화면의 "AI 에게 묻기" */
export type ChatOpen = { ticker?: string | null; run?: string | null; prompt?: string | null };
export const CHAT_OPEN_EVENT = "desk:open-chat";
export function openChat(detail: ChatOpen) {
  window.dispatchEvent(new CustomEvent<ChatOpen>(CHAT_OPEN_EVENT, { detail }));
}

/** 도구 이름 → 칩 문구 */
const TOOL_KO: Record<string, string> = {
  macro_brief: "거시 브리핑", calendar: "일정", playbook: "플레이북", playbooks: "플레이북", quote: "시세", price: "시세",
  bottom_zone: "인생 타점", projection: "예상도", runs: "분석 기록", run: "분석", sector_news: "섹터 이슈", levels: "지지 · 저항",
  longshort: "롱/숏 비율", futures: "선물 수급", flow: "시장 수급", weekly_map: "주간 지도",
};
export function toolLabel(name: string, args?: Record<string, unknown> | null): string {
  const base = TOOL_KO[name] ?? name.replace(/^get_|^desk_/, "").replace(/_/g, " ");
  const t = typeof args?.ticker === "string" ? args.ticker : null;
  return t ? `${t} ${base}` : base;
}
