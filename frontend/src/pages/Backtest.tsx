import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { REGIME, SETUP } from "../components/PlaybookCard";
import type { BacktestAgg, BacktestReport, BacktestSummary, RegimeId } from "../types";

/**
 * #150 · #185 바스켓 백테스트 리포트 — 저장소에 커밋한 스냅샷(서버는 백테스트를 돌리지 않는다).
 * 셋업 · 국면 · 종목 · 폭락 구간별 체결률 · T1 먼저 도달률 · 비용 차감 기대 R, 학습 / 검증 구간, 투매 규칙(#146) 켬 / 끔.
 */

type View = "setup" | "regime" | "ticker" | "crash" | "direction" | "risk" | "steps";
const VIEWS: { key: View; label: string; title: string }[] = [
  { key: "setup", label: "셋업", title: "방향 · 셋업" },
  { key: "regime", label: "국면", title: "셋업 · 분석 당시 국면" },
  { key: "ticker", label: "종목", title: "종목" },
  { key: "crash", label: "폭락 구간", title: "폭락 구간(2020-03 · 2022 · 2025-04)" },
  { key: "direction", label: "방향 판정", title: "셋업 · 방향 판정과 같은 쪽인가" },
  { key: "risk", label: "투매 점수", title: "셋업 · 시장 투매 국면" },
  { key: "steps", label: "사다리 차수", title: "셋업 · 체결된 사다리 차수(체결 순서)" },
];

const groupsOf = (r: BacktestReport, v: View): Record<string, BacktestAgg> | undefined =>
  ({ setup: r.by_setup, regime: r.by_regime, ticker: r.by_ticker, crash: r.crash_windows, direction: r.by_direction,
    risk: r.by_risk, steps: r.by_steps })[v];

const SIDE: Record<string, string> = { long: "롱", short: "숏" };
const DIRECTION: Record<string, string> = { with: "순방향", against: "역방향", neutral: "중립" };
const RISK: Record<string, string> = { selling: "투매 진행", exhausting: "투매 끝물", calm: "평상", unknown: "판단 불가" };
const STEPS = /^\d(\+\d)*$/;

/** "long:dip_catch:crash" → "롱 · 하따(급락 받기) · 급락", "crash:2022:long:dip_catch" → "폭락 2022 · 롱 · 하따" */
function label(key: string): string {
  return key.split(":").map((part, i, all) => {
    if (part === "crash" && i === 0 && all.length > 1) return "폭락";
    if (SIDE[part]) return SIDE[part];
    if (SETUP[part]) return SETUP[part];
    if (REGIME[part as RegimeId] && i > 0) return REGIME[part as RegimeId];
    if (DIRECTION[part]) return DIRECTION[part];
    if (RISK[part]) return RISK[part];
    if (STEPS.test(part) && i > 0) return `${part.split("+").join("→")}차`;
    if (part === "all") return "전체";
    if (part === "valid") return "검증 구간";
    return part;
  }).join(" · ");
}

