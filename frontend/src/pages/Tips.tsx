import { useEffect, useState } from "react";
import { api } from "../api";
import TrendCard from "../components/TrendCard";
import type { Tip, TipStats, TipsView } from "../types";

const RATES_KO = { hiking: "인상기", cutting: "인하기", hold: "동결" } as const;
const YIELD_KO = { rising: "상승", falling: "하락", flat: "보합" } as const;
const DOLLAR_KO = { strong: "강세", weak: "약세", flat: "보합" } as const;

function signed(v: number | null | undefined, unit = "%"): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v}${unit}`;
}

function Stats({ label, s, decay }: { label: string; s: TipStats | undefined; decay: boolean }) {
  if (!s || !s.n) return <tr><th scope="row">{label}</th><td colSpan={4} className="muted">표본 없음</td></tr>;
  return (
    <tr>
      <th scope="row">{label}</th>
      <td className="num">{s.n.toLocaleString("ko-KR")}일</td>
      <td className="num">{signed(s.median)} <span className="muted">/ 전체 {signed(s.all_median)}</span></td>
      <td className={`num ${s.vs_all != null && s.vs_all < 0 ? "down" : "up"}`}>{signed(s.vs_all, "%p")}</td>
      <td className="num">{decay ? "—" : `${s.win ?? "—"}% / ${s.all_win ?? "—"}%`}</td>
    </tr>
  );
}

export function TipCard({ tip, focus }: { tip: Tip; focus: boolean }) {
  const ev = tip.evidence;
  const decay = tip.metric === "decay";
  return (
    <article id={`tip-${tip.id}`} className={`card tip-card${tip.active ? " tip-on" : ""}${focus ? " tip-focus" : ""}`}>
      <header className="tip-head">
        {tip.active ? <span className="pill pill-warn">지금 해당</span> : <span className="pill">{tip.known ? "해당 없음" : "판단 불가"}</span>}
        {tip.weak && <span className="pill pill-bad" title="학습 · 검증 두 구간 모두에서 전체 기간보다 뚜렷이 나쁘지 않았습니다">근거 약함</span>}
        <h3>{tip.title}</h3>
      </header>
      <p className="small"><strong>조건</strong> {tip.condition}{tip.why ? <span className="muted"> · 지금 {tip.why}</span> : null}</p>
      {ev ? (
        <>
          <p className="muted small">
            근거: {ev.proxy} {decay ? "3배 일일 레버리지의 감쇠(3배 레버리지 ETF − 지수 수익률×3)" : "60거래일 뒤 수익률"} · {ev.since?.slice(0, 4)}년~,
            {` ${ev.split}년 전 학습 · 이후 검증`}
          </p>
          <div className="tip-table-wrap">
            <table className="tip-table small">
              <thead>
                <tr><th /><th>국면 날</th><th>중앙값 / 전체</th><th>차이</th><th>오른 비율 / 전체</th></tr>
              </thead>
              <tbody>
                <Stats label="학습" s={ev.train["60"]} decay={decay} />
                <Stats label="검증" s={ev.held_out["60"]} decay={decay} />
              </tbody>
            </table>
          </div>
        </>
      ) : <p className="muted small">근거 계산 결과가 아직 없습니다.</p>}
      {tip.exceptions.length > 0 && (
        <ul className="tip-exc small">
          {tip.exceptions.map((e) => <li key={e}>예외: {e}</li>)}
        </ul>
      )}
      <p className="muted small">해당 종목: {tip.applies_to.join(" · ")}</p>
    </article>
  );
}

/** #332 국면별 매매 팁(#330): 지금 금리 · 달러 국면과 그에 해당하는 팁을 근거 숫자와 함께. */
export default function Tips({ query }: { query: URLSearchParams }) {
  const [view, setView] = useState<TipsView | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const focus = query.get("tip");
  useEffect(() => {
    api.tips().then(setView, (e: unknown) => setFailed(e instanceof Error ? e.message : "불러오지 못했습니다"));
  }, []);
  // #461 지수 일봉이 아직 오는 중이면(trend_error, 빈 trend) 한 번 더 부른다
  const loadingTrend = !!view && !(view.trend?.length) && !!view.trend_error?.includes("불러오는 중");
  useEffect(() => {
    if (!loadingTrend) return;
    const t = window.setTimeout(() => { api.tips().then(setView, () => undefined); }, 10_000);
    return () => window.clearTimeout(t);
  }, [loadingTrend]);
  useEffect(() => {
    if (view && focus) document.getElementById(`tip-${focus}`)?.scrollIntoView({ block: "start" });
  }, [view, focus]);

  if (failed) return <main className="page"><h2>매매 팁</h2><p className="muted">금리 · 달러 시세를 불러오지 못했습니다: {failed}</p></main>;
  if (!view) return <main className="page"><h2>매매 팁</h2><p className="muted">불러오는 중…</p></main>;
  const g = view.regime;
  const v = g.values ?? {};
  const tips = [...view.tips].sort((a, b) => Number(b.active) - Number(a.active) || Number(a.weak) - Number(b.weak));
  return (
    <main className="page tips-page">
      <h2>국면별 매매 팁</h2>
      <TrendCard trend={view.trend ?? []} guide={view.guide ?? {}} error={view.trend_error ?? null} />
      <section className="card tip-regime" aria-label="지금 국면">
        <div><span className="muted small">기준금리 방향</span><strong>{g.rates ? RATES_KO[g.rates] : "—"}</strong>
          <span className="muted small">13주물 3개월 {signed(v.bill_3m_change_pp, "%p")}</span></div>
        <div><span className="muted small">10년물</span><strong>{g.yield10 ? YIELD_KO[g.yield10] : "—"}{g.yield_spike ? " · 급등" : ""}</strong>
          <span className="muted small">{v.yield10 ?? "—"}% · 3개월 {signed(v.yield10_3m_change_pp, "%p")}</span></div>
        <div><span className="muted small">달러</span><strong>{g.dollar ? DOLLAR_KO[g.dollar] : "—"}</strong>
          <span className="muted small">달러 지수 3개월 {signed(v.dollar_3m_pct)}</span></div>
        <div><span className="muted small">나스닥100</span><strong>{g.sideways ? "횡보" : g.sideways === false ? "추세" : "—"}</strong>
          <span className="muted small">3개월 {signed(v.nasdaq_3m_pct)}</span></div>
        <p className="muted small tip-asof">기준일 {view.as_of ?? "—"} · 근거 계산 {view.evidence_as_of ?? "—"}</p>
      </section>
      <div className="tip-list">
        {tips.map((t) => <TipCard key={t.id} tip={t} focus={t.id === focus} />)}
      </div>
      <p className="muted small">
        근거는 2000년부터 국면이 맞았던 날과 모든 날의 60거래일 뒤 결과를 비교한 것입니다. 날마다 창이 겹쳐 표본(일)은 독립된 사례 수가 아니며,
        경향일 뿐 규칙이 아닙니다. 학습 · 검증 두 구간 모두에서 뚜렷이 나쁘지 않은 팁은 "근거 약함" 으로 표시합니다.
      </p>
    </main>
  );
}
