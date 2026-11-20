import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries,
  type IChartApi, type ISeriesApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { crosshairTime, ticksClearOfLabels } from "./chartFormat";
import { api, ApiError, type MarketAlert } from "../api";
import { badgeOf, hhmm as alertTime, useTodayAlerts } from "../alertsFeed";
import AlertTimeline from "./AlertTimeline";
import type { IntradayBucket, IntradayFlow, IntradayFuturesBar, IntradaySpotBar, IntradayTurn } from "../types";
import { css, useColorScheme } from "../chartColors";

// #360 시장 수급 "오늘 장중": 현물(코스피/코스닥) · 코스피200 선물의 외국인 · 기관 · 개인 누적 수급과
// 고른 봉의 구간 순매수 막대, 외국인 순매수/순매도 전향 마커. 데이터는 #359 GET /api/market/flow/intraday.
// #401 현물 차트(억원)와 선물 차트(계약)를 위아래로 나눠 각 차트가 축 하나만 쓰고, 십자선 시각을 서로 맞춘다.
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
// #401 주체 기본값이 "외국인만" → "셋 다"로 바뀌었다. 예전에 저장한 주체 선택(v 없음)은 버리고 새 기본을 보인다
const PREFS_VERSION = 2;
type Prefs = { bucket: IntradayBucket; spot: Spot; who: Record<Who, boolean> };

