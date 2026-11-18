import { lazy, Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { krwShort, price } from "../format";
import type { FlowInterval, FlowMarket, FlowRecord, FlowSide, InstitutionKind, Investor, MarketFlow } from "../types";
import FuturesFlowView from "./FuturesFlowView";
// #644 오늘 장중(차트 라이브러리 160KB)은 그 탭을 열 때만 받는다 — 대시보드 첫 화면은 현물 탭이라 차트가 없다
const IntradayFlowView = lazy(() => import("./IntradayFlowView"));

// 메인 화면 "시장 수급"(#78): 코스피 · 코스닥에서 개인 · 외국인 · 기관 · 기타법인이 얼마를 사고팔았는지.
// 막대 색은 극성(순매수 빨강 · 순매도 파랑, 한국식), 추이 선 색은 주체(외국인 · 기관 · 개인) —
// 두 쓰임을 섞지 않으려고 선에는 빨강 · 파랑을 쓰지 않는다(색은 dataviz 검증 통과: styles.css --flow-*).

const MARKETS: { key: FlowMarket; label: string }[] = [{ key: "KOSPI", label: "코스피" }, { key: "KOSDAQ", label: "코스닥" }];
// 탭: 현물 두 시장 + 코스피200 선물(#161)
type Tab = FlowMarket | "FUTURES" | "INTRADAY";
const TABS: { key: Tab; label: string }[] = [...MARKETS, { key: "FUTURES", label: "선물" }, { key: "INTRADAY", label: "오늘 장중" }];
const INTERVALS: { key: FlowInterval; label: string }[] = [
  { key: "1d", label: "일" }, { key: "1w", label: "주" }, { key: "1mo", label: "월" }, { key: "1y", label: "년" },
];
const INVESTORS: { key: Investor; label: string }[] = [
  { key: "individual", label: "개인" }, { key: "foreigner", label: "외국인" },
  { key: "institution", label: "기관" }, { key: "other_corporation", label: "기타법인" },
];
const KINDS: { key: InstitutionKind; label: string }[] = [
  { key: "financial_investment", label: "금융투자" }, { key: "insurance", label: "보험" }, { key: "trust", label: "투신" },
  { key: "private_equity_fund", label: "사모펀드" }, { key: "bank", label: "은행" },
  { key: "other_financial_institution", label: "기타금융" }, { key: "pension_fund", label: "연기금" },
];
// 추이 선: 고정 순서(외국인 → 기관 → 개인), 색은 주체를 따른다 — 켜고 꺼도 남은 선의 색이 바뀌지 않는다.
const SERIES: { key: Investor; label: string; color: string }[] = [
  { key: "foreigner", label: "외국인", color: "var(--flow-foreigner)" },
  { key: "institution", label: "기관", color: "var(--flow-institution)" },
  { key: "individual", label: "개인", color: "var(--flow-individual)" },
];
const COUNT = 20;
// 장중(최신 기록이 잠정) 60초, 그 밖엔 5분 — 서버도 장 밖에는 10분 캐시한다.
const REFRESH_LIVE_MS = 60_000;
const REFRESH_IDLE_MS = 300_000;
const PREFS = "desk.marketFlow.v1";

type Prefs = { market: Tab; interval: FlowInterval; series: Record<string, boolean>; table: boolean };

function loadPrefs(): Prefs {
  const base: Prefs = { market: "KOSPI", interval: "1d", series: { foreigner: true, institution: true, individual: true }, table: false };
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    if (TABS.some((m) => m.key === raw.market)) base.market = raw.market;
    if (INTERVALS.some((i) => i.key === raw.interval)) base.interval = raw.interval;
    for (const s of SERIES) if (typeof raw.series?.[s.key] === "boolean") base.series[s.key] = raw.series[s.key];
    if (typeof raw.table === "boolean") base.table = raw.table;
  } catch {
    /* 손상 · 막힌 저장소: 기본값 */
  }
  return base;
}

function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS, JSON.stringify(p));
  } catch {
    /* 사생활 모드: 기억만 못 한다 */
  }
}

