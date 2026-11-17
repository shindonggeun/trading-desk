import { pct, range, useHalving, WHERE_DAYS_SHORT } from "../halving";

/** #515 대시보드 작은 카드: 반감기 뒤 일수(과거 바닥 범위와 견줘) · 고점 대비 · 고점 뒤 최저 대비 — 누르면 #/halving */
export default function HalvingBrief() {
  const { data, fake, error } = useHalving();
  if (error) return <p className="muted small">반감기 사이클을 불러오지 못했습니다.</p>;
  if (!data) return <p className="muted small">불러오는 중…</p>;
  const c = data.current;
  const bd = data.ranges.bottom_days_from_halving;
  return (
    <a className="card hv-brief" href="#/halving" aria-label="비트코인 반감기 사이클 화면으로">
      {fake && <span className="pill pill-warn">예시</span>}
      <span className="hv-brief-stat">
        <span className="muted small">반감기 뒤</span>
        <strong>{c.days_since_halving}일째</strong>
        <span className="muted small">과거 바닥 {range(bd.min, bd.max, "일")} · {WHERE_DAYS_SHORT[data.ranges.now.days_since_halving]}</span>
      </span>
      <span className="hv-brief-stat">
        <span className="muted small">고점 대비</span>
        <strong>{pct(c.from_high_pct)}</strong>
        <span className="muted small">{c.high.date} 고점</span>
      </span>
      {c.low_after_high && (
        <span className="hv-brief-stat">
          <span className="muted small">고점 뒤 최저 대비</span>
          <strong>{pct(c.from_low_pct)}</strong>
          <span className="muted small">바닥 확정 아님</span>
        </span>
      )}
      <span className="hv-brief-more small">사이클 그래프 보기 →</span>
    </a>
  );
}