function loadPrefs(): Prefs {
  const base: Prefs = { bucket: 5, spot: "kospi", who: { foreign: true, institution: true, individual: true } };
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    if (BUCKETS.includes(raw.bucket)) base.bucket = raw.bucket;
    if (raw.spot === "kospi" || raw.spot === "kosdaq") base.spot = raw.spot;
    if (raw.v === PREFS_VERSION) for (const w of WHO) if (typeof raw.who?.[w.key] === "boolean") base.who[w.key] = raw.who[w.key];
  } catch {
    /* 손상 · 막힌 저장소: 기본값 */
  }
  return base;
}
function savePrefs(p: Prefs) {
  try { localStorage.setItem(PREFS, JSON.stringify({ ...p, v: PREFS_VERSION })); } catch { /* 저장 못 해도 화면은 그대로 */ }
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

type Point = { t: string; v: number | null };
type Pane = {
  /** 누적선 — 주체마다 하나 */
  lines: { who: Who; color: string; points: Point[] }[];
  /** 아래 막대 — 외국인 구간 순매수 */
  deltas: Point[];
  /** 전향 마커를 다는 시장(외국인 선이 켜져 있을 때만) */
  turns: IntradayTurn[];
  /** #581 이 시장의 수급 흐름 알림(#579) — 막대 위에 시각 · 강도 표시 */
  alerts: MarketAlert[];
  unit: string;
};

/** #401 차트 하나: 오른쪽 축 = 누적, 아래 1/4 = 외국인 구간 순매수 막대, 가로축은 08:45~15:45 bucket 격자.
 *  막대 시리즈를 돌려준다 — 모든 격자 시각에 점(또는 빈 점)이 있어 십자선 맞추기의 기준이 된다. */
function drawPane(el: HTMLElement, date: string, bucketMin: number, pane: Pane): { chart: IChartApi; anchor: ISeriesApi<"Histogram"> } {
  const grid = css("--border");
  const up = css("--up"), down = css("--down");
  const chart = createChart(el, {
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), attributionLogo: false },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { visible: true, borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.3 } },
    leftPriceScale: { visible: false },
    timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
    localization: { locale: "ko-KR", timeFormatter: crosshairTime }, // #434 금액 표기는 선 시리즈 priceFormat 에서
    handleScroll: false, handleScale: false,
  });

  // 막대(외국인 구간 순매수) + 08:45~15:45 빈 시각으로 가로축 고정
  const bars = chart.addSeries(HistogramSeries, { priceScaleId: "delta", priceLineVisible: false, lastValueVisible: false, base: 0 });
  chart.priceScale("delta").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
  const barData = pane.deltas.filter((b) => b.v != null).map((b) => ({
    time: kstTime(b.t), value: b.v as number, color: (b.v as number) >= 0 ? up : down,
  }));
  // 가로축은 시각 비례가 아니라 점 순서라, 08:45~15:45 의 bucket 격자 전체를 막대 시리즈에 깐다
  // (봉이 없는 칸은 빈 시각). 봉 시각은 모두 격자 위이고, 격자를 넘는 봉(마감 스냅샷)만 뒤에 붙인다.
  const open = sessionEdge(date, "08:45"), close = sessionEdge(date, "15:45");
  const step = Math.max(1, bucketMin) * 60;
  const byTime = new Map(barData.map((b) => [b.time as number, b]));
  const slots: ({ time: UTCTimestamp } | (typeof barData)[number])[] = [];
  for (let t = open; t <= close; t = (t + step) as UTCTimestamp) slots.push(byTime.get(t) ?? { time: t });
  const onGrid = new Set(slots.map((x) => x.time as number));
  for (const b of barData) if (!onGrid.has(b.time as number)) slots.push(b);
  slots.sort((a, b) => (a.time as number) - (b.time as number));
  bars.setData(slots);

  let foreignLine: ISeriesApi<"Line"> | null = null;
  // #434 주체별 마지막 값 라벨 가까운 축 눈금은 비운다 — "개인 16,666" 뒤로 눈금 "13,141" 이 비쳐 겹쳐 보였다
  const labelled: ISeriesApi<"Line">[] = [];
  const amount = (v: number) => Math.round(v).toLocaleString("ko-KR");
  const priceFormat = { type: "custom" as const, minMove: 1, formatter: amount, tickmarksFormatter: ticksClearOfLabels(() => labelled, amount) };
  for (const l of pane.lines) {
    const label = WHO.find((w) => w.key === l.who)!.label;
    const line = chart.addSeries(LineSeries, { color: l.color, lineWidth: 2, priceScaleId: "right", priceLineVisible: false, title: label, priceFormat });
    labelled.push(line);
    line.setData(l.points.filter((p) => p.v != null).map((p) => ({ time: kstTime(p.t), value: p.v as number })));
    if (l.who === "foreign") foreignLine = line;
  }

  // 전향 마커: 외국인 선이 켜져 있을 때 그 선 위에. 좁은 화면(휴대폰)은 글자가 오른쪽 끝에서 잘리지 않게 "10:41 매도" 로 줄인다
  // — 값 · 전향 이름 전체는 위 요약 줄에 있다
  const narrow = el.clientWidth < 560;
  if (foreignLine) {
    const ms: SeriesMarker<Time>[] = pane.turns.map((t): SeriesMarker<Time> => ({
      time: kstTime(t.t),
      position: t.kind === "buy_turn" ? "belowBar" : "aboveBar",
      shape: t.kind === "buy_turn" ? "arrowUp" : "arrowDown",
      color: t.kind === "buy_turn" ? up : down,
      text: narrow
        ? `${hhmm(t.t)} ${t.kind === "buy_turn" ? "매수" : "매도"}`
        : `${hhmm(t.t)} ${TURN_LABEL[t.kind]}${t.value != null ? ` ${signed(t.value, pane.unit)}` : ""}`,
    })).sort((a, b) => (a.time as number) - (b.time as number));
    if (ms.length) createSeriesMarkers(foreignLine, ms);
  }
  // #581 수급 흐름 알림: 막대(격자 전체가 있는 시리즈) 위에, 알림 시각을 봉 격자로 내려 붙인다
  const warn = css("--warn");
  const toneColor = { positive: css("--ok"), neutral: css("--muted"), caution: warn, warning: css("--danger") };
  const am: SeriesMarker<Time>[] = pane.alerts.map((a): SeriesMarker<Time> => {
    const t = kstTime(a.t) as number;
    const snapped = (open + Math.floor((t - open) / step) * step) as UTCTimestamp;
    return {
      time: Math.min(Math.max(snapped, open), close) as UTCTimestamp,
      position: "aboveBar",
      // #642 tone(#641)이 있으면 그 뜻으로: 호재 초록 · 참고 회색 · 주의 주황 · 경고 진한 빨강 네모(글자 · 기호도 같이)
      shape: a.tone === "warning" || (!a.tone && a.severity === "strong") ? "square" : "circle",
      color: a.tone ? toneColor[a.tone] : a.severity === "info" ? css("--muted") : warn,
      text: narrow ? `${badgeOf(a).mark}${badgeOf(a).label}` : `${alertTime(a.t)} ${badgeOf(a).mark}${badgeOf(a).label}`,
    };
  }).sort((a, b) => (a.time as number) - (b.time as number));
  if (am.length) createSeriesMarkers(bars, am);
  chart.timeScale().fitContent();
  return { chart, anchor: bars };
}

