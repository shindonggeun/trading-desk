import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { fakeDayCallHistory, fakeDayCallStats } from "../briefsFake";
import { estimateNote, LABEL_KO, LABEL_MARK, MARKET_KO, pct } from "../dayCall";
import type { DayCallHistory, DayCallLabel, DayCallMarket, DayCallStats } from "../types";

const dev404 = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;
const DAYS = 90;

function Label({ l }: { l: DayCallLabel | null }) {
  if (!l) return <span className="muted">채점 전</span>;
  return <span className={`daycall-tag daycall-${l}`}><span aria-hidden="true">{LABEL_MARK[l]} </span>{LABEL_KO[l]}</span>;
}

function Mark({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="muted">—</span>;
  return ok ? <span className="daycall-hit">✓ 맞음</span> : <span className="daycall-miss">✗ 틀림</span>;
}

/** #573 오늘 방향(#572) 적중 기록 — 날짜별 판정 · 실제 결과 · 맞음/틀림, 적중률 · 단계별 정확도 · 보정 표 */
export default function DayCallRecord() {
  const [market, setMarket] = useState<DayCallMarket>("kr");
  const [hist, setHist] = useState<DayCallHistory | null>(null);
  const [stats, setStats] = useState<DayCallStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setHist(null);
    setStats(null);
    setFailed(false);
    api.dayCallHistory(market, DAYS).then((h) => alive && setHist(h)).catch((e: unknown) => {
      if (!alive) return;
      if (dev404(e)) setHist(fakeDayCallHistory(market, DAYS));
      else setFailed(true);
    });
    api.dayCallStats(market, DAYS).then((s) => alive && setStats(s)).catch((e: unknown) => {
      if (alive && dev404(e)) setStats(fakeDayCallStats(market, DAYS));
    });
    return () => { alive = false; };
  }, [market]);

  return (
    <div className="paper-page daycall-page">
      <section className="card">
        <div className="daycall-page-head">
          <h2>오늘 방향 적중 기록</h2>
          <div className="seg seg-small" role="group" aria-label="시장">
            {(["kr", "us"] as const).map((m) => (
              <button key={m} type="button" aria-pressed={market === m} className={market === m ? "on" : ""} onClick={() => setMarket(m)}>
                {MARKET_KO[m]}
              </button>
            ))}
          </div>
        </div>
        <p className="muted small">
          {estimateNote(stats?.hit_rate, DAYS, stats?.n)}. 적중은 방향(롱 · 약보합 · 숏)이 맞은 날, 단계 일치는 다섯 단계까지 맞은 날이에요.
          정답은 {market === "kr" ? "코스피 시가 → 종가" : "S&P500 시가 → 종가"} 등락률이에요.
        </p>
        {stats && (
          <dl className="daycall-stats">
            <div><dt>적중률</dt><dd>{pct(stats.hit_rate)}</dd></div>
            <div><dt>단계 일치</dt><dd>{pct(stats.exact_rate)}</dd></div>
            <div><dt>채점한 날</dt><dd>{stats.n}일</dd></div>
            <div><dt>Brier 점수</dt><dd>{stats.brier == null ? "—" : stats.brier.toFixed(3)}<span className="muted small"> (낮을수록 좋음)</span></dd></div>
          </dl>
        )}
      </section>

      {stats && (
        <section className="card">
          <h3>단계별 정확도</h3>
          <div className="table-wrap">
            <table className="stats-table">
              <thead><tr><th>판정</th><th className="num">날 수</th><th className="num">적중률</th></tr></thead>
              <tbody>{stats.by_label.map((r) => (
                <tr key={r.label}><td><Label l={r.label} /></td><td className="num">{r.n}</td><td className="num">{pct(r.hit_rate)}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <h3>확률 보정</h3>
          <p className="muted small">판정 확률이 그 구간이었던 날, 실제로 맞은 비율이에요. 두 숫자가 가까울수록 확률을 믿을 만해요.</p>
          <div className="table-wrap">
            <table className="stats-table">
              <thead><tr><th>확률 구간</th><th className="num">날 수</th><th className="num">평균 확률</th><th className="num">실제 적중</th></tr></thead>
              <tbody>{stats.calibration.map((c) => (
                <tr key={c.lo}><td>{pct(c.lo)}–{pct(c.hi)}</td><td className="num">{c.n}</td><td className="num">{pct(c.predicted)}</td><td className="num">{pct(c.actual)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <h3>날짜별 기록</h3>
        {failed ? <p className="muted small">적중 기록을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>
          : !hist ? <p className="muted small">불러오는 중…</p>
          : hist.items.length === 0 ? <p className="muted small">아직 기록이 없어요.</p> : (
            <div className="table-wrap">
              <table className="stats-table daycall-table">
                <thead><tr><th>날짜</th><th>판정</th><th className="num">확률</th><th>실제</th><th className="num">등락</th><th>결과</th></tr></thead>
                <tbody>{hist.items.map((it) => (
                  <tr key={`${it.date}-${it.round ?? 0}`}>
                    <td>{it.date.slice(5).replace("-", "/")}{it.round ? <span className="muted"> · {it.round}차</span> : null}</td>
                    <td><Label l={it.label} /></td>
                    <td className="num">{pct(it.probs[it.label])}</td>
                    <td><Label l={it.actual} /></td>
                    <td className={`num ${it.actual_pct == null ? "" : it.actual_pct > 0 ? "up" : it.actual_pct < 0 ? "down" : ""}`}>
                      {it.actual_pct == null ? "—" : `${it.actual_pct > 0 ? "+" : ""}${it.actual_pct.toFixed(2)}%`}
                    </td>
                    <td><Mark ok={it.hit} />{it.exact ? <span className="muted small"> · 단계 일치</span> : null}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
      </section>
    </div>
  );
}
