import { useState } from "react";
import HalvingChart, { type HalvingMode } from "../components/HalvingChart";
import { cycleName, ddRange, pct, range, usd, useHalving, WHERE_DAYS, WHERE_DD } from "../halving";
import type { Halving as HalvingData } from "../types";

// #515 비트코인 반감기 사이클(#/halving): 지난 사이클과 겹쳐 그린 그래프 · 표 · 쉬운 요약.
// 바닥은 지나고 나서야 안다 — 지금까지의 최저를 바닥이라 부르지 않고, 비교 표본이 3개뿐임을 늘 같이 적는다.

const MODE_KEY = "desk.halving.mode";
function savedMode(): HalvingMode {
  try { return localStorage.getItem(MODE_KEY) === "peak" ? "peak" : "halving"; } catch { return "halving"; }
}

/** 비교한 지난 사이클 수(바닥 일수 기준 — 하락률은 고점이 있는 사이클만이라 따로 센다) */
export const sampleCount = (d: HalvingData) => d.ranges.bottom_days_from_halving?.cycles.length ?? 0;

/** 쉬운 말 세 줄 — 표본 수는 그 범위를 만든 사이클 수 그대로 적는다 */
export function halvingLines(d: HalvingData): string[] {
  const c = d.current;
  if (!c) return ["지금 사이클의 가격 자료가 모자라 위치를 계산하지 못했어요. 아래 지난 사이클 표만 참고하세요."];
  const r = d.ranges;
  const n = d.halvings.find((h) => h.date === c.halving)?.n;
  const bd = r.bottom_days_from_halving;
  const where = r.now.days_since_halving ? `, 지금은 ${WHERE_DAYS[r.now.days_since_halving]}` : "";
  const out = [
    `지금은 ${n ? `${n}번째 ` : ""}반감기(${c.halving}) 뒤 ${c.days_since_halving}일째예요.${bd && bd.cycles.length
      ? ` 지난 ${bd.cycles.length}개 사이클의 바닥은 반감기 뒤 ${range(bd.min, bd.max, "일")}에 나왔고${where}.` : " 견줄 지난 사이클 바닥 자료가 없어요."}`,
  ];
  const dd = r.bottom_drawdown_pct;
  const down = Math.abs(c.from_high_pct).toFixed(1);
  out.push(`이번 사이클 고점(${c.high.date}, ${usd(c.high.price)})보다 ${down}% 아래예요(${pct(c.from_high_pct)}).${dd && dd.cycles.length
    ? ` 지난 ${dd.cycles.length}개 사이클의 바닥은 고점보다 ${ddRange(dd.min, dd.max)} 자리였어요${r.now.from_high_pct ? ` — 지금은 ${WHERE_DD[r.now.from_high_pct]}` : ""}.` : ""}`);
  const k = Math.max(sampleCount(d), 1);
  out.push(c.low_after_high && c.from_low_pct != null
    ? `고점 뒤 가장 낮았던 값(${c.low_after_high.date}, ${usd(c.low_after_high.price)})에서 ${pct(c.from_low_pct)} 움직였어요. 이 값이 바닥이라는 뜻은 아니에요 — 비교할 지난 사이클이 ${k}개뿐이라 범위는 참고용이에요.`
    : `아직 고점 뒤 낮은 값이 없어요. 바닥은 지나고 나서야 알 수 있고, 비교할 지난 사이클이 ${k}개뿐이라 범위는 참고용이에요.`);
  return out;
}

