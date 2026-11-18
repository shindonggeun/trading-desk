import { useEffect, useRef } from "react";
import { CAUSE_KO, oddsLabel, pct, sessions, SIZE_KO } from "../gapOdds";
import type { GapOdds, GapReport, OpenGap } from "../types";

const DIR_KO = { up: "상승 갭", down: "하락 갭" } as const;

function Row({ name, o }: { name: string; o: GapOdds | undefined }) {
  if (!o || o.n === 0) return null;
  return (
    <tr className={o.few ? "gap-few" : undefined}>
      <th scope="row">{name}</th>
      <td className="num">{o.n}{o.few ? " ⚠" : ""}</td>
      <td className="num">{pct(o.p1)}</td>
      <td className="num">{pct(o.p3)}</td>
      <td className="num">{pct(o.p5)}</td>
      <td className="num">{pct(o.p20)}</td>
      <td className="num">{sessions(o.median_sessions)}</td>
    </tr>
  );
}

/**
 * #578 차트의 열린 갭 띠를 누르면 — 이 갭과 같은 방향 · 크기의 과거 갭이 1 · 3 · 5 · 20거래일 안에 메워진 비율, 중앙 소요, 표본(30 미만 ⚠).
 * 확률은 과거 빈도다. Esc · 닫기로 닫는다.
 */
export default function GapOddsCard({ rep, gap, onClose }: { rep: GapReport; gap: OpenGap; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.closest("input, textarea, select, [contenteditable=true], dialog[open]"))) return;
      e.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const t = rep.table;
  const key = `${gap.direction}_${gap.size}`;
  const where = gap.product_fill_price != null
    ? <>{gap.fill_price} <span className="muted">(이 상품 약 {gap.product_fill_price}, {gap.product_move_pct! > 0 ? "+" : ""}{gap.product_move_pct}%)</span></>
    : <>{gap.fill_price}</>;
  return (
    <section className="gap-card" aria-label="갭 메움 확률">
      <header className="gap-card-head">
        <strong>{gap.day} {DIR_KO[gap.direction]} {gap.gap_pct > 0 ? "+" : ""}{gap.gap_pct}%</strong>
        <span className="muted small">{SIZE_KO[gap.size]} · {gap.atr_multiple} ATR · {CAUSE_KO[gap.cause]}</span>
        <button ref={closeRef} type="button" className="link small" onClick={onClose} aria-label="갭 카드 닫기">닫기</button>
      </header>
      <p className="small">
        메움 가격 {where} — 지금에서 {gap.to_fill_pct > 0 ? "+" : ""}{gap.to_fill_pct}% · {gap.open_sessions}거래일째 열림
      </p>
      <p className="small">
        <strong>이만큼 열려 있던 비슷한 갭</strong>: {oddsLabel(gap.odds)} · 20일 {pct(gap.odds.p20)}
      </p>
      <div className="gap-card-table">
        <table className="small">
          <caption className="muted">처음부터 센 메움 비율(연 날 = 1일째)</caption>
          <thead>
            <tr><th scope="col">구분</th><th scope="col">표본</th><th scope="col">1일</th><th scope="col">3일</th><th scope="col">5일</th><th scope="col">20일</th><th scope="col">중앙</th></tr>
          </thead>
          <tbody>
            <Row name={`${DIR_KO[gap.direction]} · ${SIZE_KO[gap.size]}`} o={t.direction_size[key]} />
            <Row name={DIR_KO[gap.direction]} o={t.direction[gap.direction]} />
            <Row name={`원인: ${CAUSE_KO[gap.cause]}`} o={t.cause[gap.cause]} />
            <Row name="전체" o={t.all} />
          </tbody>
        </table>
      </div>
      <p className="muted small">
        {rep.basis.note ? `${rep.basis.note}. ` : ""}{rep.basis.symbol} 일봉 {rep.since} 이후 갭 {rep.gaps_counted}개의 과거 빈도 — 예측이 아닙니다. ⚠ 는 표본 30개 미만.
      </p>
    </section>
  );
}
