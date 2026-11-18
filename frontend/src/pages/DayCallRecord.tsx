import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { fakeDayCallHistory, fakeDayCallStats } from "../briefsFake";
import { estimateNote, MARKET_INDEX, MARKET_KO, percent, rate, STAGE_KO, STAGE_MARK } from "../dayCall";
import type { DayCallHistory, DayCallMarket, DayCallStage, DayCallStats } from "../types";

const dev404 = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;
const DAYS = 90;

function Stage({ s }: { s: DayCallStage | null | undefined }) {
  if (!s) return <span className="muted">채점 전</span>;
  return <span className={`daycall-tag daycall-${s}`}><span aria-hidden="true">{STAGE_MARK[s]} </span>{STAGE_KO[s]}</span>;
}

function Mark({ ok }: { ok: boolean | null | undefined }) {
  if (ok == null) return <span className="muted">—</span>;
  return ok ? <span className="daycall-hit">✓ 맞음</span> : <span className="daycall-miss">✗ 틀림</span>;
}

/** #573 오늘 방향(#572) 적중 기록 — 날짜별 판정 · 실제 결과 · 맞음/틀림, 적중률 · 기준선 · 단계별 · 보정 표 */
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

  const b = stats?.baselines;
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
          {estimateNote(stats?.direction_hit_rate, DAYS, stats?.n)}. 방향 적중은 롱 · 숏 방향이 맞은 판정, 단계 적중은 다섯 단계까지 맞은 판정이에요.
          정답은 {MARKET_INDEX[market]} 시가 → 종가 움직임을 그날 변동폭(ATR)으로 나눠 매겨요.
        </p>
        {stats && (
          <dl className="daycall-stats">
            <div><dt>방향 적중</dt><dd>{rate(stats.direction_hit_rate)}</dd></div>
            <div><dt>단계 적중</dt><dd>{rate(stats.stage_hit_rate)}</dd></div>
            <div><dt>채점한 판정</dt><dd>{stats.n}번{stats.missed ? <span className="muted small"> · 놓친 회차 {stats.missed}</span> : null}</dd></div>
            <div><dt>Brier 점수</dt><dd>{stats.brier == null ? "—" : stats.brier.toFixed(2)}<span className="muted small"> (0~2, 낮을수록 좋음)</span></dd></div>
          </dl>
        )}
        {b && (
          <p className="muted small">
            비교 기준 — 늘 롱이라고 했다면 {rate(b.always_long?.direction_hit_rate)}, 전날 방향을 따라갔다면 {rate(b.follow_yesterday?.direction_hit_rate)}
            {b.climatology?.brier != null ? `, 평소 비율만 썼다면 Brier ${b.climatology.brier.toFixed(2)}` : ""}.
          </p>
        )}
      </section>

      {stats && (
        <section className="card">
          <h3>단계별</h3>
          <div className="table-wrap">
            <table className="stats-table">
              <thead><tr><th>단계</th><th className="num">판정한 번</th><th className="num">그중 맞음</th><th className="num">실제로 나온 번</th></tr></thead>
              <tbody>{stats.per_stage.map((r) => (
                <tr key={r.stage}><td><Stage s={r.stage} /></td><td className="num">{r.called}</td>
                  <td className="num">{r.called_hit}{r.called ? <span className="muted"> ({rate(r.called_hit / r.called)})</span> : null}</td>
                  <td className="num">{r.actual}</td></tr>
              ))}</tbody>
            </table>
          </div>
          <h3>확률 보정</h3>
          <p className="muted small">다섯 단계 확률을 10% 구간으로 모아, 그 구간에서 실제로 그 단계가 나온 비율이에요. 두 숫자가 가까울수록 확률을 믿을 만해요.</p>
          <div className="table-wrap">
            <table className="stats-table">
              <thead><tr><th>확률 구간</th><th className="num">개수</th><th className="num">평균 확률</th><th className="num">실제로 나옴</th></tr></thead>
              <tbody>{stats.calibration.filter((c) => c.n > 0).map((c) => (
                <tr key={c.lo}><td>{c.lo}–{c.hi}%</td><td className="num">{c.n}</td><td className="num">{percent(c.mean_prob)}</td><td className="num">{percent(c.actual_rate)}</td></tr>
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
                <thead><tr><th>날짜</th><th>판정</th><th className="num col-prob">확률</th><th>실제</th><th className="num">등락</th><th>방향</th></tr></thead>
                <tbody>{hist.items.map((it) => (
                  <tr key={`${it.date}-${it.round}`}>
                    <td>{it.date.slice(5).replace("-", "/")} <span className="muted">{it.round_label}{it.source === "backfill" ? " · 백필" : ""}</span></td>
                    <td><Stage s={it.stage} /></td>
                    <td className="num col-prob">{percent(it.probs[it.stage], 0)}</td>
                    <td><Stage s={it.outcome?.stage} /></td>
                    <td className={`num ${!it.outcome ? "" : it.outcome.return_pct > 0 ? "up" : it.outcome.return_pct < 0 ? "down" : ""}`}>
                      {!it.outcome ? "—" : `${it.outcome.return_pct > 0 ? "+" : ""}${it.outcome.return_pct.toFixed(2)}%`}
                    </td>
                    <td><Mark ok={it.outcome?.hit_direction} />{it.outcome?.hit_stage ? <span className="muted small"> · 단계까지</span> : null}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
      </section>
    </div>
  );
}
