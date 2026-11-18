/**
 * #582 회사에서 조용히 띄워 두는 작은 창. Chrome · Edge 는 Document Picture-in-Picture(다른 앱 위에 늘 떠 있는 창),
 * 그 밖의 브라우저는 작은 팝업 창(#/mini). PiP 창에는 iframe 을 쓰지 않는다 — 웹 서버가 X-Frame-Options DENY 라
 * 같은 출처라도 막힌다. 대신 그 창 문서에 스타일을 옮기고 React 루트를 따로 만든다(창을 연 탭이 살아 있는 동안).
 */

import { useEffect, useState } from "react";
import { TICKER } from "./markets";
import { sameTicker } from "./ticker";

export type MiniPrefs = { quiet: boolean; size: "s" | "m" | "l" };
const KEY = "desk.mini.v1";
const STRIP = { width: 640, height: 76 };

export function loadMiniPrefs(): MiniPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { quiet: raw.quiet === true, size: raw.size === "s" || raw.size === "l" ? raw.size : "m" };
  } catch {
    return { quiet: false, size: "m" };
  }
}

export function saveMiniPrefs(p: MiniPrefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 기억만 못 한다 — 이번 창에서는 그대로 */
  }
}

type PipApi = { requestWindow(opts: { width: number; height: number }): Promise<Window>; window: Window | null };
const pipApi = (): PipApi | undefined => (window as unknown as { documentPictureInPicture?: PipApi }).documentPictureInPicture;

/** 이 브라우저가 늘 위에 뜨는 작은 창(Document PiP)을 쓸 수 있는가 */
export const canPip = () => typeof pipApi()?.requestWindow === "function";

const miniUrl = () => `${location.origin}${location.pathname}#/mini`;

/** 버튼을 누른 그 동작 안에서 부른다(창 열기는 사용자 동작이 있어야 한다). "pip" · "popup" · "blocked" */
export async function openMini(): Promise<"pip" | "popup" | "blocked"> {
  const api = pipApi();
  if (api && typeof api.requestWindow === "function") {
    if (api.window) { api.window.focus(); return "pip"; }   // 이미 떠 있으면 그 창
    try {
      const win = await api.requestWindow(STRIP);
      copyStyles(win.document);
      win.document.title = "Trading Desk · 작은 창";
      const host = win.document.createElement("div");
      host.id = "mini-root";
      win.document.body.append(host);
      // 화면 코드는 창을 연 뒤에 받는다(창 열기에 사용자 동작이 필요해 먼저 연다)
      const [{ createRoot }, { default: MiniWindow }, { createElement }] = await Promise.all([
        import("react-dom/client"), import("./pages/MiniWindow"), import("react"),
      ]);
      const root = createRoot(host);
      root.render(createElement(MiniWindow, { pip: true }));
      win.addEventListener("pagehide", () => root.unmount(), { once: true });
      return "pip";
    } catch {
      /* PiP 를 열지 못했다 — 팝업으로 */
    }
  }
  const w = window.open(miniUrl(), "desk-mini", `popup,width=${STRIP.width},height=${STRIP.height + 44}`);
  return w ? "popup" : "blocked";
}

/** 지금 문서의 스타일(개발: <style>, 운영: <link rel=stylesheet>)과 글꼴을 작은 창 문서로 옮긴다 */
function copyStyles(doc: Document) {
  for (const node of document.head.querySelectorAll('style, link[rel="stylesheet"]')) {
    doc.head.append(node.cloneNode(true));
  }
  const meta = doc.createElement("meta");
  meta.name = "color-scheme";
  meta.content = "dark light";
  doc.head.append(meta);
  doc.body.style.margin = "0";
  doc.body.style.background = getComputedStyle(document.body).backgroundColor;
}

/* #616 작은 창 전용 종목 — 티커 대신 한글 이름으로 보이고, 설정 · 창 안 ⚙ 에서 고친다(이 브라우저에 기억).
 * PiP 창은 연 탭의 React 로 그려 같은 window 이벤트를 받고, 팝업 창은 storage 이벤트로 바뀐 목록을 받는다. */
