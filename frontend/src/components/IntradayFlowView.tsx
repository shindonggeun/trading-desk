import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { api, ApiError } from "../api";
import type { IntradayBucket, IntradayFlow, IntradayFuturesBar, IntradaySpotBar, IntradayTurn } from "../types";

// #360 시장 수급 "오늘 장중": 현물(코스피/코스닥) · 코스피200 선물의 외국인(+ 기관 · 개인) 누적 수급과
// 고른 봉의 구간 순매수 막대, 외국인 순매수/순매도 전향 마커. 데이터는 #359 GET /api/market/flow/intraday.
// 선 색은 주체(--flow-*), 막대 색은 극성(순매수 빨강 · 순매도 파랑) — MarketFlowPanel 과 같은 규칙.

const BUCKETS: IntradayBucket[] = [1, 3, 5, 10, 15, 30, 60];
type Spot = "kospi" | "kosdaq";
type Who = "foreign" | "institution" | "individual";
const WHO: { key: Who; label: string; color: string }[] = [
  { key: "foreign", label: "외국인", color: "--flow-foreigner" },
  { key: "institution", label: "기관", color: "--flow-institution" },
  { key: "individual", label: "개인", color: "--flow-individual" },
];
const FUT_KEY: Record<Who, keyof IntradayFuturesBar> = {
  foreign: "foreign_contracts", institution: "institution_contracts", individual: "individual_contracts",
};
const KST_MS = 9 * 3600_000;
const PREFS = "desk.intradayFlow";
type Prefs = { bucket: IntradayBucket; spot: Spot; who: Record<Who, boolean> };

function loadPrefs(): Prefs {
  const base: Prefs = { bucket: 5, spot: "kospi", who: { foreign: true, institution: false, individual: false } };
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    if (BUCKETS.includes(raw.bucket)) base.bucket = raw.bucket;
    if (raw.spot === "kospi" || raw.spot === "kosdaq") base.spot = raw.spot;
    for (const w of WHO) if (typeof raw.who?.[w.key] === "boolean") base.who[w.key] = raw.who[w.key];
  } catch {
    /* 손상 · 막힌 저장소: 기본값 */
  }
  return base;
}
function savePrefs(p: Prefs) {
  try { localStorage.setItem(PREFS, JSON.stringify(p)); } catch { /* 저장 못 해도 화면은 그대로 */ }
}

