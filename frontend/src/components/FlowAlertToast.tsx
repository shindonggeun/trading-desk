import { useEffect, useRef, useState } from "react";
import { api, type FlowAlert, type PushKinds } from "../api";

/** #362 앱 안 토스트 — 장중에 수급 전향 목록(/api/alerts/flow)을 폴링해 새로 온 것만 화면 위에 띄운다.
 * 푸시가 꺼진 기기 · 지원하지 않는 브라우저에서도 앱을 열어 두면 볼 수 있게. */

export type ToastMarkets = { futures: boolean; kospi: boolean; kosdaq: boolean };
export const TOAST_MARKETS: { key: keyof ToastMarkets; label: string }[] = [
  { key: "futures", label: "선물" },
  { key: "kospi", label: "코스피" },
  { key: "kosdaq", label: "코스닥" },
];
const KEY = "desk.flowAlertMarkets";
const POLL_MS = 60_000;
const SHOW_MS = 20_000;
const MAX_SHOWN = 3;
export const FLOW_ROUTE = "#/market";

export function loadToastMarkets(): ToastMarkets {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { futures: raw.futures !== false, kospi: raw.kospi !== false, kosdaq: raw.kosdaq !== false };
  } catch {
    return { futures: true, kospi: true, kosdaq: true };
  }
}

export function saveToastMarkets(m: ToastMarkets) {
  try {
    localStorage.setItem(KEY, JSON.stringify(m));
  } catch {
    /* 저장 못 해도 이번 화면에서는 그대로 */
  }
}

/** 시장 토글 → 푸시 구독 종류(#361): 하나라도 켜져 있으면 전체 스위치(flow)도 켬 */
export function pushKindsOf(m: ToastMarkets): PushKinds {
  return { flow: m.futures || m.kospi || m.kosdaq, flow_futures: m.futures, flow_kospi: m.kospi, flow_kosdaq: m.kosdaq };
}

/** 장중(평일 08:45–15:45 KST)인가 — 선물이 08:45 에 열리고 현물 장후 15:45 까지 */
export function inFlowHours(now = new Date()): boolean {
  const kst = new Date(now.getTime() + (now.getTimezoneOffset() + 540) * 60_000);
  const day = kst.getDay();
  const minutes = kst.getHours() * 60 + kst.getMinutes();
  return day >= 1 && day <= 5 && minutes >= 8 * 60 + 45 && minutes <= 15 * 60 + 45;
}

const keyOf = (a: FlowAlert) => `${a.trade_date}|${a.market}|${a.kind}|${a.t}`;

type Shown = FlowAlert & { key: string; until: number };

export default function FlowAlertToast() {
  const [shown, setShown] = useState<Shown[]>([]);
  const seen = useRef<Set<string> | null>(null); // 처음 읽은 목록은 이미 지난 것 — 토스트 없이 기준으로

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      if (inFlowHours() && document.visibilityState === "visible") {
        try {
          const { items } = await api.flowAlerts();
          if (!alive) return;
          if (seen.current === null) {
            seen.current = new Set(items.map(keyOf));
          } else {
            const markets = loadToastMarkets();
            const fresh = items.filter((a) => !seen.current!.has(keyOf(a)));
            fresh.forEach((a) => seen.current!.add(keyOf(a)));
            const wanted = fresh.filter((a) => markets[a.market]);
            if (wanted.length) {
              const until = Date.now() + SHOW_MS;
              setShown((s) => [...wanted.map((a) => ({ ...a, key: keyOf(a), until })), ...s].slice(0, MAX_SHOWN));
            }
          }
        } catch {
          /* 서버가 잠깐 안 되면 다음 회차에 */
        }
      }
      if (alive) timer = window.setTimeout(tick, POLL_MS);
    };
    tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!shown.length) return;
    const next = Math.min(...shown.map((s) => s.until)) - Date.now();
    const t = window.setTimeout(() => setShown((s) => s.filter((x) => x.until > Date.now())), Math.max(next, 0) + 50);
    return () => window.clearTimeout(t);
  }, [shown]);

  if (!shown.length) return null;
  return (
    <div className="flow-toasts" role="status" aria-live="polite">
      {shown.map((a) => (
        <div key={a.key} className={`flow-toast ${a.kind === "buy_turn" ? "flow-toast-buy" : "flow-toast-sell"}`}>
          <a href={FLOW_ROUTE} onClick={() => setShown((s) => s.filter((x) => x.key !== a.key))}>{a.text}</a>
          <button type="button" className="flow-toast-close" aria-label="닫기"
            onClick={() => setShown((s) => s.filter((x) => x.key !== a.key))}>×</button>
        </div>
      ))}
    </div>
  );
}
