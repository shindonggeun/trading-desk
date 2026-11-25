import { useEffect, useId, useState } from "react";
import {
  conditionState, EVIDENCE_KO, evidencePriceLabels, EVIDENCE_STATUS_KO, evidenceMeta, formatTrendDistance, formatTrendPrice, LEAD_KO,
  productName, TREND_NOTE, trendPresentation, trendProduct,
  type TrendFocusKey, type TrendFollowObservation, type TrendKeyPrice,
} from "../trendFollow";
import "./TrendFollowPanel.css";

const KST = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const KST_DAY = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit" });

function Stamp({ value, day }: { value: string | null | undefined; day?: boolean }) {
  const at = value ? Date.parse(value) : NaN;
  return value && Number.isFinite(at) ? <time dateTime={value}>{(day ? KST_DAY : KST).format(at)}</time> : <>미제공</>;
}

export type TrendFocusControl = {
  /** 지금 차트에서 강조할 수 있는 대상(같은 시각 봉 · 가격) */
  available: (key: TrendFocusKey) => boolean;
  /** 앵커 중 지금 차트에 있는 수 */
  coverage: (key: TrendFocusKey) => { shown: number; total: number } | null;
  active: TrendFocusKey | null;
  onPick: (key: TrendFocusKey | null) => void;
  /** 근거 앵커로 작도 연습을 시작할 수 있으면 */
  canPractice: (evidenceId: string) => boolean;
  onPractice: (evidenceId: string) => void;
};

/**
 * #978 차트 읽기 맨 위 — 일봉 추세 · 진행 상태 한 줄 → 지킬 가격 · 넘을 저항 → 최근 확정봉 → 다음 확인 · 무효화 →
 * 근거(누르면 차트 강조, 앵커가 맞으면 작도 연습) → 선행 돌파 비교. 관찰이지 매매 신호가 아니다.
 */
