import { pct, range, useHalving, WHERE_DAYS_SHORT } from "../halving";

/**
 * #515 대시보드 작은 카드: 반감기 뒤 일수(과거 바닥 범위와 견줘) · 고점 대비 · 고점 뒤 최저 대비 — 누르면 #/halving.
 * 링크 이름은 안의 글 그대로(숫자까지 화면 낭독기가 읽게). 서버가 지금 사이클 · 범위를 못 내면(null) 그 칸만 뺀다.
 */
export default function HalvingBrief() {
  const { data, fake, error } = useHalving();
  if (error) return <p className="muted small">반감기 사이클을 불러오지 못했습니다.</p>;
  if (!data) return <p className="muted small">불러오는 중…</p>;
  const c = data.current;
  const bd = data.ranges.bottom_days_from_halving;
  const now = data.ranges.now.days_since_halving;
  return (
    <a className="card hv-brief" href="#/halving">
      {fake && <span className="pill pill-warn">예시</span>}
      {!c ? (
        <span className="muted small">지금 사이클 자료가 모자라요 — 지난 사이클 표 보기</span>
      ) : (
        <>
          <span className="hv-brief-stat">
            <span className="muted small">반감기 뒤</span>
            <strong>{c.days_since_halving}일째</strong>
            {bd && bd.cycles.length > 0 && (
              <span className="muted small">과거 바닥 {range(bd.min, bd.max, "일")}{now ? ` · ${WHERE_DAYS_SHORT[now]}` : ""}</span>
            )}
          </span>
          <span className="hv-brief-stat">
            <span className="muted small">고점 대비</span>
            <strong>{pct(c.from_high_pct)}</strong>
            <span className="muted small">{c.high.date} 고점</span>
          </span>
          {c.low_after_high && c.from_low_pct != null && (
            <span className="hv-brief-stat">
              <span className="muted small">고점 뒤 최저 대비</span>
              <strong>{pct(c.from_low_pct)}</strong>
              <span className="muted small">바닥 확정 아님</span>
            </span>
          )}
        </>
      )}
      <span className="hv-brief-more small">사이클 그래프 보기 →</span>
    </a>
  );
}
