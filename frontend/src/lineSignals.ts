import { useEffect, useState } from "react";
import { api, type LineSignal, type LineWatchEvent } from "./api";

/** #614 차트 선 알림(#613) — 설정 · 알림 타임라인 · 최근 알림 띠가 같이 쓴다 */
export const LINE_TF_KO: Record<string, string> = { "5m": "5분봉", "15m": "15분봉", "60m": "1시간봉", "120m": "2시간봉", "240m": "4시간봉", "1d": "일봉" };
export const LINE_EVENT_KO: Record<LineWatchEvent, string> = { touch: "닿음", break: "이탈", retest: "되돌림", long: "장기 선" };
export const LINE_EVENT_NOTE: Record<LineWatchEvent, string> = {
  touch: "가격이 채널 하단 · 상단이나 추세선에 닿았을 때",
  break: "종가가 추세선 · 채널을 넘었을 때",
  retest: "이탈한 추세선에 다시 닿았지만 넘지 못했을 때",
  long: "일봉: 몇 달 이어 온 장기 지지 · 저항선을 시험하거나 종가 이틀로 넘었을 때(일봉 마감 뒤)",
};
/** 짧은 봉 — 잡음이 많아 자주 틀린다고 적는 시간봉 */
export const SHORT_TFS = new Set(["5m", "15m", "60m", "120m"]);

/** 그 종목 · 시간봉 차트(차트 화면에 2시간봉이 없어 2시간봉 알림은 1시간봉으로 연다) */
export const chartHref = (s: Pick<LineSignal, "ticker" | "timeframe">) =>
  `#/charts?t=${encodeURIComponent(s.ticker)}&iv=${s.timeframe === "120m" ? "60m" : s.timeframe}`;

const TF_MIN: Record<string, number> = { "5m": 5, "15m": 15, "60m": 60, "120m": 120, "240m": 240, "1d": 1440 };
/** 그 알림의 시각 — at 은 판정한 봉의 시작이라 봉이 끝난 시각(알림이 나간 즈음)으로 보이고 정렬한다 */
export const signalTime = (s: Pick<LineSignal, "at" | "timeframe">): number => Date.parse(s.at) + (TF_MIN[s.timeframe] ?? 0) * 60_000;

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

/**
 * 오늘(date, KST) 보낸 선 알림 — 알림 피드가 다시 읽힐 때(refresh 가 바뀔 때) 같이 다시 읽는다.
 * 서버에 #613 이 없으면(404 등) 빈 목록 — 타임라인은 수급 알림만 그대로 보인다.
 */
export function useTodayLineSignals(date: string | null | undefined, refresh: unknown): LineSignal[] {
  const [items, setItems] = useState<LineSignal[]>([]);
  useEffect(() => {
    if (!date) return;
    let alive = true;
    api.lineWatch().then(
      (w) => alive && setItems(w.recent.filter((s) => s.trade_date === date)),
      () => { /* 없으면 수급 알림만 */ },
    );
    return () => { alive = false; };
  }, [date, refresh]);
  return items;
}
