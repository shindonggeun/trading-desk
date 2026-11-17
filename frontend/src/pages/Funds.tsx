import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { usdKo } from "../format";
import { FAKE_FUNDS, fakeFund } from "../fundsFake";
import type { FundBrief, FundChange, FundDetail, FundHolding } from "../types";

/** #537 보유 종목 표를 한 번에 그리는 줄 수 — 비중 큰 순이라 앞쪽이 중요한 줄이다 */
const ROW_STEP = 100;

// #484 기관 포트폴리오(#/funds): 유명 기관 13F 보유 공시(#483). 목록 → 기관 상세(요약 · 보유 표 · 분기 고르기).
// 13F 는 분기 끝 뒤 45일 안에 내는 공시라 보이는 보유는 최대 4~5개월 늦은 값이다 — 화면 맨 위에 밝힌다.

const CHANGE_KO: Record<FundChange, string> = { new: "신규", increase: "증가", decrease: "감소", sold: "전량 매도", unchanged: "유지" };
const LAG_NOTE = "13F 는 분기 끝(기준일) 뒤 45일 안에 내는 공시라, 여기 보이는 보유는 최대 4~5개월 전 모습입니다. 그 뒤 이미 사고팔았을 수 있습니다.";

const num = (v: number) => v.toLocaleString("ko-KR");
const signedNum = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${num(Math.abs(v))}`;
const day = (iso: string | null | undefined) => (iso ? iso.slice(0, 10) : "—");
const qKo = (q: string | null | undefined) => (q ? q.replace(/^(\d{4})Q([1-4])$/, "$1년 $2분기") : "—");
const lagDays = (period: string, filed: string) => Math.round((Date.parse(filed) - Date.parse(period)) / 86_400_000);

// 개발 서버에서 #483 API 가 없거나(404) 아직 한 번도 못 받았으면(503) 예시 데이터 — 운영에서는 오류를 그대로 보인다
const fakeable = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && (e.status === 404 || e.status === 503);

function ChangeTag({ h }: { h: FundHolding }) {
  // 전 분기를 못 읽었으면(change null) 비교하지 않는다
  if (!h.change) return <span className="fund-chg fund-chg-unchanged" title="전 분기 공시를 읽지 못해 비교할 수 없습니다">비교 불가</span>;
  const pct = h.change_pct != null && h.change !== "new" && h.change !== "sold" ? ` (${h.change_pct > 0 ? "+" : ""}${h.change_pct.toFixed(1)}%)` : "";
  const unit = h.share_type === "PRN" ? " 달러" : "주";
  return (
    <span className={`fund-chg fund-chg-${h.change}`}>
      {CHANGE_KO[h.change]}
      {h.change !== "unchanged" && h.change_shares != null && h.change_shares !== 0 && <span className="fund-chg-n"> {signedNum(h.change_shares)}{unit}{pct}</span>}
    </span>
  );
}

function Detail({ id, quarter, onQuarter }: { id: string; quarter: string | null; onQuarter: (q: string) => void }) {
  const [d, setD] = useState<FundDetail | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [fake, setFake] = useState(false);
  const [showSold, setShowSold] = useState(true);
  const [limit, setLimit] = useState(ROW_STEP);   // #537 보유 종목이 수백 개인 기관도 있다 — 처음엔 앞 100줄만
  useEffect(() => {
    let alive = true;
    setFailed(null);
    setD((cur) => (cur && cur.quarter !== quarter ? null : cur)); // 다른 분기로 바꾸면 이전 표 대신 "불러오는 중"
    api.fund(id, quarter ?? undefined)
      .then((v) => { if (alive) { setD(v); setFake(false); } })
      .catch((e) => {
        if (!alive) return;
        const f = fakeable(e) ? fakeFund(id, quarter ?? undefined) : null;
        if (f) { setD(f); setFake(true); return; }
        setD(null);
        // 서버가 이유를 준다: 미제출 분기(404) · 다른 요청이 SEC 에서 받는 중(503) · SEC 연락처(UA) 거절 등
        setFailed(e instanceof Error && e.message ? e.message : "불러오지 못했습니다");
      });
    return () => { alive = false; };
  }, [id, quarter]);
  const rows = useMemo(() => (d?.holdings ?? []).filter((h) => showSold || h.change !== "sold"), [d, showSold]);
  if (failed) return <p className="error">{failed}</p>;
  if (!d) return <p className="muted">불러오는 중…</p>;
  const sold = d.holdings.filter((h) => h.change === "sold").length;
  return (
    <section className="fund-detail">
      {fake && <p className="notice-warn app-notice">예시 데이터 — 개발 서버에서 #483 API 없이 화면을 보기 위한 값이며 실제 공시 숫자가 아닙니다.</p>}
      <h2 className="fund-headline">{d.summary}</h2>
      {d.bullets.length > 0 && <ul className="fund-bullets">{d.bullets.map((b, n) => <li key={n}>{b}</li>)}</ul>}
      <p className="muted small fund-meta">
        {d.name}{d.manager ? ` · ${d.manager}` : ""} · {qKo(d.quarter)} · 기준일 {day(d.period_of_report)} · 공시일 {day(d.filed_at)}
        {" "}({lagDays(d.period_of_report, d.filed_at)}일 뒤 공시) · {d.form}
        {d.reported_by && ` — 보유는 다른 운용사(${d.reported_by.name})가 보고`}
        {" "}· 보유 {d.count}종목 · 합계 {usdKo(d.total_value)}(주식 · 채권){d.options_value ? ` · 옵션 명목가 ${usdKo(d.options_value)}` : ""}
      </p>
      <div className="fund-tools">
        <div className="chips" role="group" aria-label="분기">
          {d.quarters.map((q) => (
            <button key={q} type="button" className={`chip${q === d.quarter ? " chip-on" : ""}`} aria-pressed={q === d.quarter} onClick={() => onQuarter(q)}>{qKo(q)}</button>
          ))}
        </div>
        {sold > 0 && (
          <label className="small fund-sold-toggle"><input type="checkbox" checked={showSold} onChange={(e) => setShowSold(e.target.checked)} /> 전량 매도 {sold}개 보기</label>
        )}
      </div>
      <div className="table-wrap fund-table-wrap">
        <table className="fund-table">
          <thead>
            <tr><th scope="col">종목</th><th scope="col" className="num">주식 수</th><th scope="col" className="num">평가액</th><th scope="col" className="num">비중</th><th scope="col">전 분기 대비{d.prev_quarter ? `(${qKo(d.prev_quarter)})` : ""}</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((h, n) => (
              <tr key={`${h.cusip}-${h.put_call ?? ""}-${n}`} className={h.change === "sold" ? "fund-row-sold" : undefined}>
                <th scope="row">
                  {h.ticker ? <a href={`#/chart/${encodeURIComponent(h.ticker)}`}>{h.ticker}</a> : <span className="muted">티커 없음</span>}
                  {h.put_call && <span className="fund-pc"> {h.put_call === "PUT" ? "풋" : "콜"}</span>}
                  <span className="fund-name muted small">{h.name}{h.title && h.title !== "COM" ? ` · ${h.title}` : ""}</span>
                </th>
                <td className="num">{h.change === "sold" ? `0 (전 ${num(h.prev_shares ?? 0)})` : h.share_type === "PRN" ? `원금 ${usdKo(h.shares)}` : num(h.shares)}</td>
                <td className="num">{h.change === "sold" ? "—" : h.put_call ? <span title="옵션은 명목가 — 합계 · 비중에 넣지 않습니다">{usdKo(h.value)} <span className="muted small">(명목)</span></span> : usdKo(h.value)}</td>
                <td className="num">{h.change === "sold" || h.weight == null ? "—" : `${h.weight.toFixed(2)}%`}</td>
                <td><ChangeTag h={h} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button type="button" className="link small" onClick={() => setLimit((v) => v + ROW_STEP * 4)}>   {/* 한 번에 400줄 더 */}
          더 보기 ({limit.toLocaleString("ko-KR")} / {rows.length.toLocaleString("ko-KR")}줄)
        </button>
      )}
    </section>
  );
}