const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const signedR = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(3)}R`);
const tone = (v: number | null | undefined) => (v == null ? "muted" : v > 0 ? "up" : v < 0 ? "down" : "");
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "—");

function Tile({ name, value, cls, hint }: { name: string; value: string; cls?: string; hint?: string }) {
  return (
    <div className="bt-tile" title={hint}>
      <span className="muted small">{name}</span>
      <strong className={`num ${cls ?? ""}`}>{value}</strong>
    </div>
  );
}

function GroupTable({ groups, title }: { groups: Record<string, BacktestAgg>; title: string }) {
  const rows = Object.entries(groups).sort((a, b) => b[1].plans - a[1].plans);
  return (
    <div className="table-wrap">
      <table className="stats-table bt-table">
        <thead>
          <tr>
            <th scope="col">{title}</th>
            <th scope="col" className="num">계획</th>
            <th scope="col" className="num" title="한 번이라도 체결된 비율">체결률</th>
            <th scope="col" className="num" title="체결된 것 중 손절보다 T1 에 먼저 닿은 비율">T1 먼저</th>
            <th scope="col" className="num" title="체결된 건당, 수수료 · 슬리피지 차감">기대 R</th>
            <th scope="col" className="num" title="미체결까지 넣은 계획당 기대 R">계획당 R</th>
            <th scope="col" className="num" title="진입 뒤 가장 불리했던 폭(ATR) 중앙값">MAE</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([key, g]) => (
            <tr key={key}>
              <th scope="row">{label(key)}</th>
              <td className="num">{g.plans.toLocaleString("ko-KR")}</td>
              <td className="num">{pct(g.fill_rate)}</td>
              <td className="num">{pct(g.t1_first_rate)}</td>
              <td className={`num ${tone(g.expectancy_r)}`}><strong>{signedR(g.expectancy_r)}</strong></td>
              <td className={`num ${tone(g.expectancy_per_plan_r)}`}>{signedR(g.expectancy_per_plan_r)}</td>
              <td className="num muted">{g.mae_atr_median ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const W = 600;
const H = 180;

/**
 * #192 누적 R 곡선 — 계획일 기준(같은 날 세운 계획이 겹쳐 실제 계좌 곡선이 아니다). 0 선과 학습 / 검증 경계(split.cut).
 * SVG 는 가로로 늘어나므로 선은 non-scaling-stroke, 글자는 SVG 밖 HTML 로 둔다.
 */
function EquityCurve({ equity, cut }: { equity: NonNullable<BacktestReport["equity"]>; cut: string | null }) {
  const keys = Object.keys(equity.series);
  const [key, setKey] = useState("all");
  const k = equity.series[key] ? key : "all";
  const ys = equity.series[k] ?? [];
  const n = equity.days.length;
  const geo = useMemo(() => {
    const lo = Math.min(0, ...ys);
    const hi = Math.max(0, ...ys);
    const span = hi - lo || 1;
    const x = (i: number) => (n > 1 ? (i / (n - 1)) * W : W / 2);
    const y = (v: number) => H - ((v - lo) / span) * H;
    const path = ys.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const cutAt = cut ? equity.days.findIndex((d) => d >= cut) : -1;
    return { lo, hi, path, zero: y(0), cutX: cutAt > 0 ? x(cutAt) : null };
  }, [ys, n, cut, equity.days]);
  if (!n) return null;
  const last = ys[ys.length - 1];
  const r = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}R`;
  return (
    <section className="card bt-equity">
      <h3>누적 R <span className="muted small">계획일 기준 · 비용 차감 — 계획이 겹쳐 실제 계좌 곡선은 아닙니다</span></h3>
      <div className="chips" role="group" aria-label="곡선 묶음">
        {keys.map((s) => (
          <button key={s} type="button" className={`chip ${k === s ? "chip-on" : ""}`} aria-pressed={k === s} onClick={() => setKey(s)}>
            {s === "all" ? "전체" : label(s)}
          </button>
        ))}
      </div>
      <div className="bt-curve-box">
        <span className="bt-curve-y bt-curve-hi num muted small">{r(geo.hi)}</span>
        <span className="bt-curve-y bt-curve-lo num muted small">{r(geo.lo)}</span>
        <svg className="bt-curve" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
          aria-label={`${k === "all" ? "전체" : label(k)} 누적 ${r(last)}, 최대 낙폭 ${r(equity.max_dd[k] ?? 0)}, ${equity.trades[k] ?? 0}건`}>
          <line className="bt-curve-zero" x1={0} x2={W} y1={geo.zero} y2={geo.zero} vectorEffect="non-scaling-stroke" />
          {geo.cutX != null && (
            <line className="bt-curve-cut" x1={geo.cutX} x2={geo.cutX} y1={0} y2={H} vectorEffect="non-scaling-stroke" />
          )}
          <path className={`bt-curve-line ${last >= 0 ? "bt-curve-up" : "bt-curve-down"}`} d={geo.path} vectorEffect="non-scaling-stroke" />
        </svg>
      </div>
      <div className="bt-curve-x muted small">
        <span>{equity.days[0]}</span>
        <span>{equity.days[n - 1]}</span>
      </div>
      {geo.cutX != null && <p className="muted small bt-curve-cut-note">점선 = 검증 구간 시작({cut}) — 그 뒤는 규칙 · 값을 고를 때 안 본 구간</p>}
      <p className="small bt-curve-sum">
        끝 <strong className={`num ${last > 0 ? "up" : last < 0 ? "down" : ""}`}>{r(last)}</strong> · 최대 낙폭{" "}
        <strong className="num down">{r(equity.max_dd[k] ?? 0)}</strong> · {(equity.trades[k] ?? 0).toLocaleString("ko-KR")}건
      </p>
    </section>
  );
}