function css(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
// 가로축을 KST 시계로 읽게: UTC 초에 9시간을 더해 차트(UTC 표시)에 넘긴다.
function kstTime(iso: string): UTCTimestamp {
  return Math.floor((Date.parse(iso) + KST_MS) / 1000) as UTCTimestamp;
}
function sessionEdge(date: string, hhmm: string): UTCTimestamp {
  return Math.floor(Date.parse(`${date}T${hhmm}:00Z`) / 1000) as UTCTimestamp;
}
function hhmm(iso: string): string {
  const d = new Date(Date.parse(iso) + KST_MS);
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
// 지금(KST)이 평일 15:45 이전인가 — 장 시작 전이면 아직 기다릴 장이 남았다.
function beforeClose(): boolean {
  const k = new Date(Date.now() + KST_MS);
  const day = k.getUTCDay();
  return day !== 0 && day !== 6 && k.getUTCHours() * 60 + k.getUTCMinutes() < 15 * 60 + 45;
}
function signed(raw: number, unit: string): string {
  const v = Math.round(raw) || 0; // -0.4 → "−0" 대신 0
  const s = v.toLocaleString("ko-KR");
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${s.replace("-", "")}${unit}`;
}
function lastValue<T>(rows: T[], pick: (r: T) => number | null): number | null {
  for (let i = rows.length - 1; i >= 0; i--) { const v = pick(rows[i]); if (v != null) return v; }
  return null;
}
const TURN_LABEL: Record<IntradayTurn["kind"], string> = { buy_turn: "순매수 전향", sell_turn: "순매도 전향" };

/** 상단 한 줄: "외인 현물 −1,240억(10:30 순매도 전향) · 선물 −3,100계약 · 둘 다 매도" */
export function summarize(d: IntradayFlow, spot: Spot): string | null {
  const s = lastValue(d.spot[spot], (b) => b.foreign);
  const f = lastValue(d.futures, (b) => b.foreign_contracts);
  if (s == null && f == null) return null;
  const turnOf = (m: IntradayTurn["market"]) => [...d.turns].reverse().find((t) => t.market === m);
  const parts: string[] = [];
  if (s != null) {
    const t = turnOf(spot);
    parts.push(`외인 현물 ${signed(s, "억")}${t ? `(${hhmm(t.t)} ${TURN_LABEL[t.kind]})` : ""}`);
  }
  if (f != null) {
    const t = turnOf("futures");
    parts.push(`선물 ${signed(f, "계약")}${t ? `(${hhmm(t.t)} ${TURN_LABEL[t.kind]})` : ""}`);
  }
  if (s != null && f != null && s !== 0 && f !== 0) parts.push(s > 0 && f > 0 ? "둘 다 매수" : s < 0 && f < 0 ? "둘 다 매도" : "엇갈림");
  return parts.join(" · ");
}

// 개발 서버에서 #359 API 가 아직 없을 때(404)만 쓰는 가짜 데이터 — 운영 빌드에서는 쓰지 않는다.
function fakeFlow(bucket: IntradayBucket): IntradayFlow {
  const date = new Date(Date.now() + KST_MS).toISOString().slice(0, 10);
  const start = Date.parse(`${date}T09:00:00+09:00`);
  const spotBars = (scale: number): IntradaySpotBar[] => {
    let f = 0, i = 0, p = 0; const out: IntradaySpotBar[] = [];
    for (let m = bucket; m <= 390; m += bucket) {
      const fd = Math.round(Math.sin(m / 60) * 40 * scale * bucket / 5), id = Math.round(-fd * 0.6), pd = -fd - id;
      f += fd; i += id; p += pd;
      out.push({ t: new Date(start + m * 60_000).toISOString(), foreign: f, institution: i, individual: p, foreign_delta: fd, institution_delta: id, individual_delta: pd });
    }
    return out;
  };
  const kospi = spotBars(1);
  const futures: IntradayFuturesBar[] = kospi.map((b, k) => ({
    t: b.t, foreign_contracts: (b.foreign ?? 0) * 3, institution_contracts: (b.institution ?? 0) * 2, individual_contracts: (b.individual ?? 0) * 2,
    foreign_delta: (b.foreign_delta ?? 0) * 3, institution_delta: (b.institution_delta ?? 0) * 2, individual_delta: (b.individual_delta ?? 0) * 2, price: 400 + k * 0.1,
  }));
  const turns: IntradayTurn[] = [];
  kospi.forEach((b, k) => {
    const prev = kospi[k - 1]?.foreign ?? 0;
    if (k && prev <= 0 && (b.foreign ?? 0) > 0) turns.push({ t: b.t, market: "kospi", who: "foreign", kind: "buy_turn", value: b.foreign });
    if (k && prev >= 0 && (b.foreign ?? 0) < 0) turns.push({ t: b.t, market: "kospi", who: "foreign", kind: "sell_turn", value: b.foreign });
  });
  return { supported: true, date, bucket_min: bucket, market_open: true, spot: { kospi, kosdaq: spotBars(0.4) }, futures, turns };
}

export default function IntradayFlowView() {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [data, setData] = useState<IntradayFlow | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  const update = (p: Partial<Prefs>) => setPrefs((cur) => { const next = { ...cur, ...p }; savePrefs(next); return next; });

  // 불러오기 + 장중 자동 갱신(봉 간격에 맞추되 최소 30초). 장이 끝나면 멈춘다.
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const load = () => {
      api.marketFlowIntraday(prefs.bucket)
        .then((d) => {
          if (!alive) return;
          setData(d); setFailed(null);
          // 장중: 봉 간격(최소 30초). 장 시작 전(평일 15:45 이전): 60초로 열리기를 기다린다. 마감 뒤: 멈춘다.
          if (d.supported === false) return;
          if (d.market_open) timer = window.setTimeout(load, Math.max(30, prefs.bucket * 60) * 1000);
          else if (beforeClose()) timer = window.setTimeout(load, 60_000);
        })
        .catch((e) => {
          if (!alive) return;
          if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) { setData(fakeFlow(prefs.bucket)); setFailed(null); return; }
          setFailed(e instanceof Error ? e.message : "불러오지 못했습니다");
          timer = window.setTimeout(load, 60_000);
        });
    };
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [prefs.bucket]);

  const summary = useMemo(() => (data ? summarize(data, prefs.spot) : null), [data, prefs.spot]);

  // 차트: 오른쪽 축 = 현물 누적(억원), 왼쪽 축 = 선물 누적(계약), 아래 1/4 = 현물 외국인 구간 순매수 막대.
  useEffect(() => {
    const el = box.current;
    if (!el || !data) return;
    const grid = css("--border", "#2a3038");
    const up = css("--up", "#f2464b"), down = css("--down", "#3a86f8");
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted", "#8c939d"), attributionLogo: false },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { visible: true, borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.3 } },
      leftPriceScale: { visible: true, borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.3 } },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      localization: { locale: "ko-KR", priceFormatter: (v: number) => Math.round(v).toLocaleString("ko-KR") },
      handleScroll: false, handleScale: false,
    });
    chartRef.current = chart;

    // 막대(현물 외국인 구간 순매수) + 08:45~15:45 빈 시각으로 가로축 고정
    const bars = chart.addSeries(HistogramSeries, { priceScaleId: "delta", priceLineVisible: false, lastValueVisible: false, base: 0 });
    chart.priceScale("delta").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    const rows = data.spot[prefs.spot];
    const barData = rows.filter((b) => b.foreign_delta != null).map((b) => ({
      time: kstTime(b.t), value: b.foreign_delta as number, color: (b.foreign_delta as number) >= 0 ? up : down,
    }));
    // 가로축은 시각 비례가 아니라 점 순서라, 08:45~15:45 의 bucket 격자 전체를 막대 시리즈에 깐다
    // (봉이 없는 칸은 빈 시각). 봉 시각은 모두 격자 위이고, 격자를 넘는 봉(마감 스냅샷)만 뒤에 붙인다.
    const open = sessionEdge(data.date, "08:45"), close = sessionEdge(data.date, "15:45");
    const step = Math.max(1, data.bucket_min) * 60;
    const byTime = new Map(barData.map((b) => [b.time as number, b]));
    const slots: ({ time: UTCTimestamp } | (typeof barData)[number])[] = [];
    for (let t = open; t <= close; t = (t + step) as UTCTimestamp) slots.push(byTime.get(t) ?? { time: t });
    const onGrid = new Set(slots.map((s) => s.time as number));
    for (const b of barData) if (!onGrid.has(b.time as number)) slots.push(b);
    slots.sort((a, b) => (a.time as number) - (b.time as number));
    bars.setData(slots);

    const markersFor: Record<string, ISeriesApi<"Line">> = {};
    for (const w of WHO) {
      if (!prefs.who[w.key]) continue;
      const color = css(w.color, "#a371f7");
      const spotLine = chart.addSeries(LineSeries, { color, lineWidth: 2, priceScaleId: "right", priceLineVisible: false, title: `현물 ${w.label}` });
      spotLine.setData(rows.filter((b) => b[w.key] != null).map((b) => ({ time: kstTime(b.t), value: b[w.key] as number })));
      const futLine = chart.addSeries(LineSeries, { color, lineWidth: 2, lineStyle: LineStyle.Dashed, priceScaleId: "left", priceLineVisible: false, title: `선물 ${w.label}` });
      const fk = FUT_KEY[w.key];
      futLine.setData(data.futures.filter((b) => b[fk] != null).map((b) => ({ time: kstTime(b.t), value: b[fk] as number })));
      if (w.key === "foreign") { markersFor[prefs.spot] = spotLine; markersFor.futures = futLine; }
    }

    // 전향 마커: 외국인 선이 켜져 있을 때 해당 선 위에. 좁은 화면(휴대폰)은 글자가 오른쪽 끝에서 잘리지 않게 "10:41 매도" 로 줄인다
    // — 값 · 전향 이름 전체는 위 요약 줄에 있다
    const narrow = (box.current?.clientWidth ?? 1000) < 560;
    for (const [market, series] of Object.entries(markersFor)) {
      const unit = market === "futures" ? "계약" : "억";
      const ms: SeriesMarker<Time>[] = data.turns.filter((t) => t.market === market).map((t): SeriesMarker<Time> => ({
        time: kstTime(t.t),
        position: t.kind === "buy_turn" ? "belowBar" : "aboveBar",
        shape: t.kind === "buy_turn" ? "arrowUp" : "arrowDown",
        color: t.kind === "buy_turn" ? up : down,
        text: narrow
          ? `${hhmm(t.t)} ${t.kind === "buy_turn" ? "매수" : "매도"}`
          : `${hhmm(t.t)} ${TURN_LABEL[t.kind]}${t.value != null ? ` ${signed(t.value, unit)}` : ""}`,
      })).sort((a, b) => (a.time as number) - (b.time as number));
      if (ms.length) createSeriesMarkers(series, ms);
    }
    chart.timeScale().fitContent();
    return () => { chart.remove(); chartRef.current = null; };
  }, [data, prefs.spot, prefs.who]);

  const supported = data?.supported !== false;

  return (
    <div className="intraday-flow">
      <div className="flow-sub intraday-controls">
        <div className="seg seg-small" role="group" aria-label="현물 시장">
          {(["kospi", "kosdaq"] as Spot[]).map((s) => (
            <button key={s} type="button" className={s === prefs.spot ? "on" : ""} aria-pressed={s === prefs.spot}
              onClick={() => update({ spot: s })}>{s === "kospi" ? "코스피" : "코스닥"}</button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="봉 간격">
          {BUCKETS.map((b) => (
            <button key={b} type="button" className={`chip${b === prefs.bucket ? " chip-on" : ""}`} aria-pressed={b === prefs.bucket}
              onClick={() => update({ bucket: b })}>{b}분</button>
          ))}
        </div>
        <div className="chips" role="group" aria-label="주체">
          {WHO.map((w) => (
            <button key={w.key} type="button" className={`chip${prefs.who[w.key] ? " chip-on" : ""}`} aria-pressed={prefs.who[w.key]}
              style={{ ["--chip-dot" as string]: `var(${w.color})` }}
              onClick={() => update({ who: { ...prefs.who, [w.key]: !prefs.who[w.key] } })}>
              <span className="chip-dot" aria-hidden="true" />{w.label}
            </button>
          ))}
        </div>
      </div>

      {failed && <p className="muted small">장중 수급을 불러오지 못했습니다: {failed}</p>}
      {!supported && <p className="muted small">한국투자증권 키가 없어 장중 수급을 받을 수 없습니다.</p>}
      {data && supported && (
        <>
          <p className="intraday-summary">
            {summary ?? <span className="muted">아직 장중 수급이 없습니다.</span>}
            {!data.market_open && <span className="badge muted"> {beforeClose() ? "장 시작 전" : "장 마감"}</span>}
          </p>
          <div className="intraday-chart" ref={box} aria-label="오늘 장중 현물 · 선물 수급 차트" />
          <p className="muted small">실선 = 현물 누적(억원, 오른쪽 축) · 점선 = 선물 누적(계약, 왼쪽 축) · 막대 = 현물 외국인 {data.bucket_min}분 순매수</p>
        </>
      )}
      {!data && !failed && <p className="muted small">불러오는 중…</p>}
    </div>
  );
}
