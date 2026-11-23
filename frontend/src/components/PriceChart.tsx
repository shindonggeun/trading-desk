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
import { type ReactNode, type RefObject, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { api, ApiError, type CandleInterval, type CandleLive, type Candles, type OscillatorView, type OscReading, type ReviewRow } from "../api";
import { price } from "../format";
import FillsTable from "./FillsTable";
import { drawEntry, useEntryNow } from "./chartEntry";
import EntrySummary from "./EntrySummary";
import {
  buyZoneDrawing, keepAxisLabelsApart, nearestLevels, planBands, playbookDrawing, PriceBands, srBands, stopInsideZone,
  volumeProfileDrawing, withAlpha, zoneEdges,
} from "./chartBands";
import { crosshairTime, ticksClearOfLabels } from "./chartFormat";
import { loadMaOn, MA_PERIODS, type MaOn, type MaPeriod, PANE_TEXT, type PaneId, saveMaOn, shortVolume, sma } from "./chartIndicators";
import { levelsContradict } from "../listLevels";
import { ALL_ON, devExample, KIND_KO, loadTrendFilter, passes, saveTrendFilter, SCALE_KO, STATUS_KO, trendKey, trendLabel, trendWidth, type TrendFilter } from "../trendLines";
import TrendLineCard from "./TrendLineCard";
import GapOddsCard from "./GapOddsCard";
import ChannelLineCard, { type LinePick } from "./ChannelLineCard";
import { channelTitle, fakeLines, isLineTf, TF_KO, TREND_STATE_KO, CHANNEL_STATE_KO, trendTitle } from "../channelLines";
import { oddsLabel } from "../gapOdds";
import type { AccumulationViewKey, ChartRead, ChartReadRef, GapReport, LinesReport, LineTf, OpenGap } from "../types";
import type { BottomZone, ChartPattern, FibLevel, HsPattern, Fibonacci, OutlookPeriodId, Plan, Positioning, Projection, RunOutlook, SrLevel, SupportResistance, Trendline, VolumeProfile } from "../types";
import { futureKeys, PROJECTION_HORIZON } from "./chartProjection";
import PatternCard, { patternKey } from "./PatternCard";
import { clipPath, ConeShade, directionLabel, OUTLOOK_PERIODS, outlookCone } from "./chartOutlook";
import { css, useColorScheme } from "../chartColors";
import { every, usePoll } from "../poll";
import { delayText, LIVE_NOTE, liveLabel, liveMs, useInView } from "../liveBar";
import ChartReadPanel from "./ChartReadPanel";
import AccumulationPanel from "./AccumulationPanel";
import { useAccumulationOverlay } from "./accumulationOverlay";
import { beginnerLines, loadReadPrefs, marksOf, MARKS, type ReadPrefs, refIds, refKinds, saveReadPrefs } from "../chartRead";

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

type Show = { long: boolean; levels: boolean; pb: boolean; sr: boolean; all: boolean; vp: boolean; trend: boolean; pattern: boolean; proj: boolean; fib: boolean; fills: boolean; analysis: boolean; rsi: boolean; opt: boolean; stoch: boolean; bb: boolean; osc: boolean; bz: boolean; entry: boolean; gaps: boolean; ma: boolean; chan: boolean; ltrend: boolean };
const SHOW_LABEL: Record<keyof Show, string> = {
  levels: "타점선", pb: "플레이북", sr: "지지 · 저항", all: "모든 지지 · 저항", vp: "매물대", trend: "추세선", pattern: "삼각수렴 · 쐐기", proj: "예상도", fib: "피보나치", fills: "매수 · 매도", analysis: "분석일", rsi: "RSI",
  opt: "옵션 벽 · 맥스 페인",
  bz: "인생 타점 줍줍",
  entry: "지금 타점(최적)",
  gaps: "안 메운 갭 · 메움 확률",
  chan: "평행 채널(분봉 · 일봉)",
  ltrend: "짧은 추세선 · 이탈 표시",
  long: "긴 추세선(일봉 · 몇 달)",
  stoch: "스토캐스틱", bb: "볼린저밴드(20)", osc: "과열 · 침체 표시",
  ma: "이동평균(5 · 20 · 60 · 120)",
};
// #158 옵션 벽: 미국 상장 종목만(Cboe), 현재가에서 이 범위 안의 벽만 그린다(멀리 있는 벽이 가격 축을 늘이지 않게)
const US_TICKER = /^[A-Z]{1,6}(?:[.-][A-Z])?$/;
const WALL_RANGE = 0.15;
const WALLS_SHOWN = 2;
// #98: 지지 · 저항은 가까운 3개씩만, 피보나치는 켤 때만 — 차트가 선으로 덮이지 않게
const DEFAULT_SHOW: Show = { long: true, levels: true, pb: true, sr: true, all: false, vp: true, trend: true, pattern: false, proj: false, fib: false, fills: true, analysis: true, rsi: true, opt: true, stoch: true, bb: false, osc: true, bz: true, entry: true, gaps: true, ma: true, chan: true, ltrend: true };
// #298 표시 묶음: 가격 칸 위의 선 · 표시만 한 번에 바꾼다(RSI · 스토캐스틱 칸 · #550 이동평균은 그대로). 체크를 손으로 바꾸면 "직접".
// #313 "타점 보기" = 지금가 기준 최적 타점 + 분석 타점선 · 체결 · 분석일만(처음 여는 사람의 기본)
type Preset = "entry" | "sr" | "trend" | "all" | "custom";
const PRESET_LABEL: Record<Preset, string> = { entry: "타점 보기", sr: "지지·저항만", trend: "추세·패턴", all: "전부", custom: "직접" };
const OVERLAYS: (keyof Show)[] = ["levels", "pb", "sr", "all", "vp", "trend", "pattern", "proj", "fib", "fills", "analysis", "opt", "bb", "osc", "bz", "entry", "gaps", "chan", "ltrend", "long"];
const PRESET_ON: Record<Exclude<Preset, "custom">, (keyof Show)[]> = {
  // #701 긴 추세선은 몇 달 동안 지켜진 선이라 어느 묶음에서나(타점 · 지지저항 · 추세) 보인다
  entry: ["entry", "levels", "fills", "analysis", "gaps", "long"],
  sr: ["sr", "vp", "gaps", "long"],
  trend: ["sr", "trend", "pattern", "fib", "chan", "ltrend", "long"],
  all: OVERLAYS,
};
function applyPreset(show: Show, p: Exclude<Preset, "custom">): Show {
  const out = { ...show };
  for (const k of OVERLAYS) out[k] = PRESET_ON[p].includes(k);
  return out;
}
const NEAR_LEVELS = 3;
/**
 * #672 초보 모드 — 가격 칸에는 설명에 나온 선만: 채널 · 짧은 추세선(설명에 나온 것만, beginnerLines) · 이동평균 20.
 * 갭 · 삼각수렴은 설명에 나올 때만. 분석 타점선 · 내 체결 · 분석일은 그대로(그 화면의 주인공).
 * RSI · 스토캐스틱 · 볼린저밴드는 고른 대로 둔다 — 봉과 같은 응답에 실려 바꾸면 차트를 새로 만들어야 해서,
 * 켜고 끌 때마다 보던 자리가 풀리고 지운 차트에 예약된 그리기가 돈다.
 */
function beginnerShow(s: Show, kinds: Set<ChartReadRef["kind"]>): Show {
  return { ...s, pb: false, sr: false, all: false, vp: false, trend: false, pattern: kinds.has("pattern"), proj: false, fib: false,
    opt: false, osc: false, bz: false, entry: false, gaps: kinds.has("gap"), ma: true,
    chan: kinds.has("channel"), ltrend: kinds.has("trendline") };
}
const BEGINNER_MA: MaOn = { 5: false, 20: true, 60: false, 120: false };
/** 강조할 때 나머지 선을 흐리게 — 8자리 색(#rrggbbaa)은 알파를 갈아 끼운다 */
const faded = (c: string) => withAlpha(/^#[0-9a-f]{8}$/i.test(c) ? c.slice(0, 7) : c, 0.18);
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
    // #466 갭 표시가 생기기 전 설정: 고른 묶음을 따르고, "직접" 이면 켠다
    if (typeof raw.show?.gaps !== "boolean") show.gaps = preset === "custom" || PRESET_ON[preset].includes("gaps");
    // #612 평행 채널 · 짧은 추세선이 생기기 전 설정: 고른 묶음을 따르고, "직접" 이면 켠다
    for (const k of ["chan", "ltrend", "long"] as const) if (typeof raw.show?.[k] !== "boolean") show[k] = preset === "custom" || PRESET_ON[preset].includes(k);
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
// #560 전망 기준가와 분석일 종가가 이만큼 넘게 다르면 원뿔을 그리지 않는다
const OUTLOOK_FAR = 0.2;

/** 체결이 든 봉 — 분봉은 체결 시각이 든 봉(장 전 · 시간외는 그날 첫 봉, 그날 봉이 없으면 null), 일봉 이상은 그 거래일이 든 봉. */
function fillBar(f: ReviewRow, rows: { key: Key; day?: string }[], intraday: boolean, ticker: string): Key | null {
  const keys = rows.map((r) => r.key);
  const fillOn = marketDay(f.filled_at, ticker);
  const at = barFor(keys, intraday ? kstStamp(f.filled_at) : fillOn);
  if (at == null) return null;
  if (intraday && rows.find((r) => r.key === at)?.day !== fillOn) return rows.find((r) => r.day === fillOn)?.key ?? null;
  return at;
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

// #438 방향 전망 보기 · 기간 — 이 브라우저에 기억(기본 켜짐 · 1개월)
const OUTLOOK_KEY = "desk.chart.outlook";
type OutlookPrefs = { on: boolean; period: OutlookPeriodId };
function loadOutlookPrefs(): OutlookPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(OUTLOOK_KEY) ?? "{}");
    return { on: raw.on !== false, period: raw.period === "1w" ? "1w" : "1m" };
  } catch {
    return { on: true, period: "1m" };
  }
}
function saveOutlookPrefs(p: OutlookPrefs) {
  try {
    localStorage.setItem(OUTLOOK_KEY, JSON.stringify(p));
  } catch {
    /* 저장 못 해도 이번 화면은 그대로 */
  }
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

const ohlc = (b: Row["bar"]) => ({ open: b.open, high: b.high, low: b.low, close: b.close });
/** 봉 한 칸(초) — 실시간 값이 이보다 오래 끊겼다 오면(가린 탭 · 화면 밖) 그 사이 봉이 빠지므로 봉을 다시 받는다 */
const STEP_SEC: Record<Interval, number> = {
  "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "120m": 7200, "240m": 14400, "1d": 86400, "1w": 7 * 86400, "1M": 28 * 86400, "1y": 365 * 86400,
};

/**
 * #654 마지막 봉 · 현재가를 몇 초마다(GET /api/candles/live, #653) — 차트가 화면에 보이고 탭이 보일 때만 묻는다.
 * 차트를 다시 만들지 않고 series.update 로 마지막 봉만: 진행 중 봉은 반투명 몸통 + 테두리, 새 봉이 열리면 앞 봉은 보통 색으로.
 * 추정 값이 실제 봉 칸에 들면(price_only) 봉은 그대로 두고 "추정" 점선만. 실패(503)면 그린 봉 그대로 둔다.
 * 이 이름표만 몇 초마다 다시 그려진다(큰 차트 컴포넌트는 그대로).
 */
function LiveBarTag({ drawn, ticker, interval, watch, fallback, onStale }: {
  drawn: Drawn | null;
  ticker: string;
  interval: Interval;
  /** 이 요소가 화면 밖이면 쉰다 */
  watch: RefObject<HTMLElement | null>;
  /** 실시간 값이 아직 없을 때 적을 봉 원천 */
  fallback: string;
  /** 봉 한 칸보다 오래 쉬었다 돌아왔다 — 봉을 다시 받아 달라 */
  onStale: () => void;
}) {
  const inView = useInView(watch);
  const [live, setLive] = useState<CandleLive | null>(null);
  useEffect(() => setLive(null), [ticker, interval]);
  const poll = usePoll(drawn && inView ? `candle-live:${ticker}:${interval}` : null, () => api.candlesLive(ticker, interval),
    liveMs(INTRADAY.has(interval)), { onData: setLive });
  const painted = useRef<{ drawn: Drawn; key: Key; bar: Row["bar"] } | null>(null);
  const beforeEstimate = useRef<{
    drawn: Drawn;
    painted: typeof painted.current;
    candles: ReturnType<Drawn["series"]["data"]>;
    volume: ReturnType<Drawn["volume"]["data"]>;
  } | null>(null);
  // 마지막으로 값을 받은 때(차트를 새로 그린 때부터) — 화면 밖 · 가린 탭에서는 묻지 않으므로 오래 비면 쉬다 온 것
  const heard = useRef<{ drawn: Drawn | null; at: number }>({ drawn: null, at: 0 });
  if (heard.current.drawn !== drawn) heard.current = { drawn, at: Date.now() };
  useEffect(() => {
    if (!drawn || !live || !drawn.alive.current || live.interval !== drawn.interval) return;
    const away = Date.now() - heard.current.at > STEP_SEC[drawn.interval] * 1000;
    heard.current = { drawn, at: Date.now() };
    const { series, volume, rows, intraday } = drawn;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    let prev = painted.current?.drawn === drawn ? painted.current : null;
    const bar = live.state === "price_only" ? null : live.bar;
    const key = bar ? (intraday ? kstStamp(bar.at) : kstDay(bar.at)) : null;
    const saved = beforeEstimate.current?.drawn === drawn ? beforeEstimate.current : null;
    if (!saved) beforeEstimate.current = null;
    if (saved && live.source === "yahoo" && live.live === false) {
      // #653 재개 후 지연 실봉이 들어오면 장외 추정봉 전체를 걷고 직전에 그린 실제 봉을 되돌린다.
      series.setData([...saved.candles]);
      volume.setData([...saved.volume]);
      prev = saved.painted;
      painted.current = prev;
      beforeEstimate.current = null;
    }
    if (away) {   // 그 사이 끝난 봉들이 빠진다 — 이어 붙이지 말고 봉을 다시 받는다
      onStale();
      return;
    }
    // 체결이 멈췄다(장 마감 · 추정만) — 진행 중으로 칠했던 봉을 보통 색으로(같은 봉이면 서버의 마지막 값으로)
    if (!bar && prev) {
      const same = live.bar && (intraday ? kstStamp(live.bar.at) : kstDay(live.bar.at)) === prev.key;
      try {
        series.update({ time: time(prev.key), ...ohlc(same && live.bar ? live.bar : prev.bar) });
      } catch {
        /* 이번만 버린다 */
      }
      painted.current = null;
    }
    if (bar && key !== null && rows.length) {
      const lastKey = prev?.key ?? rows[rows.length - 1].key;
      if (key >= lastKey) {   // 더 앞 봉이면(원천이 막 바뀐 때 등) 건드리지 않는다 — update 가 throw 한다
        try {
          if (prev && prev.key < key) series.update({ time: time(prev.key), ...ohlc(prev.bar) }, true);
          if (bar.estimated && (live.ticker === "NQ=F" || live.ticker === "ES=F") && !saved) {
            beforeEstimate.current = { drawn, painted: prev, candles: series.data(), volume: volume.data() };
          }
          const c = css(bar.close >= bar.open ? "--up" : "--down");
          series.update({ time: time(key), ...ohlc(bar), color: live.live === false ? c : withAlpha(c, 0.35),
            borderColor: c, wickColor: c });
          if (bar.volume > 0) volume.update({ time: time(key), value: bar.volume, color: withAlpha(c, 0.33) });
          painted.current = { drawn, key, bar };
        } catch {
          /* 시간축과 맞지 않는 값은 이번만 버린다 */
        }
      }
    }
    if (live.state === "price_only" && live.estimated && live.price != null) {
      const line = series.createPriceLine({ price: live.price, title: "추정", color: css("--muted"), lineWidth: 1,
        lineStyle: LineStyle.Dashed, axisLabelVisible: true });
      return () => {
        if (drawn.alive.current) series.removePriceLine(line);
      };
    }
    // onStale 은 부모의 useCallback — 값이 올 때만 다시 본다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawn, live]);
  if (!live || live.interval !== interval) return fallback ? <>{` · ${fallback}`}</> : null;
  const real = live.live !== false && !live.estimated;
  return (
    <span className={`chart-live-tag${real ? "" : " chart-live-est"}${poll.failing ? " chart-live-stale" : ""}`} title={LIVE_NOTE}>
      {liveLabel(live)}{poll.failing ? " · 다시 시도 중" : ""}
    </span>
  );
}

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
function PriceChartInner({ ticker, tradeDate, plan, title, extraLines, initialInterval, lists = true, startExpanded = false, onClose, notice, banner, onIntervalChange, highlight = null, outlook = null, planLabels, onLineClick, trendPick, onTrendPick, readable = false, readOpen = false }: {
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
  /** #539 머리줄 아래 자리(차트 주소 화면: 상세 분석 시작 · 최근 분석 보기) — 크게 보기 안에서도 보인다 */
  banner?: ReactNode;
  /** #220 봉 주기를 바꿀 때마다 — 차트 탭이 주소(iv=)를 맞춘다 */
  onIntervalChange?: (interval: Interval) => void;
  /** #440 가격 사다리에서 고른 가격 — 같은 가격에 굵은 강조선을 잠깐 긋는다 */
  highlight?: number | null;
  /** #438 run 의 1주 · 1개월 방향 전망 — 일봉에서 마지막 봉 오른쪽에 원뿔 · 경로를 그리고 아래에 한 줄 요약 */
  outlook?: RunOutlook | null;
  /** #466 진입 · 손절 · 목표 선 제목 · 색(시나리오 · 근거 한 단어) — 없으면 "진입" · "손절" · "목표" */
  planLabels?: Partial<Record<"entry_price" | "stop_loss" | "price_target", { title: string; color?: string }>>;
  /** #466 계획선(진입 · 손절 · 목표 · extraLines) 가까이를 누르면 그 선의 가격 — 해설 패널이 근거를 펼친다 */
  onLineClick?: (price: number) => void;
  /**
   * #531 추세선을 누르면 그 선(빈 곳 · Esc 면 null) — 주면 바깥(분석 화면 오른쪽 자리)이 선 카드를 그리고 trendPick 으로 고른 선을 알려 준다.
   * 없으면 차트가 제 아래에 카드를 그린다(큰 차트 화면).
   */
  trendPick?: string | null;
  /** when: 그 선 값의 기준("지금" · "분석 당시") — 카드가 같은 기준으로 말하게 */
  onTrendPick?: (line: Trendline | null, when?: string) => void;
  /** #632 · #672 '지금 이 차트 읽기' 칸(차트만 분석 /api/chart-read · 번호 표식 · 초보 모드)을 둔다 — 큰 차트 · 차트 탭 */
  readable?: boolean;
  /** 그 칸을 켜고 연다(#/chart/…?read=1 — 분석 결과의 "차트만 다시 보기") */
  readOpen?: boolean;
}) {
  const [outlookPrefs, setOutlookPrefs] = useState(loadOutlookPrefs);
  const pickOutlook = (next: Partial<OutlookPrefs>) => {
    const p = { ...outlookPrefs, ...next };
    setOutlookPrefs(p);
    saveOutlookPrefs(p);
  };
  const [prefs] = useState(loadPrefs);
  const [interval, setBarInterval] = useState<Interval>(initialInterval ?? prefs.interval);
  const [showSaved, setShow] = useState<Show>(prefs.show);
  // #632 · #672 지금 이 차트 읽기 — 칸 켜기 · 초보 모드(기본 켬)는 이 브라우저에 기억, 초보 모드면 설명에 나온 선만 남긴다
  const [readPrefs, setReadPrefs] = useState<ReadPrefs>(() => {
    const p = loadReadPrefs();
    return readOpen ? { ...p, on: true } : p;
  });
  const pickRead = (next: Partial<ReadPrefs>) => setReadPrefs((cur) => {
    const out = { ...cur, ...next };
    saveReadPrefs(out);
    return out;
  });
  const readOn = readable && readPrefs.on;
  // 넓은 화면은 펼쳐 두고, 좁은 화면은 차트 위에 접어 둔다(번호 표식은 차트에 그대로)
  const [readExpanded, setReadExpanded] = useState(() => readOpen || typeof window === "undefined" || !window.matchMedia
    || window.matchMedia("(min-width: 1100px)").matches);
  const [chartRead, setChartRead] = useState<ChartRead | null>(null);
  const [readFailed, setReadFailed] = useState(false);
  useEffect(() => {
    setChartRead(null);
    setReadFailed(false);
  }, [ticker]);
  // 서버가 가장 짧은 봉이 끝날 때까지 한 답을 캐시한다 — 1분마다 물어도 새로 계산하는 건 봉이 끝날 때뿐
  usePoll(readOn ? `chart-read:${ticker}` : null, () => api.chartRead(ticker), 60_000, {
    // 서버 캐시가 같은 답을 주면 그대로 — 초보 모드의 선을 1분마다 지우고 다시 그리지 않게
    onData: (r) => { setChartRead((cur) => (cur && cur.ticker === r.ticker && cur.generated_at === r.generated_at ? cur : r)); setReadFailed(false); },
    onError: () => setReadFailed(true),
  });
  const readFrame = useMemo(() => chartRead?.timeframes.find((f) => f.timeframe === interval && !f.error) ?? null, [chartRead, interval]);
  const readMarks = useMemo(() => (readOn ? marksOf(readFrame) : []), [readOn, readFrame]);
  const beginner = readOn && readPrefs.beginner && readMarks.length > 0;
  const readKinds = useMemo(() => refKinds(readMarks), [readMarks]);
  const show: Show = useMemo(() => (beginner ? beginnerShow(showSaved, readKinds) : showSaved), [beginner, showSaved, readKinds]);
  const [readPick, setReadPick] = useState<number | null>(null);
  // 번호가 다시 매겨지면(새 읽기 · 주기 · 종목) 고른 번호를 푼다 — 강조가 말없이 다른 선으로 옮겨 가지 않게
  useEffect(() => setReadPick(null), [ticker, interval, readOn, readMarks]);
  const readId = useId();
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
  // #654 실시간 값이 오래 끊겼다 돌아오면 봉을 다시 받는다 — 지우지 않고 받은 뒤 갈아 끼운다
  const [refreshed, setRefreshed] = useState(0);
  const quietReload = useRef(false);
  const loadingOlder = useRef(false);
  const [fills, setFills] = useState<ReviewRow[]>([]);
  // #334 체결 표에서 고른 체결(filled_at) — 그 봉의 마커를 크게
  const [pickedFill, setPickedFill] = useState<string | null>(null);
  // #578 세션 갭 메움 확률(#577 GET /api/gaps) — 띠 이름표와 띠를 누르면 여는 카드
  const [gapRep, setGapRep] = useState<GapReport | null>(null);
  const [gapPick, setGapPick] = useState<OpenGap | null>(null);
  const closeGap = useCallback(() => setGapPick(null), []);
  // #612 평행 채널 · 짧은 추세선(#611 /api/lines) — 이 주기(5m · 15m · 60m · 240m · 1d)의 선, 누른 선의 설명 카드
  const [linesRep, setLinesRep] = useState<LinesReport | "fake" | null>(null);
  const [linePick, setLinePick] = useState<LinePick | null>(null);
  const closeLine = useCallback(() => setLinePick(null), []);
  const [lineDrawn, setLineDrawn] = useState<{ pick: LinePick; series: ISeriesApi<"Line"> }[]>([]);
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
  const [accumulationView, selectAccumulationView] = useState<AccumulationViewKey | null>(null);
  const selectedAccumulation = accumulationView
    ?? (interval === "1d" ? "mid" : isIntradayTf(interval) ? "short" : "long");
  const accumulationTime = useCallback((at: string): Time | null => {
    if (!drawn || !Number.isFinite(Date.parse(at))) return null;
    const key = drawn.intraday ? kstStamp(at) : kstDay(at);
    return drawn.rows.some((row) => row.key === key) ? key as Time : null;
  }, [drawn]);
  useAccumulationOverlay(
    drawn, readOn ? chartRead?.accumulation?.[selectedAccumulation] : null,
    chartRead?.legs?.[selectedAccumulation], accumulationTime,
    { accent: css("--sr-resistance"), muted: css("--muted"), surface: css("--surface") }, "12px sans-serif",
  );
  const reloadBars = useCallback(() => {
    // 보던 논리 범위 그대로(봉 수가 같으면 오른쪽 끝이 새 마지막 봉)
    const r = drawn?.alive.current ? drawn.chart.timeScale().getVisibleLogicalRange() : null;
    if (r && drawn) keepView.current = { from: r.from, to: r.to, bars: drawn.rows.length };
    quietReload.current = true;
    setRefreshed((n) => n + 1);
  }, [drawn]);
  const box = useRef<HTMLDivElement>(null);
  // #550 이동평균 선마다 켜고 끄기(브라우저에 기억) · 보조 칸 이름표 위치 · 펼친 설명
  const [maOnSaved, setMaOn] = useState<MaOn>(loadMaOn);
  const maOn: MaOn = beginner ? BEGINNER_MA : maOnSaved;
  const flipMa = (n: MaPeriod) => {
    const next = { ...maOnSaved, [n]: !maOn[n] };
    if (beginner) pickRead({ beginner: false });
    setMaOn(next);
    saveMaOn(next);
  };
  const [paneTops, setPaneTops] = useState<Partial<Record<PaneId, number>>>({});
  const [paneHelp, setPaneHelp] = useState<PaneId | null>(null);
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
  // #531 여러 스윙의 선 — 범례로 짧은 · 중간 · 긴 · 지지 · 저항을 켜고 끈다(개발 서버에서 #530 이 없으면 예시 값)
  const [trendFilter, setTrendFilterState] = useState<TrendFilter>(loadTrendFilter);
  const setTrendFilter = (f: TrendFilter) => { setTrendFilterState(f); saveTrendFilter(f); };
  const allTrends: Trendline[] = useMemo(
    () => (show.trend ? devExample(srSource?.trendlines ?? [], srSource?.reference_price) : []), [show.trend, srSource]);
  const trendLines: Trendline[] = useMemo(() => allTrends.filter((t) => passes(t, trendFilter)), [allTrends, trendFilter]);
  const [ownPick, setOwnPick] = useState<string | null>(null);
  const pickedId = onTrendPick ? trendPick ?? null : ownPick;
  const pickedLine = pickedId ? trendLines.find((t) => trendKey(t) === pickedId) ?? null : null;
  const pickTrend = useCallback((t: Trendline | null) => {
    if (onTrendPick) onTrendPick(t); else setOwnPick(t ? trendKey(t) : null);
  }, [onTrendPick]);
  const pickedRef = useRef(pickedId);
  pickedRef.current = pickedId;
  // 고른 선이 거르기 · 기준 바꾸기로 사라지면 고름도 푼다
  useEffect(() => { if (pickedId && !pickedLine) pickTrend(null); }, [pickedId, pickedLine, pickTrend]);
  const patterns: ChartPattern[] = useMemo(() => (show.pattern ? srSource?.patterns ?? [] : []), [show.pattern, srSource]);
  // #630 고른 패턴(목록 칩 · 다시 누르면 풀림) — 강조해 그리고 돌파 예상 구간 띠 · 꼭짓점 · 목표선 · 설명 카드
  const [patSel, setPatSel] = useState<string | null>(null);
  // #695 헤드앤숄더: 패턴 겹(삼각수렴과 같은 스위치)이 켜졌을 때 넥라인 · 목표
  const hsList: HsPattern[] = useMemo(() => (show.pattern ? srSource?.hs ?? [] : []), [show.pattern, srSource]);
  const picked = patterns.find((p) => patternKey(p) === patSel) ?? null;
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
  const [trendDrawn, setTrendDrawn] = useState<{ line: Trendline; series: ISeriesApi<"Line">; base: string }[]>([]);

  useEffect(() => savePrefs(interval, showSaved, preset, custom), [interval, showSaved, preset, custom]);
  useEffect(() => onIntervalChange?.(interval), [interval, onIntervalChange]);
  const toggle = (k: keyof Show) => {
    // 초보 모드에서 손으로 고르면 그 모드를 끄고 저장해 둔 고름에서 — 보이던 칸만 뒤집는다
    const next = { ...showSaved, [k]: !show[k] };
    if (beginner) pickRead({ beginner: false });
    setShow(next);
    if (OVERLAYS.includes(k)) {   // 손으로 고르면 그 고름이 "직접"
      setPreset("custom");
      setCustom(next);
    }
  };
  const pickPreset = (p: Preset) => {
    if (beginner) pickRead({ beginner: false });
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
      if (e.key !== "Escape" || e.defaultPrevented) return;   // #539 위 층(상세 분석 확인 칸)이 먼저 닫았다
      if (menu.current?.open) {
        menu.current.open = false; // 열린 표시 메뉴만 닫는다
        return;
      }
      if (pickedRef.current) { pickTrend(null); return; }   // #531 고른 추세선이 있으면 그것만 푼다
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

  // #550 이동평균(SMA) — 봉 종가로 계산, 앞쪽 n-1 봉은 비운다
  const maValues = useMemo(() => {
    if (!drawn) return null;
    const closes = drawn.rows.map((r) => r.bar.close);
    return Object.fromEntries(MA_PERIODS.map((n) => [n, sma(closes, n)])) as Record<MaPeriod, (number | null)[]>;
  }, [drawn]);
  const maShown = show.ma && !!maValues;
  // #550 이동평균선 — 가는 선, 축 범위 · 마지막 값 라벨 없이(가격 축을 늘이거나 타점 라벨과 겹치지 않게). 켜고 끌 때 차트는 그대로
  useEffect(() => {
    if (!drawn || !maValues || !show.ma) return;
    const { chart, rows, intraday } = drawn;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const added: ISeriesApi<"Line">[] = [];
    try {
      for (const n of MA_PERIODS) {
        if (!maOn[n]) continue;
        const s = chart.addSeries(LineSeries, { color: css(`--ma${n}`), lineWidth: 1, priceLineVisible: false,
          lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
        added.push(s);
        s.setData(rows.flatMap(({ key }, i) => (maValues[n][i] == null ? [] : [{ time: time(key), value: maValues[n][i] as number }])));
      }
    } catch {
      /* 이동평균을 못 그려도 차트는 그대로 */
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const s of added) chart.removeSeries(s);
    };
  }, [drawn, maValues, show.ma, maOn]);

  // #550 보조 칸 이름표 자리 — 칸 높이는 창 크기 · 크게 보기에 따라 바뀌므로 그릴 때와 크기가 바뀔 때 다시 잰다.
  // 거래량은 가격 칸 아래쪽(축 여백 0.82)에 겹쳐 있다.
  useEffect(() => {
    const el = box.current;
    if (!drawn || !el) {
      setPaneTops({});
      return;
    }
    const measure = () => {
      if (!drawn.alive.current) return;
      const panes = drawn.chart.panes();
      const h = panes.map((p) => p.getHeight());
      const tops: Partial<Record<PaneId, number>> = { vol: Math.round(h[0] * 0.82) };
      let y = h[0] + 1;
      for (let i = 1; i < panes.length; i++) {
        if (i === 1 && drawn.rsi) tops.rsi = y;
        else if (drawn.stoch && (i === 2 || !drawn.rsi)) tops.stoch = y;
        y += h[i] + 1;
      }
      setPaneTops((prev) => (JSON.stringify(prev) === JSON.stringify(tops) ? prev : tops));
    };
    const frame = requestAnimationFrame(measure);
    const ro = new ResizeObserver(() => requestAnimationFrame(measure));
    ro.observe(el);
    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
    };
  }, [drawn]);

  // 십자선 범례: 커서(모바일은 누른 채로) 아래 봉의 시가 · 고가 · 저가 · 종가 · 거래량 · RSI.
  // 마우스가 움직일 때마다 렌더하지 않게 DOM 을 직접 고친다.
  const ohlc = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!drawn) return;
    const { chart, series, volume, rsi, stoch, rows } = drawn;
    const last = rows[rows.length - 1];
    const write = (o: number, h: number, l: number, c: number, v: number | null, r: number | null, prev: number | null,
      k: number | null = null, d: number | null = null, idx = -1) => {
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
      // #627 색만으로 오름 · 내림을 가르지 않게 ▲▼ 와 부호를 같이
      if (chg != null) add("", `${chg > 0 ? "▲ +" : chg < 0 ? "▼ " : ""}${chg.toFixed(2)}%`, chg >= 0 ? "up" : "down");
      if (v != null) add("거래량", v.toLocaleString("ko-KR"));
      if (r != null) add("RSI", r.toFixed(1));
      if (stoch && k != null) add("스토캐스틱", `${k.toFixed(0)}${d != null ? ` / ${d.toFixed(0)}` : ""}`);
      // #550 커서 봉의 이동평균 값 — 켠 선만, 선 색으로
      if (maShown && maValues && idx >= 0) {
        for (const n of MA_PERIODS) {
          const m = maOn[n] ? maValues[n][idx] : null;
          if (m != null) add(`MA${n}`, price(m), `ma-v ma-${n}`);
        }
      }
    };
    const showLast = () => {
      const prev = rows.length > 1 ? rows[rows.length - 2].bar.close : null;
      write(last.bar.open, last.bar.high, last.bar.low, last.bar.close, last.bar.volume, last.bar.rsi ?? null, prev,
        last.bar.stoch_k ?? null, last.bar.stoch_d ?? null, rows.length - 1);
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
        here?.stoch_k ?? null, here?.stoch_d ?? null, here ? i : -1);
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      ohlc.current?.replaceChildren(); // 다른 주기를 불러오는 동안 이전 봉 값을 남기지 않게
      if (drawn.alive.current) chart.unsubscribeCrosshairMove(onMove);
    };
  }, [drawn, maShown, maValues, maOn]);

  useEffect(() => {
    // #466 갭 상자는 분석 당시 레벨(plan 모드)이 아니라 지금 레벨 응답의 gaps 를 쓴다
    if (!((wantSr && mode === "now") || show.gaps) || nowSr) return;
    let live = true;
    setSrFailed(false);
    api.levels(ticker).then((v) => live && setNowSr(v)).catch(() => live && setSrFailed(true));
    return () => {
      live = false;
    };
  }, [wantSr, mode, nowSr, ticker, show.gaps]);

  useEffect(() => {
    setGapRep(null);
    setGapPick(null);
    if (!show.gaps) return;
    let live = true;
    api.gaps(ticker).then((r) => live && setGapRep(r), () => {});   // 없으면 기존 #465 갭 띠만
    return () => {
      live = false;
    };
  }, [ticker, show.gaps]);

  // #612 평행 채널 · 짧은 추세선 — 분봉은 1분마다 다시 받는다(서버는 같은 마지막 봉 · 종가면 다시 계산하지 않는다).
  // DEV 에서 API 가 없으면(#611 전) 그린 봉으로 만든 가짜를 쓴다
  const wantLines = (show.chan || show.ltrend || (show.long && interval === "1d")) && isLineTf(interval);
  useEffect(() => {
    setLinesRep(null);
    setLinePick(null);
  }, [ticker, interval, wantLines]);
  // #626 분봉은 1분, 일봉은 30분마다(가려진 탭은 쉰다)
  usePoll(wantLines && isLineTf(interval) ? `lines:${ticker}:${interval}` : null, () => api.lines(ticker, interval as LineTf), INTRADAY.has(interval) ? 60_000 : 30 * 60_000, {
    onData: (r) => setLinesRep(r),
    onError: (e) => { if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) setLinesRep("fake"); },
  });
  const linesAll: LinesReport | null = useMemo(() => {
    if (linesRep !== "fake") return linesRep;
    if (!drawn || !isLineTf(drawn.interval)) return null;
    return fakeLines(drawn.rows.map((r) => r.bar), drawn.interval, ticker);
  }, [linesRep, drawn, ticker]);
  const lines: LinesReport | null = useMemo(
    () => (beginner && linesAll && linesAll.timeframe === interval ? beginnerLines(linesAll, readMarks) : linesAll),
    [beginner, linesAll, readMarks, interval]);

  // RSI 를 켜고 끄면 다시 받는다(서버가 30초 캐시) — RSI 는 봉과 같은 응답에 실린다.
  useEffect(() => {
    let live = true;
    const quiet = quietReload.current;
    // 과거 봉을 더 받을 땐 지금 차트를 둔 채 받는다(깜빡임 없이)
    if (times === 1 && !quiet) {
      keepView.current = null;
      setCandles(null);
    }
    quietReload.current = false;
    setFailed(false);
    api
      .candles(ticker, interval, wanted,
        [...(show.rsi ? ["rsi"] : []), ...(show.stoch ? ["stoch"] : []), ...(show.bb ? ["bb20"] : [])])
      .then((c) => live && setCandles(c))
      .catch(() => {
        if (!live) return;
        if (times === 1 && !quiet) setFailed(true);
        else keepView.current = null;
      })
      .finally(() => {
        if (live) loadingOlder.current = false;
      });
    return () => {
      live = false;
      loadingOlder.current = false;
    };
  }, [ticker, interval, wanted, show.rsi, show.stoch, show.bb, refreshed]);

  // 현재 세션으로 복기를 조회한다. 권한이 없거나 조회에 실패하면 마커 없이 그린다.
  useEffect(() => {
    let live = true;
    setFills([]);
    api.review(180).then(
      ({ fills }) => live && setFills(fills.filter((f) => baseCode(f.ticker) === baseCode(ticker))),
      () => {},
    );
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
      const up = css("--up");
      const down = css("--down");
      const grid = css("--border");
      chart = createChart(el, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: css("--muted"),
          fontFamily: getComputedStyle(document.body).fontFamily,
          panes: { separatorColor: grid },
        },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
        rightPriceScale: { borderColor: grid },
        // 분봉은 시 · 분까지(시각은 한국 시각), 창 크기를 바꿔도 보던 기간 유지
        timeScale: { borderColor: grid, lockVisibleTimeRangeOnResize: true, timeVisible: intraday, secondsVisible: false },
        // #434 가격 표기는 시리즈 priceFormat 에서 — 차트 전체 priceFormatter 는 시리즈의 눈금 포맷(tickmarksFormatter)을 덮는다
        localization: { locale: "ko-KR", timeFormatter: crosshairTime },
      });
      const rows = toRows(candles.bars, intraday, candles.ticker);
      const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
      // #434 지금가 · 타점 라벨 가까운 눈금은 비운다(뒤로 비쳐 겹쳐 보였다)
      const series: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
        priceFormat: { type: "custom", minMove: 0.01, formatter: (v: number) => price(v), tickmarksFormatter: ticksClearOfLabels(() => [series], price) },
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
      const osc = (v: number) => v.toFixed(0);
      if (candles.indicators?.includes("rsi14")) {
        const own: ISeriesApi<"Line">[] = [];
        rsi = chart.addSeries(LineSeries, {
          color: css("--warn"), lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
          // #434 작은 패널에서 70 · 30 라벨과 눈금 80 · 0 이 겹쳐 "70/80" 으로 보였다 — 라벨 가까운 눈금은 비운다
          priceFormat: { type: "custom", formatter: osc, tickmarksFormatter: ticksClearOfLabels(() => own, osc), minMove: 0.01 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        }, 1);
        // RSI 축은 늘 0~100 일반 눈금 — 주 · 월 · 년봉의 로그 눈금이 번지지 않게.
        own.push(rsi);
        rsi.priceScale().applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
        rsi.setData(rows.flatMap(({ key, bar }) => (bar.rsi == null ? [] : [{ time: time(key), value: bar.rsi }])));
        const muted = css("--muted");
        // #550 기준선 옆 짧은 이름표
        for (const [lv, name] of [[70, "과열"], [30, "과매도"]] as const) rsi.createPriceLine({ price: lv, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: name });
        chart.panes()[1]?.setHeight(rsiHeight(el.clientHeight));
      }

      // #206 볼린저밴드(20, 2σ) — 가격 패널, 상 · 하단 실선 · 가운데 점선
      if (candles.indicators?.includes("bb20")) {
        const band = css("--muted");
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
        const own: ISeriesApi<"Line">[] = [];
        const scale = { autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
          priceFormat: { type: "custom" as const, formatter: osc, tickmarksFormatter: ticksClearOfLabels(() => own, osc), minMove: 0.01 } };
        stoch = chart.addSeries(LineSeries, { color: css("--accent"), lineWidth: 2, priceLineVisible: false,
          lastValueVisible: true, ...scale }, pane);
        own.push(stoch);
        stoch.priceScale().applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
        stoch.setData(rows.flatMap(({ key, bar }) => (bar.stoch_k == null ? [] : [{ time: time(key), value: bar.stoch_k }])));
        const d = chart.addSeries(LineSeries, { color: css("--warn"), lineWidth: 1, lineStyle: LineStyle.Dashed,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...scale }, pane);
        d.setData(rows.flatMap(({ key, bar }) => (bar.stoch_d == null ? [] : [{ time: time(key), value: bar.stoch_d }])));
        const muted = css("--muted");
        for (const [lv, name] of [[80, "과열"], [20, "침체"]] as const) stoch.createPriceLine({ price: lv, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: name });
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
        .map((w): [number, string, string, LineStyle] => [w.strike, "콜벽", css("--sr-resistance"), LineStyle.Dashed]),
      ...optionData.put_walls.filter((w) => nearPrice(w.strike)).slice(0, WALLS_SHOWN)
        .map((w): [number, string, string, LineStyle] => [w.strike, "풋벽", css("--sr-support"), LineStyle.Dashed]),
      ...(optionData.max_pain.strike != null && nearPrice(optionData.max_pain.strike)
        ? [[optionData.max_pain.strike, "맥스페인", css("--warn"), LineStyle.SparseDotted] as [number, string, string, LineStyle]]
        : []),
    ] : [];
    // #436 진입을 사이에 두지 않는 등급상 타점(손절 = 목표 등)은 그리지 않는다 — 조건선 · 띠는 그대로
    const ownLevels: [number | null | undefined, string, string, LineStyle][] = levelsContradict(plan) ? [] : [
      // #466 근거가 있으면 "롱 진입 · 갭 메움" 처럼 시나리오 · 근거 한 단어, 관망 계획은 시나리오 색
      [plan?.entry_price, planLabels?.entry_price?.title ?? "진입", planLabels?.entry_price?.color ?? css("--accent"), LineStyle.Solid],
      // #311 손절 = 손실색 · 목표 = 이익색(롱 · 숏 어느 관점이든 같은 뜻)
      [plan?.stop_loss, planLabels?.stop_loss?.title ?? "손절", planLabels?.stop_loss?.color ?? css("--danger"), LineStyle.Solid],
      [plan?.price_target, planLabels?.price_target?.title ?? "목표", planLabels?.price_target?.color ?? css("--ok"), LineStyle.Solid],
    ];
    const planLines: [number | null | undefined, string, string, LineStyle][] = show.levels
      ? [
          ...ownLevels,
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
      const color = css("--long");
      // #310 지금가가 이미 지나간 옛 단계는 그리지 않는다
      for (const s of (bottom.ladder ?? []).filter((x) => x.status !== "passed")) {
        made.push(series.createPriceLine({ price: s.price, title: `줍줍${s.step} ${s.weight_pct}%`, color: withAlpha(color, 0.7),
          lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: false }));
      }
      const voidAt = bottom.invalidation?.monthly_close_below;
      if (voidAt != null) {
        made.push(series.createPriceLine({ price: voidAt, title: "바닥 판단 접기(월봉)", color: css("--muted"),
          lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false }));
      }
    }
    // 오른쪽 축의 가까운 라벨은 하나로(좌표는 그려진 뒤에 나온다)
    const stopLabels = keepAxisLabelsApart(drawn.chart, series, labelled, 16, () => drawn.alive.current);
    const near = planLines.map(([v]) => v).filter((v): v is number => v != null && Math.abs(v - lastClose) / lastClose <= AUTOSCALE_MAX_GAP);
    series.applyOptions({
      autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = base();
        if (!r || near.length === 0) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...near), maxValue: Math.max(r.priceRange.maxValue, ...near) } };
      },
    });

    const up = css("--up");
    const down = css("--down");
    const keys = rows.map((r) => r.key);
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const list: { key: Key; m: Omit<SeriesMarker<Time>, "time"> }[] = [];
    if (show.analysis) {
      // 분봉: 분석한 거래일의 첫 봉, 일봉 이상: 그 날짜가 든 봉
      // 주말 · 휴장일 분석이면 그 전 거래일 — 일봉과 같은 규칙
      const day = intraday ? barFor(rows.map((r) => r.day), tradeDate) : null;
      const at = intraday ? (day == null ? null : rows.find((r) => r.day === day)?.key ?? null) : barFor(keys, tradeDate);
      if (at != null) list.push({ key: at, m: { position: "aboveBar", shape: "arrowDown", color: css("--warn"), text: "분석" } });
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
        if (g.buy && g.sell) list.push({ key: at, m: { position: "aboveBar", shape: "circle", size, color: css("--warn"), text } });
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
          color: hot ? css("--warn") : css("--accent"), text } });
      }
    }
    list.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    markers.setMarkers(list.map(({ key, m }) => ({ ...m, time: time(key) }) as SeriesMarker<Time>));
    return () => {
      // 봉이 바뀌면 차트 effect 가 먼저 chart.remove() 한다 — 그때는 지울 선도 이미 없다.
      stopLabels(drawn.alive.current);
      if (drawn.alive.current) for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, plan, fills, pickedFill, tradeDate, ticker, show.levels, show.analysis, show.fills, extraLines, show.opt, optionData, show.osc, osc, show.bz, bottom, planLabels]);

  // #440 가격 사다리에서 고른 가격 — 겹친 라벨과 상관없이 그 가격 하나만 굵게
  useEffect(() => {
    if (!drawn || highlight == null) return;
    const line = drawn.series.createPriceLine({ price: highlight, title: "▶", color: css("--warn"), lineWidth: 3,
      lineStyle: LineStyle.Solid, axisLabelVisible: true });
    return () => {
      if (drawn.alive.current) drawn.series.removePriceLine(line);
    };
  }, [drawn, highlight]);

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
    const muted = css("--muted");
    const profile = vp
      ? volumeProfileDrawing(vp, { bar: muted, thick: css("--warn"), poc: css("--warn"), label: muted }, price)
      : { bands: [], marks: [], bars: [] };
    // 플레이북(#117) — 분석 당시 계획의 값(지금 기준으로 다시 계산하지 않는다)
    const book = show.pb && plan?.playbooks
      ? playbookDrawing(plan.playbooks, { long: css("--long"), short: css("--short") }, price)
      : { bands: [], marks: [] };
    // #321 저가매수 구간 — 지지 · 저항을 켰을 때만(가까운 지지 몇 개)
    const buyZones = buyZoneDrawing(srShown.filter((lv) => lv.kind === "support"), css("--ok"), price);
    // #466 안 메운 갭 — 생긴 봉부터 오른쪽으로 옅은 상자(아직 안 메운 부분), 가까운 것 몇 개만
    const gapColor = css("--warn");
    const gapFrom = (date: string): Time | null => {
      if (!intraday) {
        const k = barFor(rows.map((r) => r.key), date as Key);
        return k == null ? null : time(k);
      }
      const d = barFor(rows.map((r) => r.day), date as Key);
      const k = d == null ? null : rows.find((r) => r.day === d)?.key ?? null;
      return k == null ? null : time(k);
    };
    // #578 세션 갭(전 세션 종가 → 새 세션 시가) — 이 종목 자신의 갭이면 띠 + 메움 확률 이름표, 레버리지 상품이면 기초 갭의 환산 메움 가격 표시
    const own = !!gapRep && gapRep.basis.leverage == null && gapRep.basis.symbol === gapRep.ticker;
    const sessionGaps = show.gaps && gapRep ? gapRep.open.filter((g) => Math.abs(g.to_fill_pct) <= 30) : [];
    const sessionBands = own
      ? sessionGaps.map((g) => ({
          top: Math.max(g.fill_price, g.open), bottom: Math.min(g.fill_price, g.open),
          fill: withAlpha(gapColor, 0.16), edge: withAlpha(gapColor, 0.7),
          label: `세션 갭 · ${oddsLabel(g.odds)}`, labelColor: gapColor, from: gapFrom(g.day),
        }))
      : [];
    const productMarks = !own
      ? sessionGaps.filter((g) => g.product_fill_price != null).map((g) => ({
          price: g.product_fill_price as number, color: withAlpha(gapColor, 0.9),
          label: `기초 갭 메움≈ · ${oddsLabel(g.odds)}`,
        }))
      : [];
    // 같은 날 · 같은 방향의 일봉 갭(#465)은 세션 갭 띠와 겹치니 세션 갭 쪽만 그린다
    const sameDay = new Set(own ? sessionGaps.map((g) => `${g.day}|${g.direction}`) : []);
    const gapBands = show.gaps
      ? (nowSr?.gaps ?? []).filter((g) => Math.abs(g.distance_pct) <= 30)
        .filter((g) => !(g.timeframe === "1d" && sameDay.has(`${g.formed_at}|${g.direction}`))).slice(0, 4).map((g) => ({
          top: g.top, bottom: g.bottom, fill: withAlpha(gapColor, 0.12), edge: withAlpha(gapColor, 0.5),
          label: `${g.timeframe === "1w" ? "주봉" : "일봉"} 갭 · 메움 ${price(g.fill_price)}`, labelColor: gapColor, from: gapFrom(g.formed_at),
        }))
      : [];
    bands.set(
      [
        ...profile.bands,
        ...book.bands,
        ...(show.levels ? planBands(plan, at == null ? null : time(at), { loss: css("--danger"), profit: css("--ok") }) : []),
        ...srBands(srShown, srSource?.atr, { support: css("--sr-support"), resistance: css("--sr-resistance") }, price),
        ...buyZones.bands,
        ...gapBands,
        ...sessionBands,
      ],
      [
        ...buyZones.marks,
        ...productMarks,
        ...(fib?.levels ?? []).filter((lv) => Number.isFinite(lv.price)).map((lv) => ({ price: lv.price, color: withAlpha(muted, 0.85), label: String(lv.ratio) })),
        ...profile.marks,
        ...book.marks,
      ],
      profile.bars,
    );
    return () => {
      if (drawn.alive.current) series.detachPrimitive(bands);
    };
  }, [drawn, srShown, srSource, plan, fib, vp, show.levels, show.pb, tradeDate, show.gaps, nowSr, gapRep]);

  // #531 누른 자리에서 가장 가까운(10px 안) 추세선 — 계획선 클릭(#466)과 추세선 클릭이 같은 자리면 가까운 쪽만 반응하게 둘이 같이 쓴다
  const nearestTrend = useCallback((param: MouseEventParams<Time>): { line: Trendline; d: number } | null => {
    const pt = param.point;
    if (!drawn || !pt || (param.paneIndex ?? 0) !== 0) return null;
    let best: { line: Trendline; d: number } | null = null;
    for (const { line, series: s } of trendDrawn) {
      const v = param.seriesData.get(s) as { value?: number } | undefined;
      if (v?.value == null) continue;
      const y = drawn.series.priceToCoordinate(v.value);
      if (y == null) continue;
      const d = Math.abs(y - pt.y);
      if (d <= 10 && (!best || d < best.d)) best = { line, d };
    }
    return best;
  }, [drawn, trendDrawn]);

  // #612 누른 자리에서 가장 가까운(10px 안) 평행 채널 · 짧은 추세선 — 갭 띠 클릭(#578)도 이 선에 양보한다
  const nearestLine = useCallback((param: MouseEventParams<Time>): { pick: LinePick; d: number } | null => {
    const pt = param.point;
    if (!drawn || !pt || (param.paneIndex ?? 0) !== 0) return null;
    let best: { pick: LinePick; d: number } | null = null;
    for (const { pick, series: s } of lineDrawn) {
      const v = param.seriesData.get(s) as { value?: number } | undefined;
      const y = v?.value == null ? null : drawn.series.priceToCoordinate(v.value);
      if (y == null) continue;
      const d = Math.abs(y - pt.y);
      if (d <= 10 && (!best || d < best.d)) best = { pick, d };
    }
    return best;
  }, [drawn, lineDrawn]);

  // #578 세션 갭 띠 안을 누르면 그 갭의 메움 확률 카드 — 띠 밖을 누르면 닫는다. 그린 띠(지금에서 30% 안, 갭이 생긴 봉부터 오른쪽)만
  // 반응하고, 추세선 가까이를 누른 것은 추세선 카드(#531)에 맡긴다
  useEffect(() => {
    if (!drawn || !gapRep || !show.gaps || gapRep.basis.leverage != null || gapRep.basis.symbol !== gapRep.ticker) return;
    const { chart, series, rows } = drawn;
    const gaps = gapRep.open.filter((g) => Math.abs(g.to_fill_pct) <= 30);
    const onClick = (param: MouseEventParams<Time>) => {
      if (!drawn.alive.current) return;
      const pt = param.point;
      if (!pt || (param.paneIndex ?? 0) !== 0 || rows.length === 0) return;
      if (nearestTrend(param) || nearestLine(param)) return;
      const p = series.coordinateToPrice(pt.y);
      const at = chart.timeScale().coordinateToLogical(pt.x);   // 오른쪽 빈 칸(미래)도 마지막 봉으로
      if (p == null || at == null) return;
      const day = rows[Math.min(rows.length - 1, Math.max(0, Math.round(at)))].day;
      const hit = gaps.find((g) => day >= g.day && p >= Math.min(g.fill_price, g.open) && p <= Math.max(g.fill_price, g.open));
      setGapPick(hit ?? null);
    };
    chart.subscribeClick(onClick);
    return () => {
      if (drawn.alive.current) chart.unsubscribeClick(onClick);
    };
  }, [drawn, gapRep, show.gaps, nearestTrend, nearestLine]);
  // 누를 수 있는 계획선 가격(#466 과 같은 규칙) — 추세선 클릭이 더 가까운 계획선에 양보할 때 쓴다
  const planLinePrices = useMemo(() => (!onLineClick || !show.levels ? [] : [
    ...(levelsContradict(plan) ? [] : [plan?.entry_price, plan?.stop_loss, plan?.price_target]),
    ...(extraLines ?? []).map((l) => l.price),
  ].filter((p): p is number => p != null && Number.isFinite(p))), [onLineClick, show.levels, plan, extraLines]);

  // #466 계획선 가까이(12px)를 누르면 그 선의 가격을 알린다 — 해설 패널이 근거를 펼친다
  useEffect(() => {
    // 실제로 그린 선만 — 타점선을 끄면 없고, 진입을 사이에 두지 않는 계획(#436)은 진입 · 손절 · 목표를 그리지 않는다
    if (!drawn || !onLineClick || !show.levels) return;
    const { chart, series } = drawn;
    const own = levelsContradict(plan) ? [] : [plan?.entry_price, plan?.stop_loss, plan?.price_target];
    const prices = [...own, ...(extraLines ?? []).map((l) => l.price)]
      .filter((p): p is number => p != null && Number.isFinite(p));
    const onClick = (param: MouseEventParams<Time>) => {
      const y = param.point?.y;
      if (y == null) return;
      let best: { p: number; d: number } | null = null;
      for (const p of prices) {
        const c = series.priceToCoordinate(p);
        if (c == null) continue;
        const d = Math.abs(c - y);
        if (d <= 12 && (!best || d < best.d)) best = { p, d };
      }
      // #531 같은 자리에 더 가까운 추세선이 있으면 그쪽(추세선 클릭)만 반응한다
      const t = nearestTrend(param);
      if (best && !(t && t.d < (best as { d: number }).d)) onLineClick((best as { p: number }).p);
    };
    chart.subscribeClick(onClick);
    return () => {
      if (drawn.alive.current) chart.unsubscribeClick(onClick);
    };
  }, [drawn, plan, extraLines, onLineClick, show.levels, nearestTrend]);

  // #313 타점 보기 — 자기 띠 · 선을 붙이고 뗀다(다른 그리기와 섞지 않는다)
  useEffect(() => {
    if (!drawn || !show.entry || !entry) return;
    return drawEntry(drawn.series, entry, {
      long: css("--long"), short: css("--short"), loss: css("--danger"),
      profit: css("--ok"), muted: css("--muted"),
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
    const lines: { line: Trendline; series: ISeriesApi<"Line">; base: string }[] = [];
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
        const base = css(line.kind === "support" ? "--sr-support" : "--sr-resistance");
        const broken = line.state === "broken";
        // #531 굵기 = 스윙 크기(짧게 얇게 · 길게 굵게), 먼 선(far)은 점선, 이탈은 흐린 파선. 끝 이름표는 가격축에("중기 저항 · +18%")
        const s = chart.addSeries(LineSeries, {
          ...quiet, color: broken ? `${base}80` : base, lineWidth: trendWidth(line),
          lineStyle: broken ? LineStyle.Dashed : line.status === "far" ? LineStyle.Dotted : LineStyle.Solid,
          lastValueVisible: true, title: trendLabel(line),
        });
        lines.push({ line, series: s, base });
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
        const color = css("--muted");   // 비율 가로선은 띠 effect 가 왼쪽 글자와 함께 그린다
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

  // #612 평행 채널 — 하단 굵은 실선 · 상단 얇은 실선 · 가운데 흐린 파선 · 바깥(폭 한 칸 밖) 점선, 둘째 채널은 흐리게.
  // 짧은 추세선은 다른 색(주황), 이탈한 선은 파선 + 이탈 봉에 화살표. 선은 로그 가격 × 봉 순서 직선(#611 과 같게),
  // 축 범위엔 넣지 않는다(바깥 선이 봉을 누르지 않게). 가격축 이름표는 첫 채널 상 · 하단과 추세선만.
  useEffect(() => {
    if (!drawn || !lines || lines.timeframe !== drawn.interval || (!show.chan && !show.ltrend && !show.long)) return;
    const { chart, series, rows, intraday } = drawn;
    if (rows.length < 2) return;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const keys = rows.map((r) => r.key);
    const first = Date.parse(rows[0].bar.at);
    const index = new Map(keys.map((k, i) => [k, i]));
    const idxOf = (iso: string | null | undefined): number | null => {
      if (!iso) return null;
      const t = Date.parse(iso);
      if (!Number.isFinite(t) || t < first) return null;
      const k = barFor(keys, intraday ? kstStamp(iso) : kstDay(iso));
      return k == null ? null : index.get(k) ?? null;
    };
    const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null };
    const made: { pick: LinePick; series: ISeriesApi<"Line"> }[] = [];
    const marks: { key: Key; m: Omit<SeriesMarker<Time>, "time"> }[] = [];
    type Style = { color: string; lineWidth: 1 | 2 | 3 | 4; lineStyle: LineStyle; lastValueVisible?: boolean; title?: string };
    // 두 점(또는 끝점 + 봉당 기울기)으로, 그 선이 시작한 봉(보는 기간 전이면 첫 봉)부터 마지막 봉까지
    const draw = (a: { at: string; price: number }, b: { at: string; price: number }, slopePct: number, style: Style, pick: LinePick) => {
      const ia = idxOf(a.at);
      const ib = idxOf(b.at) ?? rows.length - 1;
      const k = ia != null && ib > ia && a.price > 0 && b.price > 0 ? Math.log(b.price / a.price) / (ib - ia) : Math.log(1 + slopePct / 100);
      const from = ia ?? (Date.parse(a.at) < first ? 0 : null);
      if (from == null || !(b.price > 0)) return;
      const data = [];
      for (let i = from; i < rows.length; i++) {
        const v = b.price * Math.exp(k * (i - ib));
        if (Number.isFinite(v) && v > 0) data.push({ time: time(rows[i].key), value: v });
      }
      if (data.length < 2) return;
      const s = chart.addSeries(LineSeries, { ...quiet, ...style });
      s.setData(data);
      made.push({ pick, series: s });
    };
    try {
      if (show.chan) {
        lines.channels.slice(0, 2).forEach((c, ci) => {
          const accent = css("--accent");
          const color = accent + (ci === 0 ? "" : "99");
          const faint = accent + (ci === 0 ? "88" : "55");
          const pick: LinePick = { kind: "channel", id: c.id };
          const name = ci === 0 ? "채널" : "채널2";
          draw(c.lower.start, c.lower.end, c.slope_pct_per_bar, { color, lineWidth: 3, lineStyle: LineStyle.Solid, lastValueVisible: ci === 0, title: `${name} 하단` }, pick);
          draw(c.upper.start, c.upper.end, c.slope_pct_per_bar, { color, lineWidth: 1, lineStyle: LineStyle.Solid, lastValueVisible: ci === 0, title: `${name} 상단` }, pick);
          draw(c.mid.start, c.mid.end, c.slope_pct_per_bar, { color: faint, lineWidth: 1, lineStyle: LineStyle.Dashed }, pick);
          draw(c.outer_upper.start, c.outer_upper.end, c.slope_pct_per_bar, { color: faint, lineWidth: 1, lineStyle: LineStyle.Dotted }, pick);
          draw(c.outer_lower.start, c.outer_lower.end, c.slope_pct_per_bar, { color: faint, lineWidth: 1, lineStyle: LineStyle.Dotted }, pick);
          const bi = idxOf(c.broke_at);
          if (bi != null && (c.state === "above" || c.state === "below")) {
            const up = c.state === "above";
            marks.push({ key: keys[bi], m: { position: up ? "belowBar" : "aboveBar", shape: up ? "arrowUp" : "arrowDown", color, text: "채널 밖" } });
          }
        });
      }
      if (show.ltrend) {
        const base = css("--warn");
        for (const t of lines.trendlines) {
          if (!t.anchors[0]) continue;
          const broken = t.state === "broken";
          const label = `${t.kind === "support" ? "상승선" : "하락선"}${broken ? " 이탈" : ""}`;
          draw(t.anchors[0], t.end, t.slope_pct_per_bar,
            { color: broken ? `${base}99` : base, lineWidth: 2, lineStyle: broken ? LineStyle.Dashed : LineStyle.Solid, lastValueVisible: true, title: label },
            { kind: "trend", id: t.id });
          const bi = idxOf(t.broken_at);
          if (bi != null) {
            const down = t.kind === "support";
            marks.push({ key: keys[bi], m: { position: down ? "aboveBar" : "belowBar", shape: down ? "arrowDown" : "arrowUp", color: base, text: "이탈" } });
          }
        }
      }
      // #701 긴 추세선(일봉 차트, #679) — 굵게(4), 지지는 지지색 · 저항은 저항색, 이탈은 파선. 닿은 자리마다 ↑(지지) · ↓(저항),
      // 거짓 이탈(종가가 잠깐 넘었다 돌아온 구간)은 시작 봉에 옅은 점. 좁은 차트는 가격축 이름표를 빼고 표식만
      if (show.long && drawn.interval === "1d") {
        const narrow = (box.current?.clientWidth ?? 1000) < 520;
        for (const t of lines.long_lines ?? []) {
          if (!t.anchors[0]) continue;
          const sup = t.kind === "support";
          const color = css(sup ? "--sr-support" : "--sr-resistance");
          const broken = t.status === "broken";
          const label = `${t.timeframe === "1w" ? "주봉 " : ""}장기 ${sup ? "지지" : "저항"}${t.status === "testing" ? " 시험 중" : broken ? " 이탈" : ""}`;
          const style = { color: broken ? withAlpha(color, 0.6) : color, lineWidth: 4 as const, lineStyle: broken ? LineStyle.Dashed : LineStyle.Solid,
            lastValueVisible: !narrow, title: narrow ? "" : label };
          if (t.timeframe === "1w") {
            // 주봉 선은 기울기가 주봉 한 봉 기준이고 첫 기준점이 일봉 차트(130봉) 앞인 일이 많다 — 봉 순서가 아니라 시각으로
            // 두 점(첫 기준점 · 끝점)을 로그 가격 직선으로 잇는다(주봉은 시각 간격이 고르다)
            const ta = Date.parse(t.anchors[0].at), te = Date.parse(t.end.at);
            if (!(te > ta) || !(t.anchors[0].price > 0) || !(t.end.price > 0)) continue;
            const kt = Math.log(t.end.price / t.anchors[0].price) / (te - ta);
            const data = [];
            for (const r of rows) {
              const tr = Date.parse(r.bar.at);
              if (tr < ta) continue;
              const v = t.end.price * Math.exp(kt * (tr - te));
              if (Number.isFinite(v) && v > 0) data.push({ time: time(r.key), value: v });
            }
            if (data.length >= 2) {
              const s = chart.addSeries(LineSeries, { ...quiet, ...style });
              s.setData(data);
              made.push({ pick: { kind: "long", id: t.id }, series: s });
            }
          } else {
            draw(t.anchors[0], t.end, t.slope_pct_per_bar, style, { kind: "long", id: t.id });
          }
          for (const tp of t.touch_points) {
            const bi = idxOf(tp.at);
            if (bi != null) marks.push({ key: keys[bi], m: { position: sup ? "belowBar" : "aboveBar", shape: sup ? "arrowUp" : "arrowDown", color, text: "" } });
          }
          for (const fb of t.false_breaks) {
            const bi = idxOf(fb.from);
            if (bi != null) marks.push({ key: keys[bi], m: { position: sup ? "belowBar" : "aboveBar", shape: "circle", color: withAlpha(color, 0.45), text: narrow ? "" : "거짓 이탈" } });
          }
        }
      }
    } catch {
      /* 이상한 선 하나 때문에 차트가 깨지지 않게 — 그린 것까지만 */
    }
    marks.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    const breakMarks = createSeriesMarkers(series, marks.map(({ key, m }) => ({ ...m, time: time(key) }) as SeriesMarker<Time>));
    setLineDrawn(made);
    return () => {
      setLineDrawn([]);
      if (!drawn.alive.current) return;
      breakMarks.detach();
      for (const { series: s } of made) chart.removeSeries(s);
    };
  }, [drawn, lines, show.chan, show.ltrend, show.long]);

  // #672 번호 표식 — 설명 칸의 ①… 을 차트 오른쪽 끝(마지막 봉 위) 그 가격 높이에. 확대 · 이동 · 크기 변화 때 다시 놓는다
  const [markPos, setMarkPos] = useState<{ n: number; x: number; y: number }[]>([]);
  useEffect(() => {
    if (!drawn || readMarks.length === 0) {
      setMarkPos([]);
      return;
    }
    const { chart, series } = drawn;
    let raf = 0;
    const place = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        if (!drawn.alive.current) return;
        const w = chart.timeScale().width();
        const h = chart.panes()[0]?.getHeight() ?? 0;
        const out: { n: number; x: number; y: number }[] = [];
        for (const m of readMarks) {
          const y = series.priceToCoordinate(m.level.price);
          if (y == null || y < 8 || y > h - 8) continue;   // 축 범위 밖이면 설명 칸에만
          const near = out.filter((o) => Math.abs(o.y - y) < 18).length;   // 가까운 번호끼리 겹치지 않게 왼쪽으로
          out.push({ n: m.n, x: w - 16 - near * 22, y });
        }
        setMarkPos(out);
      });
    };
    place();
    const ts = chart.timeScale();
    ts.subscribeVisibleLogicalRangeChange(place);
    ts.subscribeSizeChange(place);
    // 높이만 바뀌어도(설명 칸을 펼침 · 접음, 보조 칸) 가격 높이가 달라진다
    const ro = typeof ResizeObserver === "undefined" || !box.current ? null : new ResizeObserver(place);
    if (ro && box.current) ro.observe(box.current);
    // 가격축만 바뀔 때(실시간 봉이 축을 넓힘 · 축을 위아래로 끎)는 알림이 없다 — 1초마다 첫 번호 높이만 견준다
    let lastY = series.priceToCoordinate(readMarks[0].level.price);
    const stopCheck = every(() => {
      if (!drawn.alive.current) return;
      const y = series.priceToCoordinate(readMarks[0].level.price);
      if (y !== lastY) {
        lastY = y;
        place();
      }
    }, 1000);
    return () => {
      cancelAnimationFrame(raf);
      stopCheck();
      ro?.disconnect();
      if (!drawn.alive.current) return;
      ts.unsubscribeVisibleLogicalRangeChange(place);
      ts.unsubscribeSizeChange(place);
    };
  }, [drawn, readMarks]);

  // #672 번호를 누르면 그 가격에 굵은 강조선 + 그 채널 · 추세선은 굵게, 나머지 채널 · 추세선은 흐리게
  useEffect(() => {
    const m = readPick == null ? null : readMarks.find((x) => x.n === readPick);
    if (!drawn || !m) return;
    const { series } = drawn;
    const ids = refIds(m.level);
    const short = m.level.name.split(" · ")[0];
    const line = series.createPriceLine({ price: m.level.price, title: `${MARKS[m.n]} ${short}`, color: css("--warn"), lineWidth: 2,
      lineStyle: LineStyle.Solid, axisLabelVisible: true });
    const restore: [ISeriesApi<"Line">, { color?: string; lineWidth?: 1 | 2 | 3 | 4 }][] = [];
    for (const { pick, series: s } of lineDrawn) {
      try {   // 선을 다시 그리는 사이(초보 모드 · 표시를 바꿈)엔 지운 선이 남아 있을 수 있다
        const o = s.options();
        if (ids.has(pick.id)) {
          restore.push([s, { lineWidth: o.lineWidth }]);
          s.applyOptions({ lineWidth: 3 });
        } else {
          restore.push([s, { color: o.color }]);
          s.applyOptions({ color: faded(o.color) });
        }
      } catch {
        /* 지운 선 */
      }
    }
    return () => {
      if (!drawn.alive.current) return;
      series.removePriceLine(line);
      for (const [s, o] of restore) {
        try {
          s.applyOptions(o);
        } catch {
          /* 그 사이 지운 선 */
        }
      }
    };
  }, [drawn, readPick, readMarks, lineDrawn]);
  const pickMark = (n: number | null, from: "chart" | "panel") => {
    setReadPick(n);
    if (n == null) return;
    if (from === "chart") {
      setReadExpanded(true);
      document.getElementById(`${readId}-${n}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else {
      box.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  };

  // #612 채널 · 짧은 추세선 가까이(10px)를 누르면 그 선의 설명 카드, 선이 아닌 곳을 누르면 닫는다
  useEffect(() => {
    if (!drawn || lineDrawn.length === 0) return;
    const { chart } = drawn;
    const onClick = (param: MouseEventParams<Time>) => {
      if (!drawn.alive.current || !param.point || (param.paneIndex ?? 0) !== 0) return;
      setLinePick(nearestLine(param)?.pick ?? null);
    };
    chart.subscribeClick(onClick);
    return () => {
      if (drawn.alive.current) chart.unsubscribeClick(onClick);
    };
  }, [drawn, lineDrawn, nearestLine]);

  // 그 종류를 끄면 열린 카드도 닫는다
  useEffect(() => {
    if (linePick && ((linePick.kind === "channel" && !show.chan) || (linePick.kind === "trend" && !show.ltrend)
      || (linePick.kind === "long" && !show.long))) setLinePick(null);
  }, [linePick, show.chan, show.ltrend, show.long]);

  // #531 고른 추세선 강조 · 나머지 흐리게. 먼 선(far)을 고르면 그 선 값까지 가격축을 넓혀 보이게 한다
  useEffect(() => {
    for (const { line, series: s, base } of trendDrawn) {
      const sel = pickedId != null && trendKey(line) === pickedId;
      const dim = pickedId != null && !sel;
      const alpha = dim ? "40" : line.state === "broken" ? "80" : "";
      s.applyOptions({
        color: `${base}${alpha}`,
        lineWidth: (sel ? Math.min(4, trendWidth(line) + 1) : trendWidth(line)) as 1 | 2 | 3 | 4,
        lastValueVisible: !dim,
        autoscaleInfoProvider: sel ? () => ({ priceRange: { minValue: line.price_now, maxValue: line.price_now } }) : () => null,
      });
    }
  }, [trendDrawn, pickedId]);

  // #531 추세선 가까이(10px)를 누르면 그 선을 고른다. 선이 아닌 곳을 누르면 고름을 푼다
  useEffect(() => {
    if (!drawn || trendDrawn.length === 0) return;
    const { chart, series } = drawn;
    const onClick = (param: MouseEventParams<Time>) => {
      const pt = param.point;
      if (!pt || (param.paneIndex ?? 0) !== 0) return;
      const hit = nearestTrend(param);
      // 같은 자리에 더 가까운 계획선(#466)이 있으면 그쪽만 반응한다
      const planD = planLinePrices.reduce<number | null>((m, p) => {
        const c = series.priceToCoordinate(p);
        if (c == null) return m;
        const d = Math.abs(c - pt.y);
        return d <= 12 && (m == null || d < m) ? d : m;
      }, null);
      if (hit && (planD == null || hit.d <= planD)) pickTrend(hit.line);
      else if (pickedRef.current) pickTrend(null);
    };
    chart.subscribeClick(onClick);
    return () => {
      if (drawn.alive.current) chart.unsubscribeClick(onClick);
    };
  }, [drawn, trendDrawn, pickTrend, nearestTrend, planLinePrices]);

  // #531 Esc 로 고름을 푼다. 다른 것의 Esc 를 가로채지 않는다 — 맨 위 층(채팅 · 입력 칸 · 대화 상자)이 먼저.
  // 크게 보기는 그 Esc 처리 안에서 "메뉴 → 고름 → 닫기" 순서로 따로 푼다
  useEffect(() => {
    if (!pickedId || expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t?.closest?.("input, textarea, select, [contenteditable], [role=dialog]")) return;
      if (document.querySelector(".cw-panel:not([hidden])")) return;   // 열린 채팅은 채팅이 닫는다
      pickTrend(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [pickedId, pickTrend, expanded]);

  // #531 제어 모드: 기준(분석 당시 ↔ 지금)이 바뀌어 같은 선의 값이 새로 오면 바깥 카드도 새 값 · 기준으로
  useEffect(() => {
    if (onTrendPick && pickedLine) onTrendPick(pickedLine, when);
  }, [pickedLine, when]); // eslint-disable-line react-hooks/exhaustive-deps

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
                  color: css(up ? "--ok" : "--danger"), text: "스윕 후 회복" } as SeriesMarker<Time>);
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
    const warn = css("--warn");
    const sel = patSel && patterns.some((x) => patternKey(x) === patSel) ? patSel : null;
    for (const p of patterns) {
      const mine = sel === patternKey(p);
      const color = sel && !mine ? withAlpha(warn, 0.3) : warn;
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
          const s = chart.addSeries(LineSeries, { ...quiet, color, lineWidth: mine ? 3 : sel ? 1 : 2, lineStyle: mine ? LineStyle.Solid : LineStyle.Dashed });
          s.setData(data);
          added.push(s);
          series.push(s);
        }
        if (mine) drawPicked(p);
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
    // #630 고른 패턴: 돌파 예상 구간(이 차트 주기와 같을 때 — 마지막 봉 뒤 bars_ahead 칸)을 세로 띠로,
    // 꼭짓점을 점으로, 위 · 아래 목표를 점선 가격선(축 이름표)으로. 띠 · 점은 축 범위에 넣지 않는다.
    function drawPicked(p: ChartPattern) {
      const last = rows[rows.length - 1].key;
      const w = p.breakout_window;
      const sameTf = p.timeframe === drawn!.interval;
      const reach = Math.max(sameTf ? Math.ceil(p.apex.bars_ahead) : 0, sameTf && w ? w.bars_ahead[1] : 0);
      const future = reach > 0 ? futureKeys(last, drawn!.interval, Math.min(reach, 400)) : [];
      const keyAt = (bar: number) => (bar <= 0 ? last : future[Math.min(bar, future.length) - 1]);
      if (w && sameTf && !w.passed && future.length) {
        const band = chart.addSeries(HistogramSeries, { priceScaleId: "pattern-band", color: withAlpha(warn, w.basis === "measured" ? 0.2 : 0.12),
          priceLineVisible: false, lastValueVisible: false, base: 0 });
        band.priceScale().applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
        const keys = new Set<Key>();
        for (let b = Math.max(1, w.bars_ahead[0]); b <= w.bars_ahead[1]; b++) keys.add(keyAt(b));
        band.setData([...keys].map((k) => ({ time: time(k), value: 1 })));
        added.push(band as unknown as ISeriesApi<"Line">);
      }
      if (sameTf && p.apex.price > 0 && p.apex.bars_ahead > 0 && future.length) {
        const apex = chart.addSeries(LineSeries, { ...quiet, color: warn, lineVisible: false, pointMarkersVisible: false });
        apex.setData([{ time: time(keyAt(Math.round(p.apex.bars_ahead))), value: p.apex.price }]);
        added.push(apex);
        const m = createSeriesMarkers(apex, [{ time: time(keyAt(Math.round(p.apex.bars_ahead))), position: "inBar", shape: "circle",
          color: warn, text: "꼭짓점", size: 0.8 } as SeriesMarker<Time>]);
        detach.push(() => m.detach());
      }
      const t = p.targets;
      if (t && t.basis !== "omitted") {      // #662 omitted: 목표선 없음(카드에 이유)
        for (const [v, label, c] of [[t.up, "위 목표", css("--long")], [t.down, "아래 목표", css("--short")]] as const) {
          if (v == null || !(v > 0)) continue;
          const line = drawn!.series.createPriceLine({ price: v, title: `${label} ${price(v)}`, color: withAlpha(c, 0.85),
            lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
          detach.push(() => drawn!.series.removePriceLine(line));
        }
      }
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const d of detach) d();
      for (const s of added) chart.removeSeries(s);
    };
  }, [drawn, patterns, ticker, patSel]);

  // #695 헤드앤숄더 넥라인(점선, 왼쪽 골부터 마지막 봉까지)과 깬 뒤의 목표(가격선) — 축 범위엔 넣지 않는다
  useEffect(() => {
    if (!drawn || hsList.length === 0) return;
    const { chart, rows, intraday } = drawn;
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const first = Date.parse(rows[0].bar.at);
    const added: ISeriesApi<"Line">[] = [];
    const detach: (() => void)[] = [];
    for (const p of hsList) {
      try {
        const t0 = Date.parse(p.neckline.from.at), t1 = Date.parse(p.neckline.to.at);
        if (!(p.neckline.from.price > 0 && p.neckline.to.price > 0) || !Number.isFinite(t0) || !Number.isFinite(t1)) continue;
        const rate = Math.log(p.neckline.to.price / p.neckline.from.price);
        const data = [];
        for (const r of rows) {
          const t = Date.parse(r.bar.at);
          if (t < Math.max(t0, first)) continue;
          data.push({ time: time(r.key), value: p.neckline.from.price * Math.exp((rate * (t - t0)) / Math.max(1, t1 - t0)) });
        }
        if (data.length < 2) continue;
        const color = css(p.kind === "head_shoulders" ? "--down" : "--up");
        const s = chart.addSeries(LineSeries, { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          autoscaleInfoProvider: () => null, color, lineWidth: 2, lineStyle: LineStyle.Dashed });
        s.setData(data);
        added.push(s);
        const m = createSeriesMarkers(s, [{ time: data[data.length - 1].time, position: "belowBar", shape: "square", color,
          text: `${p.label}(${tfLabel(p.timeframe)}) 넥라인 · ${p.state_label}`, size: 0.5 } as SeriesMarker<Time>]);
        detach.push(() => m.detach());
        if (p.state === "broken" || p.state === "retest") {
          const line = drawn.series.createPriceLine({ price: p.target, title: `${p.label} 목표 ${price(p.target)}`,
            color: withAlpha(color, 0.85), lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true });
          detach.push(() => drawn.series.removePriceLine(line));
        }
      } catch {
        /* 이상한 패턴 하나는 건너뛴다 */
      }
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const d of detach) d();
      for (const s of added) chart.removeSeries(s);
    };
  }, [drawn, hsList]);

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
    const muted = css("--muted");
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
      const color = { bull: css("--long"), bear: css("--short"), range: muted };
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

  // #438 방향 전망 — 일봉에서만: 원뿔 음영(68% 진하게 · 95% 옅게) + 68% 경계 실선, 계획 방향의 시나리오 경로(굵은 점선)와
  // 강세 · 약세 끝점 마커(확률). 원뿔은 분석한 날의 봉에서 시작한다 — 옛 분석이면 그 뒤 실제 봉과 견줘 볼 수 있다.
  // 분석일이 받아 온 봉보다 앞이면(오래된 run) 그리지 않는다. 경로 · 95% 띠는 축 범위에 넣지 않는다.
  const outlookPeriod: OutlookPeriodId = outlook?.periods[outlookPrefs.period] ? outlookPrefs.period
    : (OUTLOOK_PERIODS.find((p) => outlook?.periods[p.id])?.id ?? outlookPrefs.period);
  const outlookOutside = !!drawn && !!outlook && drawn.interval === "1d" && String(drawn.rows[0].key) > outlook.trade_date;
  // #560 전망 기준가가 분석일 봉 종가와 20% 넘게 다르면(분할 · 상품/본주 · 가짜 run) 원뿔이 봉과 떨어져 축만 늘린다 — 그리지 않고 요약에 적는다
  const outlookFar = (() => {
    if (!drawn || !outlook?.price || drawn.interval !== "1d" || outlookOutside) return false;
    for (let i = drawn.rows.length - 1; i >= 0; i--) {
      if (String(drawn.rows[i].key) <= outlook.trade_date) {
        const c = drawn.rows[i].bar.close;
        return c > 0 && Math.abs(outlook.price - c) / c > OUTLOOK_FAR;
      }
    }
    return false;
  })();
  useEffect(() => {
    if (!drawn || !outlook || !outlookPrefs.on || drawn.interval !== "1d" || outlookFar) return;
    const cone = outlookCone(outlook, outlookPeriod);
    if (cone.length < 2) return;
    const { chart, series, rows } = drawn;
    const n = cone.length - 1;
    const lastIdx = rows.length - 1;
    let from = -1;
    for (let i = lastIdx; i >= 0; i--) if (String(rows[i].key) <= outlook.trade_date) { from = i; break; }
    if (from < 0) return;
    const over = Math.max(0, from + n - lastIdx);   // 마지막 봉 뒤로 필요한 미래 칸
    const future = futureKeys(rows[lastIdx].key, "1d", over);
    const keyAt = (bar: number) => {
      const i = from + Math.min(Math.max(bar, 0), n);
      return (i <= lastIdx ? rows[i].key : future[i - lastIdx - 1]) as Time;
    };
    const times = cone.map((c) => keyAt(c.bar));
    const quiet = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };
    const muted = css("--muted");
    const color = { bull: css("--long"), bear: css("--short"), range: muted };
    const added: ISeriesApi<"Line">[] = [];
    const detach: (() => void)[] = [];
    const shade = new ConeShade();
    const ts = chart.timeScale();
    let before: { from: number; to: number } | null = null;   // 넓히기 전 보이던 범위 — 끄면 되돌린다
    try {
      series.attachPrimitive(shade);
      shade.set([
        { times, lo: cone.map((c) => c.lo95), hi: cone.map((c) => c.hi95), fill: withAlpha(muted, 0.08) },
        { times, lo: cone.map((c) => c.lo68), hi: cone.map((c) => c.hi68), fill: withAlpha(muted, 0.16) },
      ]);
      for (const k of ["lo68", "hi68"] as const) {
        const s = chart.addSeries(LineSeries, { ...quiet, color: withAlpha(muted, 0.8), lineWidth: 1 });
        added.push(s);   // setData 가 실패해도 정리되게 먼저 넣는다
        s.setData(cone.map((c) => ({ time: keyAt(c.bar), value: c[k] })));
      }
      // 굵은 경로는 계획 방향의 경로만 — 그 방향 경로가 없으면(신고가 롱 등) 굵게 그리지 않는다
      const main = outlook.decision.scenario;
      const marks: { s: ISeriesApi<"Line">; m: SeriesMarker<Time> }[] = [];
      for (const path of outlook.paths) {
        const pts = clipPath(path.points.filter((p) => p.price > 0), n);
        if (pts.length < 2) continue;
        const isMain = path.name === main;
        const s = chart.addSeries(LineSeries, { ...quiet, autoscaleInfoProvider: () => null, color: withAlpha(color[path.name], isMain ? 1 : 0.55),
          lineWidth: isMain ? 2 : 1, lineStyle: isMain ? LineStyle.Dashed : LineStyle.Dotted });
        added.push(s);
        const data = new Map<string, number>();   // 같은 칸의 두 점은 뒤 점만
        for (const p of pts) data.set(keyAt(p.bar) as string, p.price);
        s.setData([...data].map(([k, v]) => ({ time: k as Time, value: v })).sort((a, b) => (a.time < b.time ? -1 : 1)));
        if (path.name !== "range") {
          const end = pts[pts.length - 1];
          marks.push({ s, m: { time: keyAt(end.bar), position: path.name === "bear" ? "belowBar" : "aboveBar", shape: "circle",
            color: color[path.name], text: `${path.label} ${Math.round(path.prob * 100)}%`, size: 0.6 } });
        }
      }
      for (const { s, m } of marks) {
        const made = createSeriesMarkers(s, [m]);
        detach.push(() => made.detach());
      }
      // 최신 봉을 보고 있었으면 미래 칸까지 보이게 넓힌다(원뿔이 화면 오른쪽 밖에 그려지지 않게) — 끝점 마커 글자가 잘리지 않게 몇 칸 더
      const r = ts.getVisibleLogicalRange();
      if (over > 0 && r && r.to >= lastIdx - 1) {
        before = { from: r.from, to: r.to };
        ts.setVisibleLogicalRange({ from: r.from, to: lastIdx + over + 4 });
      }
    } catch {
      /* 전망을 못 그려도 차트는 그대로 */
    }
    return () => {
      if (!drawn.alive.current) return;
      for (const d of detach) d();
      for (const s of added) chart.removeSeries(s);
      series.detachPrimitive(shade);
      if (before) ts.setVisibleLogicalRange(before);
    };
  }, [drawn, outlook, outlookPrefs.on, outlookPeriod, outlookFar]);

  // #438 전망 무효 가격 — 가는 선, 축 라벨 없이(#434 타점 라벨과 겹치지 않게). 계획 손절선이 같은 가격에 그려져 있으면 생략
  // (#436 모순인 계획은 손절선을 그리지 않으므로 그때는 이 선을 긋는다).
  useEffect(() => {
    if (!drawn || !outlook || !outlookPrefs.on || drawn.interval !== "1d" || outlookOutside || outlookFar) return;
    const inv = outlook.invalidation;
    const stopDrawn = show.levels && !!plan && !levelsContradict(plan) && plan.stop_loss != null && inv != null && price(plan.stop_loss) === price(inv);
    if (inv == null || !(inv > 0) || stopDrawn) return;
    const line = drawn.series.createPriceLine({ price: inv, title: `전망 무효 ${price(inv)}`, color: withAlpha(css("--danger"), 0.7),
      lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: false });
    return () => {
      if (drawn.alive.current) drawn.series.removePriceLine(line);
    };
  }, [drawn, outlook, outlookPrefs.on, outlookOutside, outlookFar, plan, show.levels]);

  // 선 근처(5px)에 마우스를 올리면 근거 — 지지 · 저항 · 피보나치(가로선)와 추세선(그 봉에서의 선 높이).
  useEffect(() => {
    if (!drawn) return;
    const { chart, series } = drawn;
    const onMove = (param: MouseEventParams<Time>) => {
      if (!drawn.alive.current) return;          // #585 지운 차트의 시리즈는 좌표를 묻지 않는다
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
  const planShown = !!plan && !levelsContradict(plan);   // #436 모순이라 그리지 않은 값은 경고 · 범위 안내에서도 뺀다
  const levels = [...(plan && planShown ? [plan.entry_price, plan.stop_loss, plan.price_target] : []), ...(extraLines ?? []).map((l) => l.price)];
  const hasLevels = levels.some((v) => v != null);
  // #321 계획 손절이 지지 구역 안쪽이면 경고 — 그 분석이 본 레벨(없으면 지금 레벨)로
  const stopWarn = show.levels && plan && planShown ? stopInsideZone(plan.stop_loss, plan.entry_price, sr) : null;
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
    <section ref={section} data-tour="price-chart" className={`card price-chart${expanded ? " price-chart-expanded" : ""}`}
      {...(expanded ? { role: "dialog", "aria-modal": true, "aria-label": `${title ?? ticker} 큰 차트` } : {})}>
      <div className="price-chart-head">
        <h3>
          {title ?? "가격 차트"}{" "}
          <span className="muted small">{title !== ticker && `${ticker} · `}{currency}</span>
          <span className="muted small">
            <LiveBarTag drawn={drawn} ticker={ticker} interval={interval} watch={section} onStale={reloadBars}
              fallback={candles ? [candles.source, delayText(candles.delay_sec)].filter(Boolean).join(" · ") : ""} />
          </span>
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
        {readable && (
          <button type="button" className={`chip chip-sub chart-read-toggle${readOn ? " chip-on" : ""}`} aria-pressed={readOn}
            title="이 차트를 쉬운 말로 — 가까운 위 · 아래 선에 번호를 붙여 차트에도 같은 번호로" onClick={() => pickRead({ on: !readOn })}>
            차트 읽기
          </button>
        )}
        <details ref={menu} className="chart-menu" data-tour="chart-menu">
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
      {banner}
      {show.entry && <EntrySummary entry={entry} failed={entryFailed} fromRun={!!plan}
        elapsed={interval === "1d" ? candles?.session_elapsed ?? null : null} />}
      {oscLines.length > 0 && (
        <div className={`small chart-limit ${hot ? "error" : "muted"}`} role="status">
          {oscLines.map((t) => <p key={t} className="chart-osc">{hot && t.includes("과열") ? "⚠ " : ""}{t}</p>)}
        </div>
      )}
      {limited != null && <p className="muted small chart-limit">야후 보관 한도로 {LABEL[interval]}봉은 최근 {limited}일만 제공됩니다.</p>}
      {maShown && maValues && drawn && (
        <div className="ma-legend" role="group" aria-label="이동평균선 켜고 끄기">
          {MA_PERIODS.map((n) => {
            const v = maValues[n][drawn.rows.length - 1];
            return (
              <button key={n} type="button" className={`ma-chip ma-${n}${maOn[n] ? " on" : ""}`} aria-pressed={maOn[n]} onClick={() => flipMa(n)}
                title={`${n}봉 단순 이동평균${drawn.intraday ? "(분봉은 같은 개수의 봉)" : ""} — 눌러 켜고 끄기`}>
                <span className="ma-sw" aria-hidden />MA{n} <b>{v != null ? price(v) : "—"}</b>
              </button>
            );
          })}
          {drawn.intraday && <span className="muted small">분봉은 같은 개수의 봉 기준</span>}
        </div>
      )}
      <div className={`chart-read-wrap${readOn ? " on" : ""}`}>
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
          {markPos.map(({ n, x, y }) => {
            const m = readMarks.find((r) => r.n === n);
            if (!m) return null;
            return (
              <button key={n} type="button" className={`read-mark read-${m.side}${readPick === n ? " on" : readPick != null ? " dim" : ""}`}
                style={{ left: x, top: y }} aria-pressed={readPick === n} title={`${m.level.name} ${price(m.level.price)}`}
                aria-label={`${MARKS[n]} ${m.level.name} ${price(m.level.price)}`} onClick={() => pickMark(readPick === n ? null : n, "chart")}>
                {MARKS[n]}
              </button>
            );
          })}
          {drawn && (Object.keys(paneTops) as PaneId[]).map((id) => (
            <PaneTag key={id} id={id} top={paneTops[id] as number} rows={drawn.rows} open={paneHelp === id}
              bottomPane={id === "stoch" || (id === "rsi" && paneTops.stoch == null)}
              elapsed={interval === "1d" ? candles?.session_elapsed ?? null : null}
              onHelp={() => setPaneHelp((cur) => (cur === id ? null : id))} />
          ))}
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
                  <strong>{hover.line.scale ? `${SCALE_KO[hover.line.scale]} ` : ""}{TREND_NAME[hover.line.kind]} {price(hover.here)}</strong>{" "}
                  <span className="muted">{tfLabel(hover.line.timeframe)} · 터치 {hover.line.touches.length}회</span>
                  <div>
                    {when} 선 {price(hover.line.price_now)} · 가격은 선에서 {signed(hover.line.distance_atr)} ATR · {hover.line.status ? STATUS_KO[hover.line.status] : TREND_STATE[hover.line.state]}
                  </div>
                  {hover.line.scenarios && <div className="muted">눌러서 돌파 · 거절 시나리오 보기</div>}
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
      {readOn && (
        <ChartReadPanel read={chartRead} failed={readFailed} frame={readFrame} interval={interval}
          onInterval={(tf) => setBarInterval(tf as Interval)} marks={readMarks} picked={readPick} onPick={(n) => pickMark(n, "panel")}
          beginner={readPrefs.beginner} applied={beginner} onBeginner={(b) => pickRead({ beginner: b })} idPrefix={readId}
          open={readExpanded} onToggle={setReadExpanded} selectedAccumulation={selectedAccumulation}
          accumulation={chartRead && (
            <AccumulationPanel read={chartRead} selected={selectedAccumulation} onSelect={(view) => {
              selectAccumulationView(view);
              const target = chartRead.accumulation?.[view];
              if (target && target.error === undefined) setBarInterval(target.timeframe);
            }} />
          )} />
      )}
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
      {outlook && <OutlookSummary outlook={outlook} period={outlookPeriod} on={outlookPrefs.on} onPick={pickOutlook} daily={interval === "1d"} outside={outlookOutside} far={outlookFar} />}
      {/* #531 추세선 범례 — 짧은 · 중간 · 긴 · 지지 · 저항 켜고 끄기(이 브라우저에 기억), 선을 누르면 시나리오 */}
      {show.trend && allTrends.length > 0 && (
        <div className="trend-legend small" role="group" aria-label="추세선 켜고 끄기">
          <span className="muted">추세선</span>
          {allTrends.some((t) => t.scale) && (["short", "mid", "long"] as const).map((k) => (
            <button key={k} type="button" className={`chip${trendFilter[k] ? " chip-on" : ""}`} aria-pressed={trendFilter[k]}
              onClick={() => setTrendFilter({ ...trendFilter, [k]: !trendFilter[k] })}>
              <i className={`tl-sw tl-sw-${k}`} aria-hidden="true" />{SCALE_KO[k]}
            </button>
          ))}
          {(["support", "resistance"] as const).map((k) => (
            <button key={k} type="button" className={`chip${trendFilter[k] ? " chip-on" : ""}`} aria-pressed={trendFilter[k]}
              onClick={() => setTrendFilter({ ...trendFilter, [k]: !trendFilter[k] })}>
              <i className={`tl-sw tl-sw-${k}`} aria-hidden="true" />{KIND_KO[k]}
            </button>
          ))}
          {Object.values(trendFilter).some((v) => !v) && <button type="button" className="link small" onClick={() => setTrendFilter(ALL_ON)}>모두 켜기</button>}
          {/* 선 바로 고르기 — 축 밖에 있는 먼 선 · 키보드로도 카드를 열게 */}
          {trendLines.some((t) => t.scenarios) && (
            <span className="trend-pick-row" role="group" aria-label="선 고르기">
              {trendLines.filter((t) => t.scenarios).map((t) => (
                <button key={trendKey(t)} type="button" className={`chip chip-small trend-pick-${t.kind}${trendKey(t) === pickedId ? " chip-on" : ""}`}
                  aria-pressed={trendKey(t) === pickedId} onClick={() => pickTrend(trendKey(t) === pickedId ? null : t)}>
                  {trendLabel(t)}{t.status === "far" ? " (멀리)" : ""}
                </button>
              ))}
            </span>
          )}
          <span className="muted">
            {trendLines.length}개{allTrends.length > trendLines.length ? ` (꺼 둔 ${allTrends.length - trendLines.length}개)` : ""}
            {trendLines.some((t) => t.scenarios) ? " · 선을 누르면 돌파 · 거절 시나리오, Esc 로 닫기" : ""}
            {trendLines.some((t) => t.status === "far") ? " · 점선은 지금가에서 먼 선" : ""}
          </span>
        </div>
      )}
      {!onTrendPick && pickedLine && <TrendLineCard line={pickedLine} when={when} onClose={() => pickTrend(null)} />}
      {gapPick && gapRep && <GapOddsCard rep={gapRep} gap={gapPick} onClose={closeGap} />}
      {linePick && lines && <ChannelLineCard rep={lines} pick={linePick} onClose={closeLine} />}
      {lists && (show.chan || show.ltrend) && !isLineTf(interval) && (
        <p className="muted small">평행 채널 · 짧은 추세선은 5분 · 15분 · 1시간 · 4시간 · 일봉 차트에서 보여요.</p>
      )}
      {lists && wantLines && lines && lines.timeframe === interval && (
        <LineList lines={lines} chan={show.chan} ltrend={show.ltrend} onPick={setLinePick} />
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
              <li key={trendKey(t)} className={`sr-${t.kind} trend-${t.state}${trendKey(t) === pickedId ? " trend-picked" : ""}`}>
                <span className="sr-kind">{t.scale ? `${SCALE_KO[t.scale]} ` : ""}{t.kind === "support" ? "지지" : "저항"}</span>
                <strong className="num">{price(t.price_now)}</strong>
                <span className="muted small">{tfLabel(t.timeframe)} · 터치 {t.touches.length}회</span>
                <span className="small">
                  {TREND_NAME[t.kind]} · {kstDay(t.anchors[0].at)}부터 · {when} 가격 {signed(t.distance_atr)} ATR ·{" "}
                  <strong className="trend-state">{t.status ? STATUS_KO[t.status] : TREND_STATE[t.state]}</strong>
                  {t.distance_pct != null && <> · {mode === "plan" ? "그때 가격" : "지금가"} 대비 {t.distance_pct > 0 ? "+" : ""}{t.distance_pct}%</>}
                </span>
                {t.scenarios && <button type="button" className="link small" onClick={() => pickTrend(trendKey(t) === pickedId ? null : t)}>{trendKey(t) === pickedId ? "닫기" : "시나리오"}</button>}
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
              <li key={patternKey(p)} className={`pattern-${p.state}${patSel === patternKey(p) ? " pattern-picked" : ""}`}>
                <button type="button" className={`chip pattern-chip${patSel === patternKey(p) ? " chip-on" : ""}`} aria-pressed={patSel === patternKey(p)}
                  title="차트에 강조하고 돌파 예상 구간 · 목표를 그려요" onClick={() => setPatSel((cur) => (cur === patternKey(p) ? null : patternKey(p)))}>
                  {p.label}
                </button>
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
      {show.pattern && picked && (
        <PatternCard p={picked} last={drawn ? drawn.rows[drawn.rows.length - 1].bar.close : null}
          sameTf={!!drawn && picked.timeframe === drawn.interval} onClose={() => setPatSel(null)} />
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

/** #438 전망 한 줄 요약 · 보기 토글 · 1주/1개월 — "1개월 상승 · 강세 44% · 68% 범위 168~198 · 171 아래면 무효" */
function OutlookSummary({ outlook, period, on, onPick, daily, outside, far }: {
  outlook: RunOutlook; period: OutlookPeriodId; on: boolean; onPick: (p: { on?: boolean; period?: OutlookPeriodId }) => void; daily: boolean;
  /** 분석일이 받아 온 일봉보다 앞 — 원뿔을 그리지 않았다 */
  outside: boolean;
  /** #560 전망 기준가가 차트 가격과 많이 다르다 — 원뿔을 그리지 않았다 */
  far: boolean;
}) {
  const p = outlook.periods[period];
  const d = outlook.decision;
  const path = d.scenario ? outlook.paths.find((x) => x.name === d.scenario) : undefined;
  const likeliest = outlook.paths.find((x) => x.name === d.likeliest);
  return (
    <div className="outlook-note">
      <div className="outlook-tools" role="group" aria-label="방향 전망">
        <button type="button" className={`chip ${on ? "chip-on" : ""}`} aria-pressed={on} onClick={() => onPick({ on: !on })}>전망 보기</button>
        {OUTLOOK_PERIODS.filter((x) => outlook.periods[x.id]).map((x) => (
          <button key={x.id} type="button" className={`chip ${period === x.id ? "chip-on" : ""}`} aria-pressed={period === x.id}
            disabled={!on} onClick={() => onPick({ period: x.id })}>{x.label}</button>
        ))}
      </div>
      {on && (
        <p className="muted small">
          <strong>{p?.label ?? period} {directionLabel(outlook)}</strong>
          {path && <> · {path.label} {Math.round(path.prob * 100)}%</>}
          {!path && <> · 이 방향의 경로 없음</>}
          {d.agrees !== true && likeliest && <> (예상도에서 가장 큰 경로는 {likeliest.label} {Math.round(likeliest.prob * 100)}%)</>}
          {p && <> · 68% 범위 {price(p.band68[0])}~{price(p.band68[1])}({p.due.slice(5).replace("-", ".")}까지)</>}
          {outlook.invalidation != null && <> · {price(outlook.invalidation)} {d.bias === "short" ? "위" : "아래"}면 무효</>}
          {d.reason && <> — {d.reason}</>}
          {!daily && <> · 원뿔은 일봉 차트에서 그립니다</>}
          {daily && outside && <> · 분석일이 차트 범위보다 앞이라 원뿔은 그리지 않았습니다</>}
          {daily && far && <> · 전망 기준가({price(outlook.price)})가 차트 가격과 많이 달라(분할 · 다른 기준 가격) 원뿔은 그리지 않았습니다</>}
          {" · "}<strong>{outlook.disclaimer}</strong>
        </p>
      )}
    </div>
  );
}

/** #550 보조 칸 왼쪽 위 이름표 — 이름 + 지금 값 + 한 줄 뜻(좁은 화면은 짧게), ⓘ 로 쉬운 설명 몇 줄 */
function PaneTag({ id, top, rows, open, onHelp, bottomPane = false, elapsed = null }: {
  id: PaneId; top: number; rows: Row[]; open: boolean; onHelp: () => void;
  /** #560 맨 아래 칸 — 왼쪽 아래 TradingView 로고(라이선스 표기)를 비켜 오른쪽으로 민다 */
  bottomPane?: boolean;
  /** #559 일봉의 마지막 봉이 진행 중이면 그 장의 경과 비율(0~1, 서버 session_progress) */
  elapsed?: number | null;
}) {
  const t = PANE_TEXT[id];
  const last = rows[rows.length - 1]?.bar;
  let value = "";
  if (id === "vol" && last) {
    const recent = rows.slice(-21, -1).map((r) => r.bar.volume).filter((v) => v > 0);
    const avg = recent.length ? recent.reduce((a, b) => a + b, 0) / recent.length : 0;
    // #559 진행 중인 오늘 봉은 하루 평균과 그대로 견주지 않는다 — 장 전 · 초반은 판정 안 함, 이후엔 경과 비율로 환산
    // (기준은 서버 setup_filter.VOL_MIN_ELAPSED 와 같은 0.15)
    if (elapsed != null && elapsed < 0.15) value = `${shortVolume(last.volume)} (오늘 진행 중 · 집계 중)`;
    else if (elapsed != null) value = `${shortVolume(last.volume)}${avg > 0 ? ` (오늘 진행 중 · 하루치 환산 평소의 ${(last.volume / elapsed / avg).toFixed(1)}배)` : " (오늘 진행 중)"}`;
    else value = `${shortVolume(last.volume)}${avg > 0 ? ` (평소의 ${(last.volume / avg).toFixed(1)}배)` : ""}`;
  } else if (id === "rsi" && last?.rsi != null) {
    value = last.rsi.toFixed(1);
  } else if (id === "stoch" && last?.stoch_k != null) {
    value = `${last.stoch_k.toFixed(0)}${last.stoch_d != null ? ` / ${last.stoch_d.toFixed(0)}` : ""}`;
  }
  return (
    <div className={`pane-tag pane-tag-${id}${bottomPane ? " pane-tag-last" : ""}`} style={{ top: top + 3 }}>
      <span className="pane-tag-line">
        {/* #560 글은 한 줄로 — 넘치면 말줄임(ⓘ 는 늘 보이게 밖에) */}
        <span className="pane-tag-txt">
          <b>{t.name}</b>{value && <span className="pane-tag-v"> {value}</span>}
          <span className="pane-tag-long"> — {t.long}</span>
          <span className="pane-tag-short"> · {t.short}</span>
        </span>
        <button type="button" className="pane-tag-i" aria-expanded={open} aria-label={`${t.name} 설명`} onClick={onHelp}>ⓘ</button>
      </span>
      {open && (
        <div className="pane-help small" role="note">
          {t.help.map((line) => <p key={line}>{line}</p>)}
        </div>
      )}
    </div>
  );
}

/** #612 차트 아래 채널 · 짧은 추세선 목록 — 얇은 선을 누르기 어려운 좁은 화면에서도 이름을 눌러 설명을 연다 */
function LineList({ lines, chan, ltrend, onPick }: { lines: LinesReport; chan: boolean; ltrend: boolean; onPick: (p: LinePick) => void }) {
  const chans = chan ? lines.channels.slice(0, 2) : [];
  const trends = ltrend ? lines.trendlines : [];
  return (
    <div className="line-list">
      {chans.length + trends.length === 0 ? (
        <p className="muted small">이 {TF_KO[lines.timeframe] ?? "주기"}에서 찾은 채널 · 추세선이 없어요.</p>
      ) : (
        <ul className="sr-list" aria-label="평행 채널 · 짧은 추세선">
          {chans.map((c) => (
            <li key={c.id}>
              <button type="button" className="link" onClick={() => onPick({ kind: "channel", id: c.id })}>{channelTitle(c)}</button>{" "}
              <span className="muted small">{CHANNEL_STATE_KO[c.state]} · 하단 {price(c.lower.end.price)} · 상단 {price(c.upper.end.price)}</span>
            </li>
          ))}
          {trends.map((t) => (
            <li key={t.id}>
              <button type="button" className="link" onClick={() => onPick({ kind: "trend", id: t.id })}>{trendTitle(t)}</button>{" "}
              <span className="muted small">{TREND_STATE_KO[t.state]} · {price(t.end.price)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">
        굵은 선 = 채널 하단 · 얇은 선 = 상단 · 점선 = 폭 한 칸 바깥 · 주황 = 짧은 추세선(▼▲ 이탈). 선이나 이름을 누르면 설명이 떠요. {lines.note}
        {lines.fake ? " (API 전이라 화면 확인용 가짜)" : ""}
      </p>
    </div>
  );
}

/** #627 테마(다크 · 라이트)가 바뀌면 새 색으로 차트를 다시 만든다 — 차트 라이브러리는 CSS 변수를 직접 못 받는다 */
export default function PriceChart(props: Parameters<typeof PriceChartInner>[0]) {
  const scheme = useColorScheme();
  return <PriceChartInner key={scheme} {...props} />;
}
