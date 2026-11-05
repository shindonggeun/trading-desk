import { useEffect, useState } from "react";
import { api } from "../api";
import { HORIZON, OPINION, term, RATING } from "../korean";
import type { PlanStats, StatsGroup } from "../types";

const WINDOWS = [30, 90] as const;
/** 이보다 적게 확정되면 적중률은 참고만 — 기준(70%)은 이만큼 쌓인 뒤 조정한다(#64). */
const MIN_RESOLVED = 30;

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

type View = "horizon" | "opinion" | "rating" | "direction";
const VIEWS: { key: View; label: string }[] = [
  { key: "horizon", label: "시간축" },
  { key: "opinion", label: "의견" },
  { key: "rating", label: "등급" },
  { key: "direction", label: "방향" },
];

function groupLabel(view: View, key: string): string {
  if (view === "horizon") return HORIZON[key]?.ko ?? key;
  if (view === "opinion") return OPINION[key]?.ko ?? (key === "unknown" ? "예전 분석" : key);
  if (view === "rating") return term(RATING, key)?.ko ?? key;
  return key === "long" ? "롱" : key === "short" ? "숏" : key;
}

function rows(stats: PlanStats, view: View): (StatsGroup & { key: string })[] {
  return { horizon: stats.by_horizon, opinion: stats.by_opinion, rating: stats.by_rating, direction: stats.by_direction }[view];
}

/** 끝난 계획이 실제로 어떻게 됐는지(#64 서버 채점) — 추정 승률과 실제 적중률을 나란히. */
export default function PlanStatsCard() {
  const [days, setDays] = useState<number>(90);
  const [view, setView] = useState<View>("horizon");
  const [stats, setStats] = useState<PlanStats | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setFailed(false);
    api.planStats(days).then(
      (s) => alive && setStats(s),
      () => {
        if (!alive) return;
        setFailed(true);
        setStats(null); // 다른 기간의 표가 오류와 같이 남지 않게
      },
    );
    return () => {
      alive = false;
    };
  }, [days]);

  const resolved = stats
    ? stats.by_direction.reduce((n, g) => n + g.target + g.stop + g.both, 0)
    : 0;

  return (
    <section className="card plan-stats">
      <h3>
        계획 적중률 <span className="muted small">서버가 1시간마다 채점</span>
      </h3>
      <div className="chips" role="group" aria-label="기간">
        {WINDOWS.map((d) => (
          <button key={d} type="button" className={`chip ${days === d ? "chip-on" : ""}`} aria-pressed={days === d} onClick={() => setDays(d)}>
            {d}일
          </button>
        ))}
        <span className="chips-gap" />
        {VIEWS.map((v) => (
          <button key={v.key} type="button" className={`chip ${view === v.key ? "chip-on" : ""}`} aria-pressed={view === v.key} onClick={() => setView(v.key)}>
            {v.label}
          </button>
        ))}
      </div>
      {failed && <p className="error small">통계를 불러오지 못했습니다.</p>}
      {stats && stats.total.plans === 0 && <p className="muted small">아직 롱 · 숏 계획이 없습니다.</p>}
      {stats && stats.total.plans > 0 && (
        <>
          <p className="small">
            {stats.days !== days && `최근 ${stats.days}일(보관 기간) · `}계획 {stats.total.plans}건 · 채점 끝 {stats.total.scored} · 진행 중 {stats.total.waiting}
            {stats.total.unscorable > 0 && ` · 채점 불가 ${stats.total.unscorable}`}
            {stats.total.errors > 0 && ` · 오류 ${stats.total.errors}`}
          </p>
          {resolved < MIN_RESOLVED && (
            <p className="muted small">
              목표 · 손절로 끝난 계획이 {resolved}건뿐입니다 — {MIN_RESOLVED}건이 쌓이기 전 적중률은 참고만 하세요.
            </p>
          )}
          <div className="table-wrap">
            <table className="stats-table">
              <thead>
                <tr>
                  <th>{VIEWS.find((v) => v.key === view)?.label}</th>
                  <th className="num">계획</th>
                  <th className="num">목표</th>
                  <th className="num" title="손절 + 같은 봉에 목표 · 손절 둘 다">손절</th>
                  <th className="num">만료</th>
                  <th className="num" title="목표 ÷ (목표 + 손절 + 같은 봉 둘 다)">적중률</th>
                  <th className="num" title="목표 · 손절로 끝난 계획의 평균 추정 승률">추정</th>
                </tr>
              </thead>
              <tbody>
                {rows(stats, view).map((g) => (
                  <tr key={g.key}>
                    <td>{groupLabel(view, g.key)}</td>
                    <td className="num" title={`체결 ${g.filled} · 미체결 ${g.unfilled} · 대기 ${g.waiting + g.open}`}>{g.plans}</td>
                    <td className="num">{g.target}</td>
                    <td className="num">{g.stop + g.both}</td>
                    <td className="num">{g.expired}</td>
                    <td className="num"><strong>{pct(g.hit_rate)}</strong></td>
                    <td className="num muted">{pct(g.avg_probability_resolved)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {stats.calibration.some((c) => c.resolved > 0) && (
            <>
              <h4 className="small">추정 승률 대비 실제</h4>
              <div className="calibration">
                {stats.calibration.filter((c) => c.resolved > 0).map((c) => (
                  <div key={c.bucket} className="calibration-row small">
                    <span className="muted">추정 {c.bucket}%</span>
                    <span className="calibration-bar" aria-hidden>
                      <span style={{ width: `${(c.hit_rate ?? 0) * 100}%` }} />
                    </span>
                    <span>
                      실제 {pct(c.hit_rate)} <span className="muted">({c.hits}/{c.resolved})</span>
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
