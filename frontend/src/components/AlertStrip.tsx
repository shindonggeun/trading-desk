import type { ReactNode } from "react";
import { alertKey, alertLines, badgeOf, hhmm, isNotable, kindLabel, sideOf, useTodayAlerts } from "../alertsFeed";
import { chartHref, kstHhmm, signalTime, signalKey, splitText, useTodayLineSignals } from "../lineSignals";

/** #581 대시보드 맨 위 "최근 알림" 한 줄 띠 — 오늘 알림 가운데 최근 3건(참고는 빼고), 누르면 시장 수급 카드의 "오늘 장중"(타임라인)으로.
 *  알림이 없거나 못 읽으면 아무것도 그리지 않는다(자리를 차지하지 않게). */
// 시장 수급 카드의 "오늘 장중" 탭(#362 와 같은 길) — 차트 아래에 타임라인이 있다
export const TIMELINE_ROUTE = "#/?flow=intraday";
const SHOWN = 3;

export default function AlertStrip() {
  const { data } = useTodayAlerts();
  const lines = useTodayLineSignals(data?.date, data);
  const rows: { t: number; key: string; node: ReactNode }[] = [
    ...(data?.items ?? []).filter(isNotable).map((a) => {
      const side = sideOf(a);
      const b = badgeOf(a);
      return {
        t: Date.parse(a.t), key: alertKey(a), node: (
          <li key={alertKey(a)} className={b.cls}>
            <a href={TIMELINE_ROUTE}>
              <time dateTime={a.t}>{hhmm(a.t)}</time>{" "}
              <span className={`alert-sev ${b.cls}`}>{b.mark && <span aria-hidden="true">{b.mark} </span>}{b.label}</span>{" "}
              <span className="sr-only">{kindLabel(a.kind)} </span>
              {side && <span className={side === "up" ? "up" : "down"} aria-hidden="true">{side === "up" ? "▲" : "▼"} </span>}
              <span className="alert-strip-text">{alertLines(a.text).head}</span>
            </a>
          </li>
        ),
      };
    }),
    // #614 차트 선 알림 — 누르면 그 종목 · 시간봉 차트
    ...lines.map((s) => ({
      t: signalTime(s), key: signalKey(s), node: (
        <li key={signalKey(s)} className="line-row">
          <a href={chartHref(s)}>
            <time dateTime={s.at}>{kstHhmm(signalTime(s))}</time>{" "}
            <span className="alert-sev line-sev">선</span>{" "}
            <span className="alert-strip-text">{splitText(s.text).title}</span>
          </a>
        </li>
      ),
    })),
  ];
  const recent = rows.sort((a, b) => b.t - a.t).slice(0, SHOWN);
  if (!recent.length) return null;
  return (
    <nav className="alert-strip" aria-label="최근 알림">
      <span className="alert-strip-title">최근 알림</span>
      <ul>{recent.map((r) => r.node)}</ul>
      <a className="alert-strip-all small" href={TIMELINE_ROUTE}>전체 보기</a>
    </nav>
  );
}
