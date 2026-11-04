import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentInfo } from "../agents";
import type { DeskEvent } from "../types";

const COLLAPSE_AT = 900; // characters; analyst reports run long

export function ChatMessage({ event }: { event: DeskEvent }) {
  const info = agentInfo(event.agent);
  const text = event.content ?? "";
  const long = text.length > COLLAPSE_AT;
  const [open, setOpen] = useState(!long);
  return (
    <article className={`msg team-${info.team}`}>
      <div className="avatar" aria-hidden>{info.short}</div>
      <div className="bubble">
        <header>
          <strong>{info.name}</strong>
          <time>{new Date(event.ts).toLocaleTimeString("ko-KR")}</time>
        </header>
        <div className={`md ${open ? "" : "md-clipped"}`}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
        </div>
        {long && (
          <button className="link" onClick={() => setOpen(!open)}>
            {open ? "접기" : "전체 보기"}
          </button>
        )}
      </div>
    </article>
  );
}

/** Consecutive tool calls folded into one line of chips. */
export function ToolRow({ events }: { events: DeskEvent[] }) {
  return (
    <div className="tool-row">
      <span className="muted small">데이터 조회</span>
      {events.map((e) => (
        <code key={e.seq} title={JSON.stringify(e.data?.args ?? {})}>
          {e.content}
        </code>
      ))}
    </div>
  );
}

export function SystemLine({ event }: { event: DeskEvent }) {
  const text: Record<string, string> = { queued: "대기열에 등록됨", running: "분석 시작", done: "분석 완료", failed: "분석 실패" };
  return (
    <div className={`sys-line ${event.type === "error" ? "sys-error" : ""}`}>
      {event.type === "error" ? event.content : text[event.content ?? ""] ?? event.content}
    </div>
  );
}
