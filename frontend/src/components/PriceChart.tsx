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
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type CandleInterval, type Candles, type ReviewRow } from "../api";
import { price } from "../format";
import type { Plan, SrLevel, SupportResistance } from "../types";

type Interval = Exclude<CandleInterval, "1m">;
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

type Show = { levels: boolean; sr: boolean; fills: boolean; analysis: boolean; rsi: boolean };
const SHOW_LABEL: Record<keyof Show, string> = { levels: "타점선", sr: "지지 · 저항", fills: "매수 · 매도", analysis: "분석일", rsi: "RSI" };
const DEFAULT_SHOW: Show = { levels: true, sr: true, fills: true, analysis: true, rsi: true };

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
  lastClose: number;
  /** chart.remove() 뒤에는 false — 지운 차트에 선을 지우면 다시 그리기가 예약돼 throw 한다. */
  alive: { current: boolean };
};

/**
 * 분석 화면의 가격 차트 — 분봉 · 일 · 주 · 월 · 년 캔들 · 거래량, RSI(14) 보조 차트,
 * 진입 · 손절 · 목표선, 지지 · 저항선, 분석일 · 내 체결 마커(#18 · #25 · #61). 겹친 표시는 하나씩 켜고 끈다.
 */
/** 계획의 진입 · 손절 · 목표 말고 더 그을 가로선(관망 계획의 롱 · 숏 진입 조건 등, #68). */
export interface ExtraLine {
  price: number;
  label: string;
  color: string;
  dashed?: boolean;
}

export default function PriceChart({ ticker, tradeDate, plan, title, extraLines }: {
  ticker: string;
  tradeDate: string;
  plan: Plan | null;
  /** 머리글 이름(기본 "가격 차트") — 분석 결과 차트가 본주 · 레버리지를 구분한다. */
  title?: string;
  extraLines?: ExtraLine[];
}) {
  const [prefs] = useState(loadPrefs);
  const [interval, setBarInterval] = useState<Interval>(prefs.interval);
  const [show, setShow] = useState<Show>(prefs.show);
  const [candles, setCandles] = useState<Candles | null>(null);
  const [fills, setFills] = useState<ReviewRow[]>([]);
  const [failed, setFailed] = useState(false);
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const box = useRef<HTMLDivElement>(null);
  // 지지 · 저항 — 기본은 그 분석이 에이전트에게 준 레벨(계획에 저장됨), 없으면(옛 분석) 지금 봉 기준.
  const saved = plan?.support_resistance?.levels?.length ? plan.support_resistance : null;
  const [srMode, setSrMode] = useState<SrMode | null>(null);
  const mode: SrMode = srMode ?? (saved ? "plan" : "now");
  const [nowSr, setNowSr] = useState<SupportResistance | null>(null);
  const [srFailed, setSrFailed] = useState(false);
  const [hover, setHover] = useState<{ level: SrLevel; x: number; y: number } | null>(null);
  // 매 렌더 새 배열이면 선을 계속 다시 그린다 — 근거가 바뀔 때만.
  const sr: SrLevel[] = useMemo(
    () => (!show.sr ? [] : mode === "plan" ? saved?.levels ?? [] : nowSr?.levels ?? []),
    [show.sr, mode, saved, nowSr],
  );

  useEffect(() => savePrefs(interval, show), [interval, show]);
  const toggle = (k: keyof Show) => setShow((s) => ({ ...s, [k]: !s[k] }));

  useEffect(() => {
    if (!show.sr || mode !== "now" || nowSr) return;
    let live = true;
    setSrFailed(false);
    api.levels(ticker).then((v) => live && setNowSr(v)).catch(() => live && setSrFailed(true));
    return () => {
      live = false;
    };
  }, [show.sr, mode, nowSr, ticker]);

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
      setDrawn({ chart, series, markers: createSeriesMarkers(series, []), rows, intraday, lastClose: rows[rows.length - 1].bar.close, alive });
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
    const { chart, series } = drawn;
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
    // 선 근처(5px)에 마우스를 올리면 근거를 띄운다.
    const onMove = (param: { point?: { x: number; y: number }; paneIndex?: number }) => {
      const pt = param.point;
      // y 는 패널 기준 좌표다 — RSI 패널 위에서는 지지 · 저항을 찾지 않는다.
      if (!pt || (param.paneIndex ?? 0) !== 0 || sr.length === 0) return setHover(null);
      let best: { level: SrLevel; d: number } | null = null;
      for (const level of sr) {
        const y = series.priceToCoordinate(level.price);
        if (y === null) continue;
        const d = Math.abs(y - pt.y);
        if (d <= 5 && (!best || d < best.d)) best = { level, d };
      }
      // 툴팁은 선의 높이에 붙이고, 다른 선으로 옮겨 갈 때만 다시 그린다(마우스 이동마다 렌더하지 않게).
      const hit = best?.level ?? null;
      setHover((prev) => {
        if (!hit) return null;
        if (prev?.level === hit) return prev;
        return { level: hit, x: pt.x, y: series.priceToCoordinate(hit.price) ?? pt.y };
      });
    };
    chart.subscribeCrosshairMove(onMove);
    return () => {
      setHover(null);
      if (!drawn.alive.current) return;
      chart.unsubscribeCrosshairMove(onMove);
      for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, sr]);

  const srSource = mode === "plan" ? saved : nowSr;
  const currency = isKorean(ticker) ? "원" : "달러";
  const levels = plan ? [plan.entry_price, plan.stop_loss, plan.price_target] : [];
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
        {show.sr && (
          <span className="chart-sr-source" role="group" aria-label="지지 · 저항 기준">
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
          aria-describedby={hasLevels && show.levels ? "price-chart-legend" : undefined}
        >
          {!candles && <p className="muted small">불러오는 중…</p>}
          {hover && (
            <div className="sr-tip" style={tipPosition(hover, box.current)}>
              <strong>{hover.level.kind === "support" ? "지지" : "저항"} {price(hover.level.price)}</strong>{" "}
              <span className="muted">{TF_LABEL[hover.level.timeframe]} · 강도 {hover.level.strength}</span>
              <div>{hover.level.basis}</div>
            </div>
          )}
        </div>
      )}
      {plan && hasLevels && show.levels && (
        <p id="price-chart-legend" className="muted small price-chart-legend">
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
      {show.sr && (
        srFailed && mode === "now" ? (
          <p className="muted small">지지 · 저항을 계산하지 못했습니다.</p>
        ) : mode === "now" && !nowSr ? (
          <p className="muted small">지지 · 저항 계산 중…</p>
        ) : sr.length > 0 ? (
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
    </section>
  );
}
