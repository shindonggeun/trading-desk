import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { chatApi, chatError, sendChat, toolLabel, toolSummary } from "../chat";
import type { ChatMessageRow, ChatThread, ChatTool } from "../types";

/**
 * #315 AI 채팅 본체 — 메뉴 "채팅" 화면과 옆 패널이 같이 쓴다. 왼쪽(패널은 위 선택 상자) 대화 목록, 오른쪽 말풍선.
 * 답은 SSE 로 흘러나오고, 도구 호출은 "KORU 플레이북" 같은 칩으로 접힌다(펼치면 데스크 숫자 요약).
 * AI 답(의견)과 도구가 가져온 데스크 숫자를 다른 모양으로 나눠 보인다. 대화는 서버에 남아 새로고침해도 그대로다.
 *
 * 보안: 답은 모델 출력이라 마크다운만 그린다 — react-markdown 은 raw HTML 을 그리지 않고(rehype-raw 없음),
 * 링크는 http(s) · 앱 안(#/) 만, 밖으로 가는 링크는 rel="noopener noreferrer". 도구 요약 · 인자는 글자로만.
 */

const SAFE = /^(https?:\/\/|#\/)/i;
const SAFE_WEB = /^https?:\/\//i;   // #403 기사 링크는 밖으로만(서버도 걸렀지만 한 번 더)
const MD: Components = {
  a: ({ href, children }) => {
    if (!href || !SAFE.test(href)) return <span>{children}</span>;
    return href.startsWith("#/") ? <a href={href}>{children}</a> : <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
  },
  img: () => null,
};

const QUICK = [
  { label: "지금 거시 요약", text: "지금 거시 · 정책 흐름을 짧게 요약해 줘.", needs: null },
  { label: "이번 주 일정", text: "이번 주 국장 · 미장 중요 일정(발표 · 실적 · 휴장)을 알려 줘.", needs: null },
  { label: "이 종목 지금 타점", text: "{t} 지금가 기준 롱 · 숏 타점과 손절 · 목표를 데스크 숫자로 알려 줘.", needs: "ticker" },
  { label: "이 분석 너는 어떻게 봐?", text: "{t} 의 최근 분석(run {r}) 결론을 너는 어떻게 봐? 데스크 숫자와 다른 점이 있으면 짚어 줘.", needs: "run" },
] as const;

type Live = { text: string; tools: (ChatTool & { pending?: boolean })[] } | null;

const time = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "" : new Date(t).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
};

function tickersOf(tools: ChatTool[], ctx: string | null): string[] {
  const out = new Set<string>();
  if (ctx) out.add(ctx);
  for (const t of tools) if (typeof t.args?.ticker === "string") out.add(t.args.ticker.toUpperCase());
  return [...out].slice(0, 4);
}

function Tools({ tools }: { tools: (ChatTool & { pending?: boolean })[] }) {
  if (!tools.length) return null;
  return (
    <div className="ch-tools" aria-label="데스크 숫자(도구 조회)">
      {tools.map((t, i) => {
        const s = toolSummary(t.summary);
        const links = (t.links ?? []).filter((l) => SAFE_WEB.test(l.url));
        const line = links.length ? `기사 ${links.length}건` : s.line;
        return (
          <details key={`${t.name}-${i}`} className={`ch-tool${t.pending ? " ch-tool-busy" : ""}${s.error ? " ch-tool-err" : ""}`}>
            <summary>
              <span aria-hidden>{t.pending ? "⏳" : s.error ? "⚠" : links.length ? "📰" : "📊"}</span> {toolLabel(t.name, t.args)}
              {t.pending ? " 조회 중…" : <span className="ch-tool-line"> — {line}</span>}
            </summary>
            {links.length > 0 ? (
              <ul className="ch-tool-links">
                {links.map((l) => (
                  <li key={l.url}>
                    <a href={l.url} target="_blank" rel="noopener noreferrer">{l.title}</a>
                    <span className="ch-tool-src">{[l.source, l.published ? time(l.published) : null].filter(Boolean).join(" · ")}</span>
                  </li>
                ))}
              </ul>
            ) : s.raw && <pre className="ch-tool-raw">{s.raw}</pre>}
          </details>
        );
      })}
    </div>
  );
}