function FundCard({ f, onOpen }: { f: FundBrief; onOpen: () => void }) {
  const l = f.latest;
  return (
    <button type="button" className="card fund-card" onClick={onOpen} disabled={!l}>
      <span className="fund-card-name"><strong>{f.name}</strong>{f.manager && <span className="muted small"> {f.manager}</span>}</span>
      {l ? (
        <>
          {l.summary && <span className="fund-card-sum">{l.summary}</span>}
          <span className="small">{qKo(l.quarter)}{l.count != null ? ` · 보유 ${l.count}종목` : ""}{l.total_value != null ? ` · ${usdKo(l.total_value)}` : ""}</span>
          {l.top_new.length > 0 && (
            <span className="small fund-card-new">신규 매수 상위: {l.top_new.map((t) => `${t.ticker ?? t.name} ${usdKo(t.value)}`).join(" · ")}</span>
          )}
          {l.count == null && <span className="muted small">보유 표는 아직 읽는 중 — 눌러서 보면 바로 받습니다</span>}
          <span className="muted small">기준일 {day(l.period_of_report)} · 공시일 {day(l.filed_at)} · {l.form}</span>
        </>
      ) : <span className="muted small">아직 받은 공시가 없습니다</span>}
      {f.next_due && <span className="muted small">다음 공시 기한 {day(f.next_due)}</span>}
      {f.error && <span className="small error">{f.error}</span>}
    </button>
  );
}