export type MiniItem = { ticker: string; name: string };
/** 코스피200 선물 근월물 — 시세판(/api/estimates)이 아니라 선물 시세(/api/market/futures/quote)로 읽는다 */
export const MINI_FUTURES = "KOSPI200F";
export const MINI_MAX = 20;
export const MINI_NAME_MAX = 20;
export const MINI_DEFAULT: MiniItem[] = [
  { ticker: "NQ=F", name: "나스닥100 선물" },
  { ticker: "005930.KS", name: "삼성전자" },
  { ticker: "000660.KS", name: "하이닉스" },
  { ticker: "GC=F", name: "금 선물" },
  { ticker: "^KS11", name: "코스피" },
  { ticker: MINI_FUTURES, name: "코스피200 선물" },
  { ticker: "KORU", name: "KORU" },
  { ticker: "SOXL", name: "SOXL" },
  { ticker: "RAM", name: "RAM" },
  { ticker: "GDXU", name: "GDXU" },
];
/** 한 번에 넣을 수 있는 자주 쓰는 지수 · 선물 · 환율 */
export const MINI_PRESETS: MiniItem[] = [
  ...MINI_DEFAULT.slice(0, 6),
  { ticker: "ES=F", name: "S&P500 선물" },
  { ticker: "^KQ11", name: "코스닥" },
  { ticker: "^SOX", name: "필라델피아 반도체" },
  { ticker: "CL=F", name: "WTI 원유" },
  { ticker: "KRW=X", name: "달러/원" },
  { ticker: "BTC-USD", name: "비트코인" },
];
const ITEMS_KEY = "desk.mini.items.v1";
const ITEMS_CHANGED = "desk-mini-items-changed";

/** 저장 · 입력 값을 다듬는다: 받는 티커만, 이름은 MINI_NAME_MAX 자(비면 티커), 겹치는 티커는 처음 것만, MINI_MAX 개까지 */
export function cleanMiniItems(raw: unknown): MiniItem[] {
  if (!Array.isArray(raw)) return [];
  const out: MiniItem[] = [];
  for (const it of raw) {
    const ticker = String((it as MiniItem)?.ticker ?? "").trim().toUpperCase();
    if (!ticker || !(ticker === MINI_FUTURES || TICKER.test(ticker)) || out.some((o) => sameTicker(o.ticker, ticker))) continue;
    const name = String((it as MiniItem)?.name ?? "").trim().slice(0, MINI_NAME_MAX) || ticker;
    out.push({ ticker, name });
    if (out.length >= MINI_MAX) break;
  }
  return out;
}

/** 고른 적이 없으면 기본 10종. 다 지운 빈 목록은 그대로 둔다 */
export function loadMiniItems(): MiniItem[] {
  try {
    const raw = localStorage.getItem(ITEMS_KEY);
    return raw === null ? MINI_DEFAULT : cleanMiniItems(JSON.parse(raw));
  } catch {
    return MINI_DEFAULT;
  }
}

export function saveMiniItems(items: MiniItem[]) {
  const clean = cleanMiniItems(items);
  try {
    localStorage.setItem(ITEMS_KEY, JSON.stringify(clean));
  } catch {
    /* 기억만 못 한다 — 이번 화면에서는 그대로 */
  }
  window.dispatchEvent(new CustomEvent(ITEMS_CHANGED, { detail: clean }));
}

/** 작은 창 종목 — 설정 화면과 떠 있는 작은 창이 같은 목록을 본다 */
export function useMiniItems(): [MiniItem[], (items: MiniItem[]) => void] {
  const [items, setItems] = useState<MiniItem[]>(loadMiniItems);
  useEffect(() => {
    const mine = (e: Event) => setItems((e as CustomEvent<MiniItem[]>).detail);
    const other = (e: StorageEvent) => { if (e.key === ITEMS_KEY) setItems(loadMiniItems()); };
    window.addEventListener(ITEMS_CHANGED, mine);
    window.addEventListener("storage", other);
    return () => { window.removeEventListener(ITEMS_CHANGED, mine); window.removeEventListener("storage", other); };
  }, []);
  return [items, saveMiniItems];
}
