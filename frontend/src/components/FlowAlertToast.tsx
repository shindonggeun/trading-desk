import { useEffect, useRef, useState } from "react";
import { alertKey, useTodayAlerts } from "../alertsFeed";
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

/** #573 수급 전향 말고 받을 알림 종류 — 아침 시황(#571) · 오늘 방향(#572), #581 수급 흐름 변화 · 나스닥 선물 이상 징후(#579).
 *  이 브라우저에 기억하고 기본은 켬. 수급 흐름 · 나스닥 선물은 앱 안 토스트에도 같이 적용된다. */
export type ExtraKinds = { flow_shift: boolean; nq_alert: boolean; morning_brief: boolean; day_call: boolean; line_signal: boolean;
  tone_positive: boolean; tone_neutral: boolean; tone_caution: boolean; tone_warning: boolean };
const TONE_KEYS = ["tone_positive", "tone_neutral", "tone_caution", "tone_warning"] as const;
export const EXTRA_KINDS: { key: keyof ExtraKinds; label: string; note: string }[] = [
  { key: "flow_shift", label: "수급 흐름 변화", note: "외국인 · 기관의 매수 · 매도 확대 · 축소(장중)" },
  { key: "nq_alert", label: "나스닥 선물 이상", note: "나스닥100 선물의 급한 매도세(거의 24시간)" },
  { key: "morning_brief", label: "아침 시황", note: "평일 오전 7시쯤" },
  { key: "day_call", label: "오늘 방향", note: "국장 · 미장 판정이 나올 때" },
  { key: "line_signal", label: "차트 선", note: "고른 종목 · 시간봉의 채널 · 추세선 닿음 · 이탈(설정 → 차트 선 알림에서 켬)" },
  // #642 수급 알림의 뜻(#641) — 끄면 그 등급은 보내지 않는다(예: 경고만 받기)
  { key: "tone_positive", label: "◎ 호재", note: "외인 · 기관 매수 전향 · 확대, 매도 축소" },
  { key: "tone_neutral", label: "· 참고", note: "개인 수급 · 작은 움직임" },
  { key: "tone_caution", label: "! 주의", note: "매도 확대 · 매수 축소, 나스닥 선물 고점 대비 밀림" },
  { key: "tone_warning", label: "⚠ 경고", note: "매수→매도 전향 · 강한 매도 확대 · 나스닥 선물 급락" },
];
const EXTRA_KEY = "desk.pushKinds.extra";

export function loadExtraKinds(): ExtraKinds {
  try {
    const raw = JSON.parse(localStorage.getItem(EXTRA_KEY) ?? "{}");
    return { flow_shift: raw.flow_shift !== false, nq_alert: raw.nq_alert !== false,
             morning_brief: raw.morning_brief !== false, day_call: raw.day_call !== false, line_signal: raw.line_signal !== false,
             ...Object.fromEntries(TONE_KEYS.map((k) => [k, raw[k] !== false])) as Record<(typeof TONE_KEYS)[number], boolean> };
  } catch {
    return { flow_shift: true, nq_alert: true, morning_brief: true, day_call: true, line_signal: true,
             tone_positive: true, tone_neutral: true, tone_caution: true, tone_warning: true };
  }
}

export function saveExtraKinds(k: ExtraKinds) {
  try {
    localStorage.setItem(EXTRA_KEY, JSON.stringify(k));
  } catch {
    /* 저장 못 해도 이번 화면에서는 그대로 */
  }
}

/** 시장 토글 → 푸시 구독 종류(#361): 하나라도 켜져 있으면 전체 스위치(flow)도 켬. #573 아침 시황 · 오늘 방향도 같이 */
export function pushKindsOf(m: ToastMarkets, extra: ExtraKinds = loadExtraKinds()): PushKinds {
  return { flow: m.futures || m.kospi || m.kosdaq, flow_futures: m.futures, flow_kospi: m.kospi, flow_kosdaq: m.kosdaq, ...extra };
}

