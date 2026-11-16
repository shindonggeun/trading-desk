import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  type IChartApi,
  type IPriceLine,
  type MouseEventParams,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { api, type CandleInterval, type Candles, type OscillatorView, type OscReading, type ReviewRow } from "../api";
import { price } from "../format";
import FillsTable from "./FillsTable";
import { drawEntry, useEntryNow } from "./chartEntry";
import EntrySummary from "./EntrySummary";
import {
  buyZoneDrawing, keepAxisLabelsApart, nearestLevels, planBands, playbookDrawing, PriceBands, srBands, stopInsideZone,
  volumeProfileDrawing, withAlpha, zoneEdges,
} from "./chartBands";
import type { BottomZone, ChartPattern, FibLevel, Fibonacci, Plan, Positioning, Projection, SrLevel, SupportResistance, Trendline, VolumeProfile } from "../types";
import { futureKeys, PROJECTION_HORIZON } from "./chartProjection";

export type Interval = Exclude<CandleInterval, "1m">;
const INTERVALS: { iv: Interval; label: string; count: number }[] = [
  // 분봉은 야후 보관 한도 안에서(5 · 15 · 30분 60일, 1 · 4시간 730일), 일봉 이상은 백엔드 최대 500개 안에서.
  { iv: "5m", label: "5분", count: 300 },
  { iv: "15m", label: "15분", count: 300 },
  { iv: "30m", label: "30분", count: 300 },
  { iv: "60m", label: "1시간", count: 300 },
  { iv: "240m", label: "4시간", count: 250 },
  { iv: "1d", label: "일", count: 130 },
  { iv: "1w", label: "주", count: 104 },
  { iv: "1M", label: "월", count: 120 },
  { iv: "1y", label: "년", count: 30 },
];
const INTRADAY = new Set<Interval>(["5m", "15m", "30m", "60m", "240m"]);
// 주 · 월 · 년봉은 몇 배씩 오른 긴 기간이라 로그 눈금 — 0 근처에서 시작해도 축이 음수로 내려가지 않는다.
const LOG_SCALE = new Set<Interval>(["1w", "1M", "1y"]);
const LABEL = Object.fromEntries(INTERVALS.map((i) => [i.iv, i.label])) as Record<Interval, string>;
const COUNT = Object.fromEntries(INTERVALS.map((i) => [i.iv, i.count])) as Record<Interval, number>;
// #206 왼쪽 끝까지 끌면 과거 봉을 두 배씩 더 받는다 — 백엔드 최대(prices.MAX_COUNT)까지.
const MAX_BARS = 2000;
// 보이는 범위의 왼쪽 끝이 첫 봉에서 이만큼 안쪽이면 더 받는다
const OLDER_EDGE = 5;

type Show = { levels: boolean; pb: boolean; sr: boolean; all: boolean; vp: boolean; trend: boolean; pattern: boolean; proj: boolean; fib: boolean; fills: boolean; analysis: boolean; rsi: boolean; opt: boolean; stoch: boolean; bb: boolean; osc: boolean; bz: boolean; entry: boolean };
const SHOW_LABEL: Record<keyof Show, string> = {
  levels: "타점선", pb: "플레이북", sr: "지지 · 저항", all: "모든 지지 · 저항", vp: "매물대", trend: "추세선", pattern: "삼각수렴 · 쐐기", proj: "예상도", fib: "피보나치", fills: "매수 · 매도", analysis: "분석일", rsi: "RSI",
  opt: "옵션 벽 · 맥스 페인",
  bz: "인생 타점 줍줍",
  entry: "지금 타점(최적)",
  stoch: "스토캐스틱", bb: "볼린저밴드(20)", osc: "과열 · 침체 표시",
};
// #158 옵션 벽: 미국 상장 종목만(Cboe), 현재가에서 이 범위 안의 벽만 그린다(멀리 있는 벽이 가격 축을 늘이지 않게)
const US_TICKER = /^[A-Z]{1,6}(?:[.-][A-Z])?$/;
const WALL_RANGE = 0.15;
const WALLS_SHOWN = 2;
// #98: 지지 · 저항은 가까운 3개씩만, 피보나치는 켤 때만 — 차트가 선으로 덮이지 않게
const DEFAULT_SHOW: Show = { levels: true, pb: true, sr: true, all: false, vp: true, trend: true, pattern: false, proj: false, fib: false, fills: true, analysis: true, rsi: true, opt: true, stoch: true, bb: false, osc: true, bz: true, entry: true };
// #298 표시 묶음: 가격 칸 위의 선 · 표시만 한 번에 바꾼다(RSI · 스토캐스틱 칸은 그대로). 체크를 손으로 바꾸면 "직접".
// #313 "타점 보기" = 지금가 기준 최적 타점 + 분석 타점선 · 체결 · 분석일만(처음 여는 사람의 기본)
type Preset = "entry" | "sr" | "trend" | "all" | "custom";
const PRESET_LABEL: Record<Preset, string> = { entry: "타점 보기", sr: "지지·저항만", trend: "추세·패턴", all: "전부", custom: "직접" };
const OVERLAYS: (keyof Show)[] = ["levels", "pb", "sr", "all", "vp", "trend", "pattern", "proj", "fib", "fills", "analysis", "opt", "bb", "osc", "bz", "entry"];
const PRESET_ON: Record<Exclude<Preset, "custom">, (keyof Show)[]> = {
  entry: ["entry", "levels", "fills", "analysis"],
  sr: ["sr", "vp"],
  trend: ["sr", "trend", "pattern", "fib"],
  all: OVERLAYS,
};
function applyPreset(show: Show, p: Exclude<Preset, "custom">): Show {
  const out = { ...show };
  for (const k of OVERLAYS) out[k] = PRESET_ON[p].includes(k);
  return out;
}
const NEAR_LEVELS = 3;
/** RSI 패널 높이 — 낮은 화면(가로 모드 · 크게 보기)에서 가격 패널을 누르지 않게 차트 높이에 비례 */
const rsiHeight = (h: number) => Math.round(Math.min(110, Math.max(56, h * 0.22)));

// 주기 · 표시 선택은 이 브라우저에 기억한다(사생활 모드 · 막힌 저장소에서도 화면은 그대로 동작).
const PREFS_KEY = "desk.priceChart.v2";
const OLD_PREFS_KEY = "desk.priceChart.v1";   // #98 전 — 피보나치는 기본 꺼짐으로 바뀌어 옮기지 않는다
function loadPrefs(): { interval: Interval; show: Show; preset: Preset; custom: Show } {
  try {
    const stored = localStorage.getItem(PREFS_KEY);
    if (stored == null && localStorage.getItem(OLD_PREFS_KEY) == null) {
      return { interval: "1d", show: applyPreset({ ...DEFAULT_SHOW }, "entry"), preset: "entry", custom: { ...DEFAULT_SHOW } };
    }
    const raw = JSON.parse(stored ?? localStorage.getItem(OLD_PREFS_KEY) ?? "{}");
    if (stored == null && raw.show) delete raw.show.fib;
    const interval = INTERVALS.some((i) => i.iv === raw.interval) ? (raw.interval as Interval) : "1d";
    const show = { ...DEFAULT_SHOW };
    for (const k of Object.keys(DEFAULT_SHOW) as (keyof Show)[]) if (typeof raw.show?.[k] === "boolean") show[k] = raw.show[k];
    const preset: Preset = raw.preset in PRESET_LABEL ? raw.preset : "custom";
    // #313 저장된 설정에 entry 가 없으면(이 기능 전) 고른 묶음을 따른다 — 버튼과 화면이 어긋나지 않게
    if (typeof raw.show?.entry !== "boolean") show.entry = preset !== "custom" && PRESET_ON[preset].includes("entry");
    const custom = { ...show };
    for (const k of Object.keys(DEFAULT_SHOW) as (keyof Show)[]) if (typeof raw.custom?.[k] === "boolean") custom[k] = raw.custom[k];
    if (typeof raw.custom?.entry !== "boolean") custom.entry = false;
    return { interval, show, preset, custom };
  } catch {
    return { interval: "1d", show: applyPreset({ ...DEFAULT_SHOW }, "entry"), preset: "entry" as Preset, custom: { ...DEFAULT_SHOW } };
  }
}
function savePrefs(interval: Interval, show: Show, preset: Preset, custom: Show) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ interval, show, preset, custom }));
  } catch {
    /* 저장소가 막혀 있으면 기억만 못 한다 */
  }
}

// 마지막 종가에서 이만큼 넘게 떨어진 계획선(분할 전 옛 분석 등)은 축 범위 계산에서 뺀다 — 봉이 납작해지지 않게.
const AUTOSCALE_MAX_GAP = 0.5;
// 체결 마커는 복기 API(브로커 체결 + 종목별 일봉)를 쓴다. 분석 화면을 오갈 때마다 다시 부르지 않게 잠깐 기억한다.
const FILLS_TTL_MS = 60_000;
let fillsMemo: { at: number; rows: Promise<ReviewRow[]> } | null = null;

/** 체결이 든 봉 — 분봉은 체결 시각이 든 봉(장 전 · 시간외는 그날 첫 봉, 그날 봉이 없으면 null), 일봉 이상은 그 거래일이 든 봉. */
function fillBar(f: ReviewRow, rows: { key: Key; day?: string }[], intraday: boolean, ticker: string): Key | null {
  const keys = rows.map((r) => r.key);
  const fillOn = marketDay(f.filled_at, ticker);
  const at = barFor(keys, intraday ? kstStamp(f.filled_at) : fillOn);
  if (at == null) return null;
  if (intraday && rows.find((r) => r.key === at)?.day !== fillOn) return rows.find((r) => r.day === fillOn)?.key ?? null;
  return at;
}

function recentFills(): Promise<ReviewRow[]> {
  if (!fillsMemo || Date.now() - fillsMemo.at > FILLS_TTL_MS) {
    const rows = api.review(180).then((r) => r.fills).catch(() => [] as ReviewRow[]);
    fillsMemo = { at: Date.now(), rows };
  }
  return fillsMemo.rows;
}

const isKorean = (ticker: string) => /\.(KS|KQ)$/i.test(ticker);
const baseCode = (ticker: string) => ticker.split(".")[0].toUpperCase();
const KST_SEC = 9 * 3600;

/** 봉 시각(UTC) → 한국 날짜. 국장 자정(KST) · 미장 자정(ET) 모두 그 거래일 날짜가 된다. */
function kstDay(iso: string): string {
  return new Date(new Date(iso).getTime() + KST_SEC * 1000).toISOString().slice(0, 10);
}

const ET_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

/** 어떤 시각이 그 종목의 몇 일 거래인지. 미장 정규장은 KST 로 자정을 넘기므로 뉴욕 날짜로 본다. */
function marketDay(iso: string, ticker: string): string {
  return isKorean(ticker) ? kstDay(iso) : ET_DAY.format(new Date(iso));
}

/** 분봉 시간축 값 — 차트는 UTC 로 그리므로 9시간을 더해 눈금 · 십자선이 한국 시각으로 보이게 한다. */
function kstStamp(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000) + KST_SEC;
}

