import { useCallback, useEffect, useRef, useState } from "react";
import { CHAT_OPEN_EVENT, type ChatOpen } from "../chat";
import ChatCore from "./ChatCore";

/**
 * #340 채널톡형 AI 채팅 — 화면 오른쪽 아래 둥근 버튼을 누르면 채팅 창이 뜬다(휴대폰은 전체 화면).
 * - 어느 화면에서든 같은 대화를 이어 간다: 창을 닫아도 대화는 그대로 두고(답을 쓰는 중이면 계속 받는다),
 *   닫힌 사이 답이 끝나면 버튼에 점이 뜬다. 열림 · 마지막 대화는 이 브라우저에 기억한다.
 * - 지금 보는 종목(차트 `#/chart/NVDA`, 주소의 `t=`)을 "NVDA 보는 중" 칩으로 보이고 질문에 함께 보낸다(끌 수 있다).
 * - 차트 · 분석 화면의 "AI 에게 묻기"(openChat)는 그 종목 · 분석 · 질문 문맥으로 창을 연다.
 */

const OPEN_KEY = "desk.chat.open";
const THREAD_KEY = "desk.chat.thread";
const TICKER_RE = /^[A-Za-z0-9.\-^=]{1,15}$/;

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* 저장 못 해도 동작은 같다 */
  }
}

/** 주소에서 지금 보는 종목 — `#/chart/NVDA?…` 또는 `…?t=NVDA` */
export function pageTicker(hash: string): string | null {
  const chart = hash.match(/^#\/chart\/([^?]+)/);
  let raw: string | null = null;
  if (chart) {
    try {
      raw = decodeURIComponent(chart[1]);
    } catch {
      raw = null;
    }
  } else {
    const q = hash.indexOf("?");
    raw = q >= 0 ? new URLSearchParams(hash.slice(q + 1)).get("t") : null;
  }
  return raw && TICKER_RE.test(raw) ? raw.toUpperCase() : null;
}

export default function ChatWidget() {
  const [open, setOpen] = useState(() => read(OPEN_KEY) === "1");
  const [mounted, setMounted] = useState(open);
  const [unread, setUnread] = useState(false);
  const [hash, setHash] = useState(() => window.location.hash);
  const [given, setGiven] = useState<ChatOpen | null>(null);   // openChat 으로 받은 문맥(종목 · 분석 · 질문)
  const [useTicker, setUseTicker] = useState(true);
  const [thread, setThread] = useState(() => read(THREAD_KEY));
  const openRef = useRef(open);
  const launcher = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);

  const show = useCallback((next: boolean) => {
    openRef.current = next;
    setOpen(next);
    write(OPEN_KEY, next ? "1" : null);
    if (next) {
      setMounted(true);
      setUnread(false);
    }
  }, []);

  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  useEffect(() => {
    const on = (e: Event) => {
      setGiven((e as CustomEvent<ChatOpen>).detail ?? {});
      setUseTicker(true);
      show(true);
    };
    window.addEventListener(CHAT_OPEN_EVENT, on);
    return () => window.removeEventListener(CHAT_OPEN_EVENT, on);
  }, [show]);

  // 열면 입력창으로, 닫으면 버튼으로 포커스를 옮긴다. Esc 로 닫는다.
  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => panel.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus(), 0);
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        show(false);
        launcher.current?.focus();
      }
    };
    window.addEventListener("keydown", esc);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", esc);
    };
  }, [open, show]);

  // 화면을 옮기면 openChat 으로 받은 문맥은 내려놓고, 그 화면의 종목을 다시 쓴다
  const seenHash = useRef(hash);
  useEffect(() => {
    if (seenHash.current === hash) return;
    seenHash.current = hash;
    setGiven(null);
    setUseTicker(true);
  }, [hash]);

  const onThread = useCallback((id: string | null) => {
    setThread(id);
    write(THREAD_KEY, id);
  }, []);
  const onDone = useCallback(() => {
    if (!openRef.current) setUnread(true);
  }, []);

  // 채팅 크게 보기 화면에선 보이지 않게만 한다 — 언마운트하면 받던 답이 끊긴다
  const onChatPage = /^#\/chat(\?|$)/.test(hash);
  const seen = given?.ticker ?? pageTicker(hash);
  const ticker = useTicker ? seen : null;
  const run = useTicker ? given?.run ?? null : null;
  const full = `#/chat?${new URLSearchParams({ ...(thread ? { thread } : {}), ...(ticker ? { t: ticker } : {}), ...(run ? { run } : {}) })}`;

  return (
    <>
      {mounted && (
        <section ref={panel} className="cw-panel" role="dialog" aria-label="AI 채팅" hidden={!open || onChatPage}>
          <header className="cw-head">
            <span className="cw-avatar" aria-hidden>TD</span>
            <div className="cw-title">
              <strong>데스크 AI</strong>
              <span className="muted small">데스크 숫자를 조회해 답합니다</span>
            </div>
            <a className="link small" href={full} onClick={() => show(false)}>크게 보기</a>
            <button type="button" className="cw-close" onClick={() => { show(false); launcher.current?.focus(); }} aria-label="채팅 닫기">×</button>
          </header>
          {seen && (
            <div className="cw-ctx">
              <button type="button" className={`chip${useTicker ? " on" : ""}`} aria-pressed={useTicker}
                title={useTicker ? "질문과 함께 이 종목을 보냅니다 — 누르면 끕니다" : "누르면 이 종목을 질문에 붙입니다"}
                onClick={() => setUseTicker((v) => !v)}>
                {seen} 보는 중{given?.run && useTicker ? " · 분석" : ""} {useTicker ? "✓" : "끔"}
              </button>
            </div>
          )}
          <ChatCore compact ticker={ticker} run={run} prompt={given?.prompt ?? null}
            initialThread={thread} onThread={onThread} onDone={onDone} />
        </section>
      )}
      <button ref={launcher} type="button" className={`cw-launcher${open ? " cw-on" : ""}`} hidden={onChatPage}
        aria-label={open ? "AI 채팅 닫기" : unread ? "AI 채팅 열기 — 새 답 있음" : "AI 채팅 열기"}
        aria-expanded={open} onClick={() => show(!open)}>
        {open ? <span aria-hidden>×</span> : (
          <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden>
            <path fill="currentColor" d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-4.5 3.6A.6.6 0 0 1 3.5 21V18A2 2 0 0 1 2 16V6a2 2 0 0 1 2-2Zm3 6.5a1.5 1.5 0 1 0 0 .01Zm5 0a1.5 1.5 0 1 0 0 .01Zm5 0a1.5 1.5 0 1 0 0 .01Z" />
          </svg>
        )}
        {unread && !open && <span className="cw-dot" aria-hidden />}
      </button>
    </>
  );
}