function Bubble({ m, ctxTicker }: { m: ChatMessageRow; ctxTicker: string | null }) {
  if (m.role === "user") {
    return (
      <div className="ch-row ch-user">
        <div className="ch-bubble"><p>{m.content}</p><time className="ch-time">{time(m.created_at)}</time></div>
      </div>
    );
  }
  const links = tickersOf(m.tools, ctxTicker);
  return (
    <div className="ch-row ch-ai">
      <div className="ch-bubble">
        <Tools tools={m.tools} />
        <span className="ch-tag">AI 의견</span>
        <div className="md ch-md"><ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>{m.content}</ReactMarkdown></div>
        <div className="ch-foot">
          {links.map((t) => <a key={t} className="chip" href={`#/chart/${encodeURIComponent(t)}`}>{t} 차트</a>)}
          <time className="ch-time" title="답을 쓴 시각 — 데스크 숫자는 이때 조회한 값">{time(m.created_at)} 기준</time>
        </div>
      </div>
    </div>
  );
}

export default function ChatCore({ ticker = null, run = null, prompt = null, compact = false, initialThread = null, onThread, onDone }: {
  ticker?: string | null; run?: string | null; prompt?: string | null; compact?: boolean;
  initialThread?: string | null; onThread?: (id: string | null) => void;
  /** #340 답 하나가 끝났을 때(위젯이 닫혀 있으면 새 답 점) */
  onDone?: () => void;
}) {
  const [threads, setThreads] = useState<ChatThread[] | null>(null);
  const [current, setCurrent] = useState<string | null>(initialThread);
  const [messages, setMessages] = useState<ChatMessageRow[]>([]);
  const [live, setLive] = useState<Live>(null);
  const [draft, setDraft] = useState(prompt ?? "");
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  const loadThreads = useCallback(() => chatApi.threads().then((r) => { setThreads(r.items); return r.items; }, (e) => { setError(chatError(e)); setThreads([]); return []; }), []);
  useEffect(() => { loadThreads(); }, [loadThreads]);
  useEffect(() => { setDraft(prompt ?? ""); }, [prompt]);

  const open = useCallback((id: string | null) => {
    setCurrent(id);
    onThread?.(id);
    setMessages([]);
    setError(null);
    setDrawer(false);
    if (id) chatApi.messages(id).then((r) => setMessages(r.items), (e) => setError(chatError(e)));
  }, [onThread]);
  useEffect(() => { if (initialThread) open(initialThread); }, [initialThread, open]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [messages, live]);
  useEffect(() => () => abort.current?.abort(), []);

  const busy = live !== null;
  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || busy) return;
    setError(null);
    let id = current;
    try {
      if (!id) {
        const t = await chatApi.create();
        id = t.id;
        setCurrent(id);
        onThread?.(id);
        setThreads((old) => [t, ...(old ?? [])]);
      }
    } catch (e) {
      setError(chatError(e));
      return;
    }
    const mine: ChatMessageRow = { id: `local-${Date.now()}`, role: "user", content: text, tools: [], created_at: new Date().toISOString() };
    setMessages((old) => [...old, mine]);
    setDraft("");
    const ctl = new AbortController();
    abort.current = ctl;
    let acc: NonNullable<Live> = { text: "", tools: [] };
    setLive(acc);
    try {
      await sendChat(id, text, ticker, ctl.signal, (ev) => {
        if (ev.type === "tool_call") acc = { ...acc, tools: [...acc.tools, { name: ev.name, args: ev.args, summary: null, pending: true }] };
        else if (ev.type === "tool_result") {
          const i = acc.tools.findIndex((t) => t.pending && t.name === ev.name);
          const tools = [...acc.tools];
          if (i >= 0) tools[i] = { ...tools[i], summary: ev.summary, links: ev.links, pending: false };
          else tools.push({ name: ev.name, args: null, summary: ev.summary, links: ev.links });
          acc = { ...acc, tools };
        } else if (ev.type === "token") acc = { ...acc, text: acc.text + ev.text };
        else if (ev.type === "error") setError(/[가-힣]/.test(ev.message) ? ev.message : "답을 쓰다가 문제가 생겼습니다. 다시 보내 보세요.");
        else if (ev.type === "done") {
          const at = new Date().toISOString();
          setMessages((old) => [...old.map((m) => (m.id === mine.id ? { ...m, id: ev.user_message_id } : m)),
            { id: ev.message_id, role: "assistant", content: acc.text, tools: acc.tools.map(({ pending: _p, ...t }) => t), created_at: at }]);
          onDone?.();
        }
        setLive(acc);
      });
    } catch (e) {
      setError(chatError(e));
      // 답이 하나도 오기 전에 거절됐으면(429 · 409 …) 내 말풍선을 거두고 입력창에 되돌린다
      if (!acc.text && !acc.tools.length && !(e instanceof DOMException && e.name === "AbortError")) {
        setMessages((old) => old.filter((m) => m.id !== mine.id));
        setDraft(text);
      }
      // 멈췄거나 끊겼으면 받은 데까지는 보인다 — 저장된 답은 다시 불러오면 나온다
      if (acc.text) setMessages((old) => [...old, { id: `partial-${Date.now()}`, role: "assistant", content: `${acc.text}\n\n_(중단됨)_`, tools: acc.tools, created_at: new Date().toISOString() }]);
    } finally {
      abort.current = null;
      setLive(null);
      loadThreads();
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("이 대화를 지울까요?")) return;
    try {
      await chatApi.remove(id);
      setThreads((old) => (old ?? []).filter((t) => t.id !== id));
      if (current === id) open(null);
    } catch (e) {
      setError(chatError(e));
    }
  };

  const quick = QUICK.filter((q) => (q.needs === "ticker" ? !!ticker : q.needs === "run" ? !!ticker && !!run : true));
  const list = (
    <div className="ch-list">
      <button type="button" onClick={() => open(null)} disabled={busy}>＋ 새 대화</button>
      {threads === null ? <p className="muted small">불러오는 중…</p> : threads.length === 0 ? <p className="muted small">대화가 없습니다.</p> : (
        <ul>
          {threads.map((t) => (
            <li key={t.id} className={t.id === current ? "ch-on" : undefined}>
              <button type="button" className="ch-thread" onClick={() => open(t.id)} disabled={busy} title={t.title}>
                <span className="ch-thread-title">{t.title}</span>
                <span className="muted small">{time(t.updated_at)}</span>
              </button>
              <button type="button" className="link small" aria-label={`${t.title} 지우기`} onClick={() => remove(t.id)} disabled={busy}>지우기</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );

  return (
    <div className={`ch${compact ? " ch-compact" : ""}`}>
      {!compact && <aside className={`ch-side${drawer ? " ch-side-open" : ""}`} aria-label="대화 목록">{list}</aside>}
      <section className="ch-main" aria-label="대화">
        <div className="ch-top">
          {compact ? (
            <>
              <label className="sr-only" htmlFor="ch-pick">대화 고르기</label>
              <select id="ch-pick" value={current ?? ""} onChange={(e) => open(e.target.value || null)} disabled={busy}>
                <option value="">＋ 새 대화</option>
                {(threads ?? []).map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
              </select>
            </>
          ) : (
            <button type="button" className="secondary ch-drawer-btn" aria-expanded={drawer} onClick={() => setDrawer((v) => !v)}>대화 목록</button>
          )}
          {ticker && !compact && <span className="pill" title="질문과 함께 보내는 종목 문맥">{ticker}{run ? " · 분석" : ""}</span>}
        </div>
        <div className="ch-log" aria-live="polite">
          {!current && messages.length === 0 && !busy && (
            <p className="muted small ch-hello">데스크 데이터(브리핑 · 일정 · 플레이북 · 시세 …)를 조회해 답합니다. 📊 칩은 데스크 숫자, 말풍선은 AI 의견입니다.</p>
          )}
          {messages.map((m) => <Bubble key={m.id} m={m} ctxTicker={ticker} />)}
          {live && (
            <div className="ch-row ch-ai">
              <div className="ch-bubble">
                <Tools tools={live.tools} />
                <span className="ch-tag">AI 의견</span>
                <div className="md ch-md">
                  {live.text ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>{live.text}</ReactMarkdown> : <p className="muted">생각하는 중…</p>}
                </div>
              </div>
            </div>
          )}
          <div ref={end} />
        </div>
        {error && <p className="error small ch-error" role="alert">{error}</p>}
        <div className="ch-quick chips" role="group" aria-label="빠른 질문">
          {quick.map((q) => (
            <button key={q.label} type="button" className="chip" disabled={busy}
              onClick={() => send(q.text.replace("{t}", ticker ?? "").replace("{r}", run ?? ""))}>{q.label}</button>
          ))}
        </div>
        <form className="ch-input" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
          <label className="sr-only" htmlFor={compact ? "ch-text-panel" : "ch-text"}>질문</label>
          <textarea id={compact ? "ch-text-panel" : "ch-text"} value={draft} maxLength={2000} rows={2} placeholder="데스크에 물어보기 — Enter 보내기, Shift+Enter 줄바꿈"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(draft); } }} />
          {busy ? (
            <button type="button" className="secondary" onClick={() => abort.current?.abort()}>중단</button>
          ) : (
            <button type="submit" disabled={!draft.trim()}>보내기</button>
          )}
        </form>
      </section>
    </div>
  );
}
