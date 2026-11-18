import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { fakeDayCallHistory, fakeDayCallStats } from "../briefsFake";
import { DAY_CALL_KINDS, KIND_KO, LIVE_RECORD_MIN, MARKET_INDEX, MARKET_KO, percent, rate, scoredOutcome, STAGE_KO, STAGE_MARK, verdictText } from "../dayCall";
import type { DayCallCallStats, DayCallHistory, DayCallHistoryItem, DayCallKind, DayCallMarket, DayCallStage, DayCallStats } from "../types";

const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;
const DAYS = 90;
const ANSWER: Record<DayCallKind, string> = { prev_close: "전일 종가 → 종가", open: "시가 → 종가" };

function Stage({ s }: { s: DayCallStage | null | undefined }) {
  if (!s) return <span className="muted">채점 전</span>;
  return <span className={`daycall-tag daycall-${s}`}><span aria-hidden="true">{STAGE_MARK[s]} </span>{STAGE_KO[s]}</span>;
}

function Verdict({ it }: { it: DayCallHistoryItem }) {
  const v = verdictText(it);
  return <span className={v.cls}><span aria-hidden="true">{v.mark} </span>{v.text}</span>;
}

function Mark({ it }: { it: DayCallHistoryItem }) {
  if (it.outcome?.unscorable) return <span className="muted">채점 불가</span>;
  const out = scoredOutcome(it.outcome);
  const ok = out?.hit_direction;
  if (!out) return <span className="muted">—</span>;
  if (ok == null) return <span className="muted">{it.verdict === "hold" ? "보류" : "보합(채점 안 함)"}</span>;
  return ok ? <span className="daycall-hit">✓ 맞음</span> : <span className="daycall-miss">✗ 틀림</span>;
}

/** #573 오늘 방향(#572) 적중 기록 — 두 판정(전일 종가 대비 · 시가 대비)마다 적중률 · 기준선 · 표본 수 · 단계별 · 보정 표 · 날짜별 */
/** #606 이 숫자의 기준 — 여기는 지난 days 일의 1 · 2차 · 두 출처를 합친 것, 오늘 방향 카드는 그 회차 하나(실제 판정이 30번 쌓이기 전에는 검증 구간)다.
 *  카드와 같은 규칙으로 회차별 숫자를 같이 적어 두 화면의 숫자가 왜 다른지 바로 맞춰 볼 수 있게 한다. */
function Basis({ st, days, holdoutFrom }: { st: DayCallCallStats; days: number; holdoutFrom: string | null }) {
  const live = st.by_source?.live?.n ?? 0;
  const backfill = st.by_source?.backfill?.n ?? 0;
  const rounds = Object.keys(st.by_round ?? {}).sort().flatMap((k) => {
    const rr = st.by_round[k];
    const useLive = (rr.live?.n ?? 0) >= LIVE_RECORD_MIN;
    const part = useLive ? rr.live : rr.backfill;
    return part?.n ? [{ k, n: part.n, rate: part.direction_hit_rate ?? null, basis: useLive ? "실제 판정" : "검증 구간" }] : [];
  });
  return (
    <div className="daycall-basis-note small muted">
      <p>
        위 숫자는 지난 {days}일의 1 · 2차를 합친 {st.n}번이에요(검증 구간 {backfill}번 · 실제 판정 {live}번).
        {" "}검증 구간은 모델 학습에 쓰지 않은 날{holdoutFrom ? `(${holdoutFrom} 이후)` : ""}을 그날 그 시각 기준으로 다시 판정해 채점한 것이고, 실제 판정은 그날 실제로 낸 판정이에요.
      </p>
      <p>
        오늘 방향 카드의 적중은 그 회차 하나만 세고, 그 회차의 실제 판정이 {LIVE_RECORD_MIN}번 쌓이기 전에는 검증 구간 숫자예요
        {rounds.length > 0 && <> — 지금 카드 기준: {rounds.map((r, i) => (
          <span key={r.k}>{i > 0 ? " · " : ""}{r.k}차 {r.n}번 적중 {rate(r.rate)}(<span className="daycall-basis">{r.basis}</span>)</span>
        ))}</>}.
      </p>
    </div>
  );
}