function md(date: string): string {
  const [, m, d] = date.split("-");
  return `${Number(m)}. ${Number(d)}.`;
}

/** 최신 기록이 어느 기간인지 — 장 시작 전 · 휴장일에는 records[0] 이 어제다(오늘이라고 단정하지 않는다). */
function periodLabel(r: FlowRecord, interval: FlowInterval): string {
  const [y, m] = r.date.split("-");
  if (interval === "1d") return r.in_progress ? "오늘" : `${md(r.date)} 기준`;
  if (interval === "1w") return r.in_progress ? "이번 주" : `${md(r.date)} 주`;
  if (interval === "1mo") return r.in_progress ? "이번 달" : `${y}. ${Number(m)}월`;
  return r.in_progress ? "올해" : `${y}년`;
}

function shortTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

/** 가운데 0 기준 발산 막대 한 줄 — 색은 극성, 숫자는 글자 색(+ "순매수/순매도" 글자로도 구분). */
function FlowBar({ label, side, max }: { label: string; side: FlowSide | undefined; max: number }) {
  if (!side) return null;
  const share = max > 0 ? Math.min(Math.abs(side.net) / max, 1) * 50 : 0;
  const buying = side.net > 0;
  const word = side.net === 0 ? "보합" : buying ? "순매수" : "순매도";
  return (
    <li className="flow-row" title={`${label} 매수 ${krwShort(side.buy, false)} · 매도 ${krwShort(side.sell, false)}`}>
      <span className="flow-name">{label}</span>
      <span className="flow-track" aria-hidden>
        <span className="flow-zero" />
        {share > 0 && (
          <span className={`flow-bar ${buying ? "flow-buy" : "flow-sell"}`}
            style={buying ? { left: "50%", width: `${share}%` } : { right: "50%", width: `${share}%` }} />
        )}
      </span>
      <span className="flow-net">
        <strong>{krwShort(side.net)}</strong> <span className="muted small">{word}</span>
      </span>
      <span className="flow-sides muted small">매수 {krwShort(side.buy, false)} · 매도 {krwShort(side.sell, false)}</span>
    </li>
  );
}

type Point = { date: string; values: Record<string, number>; nets: Record<string, number | null> };

const PERIOD_WORD: Record<FlowInterval, string> = { "1d": "그날", "1w": "그 주", "1mo": "그 달", "1y": "그 해" };

/** 축 · 읽기 줄의 기간 표기 — 월 · 년은 연도가 있어야 어느 해인지 안다. */
function tick(date: string, interval: FlowInterval): string {
  const [y, m] = date.split("-");
  if (interval === "1y") return y;
  if (interval === "1mo") return `${y.slice(2)}.${Number(m)}`;
  return md(date);
}