/** #146 투매 규칙을 켠 계획과 끈 계획 — 같은 묶음끼리 */
function RiskGate({ gate }: { gate: NonNullable<BacktestReport["risk_gate"]> }) {
  const keys = Object.keys(gate.on).filter((k) => typeof gate.on[k] === "object" && typeof gate.off[k] === "object");
  const streak = (side: "on" | "off") => (typeof gate[side].losing_streak === "number" ? gate[side].losing_streak : null);
  return (
    <section className="card bt-gate">
      <h3>투매 규칙 켬 / 끔 <span className="muted small">같은 계획에 규칙만 달리 적용</span></h3>
      <div className="table-wrap">
        <table className="stats-table bt-table">
          <thead>
            <tr>
              <th scope="col">묶음</th>
              <th scope="col" className="num">기대 R 켬</th>
              <th scope="col" className="num">기대 R 끔</th>
              <th scope="col" className="num">차이</th>
              <th scope="col" className="num" title="체결돼 끝난 계획 수">끝난 건 켬 / 끔</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const on = gate.on[k] as BacktestAgg;
              const off = gate.off[k] as BacktestAgg;
              const diff = on.expectancy_r != null && off.expectancy_r != null ? on.expectancy_r - off.expectancy_r : null;
              return (
                <tr key={k}>
                  <th scope="row">{label(k)}</th>
                  <td className={`num ${tone(on.expectancy_r)}`}>{signedR(on.expectancy_r)}</td>
                  <td className={`num ${tone(off.expectancy_r)}`}>{signedR(off.expectancy_r)}</td>
                  <td className={`num ${tone(diff)}`}><strong>{diff == null ? "—" : signedR(Math.round(diff * 1000) / 1000)}</strong></td>
                  <td className="num muted">{on.settled.toLocaleString("ko-KR")} / {off.settled.toLocaleString("ko-KR")}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {streak("on") != null && <p className="muted small">최대 연속 손실: 켬 {streak("on")} · 끔 {streak("off")}</p>}
    </section>
  );
}

export default function Backtest() {
  const [list, setList] = useState<BacktestSummary[] | null>(null);
  const [horizon, setHorizon] = useState<string | null>(null);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [view, setView] = useState<View>("setup");
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    api.backtestReports().then(
      (rows) => {
        setList(rows);
        setHorizon((h) => h ?? rows.find((r) => r.horizon === "swing")?.horizon ?? rows[0]?.horizon ?? null);
      },
      () => setFailed("백테스트 목록을 불러오지 못했습니다."),
    );
  }, []);

  useEffect(() => {
    if (!horizon) return;
    let alive = true;
    setReport(null);
    api.backtestReport(horizon).then(
      (r) => alive && setReport(r),
      () => alive && setFailed("이 보기의 리포트를 불러오지 못했습니다."),
    );
    return () => { alive = false; };
  }, [horizon]);

  const groups = report ? groupsOf(report, view) : undefined;
  const base = report?.baseline;
  return (
    <main className="page backtest">
      <section className="card">
        <h2>백테스트 <span className="muted small">바스켓 리포트 — 규칙을 바꿀 때 로컬에서 다시 만든 스냅샷</span></h2>
        {failed && <p className="error small">{failed}</p>}
        {list && list.length === 0 && <p className="muted small">아직 커밋된 리포트가 없습니다.</p>}
        {list && list.length > 0 && (
          <div className="chips" role="group" aria-label="보기">
            {list.map((s) => (
              <button key={s.horizon} type="button" className={`chip ${horizon === s.horizon ? "chip-on" : ""}`}
                aria-pressed={horizon === s.horizon} onClick={() => { setFailed(null); setHorizon(s.horizon); }}>
                {s.label}<span className="muted">{` ${signedR(s.expectancy_r)}`}</span>
              </button>
            ))}
          </div>
        )}
        {report && base && (
          <>
            <p className="muted small bt-meta">
              {report.start ? `${day(report.start)} ~ ${report.end ? day(report.end) : "최근"}` : "받을 수 있는 전체 기간"} · 만든 날 {day(report.generated_at)}{report.commit ? ` (커밋 ${report.commit.slice(0, 7)})` : ""} · 비용 편도 {report.costs.bps_per_side}bp ·{" "}
              {report.tickers.join(" · ")}
            </p>
            {report.stale !== false && (
              <p className="bt-stale small" role="status">
                {report.stale
                  ? "이 스냅샷을 만든 뒤 계획 규칙 코드가 바뀌었습니다 — 숫자가 지금 규칙의 성적과 다를 수 있습니다."
                  : "이 스냅샷은 만든 커밋 · 규칙 지문이 없어 지금 규칙으로 만든 것인지 알 수 없습니다."}
              </p>
            )}
            <div className="bt-tiles">
              <Tile name="계획" value={base.plans.toLocaleString("ko-KR")} />
              <Tile name="체결률" value={pct(base.fill_rate)} />
              <Tile name="T1 먼저" value={pct(base.t1_first_rate)} hint="체결된 것 중 손절보다 T1 에 먼저 닿은 비율" />
              <Tile name="기대 R(전체)" value={signedR(base.expectancy_r)} cls={tone(base.expectancy_r)} hint="체결된 건당, 비용 차감" />
              <Tile name="기대 R(학습)" value={signedR(report.split.train.expectancy_r)} cls={tone(report.split.train.expectancy_r)}
                hint={`${day(report.split.cut)} 이전 — 규칙 · 값을 고른 구간`} />
              <Tile name="기대 R(검증)" value={signedR(report.split.valid.expectancy_r)} cls={tone(report.split.valid.expectancy_r)}
                hint={`${day(report.split.cut)} 이후 ${report.split.embargo_days}일 띄움 — 고를 때 안 본 구간`} />
            </div>
          </>
        )}
      </section>

      {report && (
        <section className="card">
          <div className="chips" role="group" aria-label="나눠 보기">
            {VIEWS.filter((v) => groupsOf(report, v.key) && Object.keys(groupsOf(report, v.key)!).length).map((v) => (
              <button key={v.key} type="button" className={`chip ${view === v.key ? "chip-on" : ""}`} aria-pressed={view === v.key} onClick={() => setView(v.key)}>
                {v.label}
              </button>
            ))}
          </div>
          {groups && Object.keys(groups).length > 0
            ? <GroupTable groups={groups} title={VIEWS.find((v) => v.key === view)!.title} />
            : <p className="muted small">이 리포트에는 이 구분이 없습니다.</p>}
          <p className="muted small bt-note">
            계획은 봉마다 하나씩 겹쳐 세운 것이라 건수가 실제 독립 표본보다 많습니다. 기대 R 은 수수료 · 슬리피지를 뺀 값입니다.
          </p>
        </section>
      )}

      {report?.equity?.days?.length ? <EquityCurve key={report.horizon} equity={report.equity} cut={report.split.cut} /> : null}

      {report?.risk_gate && Object.keys(report.risk_gate.on).length > 0 && <RiskGate gate={report.risk_gate} />}

      {report && report.notes.length > 0 && (
        <section className="card">
          <h3>메모</h3>
          <ul className="small bt-notes">{report.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>
      )}
    </main>
  );
}
