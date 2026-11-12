import { CandlestickSeries, ColorType, createChart, LineStyle, type IChartApi, type ISeriesApi, type UTCTimestamp, type AutoscaleInfo } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, type CandleInterval, type Candles } from "../api";
import { price } from "../format";
import type { EstimatedCandles } from "../types";

// 분봉 시각은 한국 시각으로(LiveChart · PriceChart 와 같은 방식)
const KST_SEC = 9 * 3600;
const REFRESH_MS = 60_000;

// #206: 분석 차트와 같은 주기(5 · 15 · 30분 · 1 · 4시간 · 일 · 주 · 월 · 년) + 시세판만의 10분
export const BOARD_INTERVALS = [
  { id: "5m", label: "5분" },
  { id: "10m", label: "10분" },
  { id: "15m", label: "15분" },
  { id: "30m", label: "30분" },
  { id: "1h", label: "1시간" },
  { id: "4h", label: "4시간" },
  { id: "1d", label: "일" },
  { id: "1w", label: "주" },
  { id: "1M", label: "월" },
  { id: "1y", label: "년" },
] as const;
export type BoardInterval = (typeof BOARD_INTERVALS)[number]["id"];

// 실제 봉을 읽을 주기 · 개수(10분은 5분 둘을 합친다)
const REAL: Record<BoardInterval, { iv: CandleInterval; count: number }> = {
  "5m": { iv: "5m", count: 300 },
  "10m": { iv: "5m", count: 600 },
  "15m": { iv: "15m", count: 300 },
  "30m": { iv: "30m", count: 300 },
  "1h": { iv: "60m", count: 300 },
  "4h": { iv: "240m", count: 250 },
  "1d": { iv: "1d", count: 250 },
  "1w": { iv: "1w", count: 156 },
  "1M": { iv: "1M", count: 120 },
  "1y": { iv: "1y", count: 30 },
};
// 장외 추정 봉을 낼 수 있는 주기(desk.estimate.CANDLE_INTERVALS) — 나머지는 실제 봉만
const ESTIMATED = new Set<BoardInterval>(["5m", "10m", "15m", "1h", "1d", "1w"]);

type Bar = { time: UTCTimestamp; open: number; high: number; low: number; close: number };

function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function toTime(iso: string): UTCTimestamp {
  return (Math.floor(new Date(iso).getTime() / 1000) + KST_SEC) as UTCTimestamp;
}
/** 같은 시각은 마지막 값만, 시간순 */
function clean(bars: Bar[]): Bar[] {
  const m = new Map<number, Bar>();
  for (const b of bars) m.set(b.time, b);
  return [...m.values()].sort((a, b) => a.time - b.time);
}
function merge10(bars: Bar[]): Bar[] {
  const out = new Map<number, Bar>();
  for (const b of bars) {
    const k = (b.time - (b.time % 600)) as UTCTimestamp;
    const m = out.get(k);
    if (!m) out.set(k, { ...b, time: k });
    else out.set(k, { ...m, high: Math.max(m.high, b.high), low: Math.min(m.low, b.low), close: b.close });
  }
  return [...out.values()];
}

/**
 * 시세판 차트(#124): 실제 봉(/api/candles) 뒤에 장외 추정 봉(/api/estimate/candles)을 옅은 색으로 이어 그리고
 * 기준 종가 선을 둔다. 1분마다 다시 읽는다. 추정 봉이 없으면(계약 없음 · 장중) 실제 봉만.
 */
/**
 * mark: #296 종목 정보 카드의 지지/저항 단계를 눌러 넘어온 가격 — 굵은 강조선을 긋고 축이 그 가격까지 보이게 넓힌다.
 */
