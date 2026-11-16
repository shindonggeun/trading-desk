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
import { crosshairTime, ticksClearOfLabels } from "./chartFormat";
import { keepAxisLabelsApart, nearestLevels, planBands, playbookDrawing, PriceBands, srBands } from "./chartBands";
import { levelsContradict } from "../listLevels";
import type { Plan } from "../types";
import { quoteSourceText } from "../korean";
import { bucketStart, LIVE_INTERVALS, LiveBucket, loadLiveInterval, minutesOf, saveLiveInterval } from "../liveBuckets";

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
 * 실시간 차트(#26): 처음엔 /api/candles 의 고른 간격 봉, 그 뒤엔 /api/live/stream 이 주는 최근 1분봉 · 현재가를
 * 같은 간격의 봉으로 접어(#380, 경계는 liveBuckets) 진행 중인 봉을 늘린다. 계획이 있으면 진입 · 손절 · 목표와 지지 · 저항(분석 당시)을 겹쳐 그린다.
 */
export default function LiveChart({ ticker, plan, onPrice }: {
  ticker: string;
  plan: Plan | null;
  onPrice?: (p: number) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<{ chart: IChartApi; series: ISeriesApi<"Candlestick">; volume: ISeriesApi<"Histogram"> } | null>(null);
  const last = useRef<LiveBucket | null>(null);
  const [iv, setIv] = useState(loadLiveInterval);
  const pickIv = (next: typeof iv) => {
    setIv(next);
    saveLiveInterval(next);
  };
  const [state, setState] = useState<"loading" | "open" | "closed" | "error">("loading");
  const [stream, setStream] = useState<StreamState>("live");
  const [updated, setUpdated] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const onPriceRef = useRef(onPrice);
  onPriceRef.current = onPrice;

  // 차트 · 첫 봉 · 흐름 — 종목이나 간격이 바뀌면 처음부터
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
      localization: { locale: "ko-KR", timeFormatter: crosshairTime }, // #434 가격 표기는 시리즈 priceFormat 에서
    });
    // #434 지금가 · 타점 라벨 가까운 눈금은 비운다
    const series: ISeriesApi<"Candlestick"> = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
      priceFormat: { type: "custom", minMove: 0.01, formatter: (v: number) => price(v), tickmarksFormatter: ticksClearOfLabels(() => [series], price) },
    });
    series.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chartRef.current = { chart, series, volume };
    last.current = null;
    setState("loading");
    setUpdated(null);
    setSource(null);

    const minutes = minutesOf(iv);
    const ms = (iso: string) => new Date(iso).getTime();
    const draw = (b: LiveBucket) => {
      const bar = b.bar();
      if (!bar) return;
      const t = toTime(bar.at);
      series.update({ time: t, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
      volume.update({ time: t, value: bar.volume, color: (bar.close >= bar.open ? up : down) + "55" });
    };
    // 흐름의 1분봉 하나를 고른 간격의 봉에 넣는다 — 새 구간이면 새 봉
    const put = (bar: Bar) => {
      const start = bucketStart(ms(bar.at), ticker, minutes);
      let cur = last.current;
      if (cur && start < cur.start) return; // 이미 지난 봉
      if (!cur || start > cur.start) {
        cur = new LiveBucket(start, null);
        last.current = cur;
      }
      cur.addMinute(Math.floor(ms(bar.at) / 60_000) * 60_000, bar);
      draw(cur);
    };
    // 현재가로 진행 중인 봉의 종가 · 고가 · 저가를 갱신한다(1분봉은 서버 캐시로 30초쯤 늦다).
    const tick = (p: number) => {
      const cur = last.current;
      if (!cur) return;
      cur.tick(p);
      draw(cur);
    };
    let stop: (() => void) | undefined;
    let wasLost = false;
    const history = () => api.candles(ticker, iv, HISTORY);
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
      last.current = end ? new LiveBucket(ms(end[1].at), end[1]) : null;
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
  }, [ticker, iv]);

  // 계획선(축 라벨) · 지지 · 저항 구역 띠 · 손실 · 이익 영역(#98) — 차트는 그대로 두고 갈아 끼운다
  useEffect(() => {
    const c = chartRef.current;
    if (!c) return;
    // #436 진입을 사이에 두지 않는 등급상 타점(손절 = 목표 등)은 그리지 않는다
    const levels = !levelsContradict(plan);
    const lines: [number | null | undefined, string, string, LineStyle][] = [
      ...(levels ? [
        [plan?.entry_price, "진입", css("--accent", "#c9d1db"), LineStyle.Solid],
        // #311 손절 = 손실색 · 목표 = 이익색
        [plan?.stop_loss, "손절", css("--danger", "#ff6a3d"), LineStyle.Solid],
        [plan?.price_target, "목표", css("--ok", "#3fb68b"), LineStyle.Solid],
      ] as [number | null | undefined, string, string, LineStyle][] : []),
      [plan?.long_trigger, "롱 조건", css("--long", "#f2464b"), LineStyle.Dotted],
      [plan?.short_trigger, "숏 조건", css("--short", "#3a86f8"), LineStyle.Dotted],
    ];
    const made: IPriceLine[] = [];
    for (const [value, title, color, lineStyle] of lines) {
      if (value != null) made.push(c.series.createPriceLine({ price: value, title, color, lineWidth: 1, lineStyle, axisLabelVisible: true }));
    }
    // 1분봉이 늦게 와도 · 새 봉으로 축척이 움직여도 다시 맞춘다
    const stopLabels = keepAxisLabelsApart(c.chart, c.series, made);
    // 지지 · 저항은 축 라벨 없는 띠 — 기준가에서 가까운 3개씩
    const sr = plan?.support_resistance;
    const bands = new PriceBands(`11px ${getComputedStyle(document.body).fontFamily}`);
    c.series.attachPrimitive(bands);
    // 플레이북 구역 · 사다리 · 손절 · 목표(#117) — 실시간으로 구역에 다가가는 것을 본다
    const book = plan?.playbooks
      ? playbookDrawing(plan.playbooks, { long: css("--long", "#f2464b"), short: css("--short", "#3a86f8") }, price)
      : { bands: [], marks: [] };
    bands.set(
      [
        ...book.bands,
        ...planBands(plan, null, { loss: css("--danger", css("--up", "#f2464b")), profit: css("--ok", "#3fb68b") }),
        ...srBands(
          sr?.reference_price != null ? nearestLevels(sr.levels ?? [], sr.reference_price, 3, false) : [],
          sr?.atr, { support: css("--sr-support", "#2bb3a3"), resistance: css("--sr-resistance", "#b58cf0") },
        ),
      ],
      book.marks,
    );
    return () => {
      const alive = chartRef.current?.series === c.series;
      stopLabels(alive);
      if (alive) {
        for (const l of made) c.series.removePriceLine(l);
        c.series.detachPrimitive(bands);
      }
    };
  }, [plan, ticker, iv]);

  const label = {
    loading: "불러오는 중",
    open: stream === "live" ? "장중 · 실시간" : "연결 끊김 — 다시 연결하는 중",
    closed: "장 마감 — 장이 열리면 이어서 갱신",
    error: "봉을 불러오지 못했습니다",
  }[state];
  return (
    <section className="card live-chart">
      <div className="live-chart-head">
        <span className={`live-dot live-${state === "open" && stream === "live" ? "on" : "off"}`} aria-hidden />
        <span className="small">{label}</span>
        <span className="muted small">
          {source && ` · ${quoteSourceText(source)}`}
          {updated && ` · ${new Date(updated).toLocaleTimeString("ko-KR")} 갱신`}
        </span>
      </div>
      <div className="live-intervals" role="group" aria-label="봉 간격">
        {LIVE_INTERVALS.map((i) => (
          <button key={i.iv} type="button" className={`chip${iv === i.iv ? " chip-on" : ""}`} aria-pressed={iv === i.iv}
            onClick={() => pickIv(i.iv)}>{i.label}</button>
        ))}
      </div>
      <div ref={box} className="live-chart-box" />
    </section>
  );
}