export default function Halving() {
  const { data, fake, error } = useHalving();
  const [mode, setModeState] = useState<HalvingMode>(savedMode);
  const setMode = (m: HalvingMode) => { setModeState(m); try { localStorage.setItem(MODE_KEY, m); } catch { /* 이번 화면에서만 */ } };
  if (error) return <main className="halving-page"><h1>비트코인 반감기 사이클</h1><p className="error">불러오지 못했습니다: {error}</p></main>;
  if (!data) return <main className="halving-page"><h1>비트코인 반감기 사이클</h1><p className="muted">불러오는 중…</p></main>;
  const c = data.current;
  const next = data.halvings.find((h) => h.estimated);
  const cycles = [...data.cycles].sort((a, b) => b.halving.localeCompare(a.halving));
  return (
    <main className="halving-page">
      <header className="hv-head">
        <h1>비트코인 반감기 사이클</h1>
        <span className="muted small">{data.as_of} 종가 기준 · {data.source}</span>
      </header>
      {fake && <p className="app-notice notice-warn">예시 데이터 — 개발 서버에서 #514 API 없이 화면을 보기 위한 숫자이며 실제 값이 아닙니다.</p>}

      <section className="card hv-summary" aria-label="쉬운 요약">
        <ol>{halvingLines(data).map((l, i) => <li key={i}>{l}</li>)}</ol>
        <p className="muted small">예측이 아니라 지난 사이클과 견준 위치예요. 바닥 확정이 아니고, {sampleCount(data) ? `표본이 ${sampleCount(data)}개뿐이에요` : "비교할 지난 사이클 자료가 없어요"}{data.ranges.bottom_drawdown_pct && data.ranges.bottom_drawdown_pct.cycles.length !== sampleCount(data) ? `(고점 대비 하락률은 고점 자료가 있는 ${data.ranges.bottom_drawdown_pct.cycles.length}개)` : ""}.</p>
      </section>

      <section className="card hv-graph" aria-labelledby="hv-graph-title">
        <div className="hv-graph-head">
          <h2 id="hv-graph-title">사이클 겹쳐 보기</h2>
          <div className="seg" role="group" aria-label="0일 기준">
            <button type="button" aria-pressed={mode === "halving"} className={mode === "halving" ? "on" : ""} onClick={() => setMode("halving")}>반감기 0일</button>
            <button type="button" aria-pressed={mode === "peak"} className={mode === "peak" ? "on" : ""} onClick={() => setMode("peak")}>고점 0일</button>
          </div>
        </div>
        <HalvingChart data={data} mode={mode} />
      </section>

      <section className="card hv-table-card" aria-labelledby="hv-table-title">
        <h2 id="hv-table-title">사이클별 고점 · 바닥</h2>
        <div className="hv-table-wrap" tabIndex={0} role="region" aria-labelledby="hv-table-title">
          <table className="hv-table">
            <thead>
              <tr><th scope="col">사이클</th><th scope="col">고점</th><th scope="col">바닥</th><th scope="col">반감기 뒤</th><th scope="col">고점 뒤</th><th scope="col">고점 대비</th></tr>
            </thead>
            <tbody>
              {c && <tr className="hv-row-now">
                <th scope="row">{c.halving.slice(0, 4)}~ <span className="muted small">지금까지</span></th>
                <td>{c.high.date}<br /><b>{usd(c.high.price)}</b></td>
                <td>{c.low_after_high ? <>{c.low_after_high.date}<br /><b>{usd(c.low_after_high.price)}</b> <span className="muted small">(바닥 확정 아님)</span></> : <span className="muted">—</span>}</td>
                <td>{c.low_after_high ? `${c.low_after_high.days_from_halving}일` : "—"} <span className="muted small">(오늘 {c.days_since_halving}일)</span></td>
                <td>{c.low_after_high ? `${c.low_after_high.days_from_halving - c.high.days_from_halving}일` : "—"} <span className="muted small">(오늘 {c.days_since_high}일)</span></td>
                <td>{c.low_after_high ? pct(c.low_after_high.drawdown_pct) : "—"} <span className="muted small">(오늘 {pct(c.from_high_pct)})</span></td>
              </tr>}
              {cycles.map((cy) => (
                <tr key={cy.halving}>
                  <th scope="row">{cycleName(cy.halving, cy.end)}{cy.partial && <span className="muted small"> 일부 자료</span>}</th>
                  <td>{cy.peak ? <>{cy.peak.date}<br /><b>{usd(cy.peak.price)}</b></> : <span className="muted">자료 부족</span>}</td>
                  <td>{cy.bottom ? <>{cy.bottom.date}<br /><b>{usd(cy.bottom.price)}</b></> : <span className="muted">—</span>}</td>
                  <td>{cy.bottom ? `${cy.bottom.days_from_halving}일` : "—"}</td>
                  <td>{cy.bottom?.days_from_peak != null ? `${cy.bottom.days_from_peak}일` : "—"}</td>
                  <td>{cy.bottom?.drawdown_pct != null ? pct(cy.bottom.drawdown_pct) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {cycles.some((cy) => cy.note) && <p className="muted small">{cycles.filter((cy) => cy.note).map((cy) => `${cycleName(cy.halving, cy.end)}: ${cy.note}`).join(" ")}</p>}
      </section>

      <section className="card hv-notes" aria-label="알아 둘 점">
        <ul>{data.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        <p className="muted small">
          반감기: {data.halvings.filter((h) => !h.estimated).map((h) => `${h.n}번째 ${h.date}`).join(" · ")}
          {next ? ` · 다음 ${next.date} 쯤(추정, 블록 ${next.block.toLocaleString("en-US")})` : ""}
        </p>
      </section>
    </main>
  );
}
