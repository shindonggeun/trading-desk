import { useMemo, useState } from "react";
import type { MarketAlert } from "../api";
import { alertKey, hhmm, KIND_LABEL, SEVERITY_LABEL, sideOf, useTodayAlerts } from "../alertsFeed";

/** #581 오늘 알림 타임라인 — 수급 전향 · 수급 흐름 변화 · 나스닥 선물 이상 징후를 시각 순서로.
 *  색만으로 구분하지 않게 강도는 글자(참고 · 주의 · 강함), 방향은 ▲▼ 로 함께 쓴다. */
type Filter = "all" | MarketAlert["kind"];
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "flow_turn", label: KIND_LABEL.flow_turn },
  { key: "flow_shift", label: KIND_LABEL.flow_shift },
  { key: "nq_alert", label: KIND_LABEL.nq_alert },
];

export function AlertRow({ a }: { a: MarketAlert }) {
  const side = sideOf(a);
  return (
    <li className={`alert-row sev-${a.severity}`}>
      <time className="alert-time" dateTime={a.t}>{hhmm(a.t)}</time>
      <span className={`alert-sev sev-${a.severity}`}>{SEVERITY_LABEL[a.severity]}</span>
      <span className="alert-kind muted small">{KIND_LABEL[a.kind]}</span>
      <span className="alert-text">
        {side && <span className={side === "up" ? "up" : "down"} aria-hidden="true">{side === "up" ? "▲ " : "▼ "}</span>}
        {a.text.replace(/^[🔴🟠🟡🟢⚪]\s*/u, "")}
        {!a.pushed && <span className="muted small"> · 푸시 안 함</span>}
      </span>
    </li>
  );
}

export default function AlertTimeline() {
  const { data, error } = useTodayAlerts();
  const [filter, setFilter] = useState<Filter>("all");
  const items = useMemo(() => {
    const all = data?.items ?? [];
    return [...(filter === "all" ? all : all.filter((a) => a.kind === filter))].reverse();   // 최근 것이 위
  }, [data, filter]);
  return (
    <section className="alert-timeline" id="alert-timeline" aria-label="오늘 알림">
      <div className="alert-timeline-head">
        <h4>오늘 알림{data ? <span className="muted small"> · {data.date}</span> : null}</h4>
        <div className="chips" role="group" aria-label="알림 종류">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" className={`chip${filter === f.key ? " chip-on" : ""}`} aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}>{f.label}</button>
          ))}
        </div>
      </div>
      {error && !data && <p className="error small" role="alert">알림을 불러오지 못했습니다. {error}</p>}
      {!data && !error && <p className="muted small">불러오는 중…</p>}
      {data && !items.length && <p className="muted small">{filter === "all" ? "오늘은 아직 알림이 없습니다." : "이 종류의 알림이 오늘은 없습니다."}</p>}
      {items.length > 0 && <ol className="alert-list">{items.map((a) => <AlertRow key={alertKey(a)} a={a} />)}</ol>}
      <p className="muted small">"참고" 는 화면에만 남고 푸시로 보내지 않습니다. 받을 알림 종류는 설정 → 알림에서 고릅니다.</p>
    </section>
  );
}
