import { alertKey, hhmm, KIND_LABEL, SEVERITY_LABEL, sideOf, useTodayAlerts } from "../alertsFeed";

/** #581 대시보드 맨 위 "최근 알림" 한 줄 띠 — 오늘 알림 가운데 최근 3건(참고는 빼고), 누르면 시장 수급 카드의 "오늘 장중"(타임라인)으로.
 *  알림이 없거나 못 읽으면 아무것도 그리지 않는다(자리를 차지하지 않게). */
// 시장 수급 카드의 "오늘 장중" 탭(#362 와 같은 길) — 차트 아래에 타임라인이 있다
export const TIMELINE_ROUTE = "#/?flow=intraday";
const SHOWN = 3;

export default function AlertStrip() {
  const { data } = useTodayAlerts();
  const recent = (data?.items ?? []).filter((a) => a.severity !== "info").slice(-SHOWN).reverse();
  if (!recent.length) return null;
  return (
    <nav className="alert-strip" aria-label="최근 알림">
      <span className="alert-strip-title">최근 알림</span>
      <ul>
        {recent.map((a) => {
          const side = sideOf(a);
          return (
            <li key={alertKey(a)} className={`sev-${a.severity}`}>
              <a href={TIMELINE_ROUTE}>
                <time dateTime={a.t}>{hhmm(a.t)}</time>{" "}
                <span className={`alert-sev sev-${a.severity}`}>{SEVERITY_LABEL[a.severity]}</span>{" "}
                <span className="sr-only">{KIND_LABEL[a.kind]} </span>
                {side && <span className={side === "up" ? "up" : "down"} aria-hidden="true">{side === "up" ? "▲" : "▼"} </span>}
                <span className="alert-strip-text">{a.text.replace(/^[🔴🟠🟡🟢⚪]\s*/u, "")}</span>
              </a>
            </li>
          );
        })}
      </ul>
      <a className="alert-strip-all small" href={TIMELINE_ROUTE}>전체 보기</a>
    </nav>
  );
}
