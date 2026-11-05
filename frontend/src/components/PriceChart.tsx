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
import { api, type CandleInterval, type Candles, type ReviewRow } from "../api";
import { price } from "../format";
import type { FibLevel, Fibonacci, Plan, SrLevel, SupportResistance, Trendline } from "../types";

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

type Show = { levels: boolean; sr: boolean; trend: boolean; fib: boolean; fills: boolean; analysis: boolean; rsi: boolean };
const SHOW_LABEL: Record<keyof Show, string> = {
  levels: "타점선", sr: "지지 · 저항", trend: "추세선", fib: "피보나치", fills: "매수 · 매도", analysis: "분석일", rsi: "RSI",
};
const DEFAULT_SHOW: Show = { levels: true, sr: true, trend: true, fib: true, fills: true, analysis: true, rsi: true };

// 주기 · 표시 선택은 이 브라우저에 기억한다(사생활 모드 · 막힌 저장소에서도 화면은 그대로 동작).
const PREFS_KEY = "desk.priceChart.v1";
function loadPrefs(): { interval: Interval; show: Show } {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}");
    const interval = INTERVALS.some((i) => i.iv === raw.interval) ? (raw.interval as Interval) : "1d";
    const show = { ...DEFAULT_SHOW };
    for (const k of Object.keys(DEFAULT_SHOW) as (keyof Show)[]) if (typeof raw.show?.[k] === "boolean") show[k] = raw.show[k];
    return { interval, show };
  } catch {
    return { interval: "1d", show: { ...DEFAULT_SHOW } };
  }
}
function savePrefs(interval: Interval, show: Show) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ interval, show }));
  } catch {
    /* 저장소가 막혀 있으면 기억만 못 한다 */
  }
}

