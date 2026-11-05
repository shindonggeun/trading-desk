import { useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { agentInfo } from "../agents";
import { koreanizeLabels } from "../korean";
import { annotate } from "./Glossary";
import type { DeskEvent } from "../types";

const COLLAPSE_AT = 900; // characters; analyst reports run long

// 용어 사전(#24): 본문 용어에 점선 밑줄 + 뜻 — 같은 용어는 문단 · 항목마다 처음 한 번만.
// 모듈 상수여야 한다: 렌더마다 새 함수면 React 가 다른 컴포넌트로 보고 본문을 매번 다시 만든다(선택 · 포커스가 풀림).
// 기억(Set)은 블록마다 새로 — 메시지 전체가 하나를 쓰면 StrictMode 의 두 번째 렌더에서 밑줄이 사라진다.
const WITH_TERMS: Components = {
  p: ({ children }) => <p>{annotate(children, new Set())}</p>,
  li: ({ children }) => <li>{annotate(children, new Set())}</li>,
  td: ({ children }) => <td>{annotate(children, new Set())}</td>,
};

export function ChatMessage({ event }: { event: DeskEvent }) {
  const info = agentInfo(event.agent);
  const text = koreanizeLabels(event.content ?? "");
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
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={WITH_TERMS}>{text}</ReactMarkdown>
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
  const thinking = event.agent === "consensus" ? "결론 정리가 토론 전체를 한 장으로 요약하는 중" : "레버리지 분석가가 본주 판단을 상품 기준으로 옮기는 중";
  const text: Record<string, string> = { queued: "대기열에 등록됨", running: "분석 시작", thinking, done: "분석 완료", failed: "분석 실패" };
  return (
    <div className={`sys-line ${event.type === "error" ? "sys-error" : ""}`}>
      {event.type === "error" ? event.content : text[event.content ?? ""] ?? event.content}
    </div>
  );
}
