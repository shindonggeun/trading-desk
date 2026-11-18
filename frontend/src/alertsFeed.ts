import { useEffect, useState } from "react";
import { api, ApiError, type MarketAlert, type MarketAlertsDay } from "./api";

/** #581 오늘 알림(GET /api/alerts/today) — 대시보드 띠 · 타임라인 · 장중 수급 차트 마커가 같이 쓴다.
 *  화면이 보이는 동안 POLL_MS 마다 다시 읽고, 같은 페이지의 여러 구독자는 요청 하나를 나눠 쓴다. */
const POLL_MS = 60_000;

export const KIND_LABEL: Record<MarketAlert["kind"], string> = {
  flow_turn: "수급 전향",
  flow_shift: "수급 흐름",
  nq_alert: "나스닥 선물",
};
export const SEVERITY_LABEL: Record<MarketAlert["severity"], string> = { info: "참고", caution: "주의", strong: "강함" };

export const alertKey = (a: MarketAlert) => `${a.kind}|${a.rule}|${a.market}|${a.who}|${a.t}`;

export function hhmm(iso: string): string {
  const m = iso.match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : iso;
}

/** 수급이 가리키는 쪽 — 매수 확대 · 전향은 up, 매도 확대 · 전향 · 나스닥 하락은 down. "축소" 는 반대 쪽이다
 *  (매수 축소 = 사던 힘이 줄어 아래, 매도 축소 = 팔던 힘이 줄어 위). 색과 함께 ▲▼ 글자로도 쓴다 */
export function sideOf(a: MarketAlert): "up" | "down" | null {
  // 장초 매수 뒤 매도 반전(open_buy_reversal) 도 이제 파는 쪽이라 반대
  const shrink = /shrink|reversal/.test(a.rule);
  if (/buy/.test(a.rule)) return shrink ? "down" : "up";
  if (/sell/.test(a.rule)) return shrink ? "up" : "down";
  if (/drop|from_high/.test(a.rule)) return "down";
  return null;
}

type Feed = { data: MarketAlertsDay | null; error: string | null };
const shared: { feed: Feed; listeners: Set<(f: Feed) => void>; timer?: number; inflight?: Promise<void> } = {
  feed: { data: null, error: null }, listeners: new Set(),
};

function publish(feed: Feed) {
  shared.feed = feed;
  shared.listeners.forEach((l) => l(feed));
}

async function load(): Promise<void> {
  if (shared.inflight) return shared.inflight;
  shared.inflight = api.alertsToday()
    .then((d) => publish({ data: d, error: null }))
    .catch((e) => {
      if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) return publish({ data: fakeAlerts(), error: null });
      publish({ data: shared.feed.data, error: e instanceof Error ? e.message : "불러오지 못했습니다" });
    })
    .finally(() => { shared.inflight = undefined; });
  return shared.inflight;
}

function schedule() {
  window.clearTimeout(shared.timer);
  if (!shared.listeners.size) return;
  shared.timer = window.setTimeout(async () => {
    if (document.visibilityState === "visible") await load();
    schedule();
  }, POLL_MS);
}

/** 오늘 알림 — 첫 구독자가 읽기를 시작하고, 마지막 구독자가 떠나면 멈춘다. */
export function useTodayAlerts(): Feed {
  const [feed, setFeed] = useState<Feed>(shared.feed);
  useEffect(() => {
    shared.listeners.add(setFeed);
    if (shared.listeners.size === 1) { void load(); schedule(); }
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      shared.listeners.delete(setFeed);
      document.removeEventListener("visibilitychange", onVisible);
      if (!shared.listeners.size) window.clearTimeout(shared.timer);
    };
  }, []);
  return feed;
}

/** DEV 에서 백엔드에 /api/alerts/today 가 없을 때(404)만 쓰는 예시 */
export function fakeAlerts(): MarketAlertsDay {
  const kst = new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
  const at = (hm: string) => `${kst}T${hm}:00+09:00`;
  const a = (x: Partial<MarketAlert> & Pick<MarketAlert, "kind" | "rule" | "market" | "t" | "text" | "severity">): MarketAlert =>
    ({ trade_date: kst, who: "foreign", value: null, prev: null, pushed: x.severity !== "info", ...x });
  return {
    date: kst,
    items: [
      a({ kind: "nq_alert", rule: "nq_drop", market: "nq", who: "all", severity: "caution", t: at("08:20"),
          text: "🟠 나스닥 선물 5분 -0.42% — 평소 하위 1% 보다 큰 하락, 거래량 1.8배 (예시)" }),
      a({ kind: "flow_shift", rule: "sell_grow", market: "futures", severity: "caution", t: at("09:35"),
          text: "🟠 외인 선물 매도 확대 — 30분 -1,240계약 (예시)" }),
      a({ kind: "flow_turn", rule: "buy_turn", market: "kospi", severity: "caution", t: at("10:41"),
          text: "외국인 코스피 순매수 전향 +312억 (예시)" }),
      a({ kind: "flow_shift", rule: "buy_shrink", market: "kospi", who: "institution", severity: "info", t: at("11:10"),
          text: "기관 코스피 현물 매수 축소 — 30분 -420억 (예시)" }),
      a({ kind: "flow_shift", rule: "sell_grow", market: "kospi", severity: "strong", t: at("13:25"),
          text: "🔴 외인 코스피 현물 매도 확대 — 30분 -2,850억 (예시)" }),
    ],
  };
}