/** 최근 기록의 투자자별 누적 순매수 — 선 · 0 기준선 · 끝 라벨 · 십자선 + 아래 읽기 줄. */
function TrendChart({ records, on, interval }: { records: FlowRecord[]; on: Record<string, boolean>; interval: FlowInterval }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  // 상자는 늘 그려 두고(안내 문구일 때도) 폭을 잰다 — 나중에 선이 생겨도 칸 폭을 따라간다.
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 손가락으로 고른 시점은 차트 밖을 누르면 지운다(손을 떼도 남게)
  useEffect(() => {
    if (hover === null) return;
    const close = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setHover(null);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [hover]);

  const shown = SERIES.filter((s) => on[s.key]);
  const points: Point[] = useMemo(() => {
    const sum: Record<string, number> = {};
    return [...records].reverse().map((r) => {
      const values: Record<string, number> = {};
      const nets: Record<string, number | null> = {};
      for (const s of SERIES) {
        const net = r.investors[s.key]?.net ?? null;   // 빠진 값은 누적에 더하지 않고 "—" 로 보인다
        sum[s.key] = (sum[s.key] ?? 0) + (net ?? 0);
        values[s.key] = sum[s.key];
        nets[s.key] = net;
      }
      return { date: r.date, values, nets };
    });
  }, [records]);

  let body: React.ReactNode = null;
  if (points.length < 2) body = <p className="muted small">추이를 그릴 기록이 부족합니다.</p>;
  else if (shown.length === 0) body = <p className="muted small">위에서 볼 주체를 하나 이상 켜세요.</p>;
  else if (width > 0) {
    const H = 150;
    const pad = { l: 22, r: 52, t: 10, b: 20 };
    const all = shown.flatMap((s) => points.map((p) => p.values[s.key]));
    const lo = Math.min(0, ...all);
    const hi = Math.max(0, ...all);
    const span = hi - lo || 1;
    const x = (i: number) => pad.l + (i / (points.length - 1)) * (width - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - lo) / span) * (H - pad.t - pad.b);
    // 갱신으로 기록 수가 줄어도 범위 안에서
    const at = Math.min(hover ?? points.length - 1, points.length - 1);
    const last = points[points.length - 1];

    // 끝 라벨: 14px 간격으로 아래로 밀고, 바닥을 넘으면 위로 되민다
    const ends = shown.map((s) => ({ s, y: y(last.values[s.key]) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    const floor = H - pad.b;
    if (ends.length && ends[ends.length - 1].y > floor) {
      ends[ends.length - 1].y = floor;
      for (let i = ends.length - 2; i >= 0; i--) if (ends[i + 1].y - ends[i].y < 14) ends[i].y = ends[i + 1].y - 14;
    }

    const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const i = Math.round(((e.clientX - rect.left - pad.l) / (width - pad.l - pad.r)) * (points.length - 1));
      setHover(Math.min(points.length - 1, Math.max(0, i)));
    };

    body = (
      <>
        <svg width={width} height={H} role="img" onPointerMove={onMove} onPointerDown={onMove}
          onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
          aria-label={`최근 ${points.length}개 기록의 투자자별 누적 순매수(${tick(points[0].date, interval)} ~ ${tick(last.date, interval)}): ${shown.map((s) => `${s.label} ${krwShort(last.values[s.key])}`).join(", ")}`}>
          <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} className="flow-axis" />
          {/* 0 눈금은 왼쪽에 — 오른쪽 끝 라벨과 겹치지 않게 */}
          <text x={pad.l - 6} y={y(0) + 4} className="flow-tick" textAnchor="end">0</text>
          {shown.map((s) => (
            <polyline key={s.key} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
              points={points.map((p, i) => `${x(i)},${y(p.values[s.key])}`).join(" ")} />
          ))}
          {hover !== null && <line x1={x(at)} x2={x(at)} y1={pad.t} y2={H - pad.b} className="flow-cross" />}
          {shown.map((s) => (
            <circle key={s.key} cx={x(at)} cy={y(points[at].values[s.key])} r={4} fill={s.color} className="flow-dot" />
          ))}
          {ends.map(({ s, y: ly }) => (
            <text key={s.key} x={width - pad.r + 6} y={ly + 4} className="flow-end">{s.label}</text>
          ))}
          <text x={pad.l} y={H - 4} className="flow-tick">{tick(points[0].date, interval)}</text>
          <text x={width - pad.r} y={H - 4} className="flow-tick" textAnchor="end">{tick(last.date, interval)}</text>
          {/* 손가락 · 마우스가 닿는 영역은 선보다 넓게 */}
          <rect x={0} y={0} width={width} height={H} fill="transparent" />
        </svg>
        {/* 읽기 줄: 차트를 가리지 않게 아래에. 소리 내어 읽지 않는다(aria-live 없음) — 같은 정보는 svg 이름 · 표에 */}
        <div className="flow-readout">
          <span className="muted small">{tick(points[at].date, interval)}{hover === null ? " 최신" : ""} 누적</span>
          {shown.map((s) => (
            <span key={s.key} className="flow-readout-item">
              <span className="flow-swatch" style={{ background: s.color }} />
              {s.label} <strong>{krwShort(points[at].values[s.key])}</strong>
              <span className="muted small">({PERIOD_WORD[interval]} {krwShort(points[at].nets[s.key])})</span>
            </span>
          ))}
        </div>
      </>
    );
  }
  return <div className="flow-trend" ref={box}>{body}</div>;
}

/** 메인 화면 시장 수급 패널 — 오늘(최신 기간) 4분류 순매수, 기관 세부, 최근 흐름(#78, API #76). */
export default function MarketFlowPanel() {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [data, setData] = useState<MarketFlow | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const seq = useRef(0);
  const liveRef = useRef(true); // 최신 기록이 아직 바뀔 수 있나(잠정)

  const update = (patch: Partial<Prefs>) => setPrefs((p) => {
    const next = { ...p, ...patch };
    savePrefs(next);
    return next;
  });

  // #362 알림 클릭(#/?flow=intraday): "오늘 장중" 탭을 열고 이 카드로 스크롤한다
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    const open = () => {
      if (!/[?&]flow=intraday(?:&|$)/.test(window.location.hash)) return;
      update({ market: "INTRADAY" });
      window.history.replaceState(null, "", "#/"); // 새로고침 · 뒤로 가기에 다시 끌려오지 않게
      window.requestAnimationFrame(() => card.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);

  const spotMarket: FlowMarket | null = prefs.market === "FUTURES" || prefs.market === "INTRADAY" ? null : prefs.market;

  useEffect(() => {
    if (!spotMarket) return; // 선물 탭은 FuturesFlowView 가 따로 부른다
    let alive = true;          // 탭 · 시장을 바꾼 뒤 늦게 온 응답이 타이머를 다시 만들지 않게
    let timer: number | undefined;
    const load = () => {
      const mine = ++seq.current;
      api.marketFlow(spotMarket, prefs.interval, COUNT)
        .then((d) => {
          if (!alive || mine !== seq.current) return; // 늦게 온 이전 시장 · 단위 · 탭 응답
          // 서버가 실패를 error 로 주면 마지막 값을 그대로 두고 알림만
          if (d.error && d.records.length === 0) setFailed(d.error);
          else {
            setData(d);
            setFailed(null);
            const live = !!d.records[0]?.provisional;
            if (live !== liveRef.current) {
              liveRef.current = live;
              schedule(); // 주기 다시 맞추기
            }
          }
        })
        .catch((e) => alive && mine === seq.current && setFailed(e instanceof Error ? e.message : "불러오지 못했습니다."));
    };
    const visible = () => document.visibilityState === "visible";
    const schedule = () => {
      window.clearInterval(timer);
      timer = window.setInterval(() => visible() && load(), liveRef.current ? REFRESH_LIVE_MS : REFRESH_IDLE_MS);
    };
    const onVisible = () => {
      if (visible()) load(); // 다시 보면 바로 갱신
    };
    setData((d) => (d && d.market === prefs.market && d.interval === prefs.interval ? d : null));
    setFailed(null); // 이전 시장 · 단위의 오류를 새 선택에 남기지 않는다
    load();
    schedule();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      alive = false;
      seq.current++;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [spotMarket, prefs.interval]);

  const latest = data?.records[0];
  const max = latest ? Math.max(...INVESTORS.map((i) => Math.abs(latest.investors[i.key]?.net ?? 0)), 1) : 1;
  const kinds = latest ? KINDS.filter((k) => latest.institution_breakdown[k.key]) : [];
  const kindMax = latest ? Math.max(...kinds.map((k) => Math.abs(latest.institution_breakdown[k.key]!.net)), 1) : 1;
  const marketLabel = TABS.find((m) => m.key === prefs.market)!.label;

  return (
    <section className="card flow" id="market-flow" ref={card} aria-label="시장 수급">
      <div className="flow-head">
        <h3>시장 수급</h3>
        <div className="seg" role="group" aria-label="시장">
          {TABS.map((m) => (
            <button key={m.key} type="button" className={m.key === prefs.market ? "on" : ""} aria-pressed={m.key === prefs.market}
              onClick={() => update({ market: m.key })}>{m.label}</button>
          ))}
        </div>
      </div>
      {prefs.market === "INTRADAY" ? <Suspense fallback={<p className="muted small">불러오는 중…</p>}><IntradayFlowView /></Suspense> : !spotMarket ? <FuturesFlowView /> : <>
      <div className="flow-sub">
        <div className="flow-index">
          {data?.index ? <><strong>{price(data.index.price)}</strong> <span className="muted small">{marketLabel}</span></> : <span className="muted small">{marketLabel}</span>}
        </div>
        <div className="seg seg-small" role="group" aria-label="집계 단위">
          {INTERVALS.map((i) => (
            <button key={i.key} type="button" className={i.key === prefs.interval ? "on" : ""} aria-pressed={i.key === prefs.interval}
              onClick={() => update({ interval: i.key })}>{i.label}</button>
          ))}
        </div>
      </div>

      {failed && <p className="error small" role="alert">{data ? "갱신하지 못했습니다 — 마지막 값을 보여 줍니다." : `수급을 불러오지 못했습니다. ${failed}`}</p>}
      {data && !data.supported && <p className="muted small">이 브로커는 시장 수급을 제공하지 않습니다.</p>}
      {!data && !failed && <p className="muted small">불러오는 중…</p>}
      {data?.supported && !latest && !failed && <p className="muted small">표시할 기록이 없습니다.</p>}

      {latest && (
        <>
          <p className="flow-period small">
            <strong>{periodLabel(latest, prefs.interval)}</strong>
            {latest.provisional && <span className="pill pill-warn flow-pill" title="장 마감 · 저녁 확정 전 값이라 바뀔 수 있습니다">잠정</span>}
            <span className="muted">
              {latest.updated_at ? ` · ${shortTime(latest.updated_at)} 갱신` : ""}
              {data?.source === "mock" ? " · 샘플" : ""}
            </span>
          </p>
          <ul className="flow-bars" aria-label={`${marketLabel} 투자자별 순매수`}>
            {INVESTORS.map((i) => <FlowBar key={i.key} label={i.label} side={latest.investors[i.key]} max={max} />)}
          </ul>
          {kinds.length > 0 && (
            <details className="flow-kinds">
              <summary>기관 세부 {kinds.length}개</summary>
              <ul className="flow-bars">
                {kinds.map((k) => <FlowBar key={k.key} label={k.label} side={latest.institution_breakdown[k.key]} max={kindMax} />)}
              </ul>
            </details>
          )}

          <div className="flow-trend-head">
            <span className="muted small">최근 {data!.records.length}개 누적 순매수</span>
            <span className="flow-legend" role="group" aria-label="추이에 보일 주체">
              {SERIES.map((s) => (
                <button key={s.key} type="button" className={`chip ${prefs.series[s.key] ? "chip-on" : ""}`} aria-pressed={prefs.series[s.key]}
                  onClick={() => update({ series: { ...prefs.series, [s.key]: !prefs.series[s.key] } })}>
                  <span className="flow-swatch" style={{ background: s.color }} /> {s.label}
                </button>
              ))}
            </span>
          </div>
          <TrendChart records={data!.records} on={prefs.series} interval={prefs.interval} />
          <button type="button" className="link small" aria-expanded={prefs.table} onClick={() => update({ table: !prefs.table })}>
            {prefs.table ? "표 닫기" : "표로 보기"}
          </button>
          {prefs.table && (
            <div className="table-wrap">
              <table className="flow-table">
                <thead>
                  <tr><th>기간</th>{INVESTORS.map((i) => <th key={i.key} className="num">{i.label}</th>)}</tr>
                </thead>
                <tbody>
                  {data!.records.map((r) => (
                    <tr key={r.date}>
                      <td>{r.date}{r.provisional ? " (잠정)" : ""}</td>
                      {INVESTORS.map((i) => <td key={i.key} className="num">{krwShort(r.investors[i.key]?.net)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted small flow-note">순매수 = 매수 − 매도(거래대금, 원). 4분류 합은 시장 전체에서 0 입니다.</p>
        </>
      )}
      </>}
    </section>
  );
}