/** #401 차트 머리: 켜진 주체마다 "● 외국인 −16,952억" */
function PaneHead({ title, values, unit }: { title: string; values: { who: Who; v: number | null }[]; unit: string }) {
  return (
    <p className="intraday-pane-head">
      <strong>{title}</strong>
      {values.map(({ who, v }) => {
        const w = WHO.find((x) => x.key === who)!;
        return (
          <span key={who} className="intraday-pane-val">
            <span className="chip-dot" aria-hidden="true" style={{ ["--chip-dot" as string]: `var(${w.color})` }} />
            {w.label} <span className="num">{v == null ? "—" : signed(v, unit)}</span>
          </span>
        );
      })}
    </p>
  );
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

function IntradayFlowViewInner() {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [data, setData] = useState<IntradayFlow | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const spotBox = useRef<HTMLDivElement>(null);
  const futBox = useRef<HTMLDivElement>(null);

  const update = (p: Partial<Prefs>) => setPrefs((cur) => { const next = { ...cur, ...p }; savePrefs(next); return next; });

  // 불러오기 + 장중 자동 갱신(봉 간격에 맞추되 최소 30초). 장이 끝나면 멈춘다.
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let first = true;
    let waiting = false;
    const load = () => {
      // #626 가려진 탭은 쉰다 — #678 첫 읽기는 가려져 있어도 하고, 쉬던 차례는 다시 보이는 순간 바로
      if (document.hidden && !first) { waiting = true; return; }
      first = false;
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
    const onVisible = () => {
      if (document.hidden || !waiting) return;
      waiting = false;
      load();
    };
    document.addEventListener("visibilitychange", onVisible);
    load();
    return () => { alive = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [prefs.bucket]);

  const summary = useMemo(() => (data ? summarize(data, prefs.spot) : null), [data, prefs.spot]);
  // #581 오늘 알림 가운데 이 차트의 시장 · 날짜의 수급 흐름 변화(전향은 위의 전향 마커가 이미 그린다)
  const { data: alertDay } = useTodayAlerts();
  const shifts = useMemo(() => (alertDay && data && alertDay.date === data.date
    ? alertDay.items.filter((a) => a.kind === "flow_shift") : []), [alertDay, data]);

  // #401 차트 2개(현물 · 선물). 십자선은 한쪽에서 가리킨 시각을 다른 쪽에도 그린다.
  useEffect(() => {
    const se = spotBox.current, fe = futBox.current;
    if (!se || !fe || !data) return;
    const on = WHO.filter((w) => prefs.who[w.key]);
    const rows = data.spot[prefs.spot];
    const spot = drawPane(se, data.date, data.bucket_min, {
      lines: on.map((w) => ({ who: w.key, color: css(w.color), points: rows.map((b) => ({ t: b.t, v: b[w.key] })) })),
      deltas: rows.map((b) => ({ t: b.t, v: b.foreign_delta })),
      turns: data.turns.filter((t) => t.market === prefs.spot), unit: "억",
      alerts: shifts.filter((a) => a.market === prefs.spot),
    });
    const fut = drawPane(fe, data.date, data.bucket_min, {
      lines: on.map((w) => ({ who: w.key, color: css(w.color), points: data.futures.map((b) => ({ t: b.t, v: b[FUT_KEY[w.key]] as number | null })) })),
      deltas: data.futures.map((b) => ({ t: b.t, v: b.foreign_delta })),
      turns: data.turns.filter((t) => t.market === "futures"), unit: "계약",
      alerts: shifts.filter((a) => a.market === "futures"),
    });
    const link = (from: typeof spot, to: typeof spot) => from.chart.subscribeCrosshairMove((p) => {
      if (p.time === undefined) to.chart.clearCrosshairPosition();
      else to.chart.setCrosshairPosition(0, p.time, to.anchor);
    });
    link(spot, fut);
    link(fut, spot);
    return () => { spot.chart.remove(); fut.chart.remove(); };
  }, [data, prefs.spot, prefs.who, shifts]);

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
          <PaneHead title={`현물 ${prefs.spot === "kospi" ? "코스피" : "코스닥"}`} unit="억"
            values={WHO.filter((w) => prefs.who[w.key]).map((w) => ({ who: w.key, v: lastValue(data.spot[prefs.spot], (b) => b[w.key]) }))} />
          <div className="intraday-chart intraday-chart-pane" ref={spotBox} aria-label="오늘 장중 현물 수급 차트(억원)" />
          <PaneHead title="코스피200 선물" unit="계약"
            values={WHO.filter((w) => prefs.who[w.key]).map((w) => ({ who: w.key, v: lastValue(data.futures, (b) => b[FUT_KEY[w.key]] as number | null) }))} />
          <div className="intraday-chart intraday-chart-pane" ref={futBox} aria-label="오늘 장중 선물 수급 차트(계약)" />
          <p className="muted small">선 = 주체별 오늘 누적 순매수(현물 억원 · 선물 계약) · 막대 = 외국인 {data.bucket_min}분 순매수 · 화살표 = 외국인 전향 · ● ■ = 수급 흐름 알림(■ 강함)</p>
        </>
      )}
      {!data && !failed && <p className="muted small">불러오는 중…</p>}
      {/* #581 오늘 알림 타임라인 — 장이 끝나도 · 수급을 못 받아도 보인다 */}
      <AlertTimeline />
    </div>
  );
}

/** #627 테마(다크 · 라이트)가 바뀌면 새 색으로 차트를 다시 만든다 — 차트 라이브러리는 CSS 변수를 직접 못 받는다 */
export default function IntradayFlowView() {
  const scheme = useColorScheme();
  return <IntradayFlowViewInner key={scheme} />;
}