/** 장중(평일 08:45–15:45 KST)인가 — 선물이 08:45 에 열리고 현물 장후 15:45 까지 */
export function inFlowHours(now = new Date()): boolean {
  const kst = new Date(now.getTime() + (now.getTimezoneOffset() + 540) * 60_000);
  const day = kst.getDay();
  const minutes = kst.getHours() * 60 + kst.getMinutes();
  return day >= 1 && day <= 5 && minutes >= 8 * 60 + 45 && minutes <= 15 * 60 + 45;
}

const keyOf = (a: FlowAlert) => `${a.trade_date}|${a.market}|${a.kind}|${a.t}`;

/** 토스트 한 건 — 수급 전향(#361) 또는 #579 알림(푸시로 나간 것) */
type Toast = { key: string; text: string; tone: "buy" | "sell" | "warn" };
type Shown = Toast & { until: number };

export default function FlowAlertToast() {
  const [shown, setShown] = useState<Shown[]>([]);
  const seen = useRef<Set<string> | null>(null); // 처음 읽은 목록은 이미 지난 것 — 토스트 없이 기준으로
  const seenShift = useRef<Set<string> | null>(null);
  // #606 #579 알림(수급 흐름 변화 · 나스닥 선물)은 공유 피드(alertsFeed: 60초 · 화면이 숨으면 쉼)에서 받는다 —
  // 대시보드의 알림 띠 · 타임라인과 같은 요청 하나를 나눠 써 /api/alerts/today 를 따로 읽지 않는다
  const { data: day } = useTodayAlerts();
  useEffect(() => {
    if (!day) return;
    const extra = loadExtraKinds();
    const pushed = day.items.filter((a) => a.pushed && a.kind !== "flow_turn");
    if (seenShift.current === null) {   // 처음 받은 목록은 이미 지난 것 — 토스트 없이 기준으로
      seenShift.current = new Set(pushed.map(alertKey));
      return;
    }
    const fresh = pushed.filter((a) => !seenShift.current!.has(alertKey(a)));
    fresh.forEach((a) => seenShift.current!.add(alertKey(a)));
    const wanted = fresh.filter((a) => (a.kind === "flow_shift" ? extra.flow_shift : extra.nq_alert) && (!a.tone || extra[`tone_${a.tone}`]));
    if (wanted.length) {
      const until = Date.now() + SHOW_MS;
      setShown((s) => [...wanted.map((a): Shown => ({ key: alertKey(a), text: a.text.split("\n")[0], tone: "warn", until })), ...s]
        .slice(0, MAX_SHOWN));
    }
  }, [day]);

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
            const extra = loadExtraKinds();
            const fresh = items.filter((a) => !seen.current!.has(keyOf(a)));
            fresh.forEach((a) => seen.current!.add(keyOf(a)));
            // #642 끈 등급(#641: 매수 전향 = 호재, 매도 전향 = 경고)은 앱 안에서도 띄우지 않는다
            const wanted = fresh.filter((a) => markets[a.market] && extra[a.kind === "buy_turn" ? "tone_positive" : "tone_warning"]);
            if (wanted.length) {
              const until = Date.now() + SHOW_MS;
              setShown((s) => [...wanted.map((a): Shown => ({ key: keyOf(a), text: a.text.split("\n")[0],
                tone: a.kind === "buy_turn" ? "buy" : "sell", until })), ...s].slice(0, MAX_SHOWN));
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
        <div key={a.key} className={`flow-toast flow-toast-${a.tone}`}>
          <a href={FLOW_ROUTE} onClick={() => setShown((s) => s.filter((x) => x.key !== a.key))}>{a.text}</a>
          <button type="button" className="flow-toast-close" aria-label="닫기"
            onClick={() => setShown((s) => s.filter((x) => x.key !== a.key))}>×</button>
        </div>
      ))}
    </div>
  );
}