export default function DayCallRecord() {
  const [market, setMarket] = useState<DayCallMarket>("kr");
  const [kind, setKind] = useState<DayCallKind>("prev_close");
  const [hist, setHist] = useState<DayCallHistory | null>(null);
  const [stats, setStats] = useState<DayCallStats | null>(null);
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);   // API(#572)가 아직 없음(운영 404)

  useEffect(() => {
    let alive = true;
    setHist(null);
    setStats(null);
    setFailed(false);
    setPending(false);
    api.dayCallHistory(market, DAYS).then((h) => alive && setHist(h)).catch((e: unknown) => {
      if (!alive) return;
      if (is404(e) && import.meta.env.DEV) setHist(fakeDayCallHistory(market, DAYS));
      else if (is404(e)) setPending(true);
      else setFailed(true);
    });
    api.dayCallStats(market, DAYS).then((s) => alive && setStats(s)).catch((e: unknown) => {
      if (alive && is404(e) && import.meta.env.DEV) setStats(fakeDayCallStats(market, DAYS));
    });
    return () => { alive = false; };
  }, [market]);

  const st = stats?.calls?.[kind];
  const b = st?.baselines;
  const items = (hist?.items ?? []).filter((it) => (it.call ?? "prev_close") === kind);
  return (
    <div className="paper-page daycall-page">
      <section className="card">
        <div className="daycall-page-head">
          <h2>오늘 방향 적중 기록</h2>
          <div className="daycall-page-tabs">
            <div className="seg seg-small" role="group" aria-label="시장">
              {(["kr", "us"] as const).map((m) => (
                <button key={m} type="button" aria-pressed={market === m} className={market === m ? "on" : ""} onClick={() => setMarket(m)}>
                  {MARKET_KO[m]}
                </button>
              ))}
            </div>
            <div className="seg seg-small" role="group" aria-label="판정">
              {DAY_CALL_KINDS.map((k) => (
                <button key={k} type="button" aria-pressed={kind === k} className={kind === k ? "on" : ""} onClick={() => setKind(k)}>
                  {KIND_KO[k]}
                </button>
              ))}
            </div>
          </div>
        </div>
        <p className="muted small">
          확률은 추정이에요. 방향 적중은 롱 · 숏 방향이 맞은 판정, 단계 적중은 다섯 단계까지 맞은 판정이에요.
          정답은 {MARKET_INDEX[market]} {ANSWER[kind]} 움직임을 그날 변동폭(ATR)으로 나눠 매겨요.
          {kind === "open" ? " 시가 대비는 롱 · 숏 확률 차가 10%p 안이면 판단을 보류해요. 방향 적중은 보류한 날도 확률이 기운 쪽으로 채점하고(검증과 같은 정의), 보류가 아닌 날만 센 적중은 따로 적어요." : ""}
          {" "}지수 자료가 오지 않아 채점하지 못한 날은 통계에서 빠져요.
        </p>
        {st && (
          <dl className="daycall-stats">
            <div><dt>방향 적중</dt><dd>{rate(st.direction_hit_rate)}</dd></div>
            <div><dt>늘 롱 · 늘 숏이었다면</dt><dd>{rate(b?.always_long?.direction_hit_rate)} · {rate(b?.always_short?.direction_hit_rate)}</dd></div>
            <div><dt>채점한 판정</dt><dd>{st.n}번{st.missed ? <span className="muted small"> · 놓친 회차 {st.missed}</span> : null}</dd></div>
            {kind === "open" && (
              <div><dt>판단 보류</dt><dd>{rate(st.hold_rate)}<span className="muted small">{st.called ? ` · 보류 아닌 ${st.called.n}번 적중 ${rate(st.called.direction_hit_rate)}` : ""}</span></dd></div>
            )}
            <div><dt>단계 적중</dt><dd>{rate(st.stage_hit_rate)}</dd></div>
            <div><dt>Brier 점수</dt><dd>{st.brier == null ? "—" : st.brier.toFixed(2)}<span className="muted small"> (0~2, 낮을수록 좋음)</span></dd></div>
          </dl>
        )}
        {st && <Basis st={st} days={stats?.days ?? 90} holdoutFrom={stats?.backtest?.holdout_from ?? null} />}
        {b && (b.follow_yesterday || b.climatology) && (
          <p className="muted small">
            다른 비교 기준 — 전날 방향을 따라갔다면 {rate(b.follow_yesterday?.direction_hit_rate)}
            {b.climatology?.brier != null ? `, 평소 비율만 썼다면 Brier ${b.climatology.brier.toFixed(2)}` : ""}.
          </p>
        )}
        {(stats?.note?.length ?? 0) > 0 && (
          <ul className="daycall-notes small muted">{stats!.note!.map((n, i) => <li key={i}>{n}</li>)}</ul>
        )}
      </section>

      {st && (
        <section className="card">
          <h3>단계별 · {KIND_KO[kind]}</h3>
          <div className="table-wrap">
            <table className="stats-table">
              <thead><tr><th>단계</th><th className="num">판정한 번</th><th className="num">그중 맞음</th><th className="num">실제로 나온 번</th></tr></thead>
              <tbody>{st.per_stage.map((r) => (
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
              <tbody>{st.calibration.filter((c) => c.n > 0).map((c) => (
                <tr key={c.lo}><td>{c.lo}–{c.hi}%</td><td className="num">{c.n}</td><td className="num">{percent(c.mean_prob)}</td><td className="num">{percent(c.actual_rate)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      )}

      <section className="card">
        <h3>날짜별 기록 · {KIND_KO[kind]}</h3>
        {pending ? <p className="muted small">오늘 방향 판정은 준비 중이에요. 판정이 시작되면 기록이 여기에 쌓여요.</p>
          : failed ? <p className="muted small">적중 기록을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>
          : !hist ? <p className="muted small">불러오는 중…</p>
          : items.length === 0 ? <p className="muted small">아직 기록이 없어요.</p> : (
            <div className="table-wrap">
              <table className="stats-table daycall-table">
                <thead><tr><th>날짜</th><th>판정</th><th className="col-prob">가장 큰 단계</th><th>실제</th><th className="num">등락</th><th>방향</th></tr></thead>
                <tbody>{items.map((it) => (
                  <tr key={`${it.date}-${it.round}-${it.call}`}>
                    <td>{it.date.slice(5).replace("-", "/")} <span className="muted">{it.round_label}{it.source === "backfill" ? " · 백필" : ""}{it.fallback ? " · 선물 없이" : ""}</span></td>
                    <td><Verdict it={it} /></td>
                    <td className="col-prob"><Stage s={it.stage} /> <span className="muted">{percent(it.probs[it.stage], 0)}</span></td>
                    <td>{it.outcome?.unscorable ? <span className="muted">채점 불가</span> : <Stage s={scoredOutcome(it.outcome)?.stage} />}</td>
                    <td className={`num ${(() => { const o = scoredOutcome(it.outcome); return !o ? "" : o.return_pct > 0 ? "up" : o.return_pct < 0 ? "down" : ""; })()}`}>
                      {(() => { const o = scoredOutcome(it.outcome); return !o ? "—" : `${o.return_pct > 0 ? "+" : ""}${o.return_pct.toFixed(2)}%`; })()}
                    </td>
                    <td><Mark it={it} />{scoredOutcome(it.outcome)?.hit_stage ? <span className="muted small"> · 단계까지</span> : null}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
      </section>
    </div>
  );
}
