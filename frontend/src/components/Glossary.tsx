import { Children, cloneElement, createContext, isValidElement, useContext, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import { GLOSSARY, glossaryEntry, splitTerms, termsIn, type GlossaryCategory } from "../glossary";
import { koreanizeLabels } from "../korean";
import type { DeskEvent } from "../types";

/** 본문 용어를 누르면 패널이 열리고 그 항목으로 간다. */
const GlossaryContext = createContext<{ focus: (id: string) => void } | null>(null);

export function GlossaryProvider({ children, onFocus }: { children: ReactNode; onFocus: (id: string) => void }) {
  const value = useMemo(() => ({ focus: onFocus }), [onFocus]);
  return <GlossaryContext.Provider value={value}>{children}</GlossaryContext.Provider>;
}

const WIDE = "(min-width: 1200px)";
const isWide = () => typeof window !== "undefined" && window.matchMedia(WIDE).matches;
let tipSeq = 0;

function Term({ text, id }: { text: string; id: string }) {
  const ctx = useContext(GlossaryContext);
  const entry = glossaryEntry(id);
  const [tipId] = useState(() => `gl-tip-${++tipSeq}`);
  const [flip, setFlip] = useState(false);
  if (!entry) return <>{text}</>;
  // 오른쪽 끝 용어는 툴팁을 왼쪽으로 펼친다 — 화면 밖으로 나가지 않게.
  const place = (el: HTMLElement) => setFlip(el.getBoundingClientRect().left + 290 > window.innerWidth);
  // 넓은 화면에서만 패널로 이동한다. 좁은 화면은 패널이 위에 있어 읽던 자리를 잃으니 툴팁(포커스)만.
  const open = () => {
    if (isWide()) ctx?.focus(id);
  };
  return (
    <span
      className={`gl-term ${flip ? "gl-flip" : ""}`}
      tabIndex={0}
      aria-describedby={tipId}
      onMouseEnter={(e) => place(e.currentTarget)}
      onFocus={(e) => place(e.currentTarget)}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          ctx?.focus(id);
        }
      }}
    >
      {text}
      <span className="gl-tip" id={tipId} role="tooltip">
        <strong>{entry.term}</strong> {entry.desc}
      </span>
    </span>
  );
}

/**
 * 글 조각 안의 용어에 점선 밑줄 + 툴팁을 단다. 코드 · 링크 안은 건드리지 않는다.
 * `seen` 은 호출한 블록(문단 · 항목 · 셀) 안에서 같은 용어를 처음 한 번만 표시하게 한다.
 */
export function annotate(children: ReactNode, seen: Set<string>): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") {
      const pieces = splitTerms(child, seen);
      if (pieces.length === 1 && typeof pieces[0] === "string") return child;
      return pieces.map((p, i) => (typeof p === "string" ? p : <Term key={i} text={p.text} id={p.id} />));
    }
    if (isValidElement(child)) {
      const el = child as ReactElement<{ children?: ReactNode }>;
      if (el.type === "code" || el.type === "a" || el.type === "pre") return child;
      if (el.props.children === undefined) return child;
      return cloneElement(el, undefined, annotate(el.props.children, seen));
    }
    return child;
  });
}

const CATEGORIES: GlossaryCategory[] = ["지표", "판단", "상품", "거시", "재무"];

/** 분석 화면 오른쪽 용어 사전 — 이번 대화에 나온 용어를 위에, 나머지는 분류별로. 좁은 화면에서는 접힌다. */
export function GlossaryPanel({ events, focusId, focusSeq }: { events: DeskEvent[]; focusId: string | null; focusSeq: number }) {
  const [open, setOpen] = useState(() => typeof window === "undefined" || isWide());
  const [query, setQuery] = useState("");
  const list = useRef<HTMLDivElement>(null);

  const used = useMemo(() => {
    // 본문(ChatMessage)과 같은 글로 찾는다 — 영어 라벨을 한글로 바꾼 뒤.
    const text = events.filter((e) => e.type === "message").map((e) => koreanizeLabels(e.content ?? "")).join("\n");
    return termsIn(text);
  }, [events]);

  // 본문에서 용어를 누르면 열고 그 항목으로 스크롤.
  useEffect(() => {
    if (!focusId) return;
    setOpen(true);
    setQuery("");
    // 닫혀 있었다면 열린 뒤 그려진 목록에서 찾는다(한 프레임 뒤).
    const frame = requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const el = list.current?.querySelector<HTMLElement>(`[data-term="${focusId}"]`);
        el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        el?.focus({ preventScroll: true });
      }),
    );
    return () => cancelAnimationFrame(frame);
  }, [focusId, focusSeq]);

  const q = query.trim().toLowerCase();
  const hit = (id: string) => {
    if (!q) return true;
    const e = glossaryEntry(id)!;
    return [e.term, e.desc, e.read ?? "", ...e.match].some((t) => t.toLowerCase().includes(q));
  };
  const usedShown = used.filter(hit);
  const rest = GLOSSARY.filter((e) => !used.includes(e.id) && hit(e.id));

  const item = (id: string) => {
    const e = glossaryEntry(id)!;
    return (
      <div key={id} className={`gl-item ${id === focusId ? "gl-focus" : ""}`} data-term={id} tabIndex={-1}>
        <div className="gl-name">{e.term}</div>
        <div className="small">{e.desc}</div>
        {e.read && <div className="small muted gl-read">읽는 법 · {e.read}</div>}
      </div>
    );
  };

  return (
    <aside className={`card glossary ${open ? "" : "glossary-closed"}`} aria-label="쉬운 용어 사전">
      <button type="button" className="glossary-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span>용어 사전</span>
        <span className="muted small">{used.length ? `이번 분석 ${used.length}개` : ""} {open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="glossary-body" ref={list}>
          <input
            type="search"
            className="glossary-search"
            placeholder="용어 찾기 (예: RSI, 손익비)"
            aria-label="용어 찾기"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {usedShown.length > 0 && (
            <section>
              <h4>이번 분석에 나온 용어</h4>
              {usedShown.map(item)}
            </section>
          )}
          {CATEGORIES.map((cat) => {
            const rows = rest.filter((e) => e.category === cat);
            return rows.length ? (
              <section key={cat}>
                <h4>{cat}</h4>
                {rows.map((e) => item(e.id))}
              </section>
            ) : null;
          })}
          {usedShown.length === 0 && rest.length === 0 && <p className="muted small">찾는 용어가 없습니다.</p>}
          <p className="muted small gl-note">읽기 도우미입니다 — 투자 조언이 아닙니다.</p>
        </div>
      )}
    </aside>
  );
}