export default function BoardChart({ ticker, name, mark }: { ticker: string; name?: string; mark?: { price: number; label: string } | null }) {
  const box = useRef<HTMLDivElement>(null);
  const ref = useRef<{ chart: IChartApi; real: ISeriesApi<"Candlestick">; est: ISeriesApi<"Candlestick"> } | null>(null);
  const [interval, setIv] = useState<BoardInterval>(() => {
    try {
      const v = localStorage.getItem("desk.board.interval");
      return (BOARD_INTERVALS.some((i) => i.id === v) ? v : "15m") as BoardInterval;
    } catch {
      return "15m";
    }
  });
  const [info, setInfo] = useState<{ est: EstimatedCandles | null; real: number; failed: boolean } | null>(null);

  // 차트는 종목마다 새로
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const up = css("--up") || "#f2464b";
    const down = css("--down") || "#3a86f8";
    const grid = css("--border") || "#2a3038";
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted") || "#8c939d" },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v) },
    });
    const real = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    });
    // 추정 봉: 같은 색의 옅은 면 + 진한 테두리
    const est = chart.addSeries(CandlestickSeries, {
      upColor: up + "40", downColor: down + "40", borderUpColor: up, borderDownColor: down, wickUpColor: up + "99", wickDownColor: down + "99",
      lastValueVisible: true, priceLineVisible: false,
    });
    ref.current = { chart, real, est };
    return () => {
      ref.current = null;
      chart.remove();
    };
  }, [ticker]);

  // #296 강조선: 축 자동 범위에 그 가격을 넣어 멀리 있어도 화면에 보이게 한다
  useEffect(() => {
    const c = ref.current;
    if (!c || !mark) return;
    const color = css("--accent") || "#c9d1db";
    const line = c.real.createPriceLine({ price: mark.price, title: mark.label, color, lineWidth: 3, lineStyle: LineStyle.Solid, axisLabelVisible: true });
    c.real.applyOptions({
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const r = original();
        if (!r || !r.priceRange) return r;
        return { ...r, priceRange: { minValue: Math.min(r.priceRange.minValue, mark.price), maxValue: Math.max(r.priceRange.maxValue, mark.price) } };
      },
    });
    return () => {
      if (ref.current !== c) return;
      c.real.removePriceLine(line);
      c.real.applyOptions({ autoscaleInfoProvider: undefined });
    };
  }, [ticker, mark]);

  useEffect(() => {
    try {
      localStorage.setItem("desk.board.interval", interval);
    } catch {
      /* 그대로 */
    }
    let alive = true;
    let line: ReturnType<ISeriesApi<"Candlestick">["createPriceLine"]> | null = null;
    const load = async () => {
      const c = ref.current;
      if (!c) return;
      const spec = REAL[interval];
      const [realRes, estRes] = await Promise.allSettled([
        api.candles(ticker, spec.iv, spec.count),
        ESTIMATED.has(interval) ? api.estimateCandles(ticker, interval) : Promise.reject(new Error("no estimate")),
      ]);
      if (!alive || ref.current !== c) return;
      let real: Bar[] = [];
      if (realRes.status === "fulfilled") {
        real = clean((realRes.value as Candles).bars.map((b) => ({ time: toTime(b.at), open: b.open, high: b.high, low: b.low, close: b.close })));
        if (interval === "10m") real = merge10(real);
      }
      const estData = estRes.status === "fulfilled" && "bars" in estRes.value ? (estRes.value as EstimatedCandles) : null;
      const lastReal = real.length ? real[real.length - 1].time : 0;
      // 실제 봉이 끝난 뒤만 — 장중(실제 봉이 이어지는 동안)엔 추정 봉이 겹치지 않는다
      const est = estData && estData.market_state === "closed"
        ? clean(estData.bars.map((b) => ({ time: toTime(b.at), open: b.open, high: b.high, low: b.low, close: b.close })))
            .filter((b) => b.time > lastReal)
        : [];
      c.real.setData(real);
      c.est.setData(est);
      if (line) c.real.removePriceLine(line);
      line = estData && est.length
        ? c.real.createPriceLine({ price: estData.close, title: "기준 종가", color: css("--muted") || "#8c939d", lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true })
        : null;
      setInfo({ est: est.length ? estData : null, real: real.length, failed: realRes.status === "rejected" && !est.length });
    };
    load();
    // 처음 그릴 때만 화면에 맞춘다
    const fit = window.setTimeout(() => ref.current?.chart.timeScale().fitContent(), 400);
    const t = window.setInterval(load, REFRESH_MS);
    return () => {
      alive = false;
      window.clearTimeout(fit);
      window.clearInterval(t);
      // 주기를 바꿀 때 이전 기준 종가 선을 지운다(차트는 종목이 바뀔 때만 새로 만든다)
      const c = ref.current;
      if (c && line) c.real.removePriceLine(line);
    };
  }, [ticker, interval]);

  const e = info?.est;
  return (
    <section className="card board-chart" aria-label={`${name ?? ticker} 차트`}>
      <div className="bc-head">
        <h3>{name ?? ticker} <span className="muted small">{ticker}</span></h3>
        <div className="chips" role="group" aria-label="봉 주기">
          {BOARD_INTERVALS.map((iv) => (
            <button key={iv.id} type="button" className={`chip${iv.id === interval ? " chip-on" : ""}`} aria-pressed={iv.id === interval}
              onClick={() => setIv(iv.id)}>{iv.label}</button>
          ))}
        </div>
      </div>
      <div ref={box} className="bc-box" />
      <p className="muted small bc-note">
        {info?.failed
          ? "봉을 불러오지 못했습니다."
          : e
            ? `옅은 봉은 ${e.close_date} 종가 ${price(e.close)} 이후의 추정 봉입니다(${e.method === "leveraged" ? `${e.basis} × ${e.multiple}` : e.method === "proxy" ? `${e.basis} 계약 대용` : "바이낸스 계약"}). 실제 체결이 아닙니다.`
            : info
              ? ESTIMATED.has(interval) ? "추정 봉이 없습니다(장중이거나 따라갈 계약이 없음)." : "이 주기는 실제 봉만 그립니다(추정 봉은 5분 ~ 주봉)."
              : "불러오는 중…"}
      </p>
    </section>
  );
}