type Key = string | number; // 일봉 이상: "YYYY-MM-DD", 분봉: 한국 시각 초
type Row = { key: Key; day: string; bar: Candles["bars"][number] };

/** 봉을 시간축 값으로 — 같은 값은 마지막 봉만 남기고 오름차순(야후가 진행 중 봉을 덧붙여도 setData 가 throw 하지 않게). */
function toRows(bars: Candles["bars"], intraday: boolean, ticker: string): Row[] {
  const byKey = new Map<Key, Row>();
  for (const bar of bars) {
    const key = intraday ? kstStamp(bar.at) : kstDay(bar.at);
    byKey.set(key, { key, day: intraday ? marketDay(bar.at, ticker) : (key as string), bar });
  }
  return [...byKey.values()].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
}

/** 그 시점이 들어가는 봉(그 시점 이하의 마지막 봉). 차트 앞이면 null. */
/** #206 과열 · 침체 판정의 이름과 극단 마커 문구 */
const OSC_ZONE: Record<"hot" | "cold" | "neutral", string> = { hot: "위험 구간(과열)", cold: "바닥권(침체)", neutral: "중립" };
const pct = (x: number | undefined | null) => (x == null ? "" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(0)}%`);
function oscLine(r: OscReading | null | undefined, label: string): string | null {
  if (!r || !r.zone) return null;
  return `${label} ${OSC_ZONE[r.zone]}${r.reason ? ` — ${r.reason}` : ""}`;
}

function barFor(keys: Key[], at: Key): Key | null {
  let hit: Key | null = null;
  for (const k of keys) {
    if (k <= at) hit = k;
    else break;
  }
  return hit;
}

function css(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

type SrMode = "plan" | "now";
const SR_LABEL: Record<SrMode, string> = { plan: "분석 당시", now: "지금" };
const TF_LABEL: Record<SrLevel["timeframe"], string> = { "1d": "일봉", "1w": "주봉" };
/** 추세선의 봉 주기 — 분봉 지평이면 5m · 60m 도 온다. */
function tfLabel(tf: string): string {
  if (tf in TF_LABEL) return TF_LABEL[tf as SrLevel["timeframe"]];
  const m = /^(\d+)m$/.exec(tf);
  return m ? `${m[1]}분봉` : tf;
}
const TREND_NAME: Record<Trendline["kind"], string> = { support: "상승 지지선", resistance: "하락 저항선" };
const TREND_STATE: Record<Trendline["state"], string> = { intact: "유지", testing: "시험 중", broken: "이탈" };
const PATTERN_STATE: Record<ChartPattern["state"], string> = { converging: "수렴 중", broke_up: "위로 이탈", broke_down: "아래로 이탈" };
const TEXTBOOK: Record<ChartPattern["expected"], string> = { up: "위", down: "아래", none: "정해진 쪽 없음" };
/** #297 측정 한 줄: 검증 구간이 무작위로 섞은 가격과 다른지 */
function patternRecord(p: ChartPattern): string {
  const s = p.stats;
  if (!s) return "이 주기는 과거 측정이 없습니다 — 선으로만 보세요.";
  const v = s.valid, b = s.null;
  const head = v?.expected_pct != null && b?.expected_pct != null
    ? `과거 검증 ${v.n}건 중 교과서 방향(${TEXTBOOK[p.expected]}) 이탈 ${v.expected_pct}% · 무작위로 섞은 가격 ${b.expected_pct}%`
    : `과거 ${s.n}건 측정`;
  const verdict = s.verdict === "edge" ? `→ 우위 있음(${s.edge_pct && s.edge_pct > 0 ? "+" : ""}${s.edge_pct}%p)`
    : s.verdict === "unstable" ? "→ 학습 · 검증 구간이 엇갈려 믿기 어려움"
      : "→ 무작위와 차이 없음: 방향 근거가 아니라 지지 · 저항선으로만";
  return `${head} ${verdict}`;
}
const signed = (v: number) => `${v > 0 ? "+" : ""}${v}`;

/**
 * 추세선의 그 시각 가격. 선은 로그 가격에서 직선이라 앵커 · 끝 세 점 사이를 로그로 시간 비례해 잇는다 —
 * 보는 차트 주기(주봉 · 분봉)가 선을 그은 주기와 달라도 같은 자리에 놓인다.
 */
function lineAt(t: Trendline, ms: number): number {
  const pts = [...t.anchors, t.end]
    .map((p) => ({ ms: Date.parse(p.at), lp: Math.log(p.price) }))
    .sort((a, b) => a.ms - b.ms);
  let [a, b] = [pts[0], pts[1]];
  for (let i = 1; i < pts.length - 1; i++) if (ms > pts[i].ms) [a, b] = [pts[i], pts[i + 1]];
  if (b.ms === a.ms) return Math.exp(b.lp);
  return Math.exp(a.lp + ((b.lp - a.lp) * (ms - a.ms)) / (b.ms - a.ms));
}

/** 분봉 주기(1m · 5m · 60m …) — 1M(월봉)과 구별 */
const isIntradayTf = (tf: string) => /^\d+m$/.test(tf);

type Hover = { x: number; y: number; key: string } & (
  | { type: "sr"; level: SrLevel }
  | { type: "trend"; line: Trendline; here: number }
  | { type: "fib"; level: FibLevel; fib: Fibonacci }
  | { type: "vp"; vp: VolumeProfile }
);

/** 지지 · 저항은 그 계산의 기준가 위 · 아래로 나뉜다 — 어느 가격 기준인지 같이 적는다. */
function srNote(mode: SrMode, src: Partial<SupportResistance> | null): string {
  const ref = src?.reference_price != null ? price(src.reference_price) : null;
  const kind = src?.reference === "live" ? "실시간가" : "종가";
  const day = src?.as_of ? ` · ${kstDay(src.as_of)}` : "";
  const base = ref ? `기준가 ${ref}(${kind}${day}) 아래는 지지, 위는 저항` : "기준가 아래는 지지, 위는 저항";
  return mode === "plan"
    ? `분석할 때 에이전트가 받은 구역 — ${base}. 지금 가격과는 위아래가 다를 수 있습니다. 가격 기록의 계산이지 예측이 아닙니다.`
    : `지금 봉 기준 — ${base}. 가격 기록의 계산이지 예측이 아닙니다.`;
}

const TIP_W = 260;
/** 툴팁을 차트 상자 안에 — 오른쪽 끝에선 왼쪽으로, 위쪽 끝에선 선 아래로. */
function tipPosition(h: { x: number; y: number }, el: HTMLDivElement | null): { left: number; top: number } {
  const width = el?.clientWidth ?? TIP_W + 8;
  const left = Math.min(Math.max(4, h.x - TIP_W / 2), Math.max(4, width - TIP_W - 4));
  const top = h.y > 80 ? h.y - 72 : h.y + 12;
  return { left, top };
}

type Drawn = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  rsi: ISeriesApi<"Line"> | null;
  /** #206 스토캐스틱 %K (아래 패널) */
  stoch: ISeriesApi<"Line"> | null;
  markers: ISeriesMarkersPluginApi<Time>;
  rows: Row[];
  intraday: boolean;
  /** 그린 봉 주기 — 같은 주기의 추세선은 봉 순서로 */
  interval: Interval;
  lastClose: number;
  /** chart.remove() 뒤에는 false — 지운 차트에 선을 지우면 다시 그리기가 예약돼 throw 한다. */
  alive: { current: boolean };
};

/** 계획의 진입 · 손절 · 목표 말고 더 그을 가로선(관망 계획의 롱 · 숏 진입 조건 등, #68). */
export interface ExtraLine {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

/**
 * 분석 화면의 가격 차트 — 분봉 · 일 · 주 · 월 · 년 캔들 · 거래량, RSI(14) 보조 차트,
 * 진입 · 손절 · 목표선, 지지 · 저항선, 분석일 · 내 체결 마커(#18 · #25 · #61). 겹친 표시는 하나씩 켜고 끈다.
 */
export default function PriceChart({ ticker, tradeDate, plan, title, extraLines, initialInterval, lists = true, startExpanded = false, onClose, notice, onIntervalChange }: {
  ticker: string;
  tradeDate: string;
  plan: Plan | null;
  /** 머리글 이름(기본 "가격 차트") — 분석 결과 차트가 본주 · 레버리지를 구분한다. */
  title?: string;
  extraLines?: ExtraLine[];
  /** 처음 봉 주기 — 분석 결과 차트는 계획의 시간축에 맞춘다. 없으면 마지막으로 고른 주기. */
  initialInterval?: Interval;
  /** 차트 아래 지지 · 저항 · 추세선 · 피보나치 글 목록. 분석 화면(#99)은 근거 탭에 따로 보여 주고 끈다. */
  lists?: boolean;
  /** 크게 보기(#97)로 연다 — 차트 주소(#/chart/…) 화면. */
  startExpanded?: boolean;
  /** 크게 보기를 닫을 때(차트 주소 화면은 이전 화면으로 돌아간다). 없으면 카드로 접힌다. */
  onClose?: () => void;
  /** 머리줄 아래 한 줄 안내(차트 주소 화면: 분석 기록을 못 찾았을 때 등). */
  notice?: string;
  /** #220 봉 주기를 바꿀 때마다 — 차트 탭이 주소(iv=)를 맞춘다 */
  onIntervalChange?: (interval: Interval) => void;
}) {
  const [prefs] = useState(loadPrefs);
  const [interval, setBarInterval] = useState<Interval>(initialInterval ?? prefs.interval);
  const [show, setShow] = useState<Show>(prefs.show);
  const [preset, setPreset] = useState<Preset>(prefs.preset);
  const [custom, setCustom] = useState<Show>(prefs.custom);   // "직접" 으로 돌아오면 이 고름으로
  const [candles, setCandles] = useState<Candles | null>(null);
  // 과거 봉 배수(1 · 2 · 4 …) — 종목 · 주기가 바뀌면 처음으로
  const [older, setOlder] = useState({ key: "", times: 1 });
  const olderKey = `${ticker}|${interval}`;
  const olderKeyRef = useRef(olderKey);
  olderKeyRef.current = olderKey;
  const times = older.key === olderKey ? older.times : 1;
  const wanted = Math.min(COUNT[interval] * times, MAX_BARS);
  // 더 받은 뒤 새 차트에서 보던 자리를 지키려고: 받기 전 보이던 논리 범위와 봉 수
  const keepView = useRef<{ from: number; to: number; bars: number } | null>(null);
  const loadingOlder = useRef(false);
  const [fills, setFills] = useState<ReviewRow[]>([]);
  // #334 체결 표에서 고른 체결(filled_at) — 그 봉의 마커를 크게
  const [pickedFill, setPickedFill] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  // #206 과열 · 침체 판정(일봉 · 주봉만 — 분봉 · 월 · 년봉은 일봉 판정을 배지로만)
  const [osc, setOsc] = useState<OscillatorView | null>(null);
  const oscInterval: "1d" | "1w" = interval === "1w" ? "1w" : "1d";
  useEffect(() => {
    if (!show.osc) return setOsc(null);
    let live = true;
    api.oscillator(ticker, oscInterval).then((v) => live && setOsc(v), () => live && setOsc(null));
    return () => {
      live = false;
    };
  }, [ticker, oscInterval, show.osc]);
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const box = useRef<HTMLDivElement>(null);
  // 한 화면에 차트가 여럿(본주 · 레버리지)이어도 범례 id 가 겹치지 않게
  const legendId = useId();
  // 지지 · 저항 — 기본은 그 분석이 에이전트에게 준 레벨(계획에 저장됨), 없으면(옛 분석) 지금 봉 기준.
  const saved = plan?.support_resistance?.levels?.length ? plan.support_resistance : null;
  const [srMode, setSrMode] = useState<SrMode | null>(null);
  const mode: SrMode = srMode ?? (saved ? "plan" : "now");
  const [nowSr, setNowSr] = useState<SupportResistance | null>(null);
  const [srFailed, setSrFailed] = useState(false);
  const [hover, setHover] = useState<Hover | null>(null);
  // 매 렌더 새 배열이면 선을 계속 다시 그린다 — 근거가 바뀔 때만.
  const sr: SrLevel[] = useMemo(
    () => (!show.sr ? [] : mode === "plan" ? saved?.levels ?? [] : nowSr?.levels ?? []),
    [show.sr, mode, saved, nowSr],
  );
  const srSource = mode === "plan" ? saved : nowSr;
  // 차트에 띠로 그릴 구역 — 기준가에서 가까운 지지 · 저항 3개씩(표시 메뉴 "모든 지지 · 저항" 이면 전부). 목록은 전부.
  const srShown: SrLevel[] = useMemo(
    () => (srSource?.reference_price != null ? nearestLevels(sr, srSource.reference_price, NEAR_LEVELS, show.all) : sr),
    [sr, srSource, show.all],
  );
  // 추세선 · 피보나치(#73) — 지지 · 저항과 같은 출처(분석 당시 / 지금)
  const trendLines: Trendline[] = useMemo(() => (show.trend ? srSource?.trendlines ?? [] : []), [show.trend, srSource]);
  const patterns: ChartPattern[] = useMemo(() => (show.pattern ? srSource?.patterns ?? [] : []), [show.pattern, srSource]);
  // #300 예상도: 이 봉 주기와 짝인 시간축만(일봉 · 1시간 · 5분). 모델 설명이 오는 중이면 20초 뒤 한 번 더 읽는다.
  const projFor = PROJECTION_HORIZON[interval];
  const [proj, setProj] = useState<Projection | null>(null);
  const [projFailed, setProjFailed] = useState(false);
  useEffect(() => {
    setProj(null);
    setProjFailed(false);
    if (!show.proj || !projFor) return;
    let alive = true;
    let again: number | undefined;
    const load = (tries: number) =>
      api.projection(ticker, projFor.horizon)
        .then((p) => {
          if (!alive) return;
          setProj(p);
          if (p.note_pending && tries < 3) again = window.setTimeout(() => load(tries + 1), 20_000);
        })
        .catch(() => alive && setProjFailed(true));
    load(0);
    return () => {
      alive = false;
      window.clearTimeout(again);
    };
  }, [show.proj, projFor, ticker]);
  const fib: Fibonacci | null = show.fib ? srSource?.fibonacci ?? null : null;
  // 매물대(#108) — 지지 · 저항과 같은 출처, 거래량이 없는 지수 · 옛 분석은 없다
  const vp: VolumeProfile | null = show.vp && srSource?.volume_profile?.bins.length ? srSource.volume_profile : null;
  const wantSr = show.sr || show.trend || show.pattern || show.fib || show.vp;
  // 분석 당시 값이면 "지금" 이라 하지 않는다 — 선은 오늘까지 그어도 거리 · 상태는 그때 기준
  const when = mode === "plan" ? "분석 당시" : "지금";
  const [trendDrawn, setTrendDrawn] = useState<{ line: Trendline; series: ISeriesApi<"Line"> }[]>([]);

  useEffect(() => savePrefs(interval, show, preset, custom), [interval, show, preset, custom]);
  useEffect(() => onIntervalChange?.(interval), [interval, onIntervalChange]);
  const toggle = (k: keyof Show) => {
    const next = { ...show, [k]: !show[k] };
    setShow(next);
    if (OVERLAYS.includes(k)) {   // 손으로 고르면 그 고름이 "직접"
      setPreset("custom");
      setCustom(next);
    }
  };
  const pickPreset = (p: Preset) => {
    setPreset(p);
    setShow((s) => (p === "custom" ? { ...custom, rsi: s.rsi, stoch: s.stoch } : applyPreset(s, p)));
  };

  // #158 옵션 벽 · 맥스 페인(#154 positioning) — 켤 때만 읽는다
  const [optionData, setOptionData] = useState<Positioning["options"] | null>(null);
  useEffect(() => {
    setOptionData(null);
    if (!show.opt || !US_TICKER.test(ticker)) return;
    let alive = true;
    api.positioning(ticker).then((p) => alive && setOptionData(p.options), () => {});
    return () => { alive = false; };
  }, [ticker, show.opt]);

  // #241 인생 타점(장기 바닥권, #240) 분할 매수 선 — "접근" 이상일 때만 그린다
  const [bottom, setBottom] = useState<BottomZone | null>(null);
  useEffect(() => {
    setBottom(null);
    if (!show.bz) return;
    let alive = true;
    api.bottomZone(ticker).then((z) => alive && setBottom(z), () => {});
    return () => { alive = false; };
  }, [ticker, show.bz]);
  // #313 지금가 기준 최적 타점(#312) — 분석 없이도
  const { entry, failed: entryFailed } = useEntryNow(ticker, show.entry);

  // 크게 보기(#97): 브라우저 전체 화면을 쓰고, 안 되면(iOS Safari 등) 화면을 꽉 채운 고정 레이어로.
  const section = useRef<HTMLElement>(null);
  const [expanded, setExpanded] = useState(startExpanded);
  const native = useRef(false);
  const closing = useRef(false); // Esc 와 fullscreenchange 가 같이 와도 한 번만(onClose 가 뒤로 가기라 두 번이면 두 화면을 건넌다)
  const close = () => {
    if (closing.current) return;
    closing.current = true;
    if (native.current && document.fullscreenElement) document.exitFullscreen().catch(() => {});
    native.current = false;
    if (onClose) onClose();
    else setExpanded(false);
  };
  const open = () => {
    closing.current = false;
    setExpanded(true);
    const el = section.current;
    if (el?.requestFullscreen && document.fullscreenEnabled) {
      el.requestFullscreen({ navigationUI: "hide" }).then(() => (native.current = true), () => {});
    }
  };
  useEffect(() => {
    if (!expanded) return;
    // 브라우저 전체 화면을 Esc 로 나가면 크게 보기도 끝낸다
    const onFs = () => {
      if (native.current && !document.fullscreenElement) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (menu.current?.open) {
        menu.current.open = false; // 열린 표시 메뉴만 닫는다
        return;
      }
      close();
    };
    document.addEventListener("fullscreenchange", onFs);
    document.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // 뒤 화면이 같이 스크롤되지 않게
    return () => {
      document.removeEventListener("fullscreenchange", onFs);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  // 표시 메뉴는 바깥을 누르면 닫는다(카드 · 크게 보기 모두)
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const m = menu.current;
      if (m?.open && !m.contains(e.target as Node)) m.open = false;
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, []);

  // 십자선 범례: 커서(모바일은 누른 채로) 아래 봉의 시가 · 고가 · 저가 · 종가 · 거래량 · RSI.
  // 마우스가 움직일 때마다 렌더하지 않게 DOM 을 직접 고친다.
  const ohlc = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!drawn) return;
    const { chart, series, volume, rsi, stoch, rows } = drawn;
    const last = rows[rows.length - 1];
    const write = (o: number, h: number, l: number, c: number, v: number | null, r: number | null, prev: number | null,
      k: number | null = null, d: number | null = null) => {
      const el = ohlc.current;
      if (!el) return;
      const chg = prev ? ((c - prev) / prev) * 100 : null;
      const tone = c >= o ? "up" : "down";
      el.replaceChildren();
      const add = (label: string, value: string, cls = "") => {
        const span = document.createElement("span");
        if (label) {
          const i = document.createElement("i");
          i.textContent = label;
          span.append(i, " ");
        }
        const strong = document.createElement("b");
        strong.textContent = value;
        if (cls) strong.className = cls;
        span.append(strong);
        el.appendChild(span);
      };
      add("시", price(o), tone); add("고", price(h), tone); add("저", price(l), tone); add("종", price(c), tone);
      if (chg != null) add("", `${chg > 0 ? "+" : ""}${chg.toFixed(2)}%`, chg >= 0 ? "up" : "down");
      if (v != null) add("거래량", v.toLocaleString("ko-KR"));
      if (r != null) add("RSI", r.toFixed(1));
      if (stoch && k != null) add("스토캐스틱", `${k.toFixed(0)}${d != null ? ` / ${d.toFixed(0)}` : ""}`);
    };
    const showLast = () => {
      const prev = rows.length > 1 ? rows[rows.length - 2].bar.close : null;
      write(last.bar.open, last.bar.high, last.bar.low, last.bar.close, last.bar.volume, last.bar.rsi ?? null, prev,
        last.bar.stoch_k ?? null, last.bar.stoch_d ?? null);
    };
    showLast();
    const onMove = (param: MouseEventParams<Time>) => {
      const bar = param.seriesData.get(series) as { open?: number; high?: number; low?: number; close?: number } | undefined;
      if (!param.point || bar?.close == null) return showLast();
      const v = param.seriesData.get(volume) as { value?: number } | undefined;
      const r = rsi ? (param.seriesData.get(rsi) as { value?: number } | undefined) : undefined;
      const i = param.logical != null ? Math.round(param.logical as number) : -1;
      const prev = i > 0 && rows[i - 1] ? rows[i - 1].bar.close : null;
      const here = i >= 0 ? rows[i]?.bar : undefined;
      write(bar.open ?? bar.close, bar.high ?? bar.close, bar.low ?? bar.close, bar.close, v?.value ?? null, r?.value ?? null, prev,
        here?.stoch_k ?? null, here?.stoch_d ?? null);
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      ohlc.current?.replaceChildren(); // 다른 주기를 불러오는 동안 이전 봉 값을 남기지 않게
      if (drawn.alive.current) chart.unsubscribeCrosshairMove(onMove);
    };
  }, [drawn]);

  useEffect(() => {
    if (!wantSr || mode !== "now" || nowSr) return;
    let live = true;
    setSrFailed(false);
    api.levels(ticker).then((v) => live && setNowSr(v)).catch(() => live && setSrFailed(true));
    return () => {
      live = false;
    };
  }, [wantSr, mode, nowSr, ticker]);

  // RSI 를 켜고 끄면 다시 받는다(서버가 30초 캐시) — RSI 는 봉과 같은 응답에 실린다.
  useEffect(() => {
    let live = true;
    // 과거 봉을 더 받을 땐 지금 차트를 둔 채 받는다(깜빡임 없이)
    if (times === 1) {
      keepView.current = null;
      setCandles(null);
    }
    setFailed(false);
    api
      .candles(ticker, interval, wanted,
        [...(show.rsi ? ["rsi"] : []), ...(show.stoch ? ["stoch"] : []), ...(show.bb ? ["bb20"] : [])])
      .then((c) => live && setCandles(c))
      .catch(() => live && (times === 1 ? setFailed(true) : (keepView.current = null)))
      .finally(() => {
        if (live) loadingOlder.current = false;
      });
    return () => {
      live = false;
      loadingOlder.current = false;
    };
  }, [ticker, interval, wanted, show.rsi, show.stoch, show.bb]);

  // 이 종목 체결만. 브로커가 체결 내역을 안 주거나 실패하면 마커 없이 그린다.
  useEffect(() => {
    let live = true;
    recentFills().then((rows) => live && setFills(rows.filter((f) => baseCode(f.ticker) === baseCode(ticker))));
    return () => {
      live = false;
    };
  }, [ticker]);

  // 차트 · 봉 · 거래량 · RSI — 봉이 바뀔 때만 새로 만든다(계획 · 체결이 늦게 와도 확대 · 이동 상태가 유지된다).
  useEffect(() => {
    const el = box.current;
    if (!el || !candles || candles.bars.length === 0) return;
    const iv = candles.interval as Interval;
    const intraday = INTRADAY.has(iv);
    let chart: IChartApi | null = null;
    const alive = { current: true };
    try {
      const up = css("--up", "#f2464b");
      const down = css("--down", "#3a86f8");
      const grid = css("--border", "#2a3038");
      chart = createChart(el, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: css("--muted", "#8c939d"),
          fontFamily: getComputedStyle(document.body).fontFamily,
          panes: { separatorColor: grid },
        },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
        rightPriceScale: { borderColor: grid },
        // 분봉은 시 · 분까지(시각은 한국 시각), 창 크기를 바꿔도 보던 기간 유지
        timeScale: { borderColor: grid, lockVisibleTimeRangeOnResize: true, timeVisible: intraday, secondsVisible: false },
        localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v) },
      });
      const rows = toRows(candles.bars, intraday, candles.ticker);
      const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
      const series = chart.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
      });
      series.priceScale().applyOptions({
        // 거래량은 트레이딩뷰처럼 아래쪽에 반투명하게 겹친다 — 큰 여백은 넓은 가격 범위에서 음수 눈금을 만들었다(#206)
        scaleMargins: { top: 0.08, bottom: 0.1 },
        mode: LOG_SCALE.has(iv) ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
      });
      series.setData(rows.map(({ key, bar }) => ({ time: time(key), open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volume.setData(rows.map(({ key, bar }) => ({ time: time(key), value: bar.volume, color: (bar.close >= bar.open ? up : down) + "55" })));

      // RSI(14) — 아래 패널(시간축 공유라 확대 · 이동이 같이 움직인다), 30 · 70 점선.
      let rsi: ISeriesApi<"Line"> | null = null;
      if (candles.indicators?.includes("rsi14")) {
        rsi = chart.addSeries(LineSeries, {
          color: css("--warn", "#e5a23a"), lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
          priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(0), minMove: 0.01 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        }, 1);
        // RSI 축은 늘 0~100 일반 눈금 — 주 · 월 · 년봉의 로그 눈금이 번지지 않게.
        rsi.priceScale().applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
        rsi.setData(rows.flatMap(({ key, bar }) => (bar.rsi == null ? [] : [{ time: time(key), value: bar.rsi }])));
        const muted = css("--muted", "#8c939d");
        for (const lv of [70, 30]) rsi.createPriceLine({ price: lv, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
        chart.panes()[1]?.setHeight(rsiHeight(el.clientHeight));
      }

      // #206 볼린저밴드(20, 2σ) — 가격 패널, 상 · 하단 실선 · 가운데 점선
      if (candles.indicators?.includes("bb20")) {
        const band = css("--muted", "#8c939d");
        for (const [k, style] of [["upper", LineStyle.Solid], ["mid", LineStyle.Dotted], ["lower", LineStyle.Solid]] as const) {
          const line = chart.addSeries(LineSeries, { color: band, lineWidth: 1, lineStyle: style, priceLineVisible: false,
            lastValueVisible: false, crosshairMarkerVisible: false });
          line.setData(rows.flatMap(({ key, bar }) => (bar.bb20 ? [{ time: time(key), value: bar.bb20[k] }] : [])));
        }
      }

      // #206 스토캐스틱(14, 1, 3) — RSI 아래 패널, %K 실선 · %D 점선, 20 · 80 점선
      let stoch: ISeriesApi<"Line"> | null = null;
      if (candles.indicators?.includes("stoch")) {
        const pane = rsi ? 2 : 1;
        const scale = { autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
          priceFormat: { type: "custom" as const, formatter: (v: number) => v.toFixed(0), minMove: 0.01 } };
        stoch = chart.addSeries(LineSeries, { color: css("--accent", "#3a86f8"), lineWidth: 2, priceLineVisible: false,
          lastValueVisible: true, ...scale }, pane);
        stoch.priceScale().applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
        stoch.setData(rows.flatMap(({ key, bar }) => (bar.stoch_k == null ? [] : [{ time: time(key), value: bar.stoch_k }])));
        const d = chart.addSeries(LineSeries, { color: css("--warn", "#e5a23a"), lineWidth: 1, lineStyle: LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...scale }, pane);
        d.setData(rows.flatMap(({ key, bar }) => (bar.stoch_d == null ? [] : [{ time: time(key), value: bar.stoch_d }])));
        const muted = css("--muted", "#8c939d");
        for (const lv of [80, 20]) stoch.createPriceLine({ price: lv, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
        chart.panes()[pane]?.setHeight(rsiHeight(el.clientHeight));
      }

      // 과거 봉을 더 받은 뒤면 보던 자리 그대로(앞에 붙은 봉 수만큼 밀어서), 아니면 전체
      const kept = keepView.current;
      keepView.current = null;
      if (kept) {
        const added = rows.length - kept.bars;
        chart.timeScale().setVisibleLogicalRange({ from: kept.from + added, to: kept.to + added });
      } else {
        chart.timeScale().fitContent();
      }
      // 왼쪽 끝 가까이 끌면 과거 봉을 더 — 요청만큼 다 왔을 때만(보관 한도 · 상장 이후 끝이면 멈춘다)
      const more = !candles.truncated && rows.length < MAX_BARS;
      const ts = chart.timeScale();
      if (more) {
        ts.subscribeVisibleLogicalRangeChange((r) => {
          if (!r || r.from > OLDER_EDGE || loadingOlder.current || !alive.current) return;
          loadingOlder.current = true;
          keepView.current = { from: r.from, to: r.to, bars: rows.length };
          setOlder((o) => ({ key: olderKeyRef.current, times: (o.key === olderKeyRef.current ? o.times : 1) * 2 }));
        });
      }
      setDrawn({ chart, series, volume, rsi, stoch, markers: createSeriesMarkers(series, []), rows, intraday, interval: iv, lastClose: rows[rows.length - 1].bar.close, alive });
    } catch {
      chart?.remove(); // 봉 데이터가 이상하면 앱 전체가 아니라 차트만 실패로
      setFailed(true);
      return;
    }
    const made = chart;
    return () => {
      alive.current = false;
      setDrawn(null);
      made.remove();
    };
  }, [candles]);

  // 계획선 · 마커 · 축 범위 — 차트를 다시 만들지 않고 갈아 끼운다. 끈 선은 축 범위에서도 뺀다.
  useEffect(() => {
    if (!drawn) return;
    const { series, markers, rows, intraday, lastClose } = drawn;
    const nearPrice = (v: number) => Math.abs(v - lastClose) / lastClose <= WALL_RANGE;
    const optionLines: [number, string, string, LineStyle][] = show.opt && optionData ? [
      ...optionData.call_walls.filter((w) => nearPrice(w.strike)).slice(0, WALLS_SHOWN)
        .map((w): [number, string, string, LineStyle] => [w.strike, "콜벽", css("--sr-resistance", "#b58cf0"), LineStyle.Dashed]),
      ...optionData.put_walls.filter((w) => nearPrice(w.strike)).slice(0, WALLS_SHOWN)
        .map((w): [number, string, string, LineStyle] => [w.strike, "풋벽", css("--sr-support", "#2bb3a3"), LineStyle.Dashed]),
      ...(optionData.max_pain.strike != null && nearPrice(optionData.max_pain.strike)
        ? [[optionData.max_pain.strike, "맥스페인", css("--warn", "#e5a23a"), LineStyle.SparseDotted] as [number, string, string, LineStyle]]
        : []),
    ] : [];
    const planLines: [number | null | undefined, string, string, LineStyle][] = show.levels
      ? [
          [plan?.entry_price, "진입", css("--accent", "#c9d1db"), LineStyle.Solid],
          // #311 손절 = 손실색 · 목표 = 이익색(롱 · 숏 어느 관점이든 같은 뜻)
          [plan?.stop_loss, "손절", css("--danger", "#ff6a3d"), LineStyle.Solid],
          [plan?.price_target, "목표", css("--ok", "#3fb68b"), LineStyle.Solid],
          ...(extraLines ?? []).map(
            (l): [number, string, string, LineStyle] => [l.price, l.label, l.color, l.dashed ? LineStyle.Dotted : LineStyle.Solid],
          ),
        ]
      : [];
    const made: IPriceLine[] = [];
    const labelled: IPriceLine[] = [];
    for (const [value, title, color, lineStyle] of planLines) {
      if (value == null) continue;
      const line = series.createPriceLine({ price: value, title, color, lineWidth: 2, lineStyle, axisLabelVisible: true });
      made.push(line);
      labelled.push(line);
    }
    // 옵션 벽은 축 라벨 없이 선 위 제목만 — 타점 라벨을 가리지 않고, 가격 축도 늘이지 않는다(분봉에서 봉이 납작해진다)
    for (const [value, title, color, lineStyle] of optionLines) {
      made.push(series.createPriceLine({ price: value, title, color, lineWidth: 1, lineStyle, axisLabelVisible: false }));
    }
    // #241 인생 타점 줍줍 선: 단계별 가격(비중) · 계획 접는 선 — 옵션 벽처럼 축 라벨 · 축 범위 없이
    if (show.bz && bottom?.supported && (bottom.stage === "approach" || bottom.stage === "bottom")) {
      const color = css("--long", "#f2464b");
      // #310 지금가가 이미 지나간 옛 단계는 그리지 않는다
      for (const s of (bottom.ladder ?? []).filter((x) => x.status !== "passed")) {
        made.push(series.createPriceLine({ price: s.price, title: `줍줍${s.step} ${s.weight_pct}%`, color: withAlpha(color, 0.7),
          lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false }));
      }
      const voidAt = bottom.invalidation?.monthly_close_below;
      if (voidAt != null) {
        made.push(series.createPriceLine({ price: voidAt, title: "바닥 판단 접기(월봉)", color: css("--muted", "#8a94a3"),
          lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false }));
      }
    }
    // 오른쪽 축의 가까운 라벨은 하나로(좌표는 그려진 뒤에 나온다)
    const stopLabels = keepAxisLabelsApart(drawn.chart, series, labelled);
    const near = planLines.map(([v]) => v).filter((v): v is number => v != null && Math.abs(v - lastClose) / lastClose <= AUTOSCALE_MAX_GAP);
    series.applyOptions({
      autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = base();
        if (!r || near.length === 0) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...near), maxValue: Math.max(r.priceRange.maxValue, ...near) } };
      },
    });

    const up = css("--up", "#f2464b");
    const down = css("--down", "#3a86f8");
    const keys = rows.map((r) => r.key);
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const list: { key: Key; m: Omit<SeriesMarker<Time>, "time"> }[] = [];
    if (show.analysis) {
      // 분봉: 분석한 거래일의 첫 봉, 일봉 이상: 그 날짜가 든 봉
      // 주말 · 휴장일 분석이면 그 전 거래일 — 일봉과 같은 규칙
      const day = intraday ? barFor(rows.map((r) => r.day), tradeDate) : null;
      const at = intraday ? (day == null ? null : rows.find((r) => r.day === day)?.key ?? null) : barFor(keys, tradeDate);
      if (at != null) list.push({ key: at, m: { position: "aboveBar", shape: "arrowDown", color: css("--warn", "#e5a23a"), text: "분석" } });
    }
    if (show.fills) {
      // #334 같은 봉의 체결은 하나로 — 매수만 ↑ · 매도만 ↓ · 둘 다 ●, 글자 없이 작게(가격은 옆 표에). 표에서 고른 날은 크게.
      const byBar = new Map<Key, { buy: boolean; sell: boolean; picked: boolean }>();
      for (const f of fills) {
        const at = fillBar(f, rows, intraday, ticker);
        if (at == null) continue;
        const g = byBar.get(at) ?? { buy: false, sell: false, picked: false };
        if (f.side === "BUY") g.buy = true;
        else g.sell = true;
        if (f.filled_at === pickedFill) g.picked = true;
        byBar.set(at, g);
      }
      for (const [at, g] of byBar) {
        const size = g.picked ? 1.6 : 0.7;
        const text = g.picked ? "선택한 체결" : "";
        if (g.buy && g.sell) list.push({ key: at, m: { position: "aboveBar", shape: "circle", size, color: css("--warn", "#e5a23a"), text } });
        else if (g.buy) list.push({ key: at, m: { position: "belowBar", shape: "arrowUp", size, color: up, text } });
        else list.push({ key: at, m: { position: "aboveBar", shape: "arrowDown", size, color: down, text } });
      }
    }
    if (show.osc && osc && !intraday && osc.interval === (drawn.interval === "1w" ? "1w" : drawn.interval === "1d" ? "1d" : null)) {
      // RSI 극단만 점으로(스토캐스틱은 너무 자주 닿아 차트를 덮는다 — 근거는 배지 문장에), 글자는 짧게 · 좁은 화면은 점만
      const narrow = (box.current?.clientWidth ?? 1000) < 600;
      for (const e of osc.own.episodes ?? []) {
        if (!e.kind.startsWith("rsi")) continue;
        const at = barFor(keys, e.at.slice(0, 10));
        if (at == null || at === keys[0]) continue;          // before the chart's first bar
        const hot = e.kind.endsWith("hot");
        const text = narrow ? "" : `${hot ? "과열" : "침체"}${e.ret20 != null ? ` ${pct(e.ret20)}` : ""}`;
        list.push({ key: at, m: { position: hot ? "aboveBar" : "belowBar", shape: "circle", size: 0.6,
          color: hot ? css("--warn", "#e5a23a") : css("--accent", "#3a86f8"), text } });
      }
    }
    list.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    markers.setMarkers(list.map(({ key, m }) => ({ ...m, time: time(key) }) as SeriesMarker<Time>));
    return () => {
      // 봉이 바뀌면 차트 effect 가 먼저 chart.remove() 한다 — 그때는 지울 선도 이미 없다.
      stopLabels(drawn.alive.current);
      if (drawn.alive.current) for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, plan, fills, pickedFill, tradeDate, ticker, show.levels, show.analysis, show.fills, extraLines, show.opt, optionData, show.osc, osc, show.bz, bottom]);

  // 지지 · 저항 구역 띠 · 타점의 손실 · 이익 영역 · 피보나치(#98) — 캔들 아래에 칠하고 축 라벨은 달지 않는다.
  useEffect(() => {
    if (!drawn) return;
    const { series, rows, intraday } = drawn;
    const bands = new PriceBands(`11px ${getComputedStyle(document.body).fontFamily}`);
    series.attachPrimitive(bands);
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    // 타점 영역은 분석한 날의 봉부터(분봉은 그날 첫 봉) — 분석일 마커와 같은 규칙
    const day = intraday ? barFor(rows.map((r) => r.day), tradeDate) : null;
    const at = intraday ? (day == null ? null : rows.find((r) => r.day === day)?.key ?? null) : barFor(rows.map((r) => r.key), tradeDate);
    const muted = css("--muted", "#8c939d");
    const profile = vp
      ? volumeProfileDrawing(vp, { bar: muted, thick: css("--warn", "#e5a23a"), poc: css("--warn", "#e5a23a"), label: muted }, price)
      : { bands: [], marks: [], bars: [] };
    // 플레이북(#117) — 분석 당시 계획의 값(지금 기준으로 다시 계산하지 않는다)
    const book = show.pb && plan?.playbooks
      ? playbookDrawing(plan.playbooks, { long: css("--long", "#f2464b"), short: css("--short", "#3a86f8") }, price)
      : { bands: [], marks: [] };
    // #321 저가매수 구간 — 지지 · 저항을 켰을 때만(가까운 지지 몇 개)
    const buyZones = buyZoneDrawing(srShown.filter((lv) => lv.kind === "support"), css("--ok", "#3fb68b"), price);
    bands.set(
      [
        ...profile.bands,
        ...book.bands,
        ...(show.levels ? planBands(plan, at == null ? null : time(at), { loss: css("--danger", css("--up", "#f2464b")), profit: css("--ok", "#3fb68b") }) : []),
        ...srBands(srShown, srSource?.atr, { support: css("--sr-support", "#2bb3a3"), resistance: css("--sr-resistance", "#b58cf0") }, price),
        ...buyZones.bands,
      ],
      [
        ...buyZones.marks,
        ...(fib?.levels ?? []).filter((lv) => Number.isFinite(lv.price)).map((lv) => ({ price: lv.price, color: withAlpha(muted, 0.85), label: String(lv.ratio) })),
        ...profile.marks,
        ...book.marks,
      ],
      profile.bars,
    );
    return () => {
      if (drawn.alive.current) series.detachPrimitive(bands);
    };
  }, [drawn, srShown, srSource, plan, fib, vp, show.levels, show.pb, tradeDate]);

  // #313 타점 보기 — 자기 띠 · 선을 붙이고 뗀다(다른 그리기와 섞지 않는다)
  useEffect(() => {
    if (!drawn || !show.entry || !entry) return;
    return drawEntry(drawn.series, entry, {
      long: css("--long", "#f2464b"), short: css("--short", "#3a86f8"), loss: css("--danger", "#ff6a3d"),
      profit: css("--ok", "#3fb68b"), muted: css("--muted", "#8c939d"),
    }, price, `11px ${getComputedStyle(document.body).fontFamily}`, () => drawn.alive.current);
  }, [drawn, entry, show.entry]);

  // RSI 패널은 차트 높이(크게 보기 · 가로 모드 · 창 크기)에 맞춰 다시 잰다
  useEffect(() => {
    const el = box.current;
    if (!drawn?.rsi || !el) return;
    const apply = () => {
      if (drawn.alive.current) drawn.chart.panes()[1]?.setHeight(rsiHeight(el.clientHeight));
    };
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [drawn]);

  // 추세선 — 앵커부터 오늘까지 잇는 선, 터치 봉에 번호, 이탈한 선은 흐린 점선. 축 범위엔 넣지 않는다(먼 선이 봉을 누르지 않게).
  // 피보나치 — 비율 제목이 붙은 가는 가로선 + 스윙 구간(시작 → 끝) 점선.
  useEffect(() => {
    if (!drawn || (trendLines.length === 0 && !fib)) return;
    const { chart, series, rows, intraday } = drawn;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const keys = rows.map((r) => r.key);
    const first = Date.parse(rows[0].bar.at);
    const index = new Map(keys.map((k, i) => [k, i]));
    /**
     * 그 시각이 든 봉. 분봉 차트에 일봉 이상 시각(자정)이 오면 그날 첫 봉 — 분석일 마커와 같은 규칙
     * (자정은 그날 첫 분봉보다 앞이라 그대로 찾으면 전 거래일 마지막 봉에 붙는다).
     */
    const keyOf = (iso: string, tf: string): Key | null => {
      if (!Number.isFinite(Date.parse(iso))) return null;
      if (intraday && !isIntradayTf(tf)) return rows.find((r) => r.day === marketDay(iso, ticker))?.key ?? null;
      return barFor(keys, intraday ? kstStamp(iso) : kstDay(iso));
    };
    const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
    const lines: { line: Trendline; series: ISeriesApi<"Line"> }[] = [];
    const marks: { key: Key; m: Omit<SeriesMarker<Time>, "time"> }[] = [];
    let swing: ISeriesApi<"Line"> | null = null;
    const marked = new Set<string>(); // 같은 봉 · 같은 쪽엔 앞(더 강한) 선의 번호 하나만 — 비슷한 선끼리 겹쳐 찍지 않게
    const sameFrame = (tf: string) => tf === drawn.interval;
    for (const line of trendLines) {
      try {
        const a0 = Date.parse(line.anchors[0]?.at);
        if (!Number.isFinite(a0) || !(line.anchors[0].price > 0)) continue;
        // 앵커가 든 봉부터(보는 기간 전에 시작한 선은 첫 봉부터)
        const startKey = a0 < first ? keys[0] : keyOf(line.anchors[0].at, line.timeframe);
        const from = startKey == null ? -1 : index.get(startKey) ?? -1;
        if (from < 0) continue;
        let value: (i: number) => number;
        if (sameFrame(line.timeframe)) {
          // 같은 주기: 백엔드와 같은 "로그 가격 × 봉 순서" 직선 — 장 마감 · 주말 공백에 계단이 생기지 않는다
          const ref = [line.anchors[0], line.anchors[1], line.end]
            .map((p) => ({ p, i: p && Date.parse(p.at) >= first ? index.get(keyOf(p.at, line.timeframe) ?? "") : undefined }))
            .filter((r): r is { p: Trendline["end"]; i: number } => r.i !== undefined && r.p.price > 0);
          const [r0, r1] = ref;
          const slope = r0 && r1 && r1.i > r0.i ? Math.log(r1.p.price / r0.p.price) / (r1.i - r0.i) : Math.log(1 + line.slope_pct_per_bar / 100);
          const at0 = r0 ?? { p: line.end, i: keys.length - 1 };
          value = (i) => at0.p.price * Math.exp(slope * (i - at0.i));
        } else {
          value = (i) => lineAt(line, Date.parse(rows[i].bar.at));
        }
        const data = [];
        for (let i = from; i < rows.length; i++) {
          const v = value(i);
          if (Number.isFinite(v) && v > 0) data.push({ time: time(rows[i].key), value: v });
        }
        if (data.length < 2) continue; // 선이 보는 기간 밖에서 끝났다
        const base = css(line.kind === "support" ? "--sr-support" : "--sr-resistance", line.kind === "support" ? "#2bb3a3" : "#b58cf0");
        const broken = line.state === "broken";
        const s = chart.addSeries(LineSeries, { ...quiet, color: broken ? `${base}80` : base, lineWidth: 2, lineStyle: broken ? LineStyle.Dashed : LineStyle.Solid });
        lines.push({ line, series: s });
        s.setData(data);
        line.touches.forEach((at, i) => {
          const key = Date.parse(at) < first ? null : keyOf(at, line.timeframe);
          const spot = `${key}-${line.kind}`;
          if (key == null || marked.has(spot)) return;
          marked.add(spot);
          marks.push({ key, m: { position: line.kind === "support" ? "belowBar" : "aboveBar", shape: "circle", color: base, text: String(i + 1), size: 0.6 } });
        });
      } catch {
        /* 이상한 선 하나는 건너뛴다 — 차트 · 분석 화면은 그대로 */
      }
    }
    if (fib) {
      try {
        const color = css("--muted", "#8c939d");   // 비율 가로선은 띠 effect 가 왼쪽 글자와 함께 그린다
        const tf = srSource?.intervals?.primary ?? "1d";
        const a = keyOf(fib.from.at, tf);
        const b = keyOf(fib.to.at, tf);
        if (a != null && b != null && a !== b && Date.parse(fib.from.at) >= first && Date.parse(fib.to.at) >= first) {
          swing = chart.addSeries(LineSeries, { ...quiet, color, lineWidth: 1, lineStyle: LineStyle.Dashed });
          swing.setData([{ time: time(a), value: fib.from.price }, { time: time(b), value: fib.to.price }].sort((x, y) => (x.time < y.time ? -1 : 1)));
        }
      } catch {
        /* 되돌림을 못 그려도 차트는 그대로 */
      }
    }
    marks.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    // 분석일 · 체결 마커와 따로 — 그 effect 를 건드리지 않고 같은 캔들에 번호만 얹는다.
    const touchMarks = createSeriesMarkers(series, marks.map(({ key, m }) => ({ ...m, time: time(key) }) as SeriesMarker<Time>));
    setTrendDrawn(lines);
    return () => {
      setTrendDrawn([]);
      if (!drawn.alive.current) return;
      touchMarks.detach();
      for (const { series: s } of lines) chart.removeSeries(s);
      if (swing) chart.removeSeries(swing);
    };
  }, [drawn, trendLines, fib, ticker, srSource]);

  // #321 휩소 — 직전 저점(고점)을 깨고 구역에 들어왔다가 되돌아 마감한 봉에 "스윕 후 회복". 지지 · 저항을 켰을 때만.
  const sweeps = show.sr ? srSource?.events ?? null : null;
  useEffect(() => {
    if (!drawn || !sweeps?.length) return;
    const tf = srSource?.intervals?.primary ?? "1d";
    if (tf !== interval) return;                     // 다른 주기 봉에는 달지 않는다
    const { rows, intraday } = drawn;
    const keys = rows.map((r) => r.key);
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const keyOf = (iso: string): Key | null =>
      Number.isFinite(Date.parse(iso)) ? barFor(keys, intraday ? kstStamp(iso) : kstDay(iso)) : null;
    const list: SeriesMarker<Time>[] = [];
    for (const e of sweeps) {
      if (e.kind !== "sweep_reclaim") continue;
      const key = keyOf(e.at);
      if (key == null) continue;
      const up = e.side === "support";
      list.push({ time: time(key), position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown",
                  color: css(up ? "--ok" : "--danger", up ? "#3fb68b" : "#ff6a3d"), text: "스윕 후 회복" } as SeriesMarker<Time>);
    }
    list.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
    // 이웃한 봉의 글자가 겹치니 글자는 가장 최근 것에만(앞의 것은 화살표만)
    for (const mk of list.slice(0, -1)) mk.text = "";
    const m = createSeriesMarkers(drawn.series, list);
    return () => {
      if (drawn.alive.current) m.detach();
    };
  }, [drawn, sweeps, srSource, interval]);

  // #298 삼각수렴 · 쐐기 — 윗선 · 아랫선을 첫 터치부터 마지막 봉까지 점선으로, 윗선 끝에 종류 · 상태 · 정점까지 남은 봉.
  // 추세선과 같은 규칙(같은 주기는 로그 가격 × 봉 순서 직선, 다른 주기는 시각으로)이고 축 범위엔 넣지 않는다.
  useEffect(() => {
    if (!drawn || patterns.length === 0) return;
    const { chart, rows, intraday } = drawn;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const keys = rows.map((r) => r.key);
    const first = Date.parse(rows[0].bar.at);
    const index = new Map(keys.map((k, i) => [k, i]));
    const keyOf = (iso: string, tf: string): Key | null => {
      if (!Number.isFinite(Date.parse(iso))) return null;
      if (intraday && !isIntradayTf(tf)) return rows.find((r) => r.day === marketDay(iso, ticker))?.key ?? null;
      return barFor(keys, intraday ? kstStamp(iso) : kstDay(iso));
    };
    const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
    const added: ISeriesApi<"Line">[] = [];
    const detach: (() => void)[] = [];
    const color = css("--warn", "#e5a23a");
    for (const p of patterns) {
      try {
        const series: ISeriesApi<"Line">[] = [];
        for (const side of [p.upper, p.lower]) {
          const t0 = Date.parse(side.from.at), t1 = Date.parse(side.to.at);
          if (!(side.from.price > 0 && side.to.price > 0) || !Number.isFinite(t0) || !Number.isFinite(t1)) continue;
          const rate = Math.log(side.to.price / side.from.price);
          const i0 = t0 >= first ? index.get(keyOf(side.from.at, p.timeframe) ?? "") : undefined;
          const i1 = index.get(keyOf(side.to.at, p.timeframe) ?? "");
          const value = p.timeframe === drawn.interval && i0 !== undefined && i1 !== undefined && i1 > i0
            ? (i: number) => side.from.price * Math.exp((rate * (i - i0)) / (i1 - i0))
            : (i: number) => side.from.price * Math.exp((rate * (Date.parse(rows[i].bar.at) - t0)) / Math.max(1, t1 - t0));
          const start = i0 ?? (t0 < first ? 0 : -1);
          if (start < 0) continue;
          const data = [];
          for (let i = start; i < rows.length; i++) {
            const v = value(i);
            if (Number.isFinite(v) && v > 0) data.push({ time: time(rows[i].key), value: v });
          }
          if (data.length < 2) continue;
          const s = chart.addSeries(LineSeries, { ...quiet, color, lineWidth: 2, lineStyle: LineStyle.Dashed });
          s.setData(data);
          added.push(s);
          series.push(s);
        }
        if (series[0]) {
          const state = p.state === "converging" ? `수렴 중 · 정점 ${Math.round(p.apex.bars_ahead)}봉 뒤` : PATTERN_STATE[p.state];
          const m = createSeriesMarkers(series[0], [{ time: time(rows[rows.length - 1].key), position: "aboveBar", shape: "square",
            color, text: `${p.label}(${tfLabel(p.timeframe)}) · ${state}`, size: 0.5 } as SeriesMarker<Time>]);
          detach.push(() => m.detach());
        }
      } catch {
        /* 이상한 패턴 하나는 건너뛴다 — 차트는 그대로 */
      }
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const d of detach) d();
      for (const s of added) chart.removeSeries(s);
    };
  }, [drawn, patterns, ticker]);

  // #300 예상도 — 마지막 봉 뒤 미래 칸에 확률 원뿔(68% 실선 · 95% 점선 · 가운데 점선)과 시나리오 경로(강세 · 약세 · 범위)를 그린다.
  // 축 범위엔 95% 띠를 넣지 않는다(먼 범위가 봉을 누르지 않게). 이 차트 주기와 같은 시간축의 예상도만 그린다.
  useEffect(() => {
    if (!drawn || !proj || proj.timeframe !== drawn.interval || proj.cone.length === 0 || !proj.price) return;
    const { chart, rows, intraday } = drawn;
    const time = (k: string | number) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const last = rows[rows.length - 1].key;
    const future = futureKeys(last, drawn.interval, proj.steps);
    const keyAt = (bar: number) => (bar <= 0 ? last : future[Math.min(bar, future.length) - 1]);
    const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    const noScale = { ...quiet, autoscaleInfoProvider: () => null };
    const muted = css("--muted", "#8c939d");
    const added: ISeriesApi<"Line">[] = [];
    const detach: (() => void)[] = [];
    const line = (values: number[], opts: object) => {
      const s = chart.addSeries(LineSeries, { ...quiet, lineWidth: 1, ...opts });
      s.setData([{ time: time(last), value: proj.price as number }, ...values.map((v, i) => ({ time: time(future[i]), value: v }))]);
      added.push(s);
      return s;
    };
    try {
      line(proj.cone.map((c) => c.p84), { color: withAlpha(muted, 0.9) });
      line(proj.cone.map((c) => c.p16), { color: withAlpha(muted, 0.9) });
      line(proj.cone.map((c) => c.p97_5), { ...noScale, color: withAlpha(muted, 0.6), lineStyle: LineStyle.Dotted });
      line(proj.cone.map((c) => c.p2_5), { ...noScale, color: withAlpha(muted, 0.6), lineStyle: LineStyle.Dotted });
      line(proj.cone.map((c) => c.p50), { color: withAlpha(muted, 0.7), lineStyle: LineStyle.Dashed });
      const color = { bull: css("--long", "#f2464b"), bear: css("--short", "#3a86f8"), range: muted };
      for (const path of proj.paths) {
        const pts = path.points.filter((p) => p.price > 0);
        if (pts.length < 2) continue;
        const s = chart.addSeries(LineSeries, { ...quiet, color: color[path.name], lineWidth: 2, lineStyle: LineStyle.LargeDashed });
        // 같은 칸에 두 점이 오면(마지막 칸에서 멈춤) 뒤 점만 둔다 — 시각이 겹치면 그릴 수 없다
        const data = new Map<string | number, number>();
        for (const p of pts) data.set(keyAt(p.bar), p.price);
        s.setData([...data].map(([k, v]) => ({ time: time(k), value: v })).sort((a, b) => (a.time < b.time ? -1 : 1)));
        added.push(s);
        const end = pts[pts.length - 1];
        const m = createSeriesMarkers(s, [{ time: time(keyAt(end.bar)), position: path.name === "bear" ? "belowBar" : "aboveBar",
          shape: "circle", color: color[path.name], text: `${path.label} ${Math.round(path.prob * 100)}%`, size: 0.6 } as SeriesMarker<Time>]);
        detach.push(() => m.detach());
      }
    } catch {
      /* 예상도를 못 그려도 차트는 그대로 */
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const d of detach) d();
      for (const s of added) chart.removeSeries(s);
    };
  }, [drawn, proj]);

  // 선 근처(5px)에 마우스를 올리면 근거 — 지지 · 저항 · 피보나치(가로선)와 추세선(그 봉에서의 선 높이).
  useEffect(() => {
    if (!drawn) return;
    const { chart, series } = drawn;
    const onMove = (param: MouseEventParams<Time>) => {
      const pt = param.point;
      // y 는 패널 기준 좌표다 — RSI 패널 위에서는 찾지 않는다.
      if (!pt || (param.paneIndex ?? 0) !== 0) return setHover(null);
      let best: { hit: Hover; d: number } | null = null;
      const consider = (price: number, make: (y: number) => Hover) => {
        const y = series.priceToCoordinate(price);
        if (y === null) return;
        const d = Math.abs(y - pt.y);
        if (d <= 5 && (!best || d < best.d)) best = { hit: make(y), d };
      };
      // 선 근처(5px) 후보가 먼저 — 추세선 · 피보나치가 구역 띠를 지나가도 그 선의 근거가 뜨게
      let inBand: { hit: Hover; d: number } | null = null;
      for (const level of srShown) {
        const make = (y: number): Hover => ({ type: "sr", level, key: `sr-${level.kind}-${level.price}`, x: pt.x, y });
        consider(level.price, make);
        const [lo, hi] = zoneEdges(level, srSource?.atr);
        const a = series.priceToCoordinate(hi);
        const b = series.priceToCoordinate(lo);
        if (a != null && b != null && pt.y >= Math.min(a, b) && pt.y <= Math.max(a, b)) {
          const d = Math.abs((a + b) / 2 - pt.y);   // 겹친 띠는 가운데에 가까운 쪽
          if (!inBand || d < inBand.d) inBand = { hit: make((a + b) / 2), d };
        }
      }
      if (fib) for (const level of fib.levels) consider(level.price, (y) => ({ type: "fib", level, fib, key: `fib-${level.ratio}`, x: pt.x, y }));
      if (vp) consider(vp.poc, (y) => ({ type: "vp", vp, key: "vp-poc", x: pt.x, y }));
      for (const { line, series: s } of trendDrawn) {
        const v = param.seriesData.get(s) as { value?: number } | undefined;
        if (v?.value != null) consider(v.value, (y) => ({ type: "trend", line, here: v.value as number, key: `trend-${line.kind}-${line.anchors[0].at}-${line.anchors[1]?.at}`, x: pt.x, y }));
      }
      // 선 근처가 없으면 커서가 든 구역 띠
      const hit = (best as { hit: Hover } | null)?.hit ?? (inBand as { hit: Hover } | null)?.hit ?? null;
      // 다른 선으로 옮겨 갈 때만 다시 그린다(마우스 이동마다 렌더하지 않게).
      setHover((prev) => (!hit ? null : prev?.key === hit.key ? prev : hit));
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      setHover(null);
      if (drawn.alive.current) chart.unsubscribeCrosshairMove(onMove);
    };
  }, [drawn, srShown, srSource, fib, vp, trendDrawn]);

  const currency = isKorean(ticker) ? "원" : "달러";
  const levels = [...(plan ? [plan.entry_price, plan.stop_loss, plan.price_target] : []), ...(extraLines ?? []).map((l) => l.price)];
  const hasLevels = levels.some((v) => v != null);
  // #321 계획 손절이 지지 구역 안쪽이면 경고 — 그 분석이 본 레벨(없으면 지금 레벨)로
  const stopWarn = show.levels && plan ? stopInsideZone(plan.stop_loss, plan.entry_price, sr) : null;
  const outOfRange = show.levels && !!drawn && levels.some((v) => v != null && Math.abs(v - drawn.lastClose) / drawn.lastClose > AUTOSCALE_MAX_GAP);
  // 보관 한도 안내는 분봉일 때만 — 년봉은 늘 요청보다 적게 오지만 한도 탓이 아니다.
  const limited = candles?.truncated && candles.history_limit_days != null ? candles.history_limit_days : null;
  const rsiOn = show.rsi && !!candles?.indicators?.includes("rsi14");
  const stochOn = show.stoch && !!candles?.indicators?.includes("stoch");
  const oscLines = show.osc && osc ? [
    oscLine(osc.own, `${osc.own.ticker ?? ticker} ${osc.interval === "1w" ? "주봉" : "일봉"}`),
    osc.underlying ? oscLine(osc.underlying, `기초자산 ${osc.underlying.ticker}(${osc.underlying.leverage ?? ""}배 기준)`) : null,
  ].filter((x): x is string => !!x) : [];
  const hot = show.osc && (osc?.own.zone === "hot" || osc?.underlying?.zone === "hot");
  // #334 표에는 이 종목 · 차트에 보이는 기간의 체결만(마커와 같은 봉 규칙으로 봉이 있는 것)
  const tableFills = useMemo(
    () => (show.fills && drawn ? fills.filter((f) => fillBar(f, drawn.rows, drawn.intraday, ticker) != null) : []),
    [show.fills, drawn, fills, ticker],
  );
  const pickFill = (filledAt: string) => {
    setPickedFill(filledAt);
    const f = fills.find((x) => x.filled_at === filledAt);
    if (!drawn || !f) return;
    const at = fillBar(f, drawn.rows, drawn.intraday, ticker);
    const i = drawn.rows.findIndex((r) => r.key === at);
    if (i < 0) return;
    const ts = drawn.chart.timeScale();
    const span = ts.getVisibleLogicalRange();
    const half = span ? Math.max(10, (span.to - span.from) / 2) : 40;
    ts.setVisibleLogicalRange({ from: i - half, to: i + half });
  };

  return (
    <section ref={section} className={`card price-chart${expanded ? " price-chart-expanded" : ""}`}
      {...(expanded ? { role: "dialog", "aria-modal": true, "aria-label": `${title ?? ticker} 큰 차트` } : {})}>
      <div className="price-chart-head">
        <h3>
          {title ?? "가격 차트"}{" "}
          <span className="muted small">{title !== ticker && `${ticker} · `}{currency}{candles ? ` · ${candles.source}` : ""}</span>
        </h3>
        <div className="chips chart-intervals" role="group" aria-label="봉 주기">
          {INTERVALS.map(({ iv, label }) => (
            <button key={iv} type="button" className={`chip ${iv === interval ? "chip-on" : ""}`} aria-pressed={iv === interval} onClick={() => setBarInterval(iv)}>
              {label}
            </button>
          ))}
        </div>
        <div className="chips chart-presets" role="group" aria-label="표시 묶음">
          {(Object.keys(PRESET_LABEL) as Preset[]).map((p) => (
            <button key={p} type="button" className={`chip chip-sub ${p === preset ? "chip-on" : ""}`} aria-pressed={p === preset}
              onClick={() => pickPreset(p)}>
              {PRESET_LABEL[p]}
            </button>
          ))}
        </div>
        <details ref={menu} className="chart-menu">
          <summary>표시</summary>
          <div className="chart-menu-body" role="group" aria-label="차트 표시">
            {(Object.keys(SHOW_LABEL) as (keyof Show)[]).filter((k) => k !== "opt" || US_TICKER.test(ticker)).map((k) => (
              <label key={k} className="chart-menu-item">
                <input type="checkbox" checked={show[k]} onChange={() => toggle(k)} />
                {SHOW_LABEL[k]}
              </label>
            ))}
            {wantSr && (
              <div className="chart-sr-source" role="group" aria-label="지지 · 저항 · 추세선 기준">
                <span className="muted small">선 기준</span>
                <div className="chips">
                  {(["plan", "now"] as SrMode[]).filter((m) => m !== "plan" || saved).map((m) => (
                    <button key={m} type="button" className={`chip chip-sub ${m === mode ? "chip-on" : ""}`} aria-pressed={m === mode} onClick={() => setSrMode(m)}>
                      {SR_LABEL[m]}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </details>
        <button type="button" className="secondary chart-expand" aria-label={expanded ? "크게 보기 닫기 (Esc)" : "차트 크게 보기"}
          onClick={() => (expanded ? close() : open())}>
          {expanded ? "닫기" : "크게"}
        </button>
      </div>
      {notice && <p className="error small chart-limit">{notice}</p>}
      {show.entry && <EntrySummary entry={entry} failed={entryFailed} fromRun={!!plan} />}
      {oscLines.length > 0 && (
        <div className={`small chart-limit ${hot ? "error" : "muted"}`} role="status">
          {oscLines.map((t) => <p key={t} className="chart-osc">{hot && t.includes("과열") ? "⚠ " : ""}{t}</p>)}
        </div>
      )}
      {limited != null && <p className="muted small chart-limit">야후 보관 한도로 {LABEL[interval]}봉은 최근 {limited}일만 제공됩니다.</p>}
      <div className={`chart-and-fills${tableFills.length > 0 ? " has-fills" : ""}`}>
      {failed ? (
        <p className="muted small">시세를 불러오지 못했습니다.</p>
      ) : candles && candles.bars.length === 0 ? (
        <p className="muted small">표시할 봉이 없습니다.</p>
      ) : (
        <div
          ref={box}
          className={`price-chart-box ${rsiOn || stochOn ? "with-rsi" : ""}${rsiOn && stochOn ? " with-osc2" : ""}`}
          role="img"
          aria-label={`${ticker} ${LABEL[interval]}봉 가격 차트${rsiOn ? "와 RSI" : ""}${stochOn ? " · 스토캐스틱" : ""}${drawn ? `, 마지막 종가 ${price(drawn.lastClose)}` : ""}`}
          aria-describedby={hasLevels && show.levels ? legendId : undefined}
        >
          {!candles && <p className="muted small">불러오는 중…</p>}
          <div ref={ohlc} className="chart-ohlc small" aria-hidden />
          {hover && (
            <div className="sr-tip" style={tipPosition(hover, box.current)}>
              {hover.type === "sr" ? (
                <>
                  <strong>{hover.level.kind === "support" ? "지지" : "저항"} {price(hover.level.price)}</strong>{" "}
                  <span className="muted">{TF_LABEL[hover.level.timeframe] ?? hover.level.timeframe} · 강도 {hover.level.strength}</span>
                  <div>{hover.level.basis}</div>
                  {hover.level.zone && (
                    <div>
                      구역 {price(hover.level.zone[0])} ~ {price(hover.level.zone[1])}
                      {hover.level.touches ? ` · 과거 ${hover.level.touches}번 중 ${hover.level.held}번 ${hover.level.kind === "support" ? "반등" : "저지"}` : " · 반응 기록 없음"}
                    </div>
                  )}
                  {hover.level.buy_zone && (
                    <div className={hover.level.buy_zone.ok ? undefined : "muted"}>
                      저가매수 구간 {price(hover.level.buy_zone.lo)} ~ {price(hover.level.buy_zone.hi)} · 손절 {price(hover.level.buy_zone.stop)}(구역 하단 밖)
                      {hover.level.buy_zone.target != null && ` · 첫 목표 ${price(hover.level.buy_zone.target)}`}
                      {hover.level.buy_zone.rr != null && ` · 손익비 ${hover.level.buy_zone.rr}`}
                      {` · 표본 ${hover.level.buy_zone.n}`}
                      {hover.level.buy_zone.why.length > 0 && ` · ${hover.level.buy_zone.why.join(" · ")}`}
                      {hover.level.buy_zone.note && ` — ${hover.level.buy_zone.note}`}
                    </div>
                  )}
                </>
              ) : hover.type === "vp" ? (
                <>
                  <strong>매물대 POC {price(hover.vp.poc)}</strong>{" "}
                  <span className="muted">가장 많이 거래된 가격 · 최근 {hover.vp.bars}봉{hover.vp.interval ? `(${tfLabel(hover.vp.interval)})` : ""}</span>
                  <div>거래량 70% 구간(가치 영역) {price(hover.vp.value_area.low)} ~ {price(hover.vp.value_area.high)}</div>
                  {hover.vp.nodes.some((n) => n.kind === "thick") && (
                    <div>두꺼운 매물대 {hover.vp.nodes.filter((n) => n.kind === "thick").map((n) => price(n.price)).join(" · ")}</div>
                  )}
                </>
              ) : hover.type === "trend" ? (
                <>
                  <strong>{TREND_NAME[hover.line.kind]} {price(hover.here)}</strong>{" "}
                  <span className="muted">{tfLabel(hover.line.timeframe)} · 터치 {hover.line.touches.length}회</span>
                  <div>
                    {when} 선 {price(hover.line.price_now)} · 가격은 선에서 {signed(hover.line.distance_atr)} ATR · {TREND_STATE[hover.line.state]}
                  </div>
                </>
              ) : (
                <>
                  <strong>피보나치 {hover.level.ratio} {price(hover.level.price)}</strong>{" "}
                  <span className="muted">{hover.fib.direction === "up" ? "상승" : "하락"} {price(hover.fib.from.price)} → {price(hover.fib.to.price)} 되돌림</span>
                  {hover.level.confluence && <div>지지 · 저항 {price(hover.level.confluence.price)}(강도 {hover.level.confluence.strength})와 겹침</div>}
                </>
              )}
            </div>
          )}
        </div>
      )}
      {tableFills.length > 0 && <FillsTable fills={tableFills} picked={pickedFill} onPick={pickFill} />}
      </div>
      {plan && hasLevels && show.levels && (
        <p id={legendId} className="muted small price-chart-legend">
          <span className="lg lg-entry" /> 진입 {price(plan.entry_price)} · <span className="lg lg-stop" /> 손절 {price(plan.stop_loss)} ·{" "}
          <span className="lg lg-target" /> 목표 {price(plan.price_target)}
          {outOfRange && " · 현재가와 너무 먼 선은 축 범위에서 뺐습니다"}
          {stopWarn && (
            <span className="stop-warn">
              {" "}· 구역 안 손절 — 지지 {price(stopWarn.level.zone![0])}~{price(stopWarn.level.zone![1])} 안이라 꼬리에 털릴 수 있음, 제안 손절 {price(stopWarn.suggest)}
            </span>
          )}
        </p>
      )}
      {show.proj && (
        <p className="muted small proj-note" aria-live="polite">
          {!projFor ? "예상도는 일봉 · 1시간봉 · 5분봉에서만 그립니다."
            : projFailed ? "예상도를 계산하지 못했습니다."
              : !proj ? "예상도 계산 중…"
                : proj.timeframe !== interval ? `예상도는 ${proj.timeframe} 봉으로 계산되어 이 주기 차트에는 그리지 않습니다.`
                  : (
                    <>
                      <strong>예상도</strong>{" "}
                      {proj.note_ko}{" "}
                      <span>({proj.note_source === "ai" ? "AI 설명 · 숫자는 데스크 계산" : proj.note_pending ? "데스크 계산 · AI 설명 준비 중" : "데스크 계산"})</span>
                      {proj.calibration?.coverage68_valid != null && (
                        <> · 과거 검증 68% 범위 적중 {proj.calibration.coverage68_valid}%{proj.calibration.n ? `(n ${proj.calibration.n.toLocaleString()})` : ""}</>
                      )}
                      {" · "}<strong>예측이 아니라 확률 범위 · 시나리오입니다.</strong>
                    </>
                  )}
        </p>
      )}
      {((show.fills && fills.length > 0) || rsiOn || stochOn || (drawn && LOG_SCALE.has(interval))) && (
        <p className="muted small price-chart-legend">
          {[
            show.fills && fills.length > 0 ? `내 체결 ${fills.length}건` : null,
            rsiOn ? "RSI 70 이상 과열 · 30 이하 과매도" : null,
            stochOn ? "스토캐스틱 80 이상 과열 · 20 이하 침체(실선 %K · 점선 %D)" : null,
            show.osc && osc && !LOG_SCALE.has(interval) && !["5m", "15m", "30m", "60m", "240m"].includes(interval)
              ? "점 = 과거 RSI 과열(위) · 침체(아래)와 그 뒤 20봉 등락" : null,
            drawn && LOG_SCALE.has(interval) ? "가격축은 로그 눈금" : null,
          ].filter(Boolean).join(" · ")}
        </p>
      )}
      {show.vp && srSource && !vp && (
        <p className="muted small price-chart-legend">
          {srSource.volume_profile_error
            ? "매물대를 계산하지 못했습니다."
            : mode === "plan" && srSource.volume_profile === undefined
              ? "매물대: 이 분석에는 기록이 없습니다 — 표시 메뉴의 \"지금\" 기준으로 보세요."
              : "매물대: 거래량이 없는 종목(지수 등)은 그리지 않습니다."}
        </p>
      )}
      {vp && (
        <p className="muted small price-chart-legend">
          매물대(오른쪽 막대) · <span className="lg lg-poc" /> POC {price(vp.poc)} · 가치 영역 {price(vp.value_area.low)} ~ {price(vp.value_area.high)} · 강조 막대는 두꺼운 매물대
        </p>
      )}
      {wantSr && srFailed && mode === "now" ? (
        <p className="muted small">지지 · 저항 · 추세선을 계산하지 못했습니다.</p>
      ) : wantSr && mode === "now" && !nowSr ? (
        <p className="muted small">지지 · 저항 · 추세선 계산 중…</p>
      ) : null}
      {lists && srSource && (show.sr || show.trend || show.pattern || !!fib) && (
        <details className="level-list">
          <summary>레벨 목록 <span className="muted small">지지 · 저항 · 추세선{fib ? " · 피보나치" : ""}</span></summary>
      {show.sr && (
        sr.length > 0 ? (
          <>
            <ul className="sr-list" aria-label="지지 · 저항 구역">
              {sr.map((lv) => (
                <li key={`${lv.kind}-${lv.price}`} className={`sr-${lv.kind}`}>
                  <span className="sr-kind">{lv.kind === "support" ? "지지" : "저항"}</span>
                  <strong className="num">{price(lv.price)}</strong>
                  <span className="muted small">{TF_LABEL[lv.timeframe]} · 강도 {lv.strength}</span>
                  <span className="small">{lv.basis}</span>
                </li>
              ))}
            </ul>
            <p className="muted small sr-note">{srNote(mode, srSource)}</p>
          </>
        ) : null
      )}
      {show.trend && (
        trendLines.length > 0 ? (
          <ul className="sr-list trend-list" aria-label="추세선">
            {trendLines.map((t) => (
              <li key={`${t.kind}-${t.timeframe}-${t.anchors[0].at}-${t.anchors[1]?.at}`} className={`sr-${t.kind} trend-${t.state}`}>
                <span className="sr-kind">{t.kind === "support" ? "지지" : "저항"}</span>
                <strong className="num">{price(t.price_now)}</strong>
                <span className="muted small">{tfLabel(t.timeframe)} · 터치 {t.touches.length}회</span>
                <span className="small">
                  {TREND_NAME[t.kind]} · {kstDay(t.anchors[0].at)}부터 · {when} 가격 {signed(t.distance_atr)} ATR ·{" "}
                  <strong className="trend-state">{TREND_STATE[t.state]}</strong>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">
            {srSource.trend_error
              ? "추세선을 계산하지 못했습니다."
              : srSource.trendlines === undefined
                ? "이 분석에는 추세선 기록이 없습니다 — '지금' 기준으로 보세요."
                : "조건(터치 3회 이상 · 종가 이탈 없음)을 채운 추세선이 없습니다."}
          </p>
        )
      )}
      {show.pattern && srSource && (
        patterns.length > 0 ? (
          <ul className="sr-list pattern-list" aria-label="삼각수렴 · 쐐기">
            {patterns.map((p) => (
              <li key={`${p.kind}-${p.timeframe}-${p.upper.from.at}`} className={`pattern-${p.state}`}>
                <span className="sr-kind">{p.label}</span>
                <strong className="num">{price(p.upper.to.price)} / {price(p.lower.to.price)}</strong>
                <span className="muted small">
                  {tfLabel(p.timeframe)} · {PATTERN_STATE[p.state]}{p.state === "converging" ? ` · 정점 ${Math.round(p.apex.bars_ahead)}봉 뒤` : ""} · 폭 {p.width_pct}%
                </span>
                <span className="small">{patternRecord(p)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small">
            {srSource.patterns_error ? "삼각수렴을 계산하지 못했습니다."
              : srSource.patterns === undefined ? "이 분석에는 삼각수렴 기록이 없습니다 — '지금' 기준으로 보세요."
                : "지금 좁혀지는 삼각수렴 · 쐐기가 없습니다."}
          </p>
        )
      )}
      {fib && (
        <p className="muted small fib-note">
          피보나치 되돌림 — {fib.direction === "up" ? "상승" : "하락"} {price(fib.from.price)}({kstDay(fib.from.at)}) → {price(fib.to.price)}({kstDay(fib.to.at)}):{" "}
          {fib.levels.filter((lv) => lv.ratio > 0 && lv.ratio < 1).map((lv) => `${lv.ratio} ${price(lv.price)}${lv.confluence ? "(지지 · 저항과 겹침)" : ""}`).join(" · ")}
        </p>
      )}
      {(show.trend || show.fib) && (trendLines.length > 0 || fib) && (
        <p className="muted small sr-note">추세선은 로그 가격에서 직선, 번호는 선에 닿은 저점 · 고점 순서. 가격 기록의 계산이지 예측이 아닙니다.</p>
      )}
        </details>
      )}
    </section>
  );
}
