import { useEffect, useState } from "react";
import { api } from "../api";
import type { EarningsEstimate, EarningsPreview, KrConsensusRow } from "../types";

/**
 * #294 실적 프리뷰 카드(#293 GET /api/earnings/preview) — 다음 발표 · EPS/매출 컨센서스 · 서프라이즈 기록 ·
 * 옵션 내재 변동폭 vs 과거 발표 뒤 움직임 · 목표주가 · 투자의견 · 공매도. 무료 출처만 쓰고, 못 구하는 항목은 이유와 함께 적는다.
 * 투자 조언이 아니라 읽기 도우미다.
 */

const WHEN: Record<string, string> = { pre: "장 전", after: "장 후", during: "장중" };

const num = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : v.toLocaleString("ko-KR", { maximumFractionDigits: digits });
const pct = (v: number | null | undefined, sign = true) =>
  v == null ? "—" : `${sign && v > 0 ? "+" : ""}${v.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}%`;
const big = (v: number | null | undefined) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return `${(v / 1e12).toFixed(2)}조`;
  if (a >= 1e8) return `${(v / 1e8).toFixed(1)}억`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}백만`;
  return num(v);
};

/** 이번 분기(this_quarter) 추정을 먼저, 없으면 첫 항목. */
function current(rows: Record<string, EarningsEstimate> | null): EarningsEstimate | null {
  if (!rows) return null;
  return rows.this_quarter ?? Object.values(rows)[0] ?? null;
}

function Estimate({ label, e, fmt }: { label: string; e: EarningsEstimate | null; fmt: (v: number | null | undefined) => string }) {
  if (!e) return <div className="ep-stat"><span className="muted small">{label}</span><strong>—</strong></div>;
  return (
    <div className="ep-stat">
      <span className="muted small">{label}{e.analysts != null && ` · ${e.analysts}명`}</span>
      <strong className="num">{fmt(e.avg)}</strong>
      <span className="muted small num">범위 {fmt(e.low)} ~ {fmt(e.high)}{e.growth_pct != null && ` · 전년 대비 ${pct(e.growth_pct)}`}</span>
    </div>
  );
}

/** 네이버 분기 실적 — 지난 실적과 추정(컨센서스) 칸을 함께. */
function KrTable({ rows, unit }: { rows: KrConsensusRow[]; unit: string }) {
  return (
    <table className="ep-history">
      <caption className="muted small">네이버 분기 실적 · 컨센서스(단위 {unit}, 이익률 %) — "추정" 은 아직 발표 전</caption>
      <thead><tr><th>분기</th><th className="num">매출</th><th className="num">영업이익</th><th className="num">순이익</th><th className="num">영업이익률</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.period}>
            <td>{r.period}{r.consensus && <span className="pill">추정</span>}</td>
            <td className="num">{num(r.revenue, 0)}</td>
            <td className="num">{num(r.op_income, 0)}</td>
            <td className="num">{num(r.net_income, 0)}</td>
            <td className="num">{num(r.op_margin, 1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EarningsPreviewBody({ p }: { p: EarningsPreview }) {
  const im = p.implied_move;
  const pm = p.past_moves;
  const si = p.short_interest as { pct_float?: number | null; days_to_cover?: number | null; as_of?: string | null } | null;
  return (
    <div className="ep-body">
      <p className="ep-next">
        {p.next ? (
          <>
            다음 발표 <strong>{p.next.date}</strong>
            {p.next.when && ` · ${WHEN[p.next.when] ?? p.next.when}`}
            {p.next.time_kst && <span className="muted small"> (한국 {p.next.time_kst})</span>}
            <span className="pill">{p.next.days === 0 ? "오늘" : p.next.days > 0 ? `D-${p.next.days}` : `D+${-p.next.days}`}</span>
          </>
        ) : <span className="muted">다음 발표일을 받지 못했습니다.</span>}
      </p>

      <div className="ep-grid">
        <Estimate label="EPS 컨센서스" e={current(p.eps)} fmt={(v) => num(v)} />
        <Estimate label="매출 컨센서스" e={current(p.revenue)} fmt={big} />
        <div className="ep-stat">
          <span className="muted small">옵션 내재 변동폭</span>
          <strong className="num">{im ? `±${num(im.move_pct, 1)}%` : "—"}</strong>
          {im && (
            <span className="muted small num">
              만기 {im.expiry}{im.days_after_report != null && ` · 발표 뒤 ${im.days_after_report}일`}
              {im.days_after_report != null && im.days_after_report > 7 && <span className="ep-warn"> · 만기가 멀어 크게 나옴</span>}
            </span>
          )}
        </div>
        <div className="ep-stat">
          <span className="muted small">과거 발표 뒤 움직임{pm && ` · ${pm.n}회`}</span>
          <strong className="num">{pm ? `평균 ±${num(pm.avg_abs_pct, 1)}%` : "—"}</strong>
          {pm && <span className="muted small">오른 날 {pm.up} / {pm.n}</span>}
        </div>
        <div className="ep-stat">
          <span className="muted small">목표주가(평균)</span>
          <strong className="num">{num(p.targets?.mean)}</strong>
          {p.targets && <span className="muted small num">현재 대비 {pct(p.targets.upside_pct)} · {num(p.targets.low)} ~ {num(p.targets.high)}</span>}
        </div>
        <div className="ep-stat">
          <span className="muted small">투자의견{p.ratings && ` · ${p.ratings.total}명`}</span>
          <strong className="num">{p.ratings ? `매수 ${p.ratings.buy_pct}%` : "—"}</strong>
          {p.ratings && <span className="muted small num">매수 {p.ratings.strong_buy + p.ratings.buy} · 보유 {p.ratings.hold} · 매도 {p.ratings.sell + p.ratings.strong_sell}</span>}
        </div>
        <div className="ep-stat">
          <span className="muted small">공매도(유통 주식 대비)</span>
          <strong className="num">{si?.pct_float != null ? `${num(si.pct_float, 1)}%` : "—"}</strong>
          {si && <span className="muted small num">상환 {num(si.days_to_cover, 1)}일{si.as_of && ` · ${si.as_of}`}</span>}
        </div>
      </div>

      {p.history.length > 0 && (
        <table className="ep-history">
          <caption className="muted small">EPS 기록 — 예상 · 실제 · 서프라이즈</caption>
          <thead><tr><th>발표일</th><th className="num">예상</th><th className="num">실제</th><th className="num">서프라이즈</th></tr></thead>
          <tbody>
            {p.history.map((r) => (
              <tr key={r.at}>
                <td>{r.date}{r.when && <span className="muted small"> {WHEN[r.when] ?? ""}</span>}</td>
                <td className="num">{num(r.estimate)}</td>
                <td className="num">{num(r.actual)}</td>
                <td className={`num${r.beat == null ? "" : r.beat ? " up" : " down"}`}>{pct(r.surprise_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {p.kr_consensus?.quarter && p.kr_consensus.quarter.length > 0 && <KrTable rows={p.kr_consensus.quarter} unit={p.kr_consensus.unit} />}

      {p.checkpoints && p.checkpoints.length > 0 && (
        <div className="ep-checks">
          <h4 className="small">체크 포인트(최근 뉴스)</h4>
          <ul>
            {p.checkpoints.slice(0, 5).map((c) => (
              <li key={c.url} className="small">
                <a href={c.url} target="_blank" rel="noreferrer noopener">{c.title_ko}</a>
                {c.why_ko && <span className="muted"> — {c.why_ko}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      <details className="ep-foot small">
        <summary className="muted">출처 · 못 구하는 항목</summary>
        <ul>
          {Object.entries(p.sources).map(([k, s]) => <li key={k} className="muted">{k}: {s.source}</li>)}
          {p.unavailable.map((u) => <li key={u.id} className="muted">{u.label} — 없음({u.why})</li>)}
          {p.errors.map((e) => <li key={e} className="error">받지 못함: {e}</li>)}
        </ul>
      </details>
      <p className="muted small">{p.note}</p>
    </div>
  );
}

export default function EarningsPreviewCard({ ticker, name, onClose }: { ticker: string; name?: string; onClose: () => void }) {
  const [data, setData] = useState<EarningsPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    api.earningsPreview(ticker)
      .then((p) => { if (live) setData(p); })
      .catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [ticker]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="ep-overlay" onClick={onClose}>
      <section className="card ep-card" role="dialog" aria-modal="true" aria-label={`${name ?? ticker} 실적 프리뷰`}
        onClick={(e) => e.stopPropagation()}>
        <header className="ep-head">
          <h3>{name ?? ticker} <span className="muted small">{ticker}</span> 실적 프리뷰</h3>
          <button type="button" className="secondary" onClick={onClose} aria-label="닫기">×</button>
        </header>
        {error ? <p className="error small">실적 프리뷰를 받지 못했습니다 — {error}</p>
          : !data ? <p className="muted small">불러오는 중… (처음 여는 종목은 몇 초 걸립니다)</p>
          : <EarningsPreviewBody p={data} />}
      </section>
    </div>
  );
}
