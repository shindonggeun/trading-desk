import { price } from "../format";
import type { ChartPattern } from "../types";

/** #630 패턴 하나를 가리키는 열쇠(차트 선 · 목록 칩 · 카드가 같이 쓴다) */
export const patternKey = (p: ChartPattern) => `${p.kind}-${p.timeframe}-${p.upper.from.at}`;

/** "11/25 02:00"(분봉 · 시간봉) 또는 "11/25"(일봉 · 주봉) — 한국 시각 */
const kst = (iso: string | null | undefined, tf: string) => {
  const t = iso ? Date.parse(iso) : NaN;
  if (!Number.isFinite(t)) return "—";
  const d = new Date(t + 9 * 3600_000);
  const day = `${d.getUTCMonth() + 1}/${d.getUTCDate()}`;
  return tf === "1d" || tf === "1w" ? day : `${day} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
};

/** 돌파 예상 구간 한 줄 — "11/25 02:00 ~ 11/25 05:00 (꼭짓점까지의 50~80%, 교과서)" · 지났으면 그렇다고 */
export function windowText(p: ChartPattern): string | null {
  const w = p.breakout_window;
  if (!w) return null;
  const share = w.share?.length === 2 ? `꼭짓점까지의 ${Math.round(w.share[0] * 100)}~${Math.round(w.share[1] * 100)}%` : "";
  const basis = w.basis === "measured" ? "과거 측정" : "교과서";
  return `${kst(w.from, p.timeframe)} ~ ${kst(w.to, p.timeframe)}${share ? ` (${share}, ${basis})` : ` (${basis})`}${w.passed ? " — 이미 지난 구간" : ""}`;
}

/** 이탈한 뒤면 목표까지 얼마 남았는지 — "위로 이탈 — 목표 27,486 까지 +1.2%" · 넘었으면 "목표 도달" */
export function progressText(p: ChartPattern, last: number | null): string | null {
  const t = p.targets;
  if (!t || last == null || !(last > 0) || p.state === "converging") return null;
  const target = p.state === "broke_up" ? t.up : t.down;
  if (!(target > 0)) return null;
  const left = ((target - last) / last) * 100;
  const reached = p.state === "broke_up" ? last >= target : last <= target;
  const side = p.state === "broke_up" ? "위로 이탈" : "아래로 이탈";
  return reached ? `${side} — 목표 ${price(target)} 도달` : `${side} — 목표 ${price(target)} 까지 ${left > 0 ? "+" : ""}${left.toFixed(1)}%`;
}

/**
 * #630 고른 삼각수렴 · 쐐기 설명 카드(#629 응답): 쉬운 설명 · 돌파 예상 구간 · 어느 쪽 확률(표본 수, 적으면 "표본 적음") ·
 * 위 · 아래 목표와 과거 도달 비율 · 거짓 돌파 비율. 방향 우위가 없으면(verdict ≠ edge) 그렇다고 먼저 적는다. 예측이 아니다.
 */
export default function PatternCard({ p, last, sameTf, onClose }: { p: ChartPattern; last: number | null; sameTf: boolean; onClose: () => void }) {
  const o = p.odds;
  const t = p.targets;
  const win = windowText(p);
  const prog = progressText(p, last);
  return (
    <section className="card pattern-card" aria-label={`${p.label} 설명`}>
      <header className="pattern-card-head">
        <strong>{p.label}</strong>
        <button type="button" className="link small" onClick={onClose}>닫기</button>
      </header>
      {prog && <p className="pattern-progress"><strong>{prog}</strong></p>}
      {p.explain && <p className="small">{p.explain}</p>}
      <dl className="pattern-facts small">
        {win && (<><dt>방향이 나오는 때</dt><dd>{win}{!sameTf && <span className="muted"> · 이 차트 주기와 달라 띠는 그리지 않아요</span>}</dd></>)}
        {p.apex_at && (<><dt>꼭짓점</dt><dd>{kst(p.apex_at, p.timeframe)} · {price(p.apex.price)}</dd></>)}
        {o && (
          <>
            <dt>어느 쪽</dt>
            <dd>
              위 {o.up_pct}% · 아래 {o.down_pct}%{o.none_pct != null ? ` · 안 깸 ${o.none_pct}%` : ""} <span className="muted">(과거 {o.n}건)</span>
              {o.small_sample && <span className="pill pill-warn"> 표본 적음</span>}
              {o.verdict !== "edge" && <span className="muted"> — 무작위로 섞은 가격과 차이가 없어 방향 근거는 약해요</span>}
            </dd>
            {o.false_break_pct != null && (<><dt>거짓 돌파</dt><dd>{o.false_break_pct}% — 깼다가 되돌아온 비율</dd></>)}
          </>
        )}
        {t && (
          <>
            <dt>목표</dt>
            <dd>
              <span className="up">위 {price(t.up)}</span> · <span className="down">아래 {price(t.down)}</span>
              <span className="muted"> (패턴 높이 {t.height_pct}%{t.reach_pct != null ? ` · 과거 ${t.reach_pct}% 가 도달` : ""})</span>
            </dd>
          </>
        )}
      </dl>
      <p className="muted small">과거 비율을 적은 것이고 예측이 아니에요.</p>
    </section>
  );
}
