import { useEffect, useState } from "react";
import { CHAT_OPEN_EVENT, type ChatOpen } from "../chat";
import ChatCore from "./ChatCore";

/**
 * #315 어느 화면에서든 여는 옆 패널 — 차트 · 분석 화면의 "AI 에게 묻기"(openChat)가 지금 보는 종목 · 분석을 문맥으로 넘긴다.
 * 휴대폰 폭에서는 화면 전체를 덮는다. Esc 로 닫는다.
 */
export default function ChatPanel() {
  const [ctx, setCtx] = useState<ChatOpen | null>(null);
  const [seq, setSeq] = useState(0);
  useEffect(() => {
    const on = (e: Event) => {
      setCtx((e as CustomEvent<ChatOpen>).detail ?? {});
      setSeq((n) => n + 1);
    };
    window.addEventListener(CHAT_OPEN_EVENT, on);
    return () => window.removeEventListener(CHAT_OPEN_EVENT, on);
  }, []);
  useEffect(() => {
    if (!ctx) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setCtx(null);
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [ctx]);
  if (!ctx) return null;
  const full = `#/chat?${new URLSearchParams({ ...(ctx.ticker ? { t: ctx.ticker } : {}), ...(ctx.run ? { run: ctx.run } : {}) })}`;
  return (
    <aside className="ch-panel" role="dialog" aria-label="AI 에게 묻기">
      <header className="ch-panel-head">
        <strong>AI 에게 묻기</strong>
        <a className="small" href={full} onClick={() => setCtx(null)}>크게 보기</a>
        <button type="button" className="link" onClick={() => setCtx(null)} aria-label="채팅 닫기">닫기</button>
      </header>
      <ChatCore key={seq} compact ticker={ctx.ticker ?? null} run={ctx.run ?? null} prompt={ctx.prompt ?? null} />
    </aside>
  );
}
