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
];

const LIST_KEY = "desk.markets";
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

export function loadMarkets(): MarketSymbol[] {
  const raw = read(LIST_KEY);
  if (!raw) return DEFAULT_MARKETS;
  try {
    const list = JSON.parse(raw);
    if (Array.isArray(list)) {
      // 손으로 고친 값 · 예전 값: 형식이 틀리거나 겹치는 티커는 버린다
      const seen = new Set<string>();
      return list.flatMap((m) => {
        if (!m || typeof m.ticker !== "string" || typeof m.name !== "string") return [];
        const ticker = m.ticker.trim().toUpperCase();
        if (!TICKER.test(ticker) || seen.has(ticker)) return [];
        seen.add(ticker);
        return [{ ticker, name: m.name.trim() || ticker }];
      });
    }
  } catch {
    /* 깨진 값은 기본 목록으로 */
  }
  return DEFAULT_MARKETS;
}

export function saveMarkets(list: MarketSymbol[] | null) {
  write(LIST_KEY, list === null ? null : JSON.stringify(list));
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