export default function TrendFollowPanel({ observation: o, interval, intervalKo, focus }: {
  observation: TrendFollowObservation;
  interval: string;
  intervalKo: string;
  focus?: TrendFocusControl;
}) {
  const title = useId();
  const { line, reason, signal, pending } = trendPresentation(o);
  const product = trendProduct(o);
  const unit = product.unit;
  const confirms = o.conditions.filter((c) => c.kind === "confirm");
  const invalidates = o.conditions.filter((c) => c.kind === "invalidate");
  const pick = (key: TrendFocusKey) => focus?.onPick(focus.active === key ? null : key);
  // 근거를 고르면 목록을 펴 두기만 하고, 강조를 꺼도 접지 않는다
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const activeEvidence = focus?.active != null && o.evidence.some((e) => e.id === focus.active);
  useEffect(() => { if (activeEvidence) setEvidenceOpen(true); }, [activeEvidence]);
  const focusButton = (key: TrendFocusKey, label: string) => focus && (
    <button type="button" className={`chip chip-small trend-focus${focus.active === key ? " chip-on" : ""}`}
      aria-pressed={focus.active === key} disabled={!focus.available(key)} onClick={() => pick(key)}>{label}</button>
  );
  // 카드 전체가 강조 버튼 — 좁은 화면에서 핵심 가격과 다음 확인이 같이 보이게 높이를 줄인다
  const keyRow = (key: "hold" | "break", head: string, k: TrendKeyPrice | null) => {
    const on = focus?.active === key;
    const body = (
      <>
        <span className="trend-key-head">{head}</span>
        {k ? <span className="trend-key-price">
          <b>{formatTrendPrice(k.price, unit)}</b> <span className="muted">({formatTrendDistance(k.distance_pct)})</span>
        </span> : <span className="muted">없음</span>}
        {k && <span className="trend-key-label small">{k.label}</span>}
      </>
    );
    if (!k || !focus) return <div className={`trend-key trend-key-${key}`}>{body}</div>;
    // 그릴 수 없을 때도 내용은 흐리지 않는다(aria-disabled) — 차트가 아직 없을 때 '해당 없음'처럼 보이지 않게
    const can = focus.available(key);
    return (
      <button type="button" className={`trend-key trend-key-${key}${on ? " on" : ""}`} aria-pressed={on} aria-disabled={!can}
        title={can ? "누르면 차트에 이 가격을 표시해요" : "지금 차트에 표시할 수 없어요"} onClick={() => can && pick(key)}>{body}</button>
    );
  };
  return (
    <section className="trend-follow" aria-labelledby={title}>
      <div className="trend-follow-head">
        <h4 id={title}>{line}</h4>
        {pending && <span className="trend-badge small">오늘 봉 진행 중</span>}
      </div>
      <p className="muted small trend-product">
        {product.label}{unit && ` · ${unit}`} · 판정 일봉{interval !== "1d" && <> · 지금 차트 <b>{intervalKo}</b></>}
      </p>
      {reason && <p className="small">{reason}</p>}
      {o.status !== "unavailable" && <>
        {signal && <p className="trend-signal">{signal}</p>}
        <div className="trend-keys">
          {keyRow("hold", "지켜야 할 가격", o.key_prices.hold)}
          {keyRow("break", "넘어야 할 저항", o.key_prices.break)}
        </div>
        {(confirms.length > 0 || invalidates.length > 0) && (
          <div className="trend-conditions">
            {confirms.length > 0 && <>
              <h5>다음 확인</h5>
              <ul>{confirms.map((c) => <Condition key={c.id} c={c} unit={unit} />)}</ul>
            </>}
            {invalidates.length > 0 && <>
              <h5>무효화</h5>
              <ul>{invalidates.map((c) => <Condition key={c.id} c={c} unit={unit} />)}</ul>
            </>}
          </div>
        )}
        {o.last_bar && (
          <p className="small trend-bar">
            최근 확정봉 <Stamp value={o.last_bar.at} day /> · 종가 {formatTrendPrice(o.last_bar.close, unit)}
            {" "}(고 {formatTrendPrice(o.last_bar.high, null)} · 저 {formatTrendPrice(o.last_bar.low, null)})
            {focusButton("stage", "봉 강조")}
          </p>
        )}
        {o.evidence.length > 0 && (
          <details className="trend-evidence" open={evidenceOpen}
            onToggle={(e) => { if (e.target === e.currentTarget) setEvidenceOpen(e.currentTarget.open); }}>
            <summary>근거 {o.evidence.length}개 — 차트에 표시할 수 있어요</summary>
            <ol>
              {o.evidence.map((e) => {
                const on = focus?.active === e.id;
                const can = !!focus && focus.available(e.id);
                const meta = evidenceMeta(e);
                const cover = on ? focus?.coverage(e.id) : null;
                return (
                  <li key={e.id} className={`trend-ev trend-ev-${e.status}${on ? " on" : ""}`}>
                    <span className="trend-ev-head">
                      <span className={`trend-status trend-status-${e.status}`}>{EVIDENCE_STATUS_KO[e.status]}</span>
                      <b>{e.label || EVIDENCE_KO[e.kind]}</b>
                    </span>
                    {e.detail && <span className="trend-ev-detail small">{e.detail}</span>}
                    {e.prices.length > 0 && (
                      <span className="small">{evidencePriceLabels(e).map((label, i) => `${label} ${formatTrendPrice(e.prices[i], null)}`).join(" · ")}{unit && ` ${unit}`}</span>
                    )}
                    {e.anchors.length > 0 && (
                      <span className="small muted">앵커 {e.anchors.map((a, i) => (
                        <span key={i}>{i > 0 && " → "}<Stamp value={a.at} day /> {formatTrendPrice(a.price, null)}
                          {a.rsi != null ? ` (RSI ${a.rsi.toFixed(1)})` : a.k != null ? ` (%K ${a.k.toFixed(1)})` : ""}</span>
                      ))}</span>
                    )}
                    {meta && <span className="small muted">{meta}</span>}
                    <span className="small muted">{e.confirmed_at ? <>확정 <Stamp value={e.confirmed_at} /></> : "아직 확정 전"}</span>
                    {cover && cover.shown < cover.total && (
                      <span className="small trend-ev-cover" role="status">
                        앵커 {cover.total}개 중 {cover.shown}개만 지금 차트에 있어요{interval === "1d" ? " — 차트를 왼쪽으로 끌어 과거 봉을 더 받으세요." : " — 일봉 차트에서 볼 수 있어요."}
                      </span>
                    )}
                    {focus && (
                      <span className="trend-ev-actions">
                        <button type="button" className={`chip chip-small${on ? " chip-on" : ""}`} aria-pressed={on} disabled={!can}
                          aria-label={`${e.label || EVIDENCE_KO[e.kind]} 차트에 표시`} onClick={() => pick(e.id)}>차트에 표시</button>
                        {focus.canPractice(e.id) && (
                          <button type="button" className="chip chip-small" aria-label={`${e.label || EVIDENCE_KO[e.kind]} 앵커로 작도 연습`}
                            onClick={() => focus.onPractice(e.id)}>이 앵커로 작도 연습</button>
                        )}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          </details>
        )}
        {o.lead && (
          <div className="trend-lead">
            <h5>{productName(o.lead.benchmark)} 대비 돌파 시점{o.lead.benchmark_at && <> · 기준 돌파 <Stamp value={o.lead.benchmark_at} day /></>}</h5>
            <p className="small muted">{o.lead.condition} — 돌파한 시점을 비교한 것이지 매수 신호가 아니에요.</p>
            <ul>
              {o.lead.items.map((it) => (
                <li key={it.ticker} className="small">
                  <b>{productName(it.ticker)}</b> {LEAD_KO[it.state]}
                  {it.at && it.state !== "not_comparable" && it.state !== "waiting" && <> · 돌파 <Stamp value={it.at} day /></>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </>}
      <details className="trend-source small">
        <summary>판정 기준과 출처</summary>
        <p className="muted">{TREND_NOTE} 진행 중인 봉은 확인 조건에서 빼고 마지막 확정 일봉으로만 판정해요.</p>
        {o.trend?.basis && <p className="muted">추세 근거 — {o.trend.basis}</p>}
        {o.stage?.basis && <p className="muted">진행 상태 근거 — {o.stage.basis}</p>}
        <dl className="trend-grid">
          <div><dt>원천</dt><dd>{product.source || "미제공"}</dd></div>
          <div><dt>마지막 확정봉 (한국 시각)</dt><dd><Stamp value={o.as_of} /></dd></div>
          <div><dt>조회 기준 (한국 시각)</dt><dd><Stamp value={o.observed_at} /></dd></div>
          {o.stage?.confirmed_at && <div><dt>진행 상태 확정 (한국 시각)</dt><dd><Stamp value={o.stage.confirmed_at} /></dd></div>}
        </dl>
        {product.note && <p className="muted">{product.note}</p>}
      </details>
      <p className="muted small trend-note">{o.note ?? TREND_NOTE}</p>
    </section>
  );
}

function Condition({ c, unit }: { c: TrendFollowObservation["conditions"][number]; unit: string | null }) {
  const s = conditionState(c);
  return (
    <li className={`trend-cond trend-cond-${s.tone}`}>
      <span className={`trend-cond-tag trend-cond-tag-${s.tone}`}>{s.label}</span>
      <span>{c.text}{c.price != null && <> <span className="muted">({formatTrendPrice(c.price, unit)})</span></>}</span>
    </li>
  );
}
