import {
  CandlestickSeries,
  ColorType,
  createChart,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, followLive, type Candles, type LiveFrame, type StreamState } from "../api";
import { price } from "../format";
import type { Plan } from "../types";

// 분봉 시각은 한국 시각으로 보인다(PriceChart 와 같은 방식).
const KST_SEC = 9 * 3600;
// Toss serves at most 200 bars; asking for more sends the history to Yahoo (KRX 1-minute data lags
// 15+ minutes there) while the stream's bars come from Toss — two sources and a gap.
const HISTORY = 200;
type Bar = Candles["bars"][number];

function css(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function toTime(iso: string): UTCTimestamp {
  return (Math.floor(new Date(iso).getTime() / 1000) + KST_SEC) as UTCTimestamp;
}

/**
 * 실시간 1분봉(#26): 처음엔 /api/candles 1분봉, 그 뒤엔 /api/live/stream 이 주는 최근 봉 · 현재가로
 * 마지막 봉을 늘린다. 계획이 있으면 진입 · 손절 · 목표와 지지 · 저항(분석 당시)을 겹쳐 그린다.
 */
export default function LiveChart({ ticker, plan, onPrice }: {
  ticker: string;
  plan: Plan | null;
  onPrice?: (p: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; series: ISeriesApi<"Candlestick">; volume: ISeriesApi<"Histogram"> } | null>(null);
  const last = useRef<{ time: UTCTimestamp; bar: Bar } | null>(null);
  const [state, setState] = useState<"loading" | "open" | "closed" | "error">("loading");
  const [stream, setStream] = useState<StreamState>("live");
  const [updated, setUpdated] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const onPriceRef = useRef(onPrice);
  onPriceRef.current = onPrice;

  // 차트 · 첫 1분봉 · 흐름 — 종목이 바뀌면 처음부터
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let alive = true;
    const up = css("--up", "#f2464b");
    const down = css("--down", "#3a86f8");
    const grid = css("--border", "#2a3038");
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted", "#8c939d") },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v) },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    });
    series.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chartRef.current = { chart, series, volume };
    last.current = null;
    setState("loading");
    setUpdated(null);
    setSource(null);

    const put = (bar: Bar) => {
      const t = toTime(bar.at);
      if (last.current && t < last.current.time) return; // 이미 지난 봉
      series.update({ time: t, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
      volume.update({ time: t, value: bar.volume, color: (bar.close >= bar.open ? up : down) + "55" });
      last.current = { time: t, bar };
    };
    // 현재가로 진행 중인 봉의 종가 · 고가 · 저가를 갱신한다(1분봉은 서버 캐시로 30초쯤 늦다).
    const tick = (p: number) => {
      const cur = last.current;
      if (!cur) return;
      const bar = { ...cur.bar, close: p, high: Math.max(cur.bar.high, p), low: Math.min(cur.bar.low, p) };
      series.update({ time: cur.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
      last.current = { time: cur.time, bar };
    };

    let stop: (() => void) | undefined;
    let wasLost = false;
    const history = () => api.candles(ticker, "1m", HISTORY);
    // After the stream was lost (sleep, network), the minutes in between come only from a fresh history.
    const refill = () =>
      history()
        .then((c) => alive && load(c))
        .catch(() => {});
    const load = (c: Candles) => {
      const seen = new Map<number, Bar>();
      for (const b of c.bars) seen.set(toTime(b.at), b); // 같은 시각은 마지막 봉만
      const rows = [...seen.entries()].sort((a, b) => a[0] - b[0]);
      series.setData(rows.map(([t, b]) => ({ time: t as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })));
      volume.setData(rows.map(([t, b]) => ({ time: t as UTCTimestamp, value: b.volume, color: (b.close >= b.open ? up : down) + "55" })));
      const end = rows[rows.length - 1];
      last.current = end ? { time: end[0] as UTCTimestamp, bar: end[1] } : null;
      setSource(c.source);
      return end;
    };
    history()
      .then((c) => {
        if (!alive) return;
        const end = load(c);
        chart.timeScale().fitContent();
        if (end) onPriceRef.current?.(end[1].close);
      })
      .catch(() => alive && setState("error"))
      .finally(() => {
        if (!alive) return;
        stop = followLive(
          ticker,
          (f: LiveFrame) => {
            if (!alive) return;
            if (f.state === "closed") {
              setState("closed");
              return;
            }
            setState("open");
            for (const b of f.bars ?? []) put(b);
            if (f.quote) {
              tick(f.quote.price);
              onPriceRef.current?.(f.quote.price);
              setUpdated(f.quote.fetched_at);
              setSource(f.quote.source);
            }
          },
          (s: StreamState) => {
            if (!alive) return;
            if (s === "lost") wasLost = true;
            else if (wasLost) {
              wasLost = false;
              refill();
            }
            setStream(s);
          },
        );
      });
    return () => {
      alive = false;
      stop?.();
      chartRef.current = null;
      chart.remove();
    };
  }, [ticker]);

  // 계획선 · 지지 · 저항 — 차트는 그대로 두고 선만 갈아 끼운다
  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    const lines: [number | null | undefined, string, string, LineStyle][] = [
      [plan?.entry_price, "진입", css("--accent", "#c9d1db"), LineStyle.Solid],
      [plan?.stop_loss, "손절", css("--short", "#3a86f8"), LineStyle.Dashed],
      [plan?.price_target, "목표", css("--long", "#f2464b"), LineStyle.Dashed],
      [plan?.long_trigger, "롱 조건", css("--long", "#f2464b"), LineStyle.Dotted],
      [plan?.short_trigger, "숏 조건", css("--short", "#3a86f8"), LineStyle.Dotted],
      ...(plan?.support_resistance?.levels ?? []).slice(0, 8).map(
        (lv): [number, string, string, LineStyle] => [
          lv.price, lv.kind === "resistance" ? "저항" : "지지",
          css(lv.kind === "resistance" ? "--sr-resistance" : "--sr-support", lv.kind === "resistance" ? "#b58cf0" : "#2bb3a3"), LineStyle.LargeDashed,
        ],
      ),
    ];
    const made: IPriceLine[] = [];
    for (const [value, title, color, lineStyle] of lines) {
      if (value != null) made.push(c.series.createPriceLine({ price: value, title, color, lineWidth: 1, lineStyle, axisLabelVisible: true }));
    }
    return () => {
      if (chartRef.current?.series === c.series) for (const l of made) c.series.removePriceLine(l);
    };
  }, [plan, ticker]);

  const label = {
    loading: "불러오는 중",
    open: stream === "live" ? "장중 · 실시간" : "연결 끊김 — 다시 연결하는 중",
    closed: "장 마감 — 장이 열리면 이어서 갱신",
    error: "1분봉을 불러오지 못했습니다",
  }[state];
  return (
    <section className="card live-chart">
      <div className="live-chart-head">
        <span className={`live-dot live-${state === "open" && stream === "live" ? "on" : "off"}`} aria-hidden />
        <span className="small">{label}</span>
        <span className="muted small">
          {source && ` · ${source}`}
          {updated && ` · ${new Date(updated).toLocaleTimeString("ko-KR")} 갱신`}
        </span>
      </div>
      <div ref={box} className="live-chart-box" />
    </section>
  );
}
