import { type ReactNode, useMemo, useState } from "react";
import type { LineSignal, MarketAlert } from "../api";
import { alertHref, alertKey, alertLines, badgeOf, hhmm, isWarning, KIND_LABEL, kindLabel, sideOf, useTodayAlerts } from "../alertsFeed";
import { chartHref, kstHhmm, signalTime, LINE_TF_KO, signalKey, splitText, useTodayLineSignals } from "../lineSignals";

/** #581 오늘 알림 타임라인 — 수급 전향 · 수급 흐름 변화 · 나스닥 선물 이상 징후(주인 계정은 보유 종목 손절 · 익절도)를 시각 순서로.
 *  색만으로 구분하지 않게 강도는 글자(참고 · 주의 · 강함), 방향은 ▲▼ 로 함께 쓴다. */
type Filter = "all" | "warning" | MarketAlert["kind"] | "line_signal";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "전체" },
  { key: "warning", label: "⚠ 경고만" },
  { key: "flow_turn", label: KIND_LABEL.flow_turn },
  { key: "flow_shift", label: KIND_LABEL.flow_shift },
  { key: "flow_warn", label: KIND_LABEL.flow_warn },
  { key: "nq_alert", label: KIND_LABEL.nq_alert },
  { key: "sector_mover", label: KIND_LABEL.sector_mover },
  { key: "line_signal", label: "차트 선" },
];
/** #581 보유 종목 칩은 그 알림이 있을 때만(주인 계정에만 온다) */
const HOLDING_FILTER: { key: Filter; label: string } = { key: "holding_alert", label: KIND_LABEL.holding_alert };

/** #614 차트 선 알림(#613) 한 건 — 누르면 그 종목 · 시간봉 차트 */
export function LineRow({ s }: { s: LineSignal }) {
  const { title, body } = splitText(s.text);
  return (
    <li className="alert-row line-row">
      <time className="alert-time" dateTime={s.at}>{kstHhmm(signalTime(s))}</time>
      <span className="alert-sev line-sev">선</span>
      <span className="alert-kind muted small">차트 선 · {LINE_TF_KO[s.timeframe] ?? s.timeframe}</span>
      <span className="alert-text">
        <a href={chartHref(s)}>{title}</a>
        {body && <span className="muted small"> · {body.split("\n")[0]}</span>}
      </span>
    </li>
  );
}

export function AlertRow({ a }: { a: MarketAlert }) {
  const side = sideOf(a);
  const b = badgeOf(a);
  const { head, reading } = alertLines(a.text);
  const href = alertHref(a);
  return (
    <li className={`alert-row ${b.cls}`}>
      <time className="alert-time" dateTime={a.t}>{hhmm(a.t)}</time>
      <span className={`alert-sev ${b.cls}`}>{b.mark && <span aria-hidden="true">{b.mark} </span>}{b.label}</span>
      <span className="alert-kind muted small">{kindLabel(a.kind)}</span>
      <span className="alert-text">
        {side && <span className={side === "up" ? "up" : "down"} aria-hidden="true">{side === "up" ? "▲ " : "▼ "}</span>}
        {href ? <a href={href}>{head}</a> : head}
        {!a.pushed && <span className="muted small"> · 푸시 안 함</span>}
        {reading && <span className="alert-reading muted small">{reading}</span>}
      </span>
    </li>
  );
}

export default function AlertTimeline() {
  const { data, error } = useTodayAlerts();
  const lines = useTodayLineSignals(data?.date, data);
  const [filter, setFilter] = useState<Filter>("all");
  const items = useMemo(() => {
    const all = data?.items ?? [];
    const rows: { t: number; key: string; node: ReactNode }[] = [
      ...(filter === "all" ? all : filter === "warning" ? all.filter(isWarning) : all.filter((a) => a.kind === filter)).map((a) => ({ t: Date.parse(a.t), key: alertKey(a), node: <AlertRow key={alertKey(a)} a={a} /> })),
      ...(filter === "all" || filter === "line_signal" ? lines : []).map((s) => ({ t: signalTime(s), key: signalKey(s), node: <LineRow key={signalKey(s)} s={s} /> })),
    ];
    return rows.sort((a, b) => b.t - a.t);   // 최근 것이 위
  }, [data, lines, filter]);
  const filters = useMemo(() => (filter === "holding_alert" || data?.items.some((a) => a.kind === "holding_alert")
    ? [...FILTERS, HOLDING_FILTER] : FILTERS), [data, filter]);
  return (
    <section className="alert-timeline" id="alert-timeline" aria-label="오늘 알림">
      <div className="alert-timeline-head">
        <h4>오늘 알림{data ? <span className="muted small"> · {data.date}</span> : null}</h4>
        <div className="chips" role="group" aria-label="알림 종류">
          {filters.map((f) => (
            <button key={f.key} type="button" className={`chip${filter === f.key ? " chip-on" : ""}`} aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}>{f.label}</button>
          ))}
        </div>
      </div>
      {error && !data && <p className="error small" role="alert">알림을 불러오지 못했습니다. {error}</p>}
      {!data && !error && <p className="muted small">불러오는 중…</p>}
      {data && !items.length && <p className="muted small">{filter === "all" ? "오늘은 아직 알림이 없습니다." : "이 종류의 알림이 오늘은 없습니다."}</p>}
      {items.length > 0 && <ol className="alert-list">{items.map((r) => r.node)}</ol>}
      <p className="muted small">◎ 호재 · · 참고 · ! 주의 · ⚠ 경고는 지수에 어떤 뜻인지예요(크기와 따로, 보유 종목 알림은 그 종목 이야기). 작은 참고 알림은 화면에만 남고 푸시로 보내지 않습니다. 받을 알림 종류 · 등급은 설정 → 알림에서 고릅니다.</p>
    </section>
  );
}
