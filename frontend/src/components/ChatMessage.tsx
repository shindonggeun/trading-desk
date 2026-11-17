import { memo, useState } from "react";
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

function ChatMessageView({ event }: { event: DeskEvent }) {
  const info = agentInfo(event.agent);
  const text = koreanizeLabels(event.content ?? "");
  const long = text.length > COLLAPSE_AT;
  const [open, setOpen] = useState(!long);
  // #171: a news/fundamentals report taken over from a recent run of the same ticker
  const reused = event.data?.reused as { minutes: number; filed_at: string } | undefined;
  return (
    <article className={`msg team-${info.team}`}>
      <div className="avatar" aria-hidden>{info.short}</div>
      <div className="bubble">
        <header>
          <strong>{info.name}</strong>
          <span className="msg-meta">
            {reused && (
              <span className="pill" title={`${new Date(reused.filed_at).toLocaleTimeString("ko-KR")}에 작성된 보고서`}>
                {reused.minutes < 1 ? "방금" : `${reused.minutes}분 전`} 보고서 재사용
              </span>
            )}
            <time>{new Date(event.ts).toLocaleTimeString("ko-KR")}</time>
          </span>
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
function ToolRowView({ events }: { events: DeskEvent[] }) {
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

function SystemLineView({ event }: { event: DeskEvent }) {
  const thinking = event.agent === "consensus" ? "결론 정리가 토론 전체를 한 장으로 요약하는 중" : "레버리지 분석가가 본주 판단을 상품 기준으로 옮기는 중";
  // #438 전망 이벤트는 본문(표)을 펼치지 않고 한 줄로 — 그림 · 요약은 차트 탭
  if (event.type === "explain") return null;   // #494 쉬운 해설(#493)은 "쉽게 보기" 탭에서만
  if (event.type === "outlook") return <div className="sys-line">1주 · 1개월 방향 전망을 정리함 — 차트 탭에서 원뿔과 요약을 볼 수 있습니다</div>;
  const text: Record<string, string> = { queued: "대기열에 등록됨", running: "분석 시작", thinking, skipped: "결론 정리를 만들지 못해 건너뜀", done: "분석 완료", failed: "분석 실패" };
  return (
    <div className={`sys-line ${event.type === "error" ? "sys-error" : ""}`}>
      {event.type === "error" ? event.content : text[event.content ?? ""] ?? event.content}
    </div>
  );
}

/*
 * #537 토론 탭은 이벤트가 올 때마다 목록 전체를 다시 그린다 — 발언(마크다운)은 같은 이벤트 객체면 다시 그리지 않는다.
 * 도구 묶음은 이벤트가 늘 때 새 배열로 오므로 길이 · 처음 · 끝 이벤트로 같은지 본다.
 */
export const ChatMessage = memo(ChatMessageView);
export const SystemLine = memo(SystemLineView);
export const ToolRow = memo(ToolRowView, (a, b) =>
  a.events.length === b.events.length && a.events[0] === b.events[0] && a.events[a.events.length - 1] === b.events[b.events.length - 1]);
