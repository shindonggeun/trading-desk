/** 대시보드 "시장" 패널(#75)의 종목 목록 · 접힘 상태 — 이 브라우저에만 기억한다. */

export interface MarketSymbol {
  ticker: string;
  name: string;
}

export const DEFAULT_MARKETS: MarketSymbol[] = [
  { ticker: "NQ=F", name: "나스닥100 선물" },
  { ticker: "ES=F", name: "S&P500 선물" },
  { ticker: "^KS11", name: "코스피" },
  { ticker: "^KQ11", name: "코스닥" },
  { ticker: "^SOX", name: "필라델피아 반도체" },
  { ticker: "GC=F", name: "금 선물" },
  { ticker: "KRW=X", name: "달러/원" },
  { ticker: "BTC-USD", name: "비트코인" },
  { ticker: "ETH-USD", name: "이더리움" },
  { ticker: "XRP-USD", name: "XRP" },
  { ticker: "SOL-USD", name: "솔라나" },
];

/** 기본 종목을 반영한 기록이 없던 시절(#463 이전)의 기본 목록 — 그때 저장한 목록은 이 종목들을 이미 본 것으로 친다. */
const LEGACY_DEFAULTS = ["NQ=F", "ES=F", "^KS11", "^KQ11", "^SOX", "GC=F", "KRW=X", "BTC-USD"];

const LIST_KEY = "desk.markets";
// 저장된 목록이 이미 반영한 기본 종목 티커들(#463). 새 기본 종목은 여기 없는 것만 한 번 합치고,
// 사용자가 지운 기본 종목은 여기 남아 있어 되살리지 않는다.
const SEEN_KEY = "desk.markets.defaultsSeen";
const OPEN_KEY = "desk.marketBoard.open";
const MODE_KEY = "desk.marketBoard.mode";
export const MARKETS_CHANGED = "desk-markets-changed";
// 백엔드 desk/api.py clean_ticker 가 받는 문자(영숫자 . - ^ =)와 같게
export const TICKER = /^[A-Za-z0-9.\-^=]{1,20}$/;

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // 사생활 모드 · 막힌 저장소 — 기본값으로
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* 기억하지 못해도 화면은 돈다 */
  }
}

function readSeen(): Set<string> {
  const raw = read(SEEN_KEY);
  if (raw) {
    try {
      const list = JSON.parse(raw);
      // 손으로 비우거나 망친 값이어도 예전 기본 종목은 본 것으로 둔다 — 지운 종목이 되살아나지 않게
      if (Array.isArray(list)) return new Set([...LEGACY_DEFAULTS, ...list.filter((t): t is string => typeof t === "string")]);
    } catch {
      /* 깨진 값은 예전 기본 목록으로 */
    }
  }
  return new Set(LEGACY_DEFAULTS);
}

/**
 * 저장된 목록에 아직 반영하지 않은 기본 종목을 끝에 붙인다(순수 함수).
 * seen 에 있는 기본 종목은 사용자가 이미 보고 남기거나 지운 것이라 건드리지 않는다.
 */
export function mergeNewDefaults(list: MarketSymbol[], seen: Set<string>): MarketSymbol[] {
  const have = new Set(list.map((m) => m.ticker));
  const added = DEFAULT_MARKETS.filter((d) => !seen.has(d.ticker) && !have.has(d.ticker));
  return added.length ? [...list, ...added] : list;
}

function parseList(raw: string): MarketSymbol[] | null {
  try {
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return null;
    // 손으로 고친 값 · 예전 값: 형식이 틀리거나 겹치는 티커는 버린다
    const seen = new Set<string>();
    return list.flatMap((m) => {
      if (!m || typeof m.ticker !== "string" || typeof m.name !== "string") return [];
      const ticker = m.ticker.trim().toUpperCase();
      if (!TICKER.test(ticker) || seen.has(ticker)) return [];
      seen.add(ticker);
      return [{ ticker, name: m.name.trim() || ticker }];
    });
  } catch {
    return null; // 깨진 값은 기본 목록으로
  }
}

export function loadMarkets(): MarketSymbol[] {
  const raw = read(LIST_KEY);
  if (!raw) return DEFAULT_MARKETS;
  const list = parseList(raw);
  if (!list) return DEFAULT_MARKETS;
  const seen = readSeen();
  if (DEFAULT_MARKETS.every((d) => seen.has(d.ticker))) return list;
  // 새 기본 종목이 생겼다: 한 번 합쳐 저장하고, 지금 기본 종목을 모두 본 것으로 기록한다
  const merged = mergeNewDefaults(list, seen);
  if (merged !== list) write(LIST_KEY, JSON.stringify(merged));
  write(SEEN_KEY, JSON.stringify(seenWithDefaults(seen)));
  return merged;
}

/** 지금까지 본 티커 + 지금 기본 종목(합집합) — 기본에서 뺐다가 다시 넣은 티커도 지운 사용자에게 되살리지 않게. */
function seenWithDefaults(seen: Set<string>): string[] {
  return [...new Set([...seen, ...DEFAULT_MARKETS.map((d) => d.ticker)])];
}

export function saveMarkets(list: MarketSymbol[] | null) {
  write(LIST_KEY, list === null ? null : JSON.stringify(list));
  // 사용자가 고른 목록은 지금 기본 종목을 모두 본 뒤의 결정이다 — 지운 기본 종목을 나중에 되살리지 않게
  write(SEEN_KEY, list === null ? null : JSON.stringify(seenWithDefaults(readSeen())));
  window.dispatchEvent(new Event(MARKETS_CHANGED));
}

export function loadOpen(): boolean {
  return read(OPEN_KEY) !== "0";
}

export function saveOpen(open: boolean) {
  write(OPEN_KEY, open ? "1" : "0");
}

export type BoardMode = "1d" | "5m";

export function loadMode(): BoardMode {
  return read(MODE_KEY) === "5m" ? "5m" : "1d";
}

export function saveMode(mode: BoardMode) {
  write(MODE_KEY, mode);
}
