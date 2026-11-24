import { createContext, useContext, useEffect, useState } from "react";
import { api, type LineSignal, type LineWatchEvent } from "./api";
import { createLineFeedCache } from "./lineFeedReuse";
export { LINE_TF_KO, chartFallbackLabel, chartHref, signalDateTime, signalTime } from "./lineWatchNavigation";

/** #614 차트 선 알림(#613) — 설정 · 알림 타임라인 · 최근 알림 띠가 같이 쓴다 */
export const LINE_EVENT_KO: Record<LineWatchEvent, string> = { touch: "닿음", break: "돌파·이탈", retest: "되돌림", long: "장기 선" };
export const LINE_EVENT_NOTE: Record<LineWatchEvent, string> = {
  touch: "가격이 채널 하단 · 상단이나 추세선에 닿았을 때",
  break: "종가가 추세선 · 채널을 넘었을 때",
  retest: "이탈한 추세선에 다시 닿았지만 넘지 못했을 때",
  long: "일봉: 몇 달 이어 온 장기 지지 · 저항선을 시험하거나 종가 이틀로 넘었을 때(일봉 마감 뒤)",
};
/** 짧은 봉 — 잡음이 많아 자주 틀린다고 적는 시간봉 */
export const SHORT_TFS = new Set(["5m", "10m", "15m", "60m", "120m"]);

/** 시각(UTC ISO 또는 ms)을 한국 시각 HH:MM 으로 */
export function kstHhmm(at: string | number): string {
  const t = new Date((typeof at === "number" ? at : Date.parse(at)) + 9 * 3600_000);
  if (Number.isNaN(t.getTime())) return "";
  return `${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`;
}

/** 알림 글은 "제목\n본문" — 첫 줄과 나머지 */
export function splitText(text: string): { title: string; body: string } {
  const i = text.indexOf("\n");
  return i < 0 ? { title: text, body: "" } : { title: text.slice(0, i), body: text.slice(i + 1) };
}

// 서버의 고유 키와 같게(line_id 에는 종목이 없다)
export const signalKey = (s: LineSignal) => `${s.ticker}|${s.timeframe}|${s.rule}|${s.line_id}|${s.at}`;

/** 소유자의 읽은 이력과 강조 가격을 이 인증 화면 수명에만 보관한다. */
export function createLineWatchScope() {
  return {
    feed: createLineFeedCache<LineSignal[]>(),
    chartAlerts: new Map<string, { ticker: string; timeframe: string; price: number; at: string }>(),
  };
}

export const LineWatchScopeContext = createContext<ReturnType<typeof createLineWatchScope> | null>(null);

/** 알림 띠와 타임라인의 동일 피드 조회를 현재 로그인에서만 공유한다. */
export function useTodayLineSignals(date: string | null | undefined, refresh: unknown): LineSignal[] {
  const scope = useContext(LineWatchScopeContext);
  const [result, setResult] = useState<{ scope: typeof scope; date: string; items: LineSignal[] } | null>(null);
  useEffect(() => {
    if (!date || !scope) return;
    const feed = JSON.stringify(refresh ?? null);
    const read = scope.feed.acquire(date, feed, async signal => {
      const view = await api.lineWatch(undefined, signal);
      return view.can_edit === true ? view.recent.filter(item => item.trade_date === date) : [];
    });
    let alive = true;
    void read.promise.then(items => {
      if (alive) setResult({ scope, date, items });
    }, () => { /* 기존 수급 알림은 그대로 표시한다 */ });
    return () => {
      alive = false;
      read.release();
    };
  }, [scope, date, refresh]);
  return result && result.scope === scope && result.date === date ? result.items : [];
}