export default function Funds({ query }: { query: URLSearchParams }) {
  const id = query.get("fund");
  const quarter = query.get("q");
  const [funds, setFunds] = useState<FundBrief[] | null>(null);
  const [checked, setChecked] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [fake, setFake] = useState(false);
  useEffect(() => {
    let alive = true;
    api.funds()
      .then((v) => { if (alive) { setFunds(v.funds); setChecked(v.checked_at); } })
      .catch((e) => {
        if (!alive) return;
        if (fakeable(e)) { setFunds(FAKE_FUNDS); setFake(true); return; }
        setFailed(e instanceof Error ? e.message : "불러오지 못했습니다");
      });
    return () => { alive = false; };
  }, []);
  const go = (fund: string | null, q?: string) => {
    const p = new URLSearchParams();
    if (fund) p.set("fund", fund);
    if (q) p.set("q", q);
    window.location.hash = `#/funds${p.toString() ? `?${p}` : ""}`;
  };
  return (
    <main className="funds-page">
      <header className="funds-head">
        <h1>기관 포트폴리오 <span className="muted small">13F 보유 공시</span></h1>
        {id && <button type="button" className="link" onClick={() => go(null)}>← 기관 목록</button>}
      </header>
      <p className="muted small funds-lag" data-tour="funds-lag">{LAG_NOTE}{checked ? ` 마지막 확인 ${day(checked)}.` : ""}</p>
      {failed && !id && <p className="error">기관 목록을 불러오지 못했습니다: {failed}</p>}
      {id ? (
        <Detail key={id} id={id} quarter={quarter} onQuarter={(q) => go(id, q)} />
      ) : (
        <>
          {fake && <p className="notice-warn app-notice">예시 데이터 — 개발 서버에서 #483 API 없이 화면을 보기 위한 값이며 실제 공시 숫자가 아닙니다.</p>}
          {!funds && !failed && <p className="muted">불러오는 중…</p>}
          <div className="fund-cards" data-tour="fund-cards">{(funds ?? []).map((f) => <FundCard key={f.id} f={f} onOpen={() => go(f.id)} />)}</div>
        </>
      )}
    </main>
  );
}
