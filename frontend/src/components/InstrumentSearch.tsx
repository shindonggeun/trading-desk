import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api";
import type { Instrument } from "../types";

const MARKET_LABEL: Record<string, string> = {
  KOSPI: "코스피",
  KOSDAQ: "코스닥",
  NASDAQ: "나스닥",
  NYSE: "뉴욕",
  AMEX: "아멕스",
};
const TYPE_LABEL: Record<string, string> = { ETF: "ETF", FOREIGN_ETF: "ETF", ETN: "ETN" };

export interface Picked {
  ticker: string;
  name?: string;
}

/**
 * 종목 검색: 한글 이름 · 티커 · 종목코드 어느 쪽으로 쳐도 후보가 뜬다(백엔드 /api/instruments).
 * 목록에 없는 티커(BTC-USD 등)는 그대로 입력해도 된다 — 그때 name 은 비어 있다.
 */
export default function InstrumentSearch({ value, onChange, onPick, inputId, placeholder }: {
  value: Picked;
  onChange: (v: Picked) => void;
  /** 목록에서 골랐을 때(직접 친 티커는 아님) — 대시보드 검색 줄이 "분석 시작" 으로 초점을 옮긴다(#100) */
  onPick?: (v: Picked) => void;
  inputId?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState(value.name ? `${value.name} (${value.ticker})` : value.ticker);
  const [hits, setHits] = useState<Instrument[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const seq = useRef(0);

  useEffect(() => {
    const q = text.trim();
    if (!open || !q) {
      setHits([]);
      return;
    }
    const mine = ++seq.current;
    const timer = window.setTimeout(() => {
      api
        .instruments(q)
        .then((rows) => {
          if (mine === seq.current) {
            setHits(rows);
            setActive(0);
          }
        })
        .catch(() => mine === seq.current && setHits([]));
    }, 180);
    return () => window.clearTimeout(timer);
  }, [text, open]);

  const pick = (hit: Instrument) => {
    onChange({ ticker: hit.ticker, name: hit.name });
    setText(`${hit.name} (${hit.ticker})`);
    setOpen(false);
    onPick?.({ ticker: hit.ticker, name: hit.name });
  };

  const typed = (raw: string) => {
    setText(raw);
    setOpen(true);
    setHits([]); // 이전 검색어의 후보를 새 결과가 올 때까지 보여 주지 않는다
    // 고르기 전까지는 친 글자를 티커로 둔다 — 목록에 없는 종목도 분석할 수 있게
    onChange({ ticker: raw.trim().toUpperCase() });
  };

  const onKey = (e: React.KeyboardEvent) => {
    // 한글 조합 중 Enter(Windows 는 조합 끝 Enter 를 두 번 보낸다)를 고르기로 받으면, 두 번째 Enter 가
    // "분석 시작" 으로 넘어가 의도하지 않은 분석이 시작될 수 있다
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (!open || hits.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + hits.length) % hits.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      pick(hits[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="search">
      <input
        id={inputId}
        role="combobox"
        aria-expanded={open && hits.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        value={text}
        onChange={(e) => typed(e.target.value)}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={onKey}
        placeholder={placeholder ?? "엔비디아 · 삼성전자 · NVDA · 005930"}
        autoComplete="off"
        spellCheck={false}
        required
      />
      {open && hits.length > 0 && (
        <ul className="search-list" id={listId} role="listbox">
          {hits.map((hit, i) => (
            <li
              key={hit.symbol}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(hit);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="search-name">{hit.name}</span>
              <span className="search-meta">
                {TYPE_LABEL[hit.security_type] && <span className="pill">{TYPE_LABEL[hit.security_type]}</span>}
                <code>{hit.symbol}</code> {MARKET_LABEL[hit.market] ?? hit.market}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