// 마지막 종가에서 이만큼 넘게 떨어진 계획선(분할 전 옛 분석 등)은 축 범위 계산에서 뺀다 — 봉이 납작해지지 않게.
const AUTOSCALE_MAX_GAP = 0.5;
// 체결 마커는 복기 API(브로커 체결 + 종목별 일봉)를 쓴다. 분석 화면을 오갈 때마다 다시 부르지 않게 잠깐 기억한다.
const FILLS_TTL_MS = 60_000;
let fillsMemo: { at: number; rows: Promise<ReviewRow[]> } | null = null;

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
export default function PriceChart({ ticker, tradeDate, plan, title, extraLines, initialInterval, lists = true }: {
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
}) {
  const [prefs] = useState(loadPrefs);
  const [interval, setBarInterval] = useState<Interval>(initialInterval ?? prefs.interval);
  const [show, setShow] = useState<Show>(prefs.show);
  const [candles, setCandles] = useState<Candles | null>(null);
  const [fills, setFills] = useState<ReviewRow[]>([]);
  const [failed, setFailed] = useState(false);
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
  // 추세선 · 피보나치(#73) — 지지 · 저항과 같은 출처(분석 당시 / 지금)
  const trendLines: Trendline[] = useMemo(() => (show.trend ? srSource?.trendlines ?? [] : []), [show.trend, srSource]);
  const fib: Fibonacci | null = show.fib ? srSource?.fibonacci ?? null : null;
  const wantSr = show.sr || show.trend || show.fib;
  // 분석 당시 값이면 "지금" 이라 하지 않는다 — 선은 오늘까지 그어도 거리 · 상태는 그때 기준
  const when = mode === "plan" ? "분석 당시" : "지금";
  const [trendDrawn, setTrendDrawn] = useState<{ line: Trendline; series: ISeriesApi<"Line"> }[]>([]);

  useEffect(() => savePrefs(interval, show), [interval, show]);
  const toggle = (k: keyof Show) => setShow((s) => ({ ...s, [k]: !s[k] }));

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
    setCandles(null);
    setFailed(false);
    api
      .candles(ticker, interval, COUNT[interval], show.rsi)
      .then((c) => live && setCandles(c))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [ticker, interval, show.rsi]);

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
      const up = css("--up", "#e5534b");
      const down = css("--down", "#4f8cff");
      const grid = css("--border", "#2a3240");
      chart = createChart(el, {
        autoSize: true,
        layout: {
          background: { type: ColorType.Solid, color: "transparent" },
          textColor: css("--muted", "#8b96a5"),
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
        scaleMargins: { top: 0.08, bottom: 0.24 }, // 아래는 거래량 자리
        mode: LOG_SCALE.has(iv) ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal,
      });
      series.setData(rows.map(({ key, bar }) => ({ time: time(key), open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volume.setData(rows.map(({ key, bar }) => ({ time: time(key), value: bar.volume, color: (bar.close >= bar.open ? up : down) + "55" })));

      // RSI(14) — 아래 패널(시간축 공유라 확대 · 이동이 같이 움직인다), 30 · 70 점선.
      if (candles.indicators?.includes("rsi14")) {
        const rsi = chart.addSeries(LineSeries, {
          color: css("--warn", "#d29922"), lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
          priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(0), minMove: 0.01 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        }, 1);
        // RSI 축은 늘 0~100 일반 눈금 — 주 · 월 · 년봉의 로그 눈금이 번지지 않게.
        rsi.priceScale().applyOptions({ mode: PriceScaleMode.Normal, scaleMargins: { top: 0.08, bottom: 0.08 } });
        rsi.setData(rows.flatMap(({ key, bar }) => (bar.rsi == null ? [] : [{ time: time(key), value: bar.rsi }])));
        const muted = css("--muted", "#8b96a5");
        for (const lv of [70, 30]) rsi.createPriceLine({ price: lv, color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "" });
        chart.panes()[1]?.setHeight(110);
      }

      chart.timeScale().fitContent();
      setDrawn({ chart, series, markers: createSeriesMarkers(series, []), rows, intraday, interval: iv, lastClose: rows[rows.length - 1].bar.close, alive });
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
    const lines: [number | null | undefined, string, string, LineStyle][] = show.levels
      ? [
          [plan?.entry_price, "진입", css("--accent", "#4f8cff"), LineStyle.Solid],
          [plan?.stop_loss, "손절", css("--short", "#e5534b"), LineStyle.Dashed],
          [plan?.price_target, "목표", css("--long", "#2ea043"), LineStyle.Dashed],
          ...(extraLines ?? []).map(
            (l): [number, string, string, LineStyle] => [l.price, l.label, l.color, l.dashed ? LineStyle.Dotted : LineStyle.Solid],
          ),
        ]
      : [];
    const made: IPriceLine[] = [];
    for (const [value, title, color, lineStyle] of lines) {
      if (value != null) made.push(series.createPriceLine({ price: value, title, color, lineWidth: 2, lineStyle, axisLabelVisible: true }));
    }
    const near = lines.map(([v]) => v).filter((v): v is number => v != null && Math.abs(v - lastClose) / lastClose <= AUTOSCALE_MAX_GAP);
    series.applyOptions({
      autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = base();
        if (!r || near.length === 0) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, ...near), maxValue: Math.max(r.priceRange.maxValue, ...near) } };
      },
    });

    const up = css("--up", "#e5534b");
    const down = css("--down", "#4f8cff");
    const keys = rows.map((r) => r.key);
    const time = (k: Key) => (intraday ? (k as UTCTimestamp) : (k as Time));
    const list: { key: Key; m: Omit<SeriesMarker<Time>, "time"> }[] = [];
    if (show.analysis) {
      // 분봉: 분석한 거래일의 첫 봉, 일봉 이상: 그 날짜가 든 봉
      // 주말 · 휴장일 분석이면 그 전 거래일 — 일봉과 같은 규칙
      const day = intraday ? barFor(rows.map((r) => r.day), tradeDate) : null;
      const at = intraday ? (day == null ? null : rows.find((r) => r.day === day)?.key ?? null) : barFor(keys, tradeDate);
      if (at != null) list.push({ key: at, m: { position: "aboveBar", shape: "arrowDown", color: css("--warn", "#d29922"), text: "분석" } });
    }
    if (show.fills) {
      for (const f of fills) {
        // 분봉: 체결 시각이 든 봉, 일봉 이상: 체결한 거래일이 든 봉(토스 체결 시각은 KST)
        const fillOn = marketDay(f.filled_at, ticker);
        let at = barFor(keys, intraday ? kstStamp(f.filled_at) : fillOn);
        if (at == null) continue;
        // 분봉은 정규장만 온다 — 장 전 · 시간외 체결이 전날 마지막 봉에 붙지 않게, 그날 첫 봉으로(그날 봉이 없으면 뺀다).
        if (intraday && rows.find((r) => r.key === at)?.day !== fillOn) {
          at = rows.find((r) => r.day === fillOn)?.key ?? null;
          if (at == null) continue;
        }
        const buy = f.side === "BUY";
        list.push({ key: at, m: { position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown", color: buy ? up : down, text: `${buy ? "매수" : "매도"} ${price(f.price)}` } });
      }
    }
    list.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    markers.setMarkers(list.map(({ key, m }) => ({ ...m, time: time(key) }) as SeriesMarker<Time>));
    return () => {
      // 봉이 바뀌면 차트 effect 가 먼저 chart.remove() 한다 — 그때는 지울 선도 이미 없다.
      if (drawn.alive.current) for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, plan, fills, tradeDate, ticker, show.levels, show.analysis, show.fills, extraLines]);

  // 지지 · 저항선 — 주봉 근거는 굵은 긴 점선, 일봉은 가는 점선. 축 라벨은 계획선만 달아 겹치지 않게.
  useEffect(() => {
    if (!drawn) return;
    const { series } = drawn;
    const made = sr.map((lv) =>
      series.createPriceLine({
        price: lv.price,
        color: css(lv.kind === "support" ? "--sr-support" : "--sr-resistance", lv.kind === "support" ? "#39c5bb" : "#c297ff"),
        lineWidth: lv.timeframe === "1w" ? 2 : 1,
        lineStyle: lv.timeframe === "1w" ? LineStyle.LargeDashed : LineStyle.Dotted,
        axisLabelVisible: false,
        title: "", // 구역이 많아 제목은 겹친다 — 근거는 툴팁 · 목록에
      }),
    );
    return () => {
      if (drawn.alive.current) for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, sr]);

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
    const fibLines: IPriceLine[] = [];
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
        const base = css(line.kind === "support" ? "--sr-support" : "--sr-resistance", line.kind === "support" ? "#39c5bb" : "#c297ff");
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
        const color = css("--muted", "#8b96a5");
        for (const lv of fib.levels) {
          if (!Number.isFinite(lv.price)) continue;
          // 축 라벨은 0 < 비율 < 1 만 — 0 · 1(스윙 끝점)까지 달면 계획선 라벨과 겹친다
          const inner = lv.ratio > 0 && lv.ratio < 1;
          fibLines.push(series.createPriceLine({ price: lv.price, color, lineWidth: 1, lineStyle: LineStyle.SparseDotted, axisLabelVisible: inner, axisLabelColor: color, title: inner ? String(lv.ratio) : "" }));
        }
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
      for (const l of fibLines) series.removePriceLine(l);
    };
  }, [drawn, trendLines, fib, ticker, srSource]);

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
      for (const level of sr) consider(level.price, (y) => ({ type: "sr", level, key: `sr-${level.kind}-${level.price}`, x: pt.x, y }));
      if (fib) for (const level of fib.levels) consider(level.price, (y) => ({ type: "fib", level, fib, key: `fib-${level.ratio}`, x: pt.x, y }));
      for (const { line, series: s } of trendDrawn) {
        const v = param.seriesData.get(s) as { value?: number } | undefined;
        if (v?.value != null) consider(v.value, (y) => ({ type: "trend", line, here: v.value as number, key: `trend-${line.kind}-${line.anchors[0].at}-${line.anchors[1]?.at}`, x: pt.x, y }));
      }
      // 다른 선으로 옮겨 갈 때만 다시 그린다(마우스 이동마다 렌더하지 않게).
      const hit = (best as { hit: Hover } | null)?.hit ?? null;
      setHover((prev) => (!hit ? null : prev?.key === hit.key ? prev : hit));
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      setHover(null);
      if (drawn.alive.current) chart.unsubscribeCrosshairMove(onMove);
    };
  }, [drawn, sr, fib, trendDrawn]);

  const currency = isKorean(ticker) ? "원" : "달러";
  const levels = [...(plan ? [plan.entry_price, plan.stop_loss, plan.price_target] : []), ...(extraLines ?? []).map((l) => l.price)];
  const hasLevels = levels.some((v) => v != null);
  const outOfRange = show.levels && !!drawn && levels.some((v) => v != null && Math.abs(v - drawn.lastClose) / drawn.lastClose > AUTOSCALE_MAX_GAP);
  // 보관 한도 안내는 분봉일 때만 — 년봉은 늘 요청보다 적게 오지만 한도 탓이 아니다.
  const limited = candles?.truncated && candles.history_limit_days != null ? candles.history_limit_days : null;
  const rsiOn = show.rsi && !!candles?.indicators?.includes("rsi14");
  return (
    <section className="card price-chart">
      <div className="price-chart-head">
        <h3>
          {title ?? "가격 차트"} <span className="muted small">{currency}{candles ? ` · ${candles.source}` : ""}</span>
        </h3>
        <div className="review-periods chart-intervals" role="group" aria-label="봉 주기">
          {INTERVALS.map(({ iv, label }) => (
            <button key={iv} type="button" className={iv === interval ? "" : "secondary"} aria-pressed={iv === interval} onClick={() => setBarInterval(iv)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="chart-toggles" role="group" aria-label="차트 표시">
        {(Object.keys(SHOW_LABEL) as (keyof Show)[]).map((k) => (
          <button key={k} type="button" className={`chip ${show[k] ? "chip-on" : ""}`} aria-pressed={show[k]} onClick={() => toggle(k)}>
            {SHOW_LABEL[k]}
          </button>
        ))}
        {wantSr && (
          <span className="chart-sr-source" role="group" aria-label="지지 · 저항 · 추세선 기준">
            {(["plan", "now"] as SrMode[]).filter((m) => m !== "plan" || saved).map((m) => (
              <button key={m} type="button" className={`chip chip-sub ${m === mode ? "chip-on" : ""}`} aria-pressed={m === mode} onClick={() => setSrMode(m)}>
                {SR_LABEL[m]}
              </button>
            ))}
          </span>
        )}
      </div>
      {limited != null && <p className="muted small chart-limit">야후 보관 한도로 {LABEL[interval]}봉은 최근 {limited}일만 제공됩니다.</p>}
      {failed ? (
        <p className="muted small">시세를 불러오지 못했습니다.</p>
      ) : candles && candles.bars.length === 0 ? (
        <p className="muted small">표시할 봉이 없습니다.</p>
      ) : (
        <div
          ref={box}
          className={`price-chart-box ${rsiOn ? "with-rsi" : ""}`}
          role="img"
          aria-label={`${ticker} ${LABEL[interval]}봉 가격 차트${rsiOn ? "와 RSI" : ""}${drawn ? `, 마지막 종가 ${price(drawn.lastClose)}` : ""}`}
          aria-describedby={hasLevels && show.levels ? legendId : undefined}
        >
          {!candles && <p className="muted small">불러오는 중…</p>}
          {hover && (
            <div className="sr-tip" style={tipPosition(hover, box.current)}>
              {hover.type === "sr" ? (
                <>
                  <strong>{hover.level.kind === "support" ? "지지" : "저항"} {price(hover.level.price)}</strong>{" "}
                  <span className="muted">{TF_LABEL[hover.level.timeframe] ?? hover.level.timeframe} · 강도 {hover.level.strength}</span>
                  <div>{hover.level.basis}</div>
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
      {plan && hasLevels && show.levels && (
        <p id={legendId} className="muted small price-chart-legend">
          <span className="lg lg-entry" /> 진입 {price(plan.entry_price)} · <span className="lg lg-stop" /> 손절 {price(plan.stop_loss)} ·{" "}
          <span className="lg lg-target" /> 목표 {price(plan.price_target)}
          {outOfRange && " · 현재가와 너무 먼 선은 축 범위에서 뺐습니다"}
        </p>
      )}
      {((show.fills && fills.length > 0) || rsiOn || (drawn && LOG_SCALE.has(interval))) && (
        <p className="muted small price-chart-legend">
          {[
            show.fills && fills.length > 0 ? `내 체결 ${fills.length}건` : null,
            rsiOn ? "RSI 70 이상 과열 · 30 이하 과매도" : null,
            drawn && LOG_SCALE.has(interval) ? "가격축은 로그 눈금" : null,
          ].filter(Boolean).join(" · ")}
        </p>
      )}
      {wantSr && srFailed && mode === "now" ? (
        <p className="muted small">지지 · 저항 · 추세선을 계산하지 못했습니다.</p>
      ) : wantSr && mode === "now" && !nowSr ? (
        <p className="muted small">지지 · 저항 · 추세선 계산 중…</p>
      ) : null}
      {lists && show.sr && (
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
      {lists && show.trend && srSource && (
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
      {lists && fib && (
        <p className="muted small fib-note">
          피보나치 되돌림 — {fib.direction === "up" ? "상승" : "하락"} {price(fib.from.price)}({kstDay(fib.from.at)}) → {price(fib.to.price)}({kstDay(fib.to.at)}):{" "}
          {fib.levels.filter((lv) => lv.ratio > 0 && lv.ratio < 1).map((lv) => `${lv.ratio} ${price(lv.price)}${lv.confluence ? "(지지 · 저항과 겹침)" : ""}`).join(" · ")}
        </p>
      )}
      {lists && (show.trend || show.fib) && srSource && (trendLines.length > 0 || fib) && (
        <p className="muted small sr-note">추세선은 로그 가격에서 직선, 번호는 선에 닿은 저점 · 고점 순서. 가격 기록의 계산이지 예측이 아닙니다.</p>
      )}
    </section>
  );
}
