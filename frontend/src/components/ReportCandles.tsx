import { CandlestickSeries, ColorType, createChart, LineStyle, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { css, useColorScheme } from "../chartColors";
import { price } from "../format";
import type { ExplainChart, ReportChartSnap, ReportFrame } from "../types";
import { PriceBands, withAlpha, type Band } from "./chartBands";
import { crosshairTime } from "./chartFormat";

/**
 * #682 리포트 칸의 작은 캔들 차트 — 리포트를 쓸 때 저장한 봉 · 선(report.charts[symbol])으로 그려 나중에 봐도 같은 그림.
 * 시간봉 탭(일 · 4시간 · 2시간 · 1시간), 그 시간봉의 지지 · 저항 띠, 여러 시간봉이 겹치는 구역(① …, 굵게 · 이름표),
 * 그 칸이 말하는 가격(구역 위 · 아래 · 전고점 …)과 '지금' 가격선. 화면에 보일 때만 그린다(부모가 WhenVisible).
 */
const FRAMES: ReportFrame[] = ["1d", "240m", "120m", "60m"];
const FRAME_KO: Record<ReportFrame, string> = { "1d": "일", "240m": "4시간", "120m": "2시간", "60m": "1시간" };
const MARKS = ["①", "②", "③", "④"];
const SHOWN_BARS = 80;
const KST_SEC = 9 * 3600;
/** 봉 시각 → 한국 날짜(국장 일봉은 KST 자정 = 전날 15:00Z 라 그대로 자르면 하루 이르다) */
const kstDay = (iso: string) => {
  const t = Date.parse(iso);
  return Number.isFinite(t) ? new Date(t + KST_SEC * 1000).toISOString().slice(0, 10) : iso.slice(0, 10);
};

export default function ReportCandles({ snap, chart }: { snap: ReportChartSnap; chart: ExplainChart }) {
  const scheme = useColorScheme();
  const frames = FRAMES.filter((f) => snap.frames[f]?.bars.length);
  // 칸이 말하는 시간봉으로 시작(없으면 첫 시간봉)
  const [tf, setTf] = useState<ReportFrame>(frames.find((f) => f === chart.timeframe) ?? frames[0] ?? "1d");
  const box = useRef<HTMLDivElement>(null);
  const frame = snap.frames[tf];
  const places = snap.confluence;

  useEffect(() => {
    const el = box.current;
    if (!el || !frame || frame.bars.length < 2) return;
    let made: IChartApi | null = null;
    try {
      const up = css("--up"), down = css("--down"), grid = css("--border");
      const intraday = tf !== "1d";
      made = createChart(el, {
        autoSize: true,
        layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted"), fontFamily: getComputedStyle(document.body).fontFamily },
        grid: { vertLines: { visible: false }, horzLines: { color: grid } },
        rightPriceScale: { borderColor: grid, scaleMargins: { top: 0.08, bottom: 0.08 } },
        timeScale: { borderColor: grid, timeVisible: intraday, secondsVisible: false },
        localization: { locale: "ko-KR", timeFormatter: crosshairTime },
        handleScroll: false, handleScale: false,
      });
      const series = made.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
        priceLineVisible: false, lastValueVisible: false,
        priceFormat: { type: "custom", minMove: 0.01, formatter: (v: number) => price(v) },
      });
      // 시각은 한국 시각으로 보이게(PriceChart 와 같다) — 같은 시각은 마지막 봉만
      const seen = new Map<number, { time: UTCTimestamp; open: number; high: number; low: number; close: number }>();
      for (const [at, o, h, l, c] of frame.bars) {
        const t = (Math.floor(Date.parse(at) / 1000) + KST_SEC) as UTCTimestamp;
        if (Number.isFinite(t)) seen.set(t, { time: t, open: o, high: h, low: l, close: c });
      }
      const data = [...seen.values()].sort((a, b) => a.time - b.time);
      series.setData(data);
      // 띠: 이 시간봉의 지지 · 저항(가까운 것은 진하게), 여러 시간봉 구역은 경고색 굵게 + 번호
      const sup = css("--sr-support"), res = css("--sr-resistance"), warn = css("--warn");
      const bands: Band[] = frame.zones.map((z) => ({
        top: z.high, bottom: z.low, fill: withAlpha(z.kind === "support" ? sup : res, z.near ? 0.16 : 0.08),
      }));
      places.forEach((p, i) => bands.push({ top: p.high, bottom: p.low, fill: withAlpha(warn, 0.22), edge: withAlpha(warn, 0.9),
        label: MARKS[i] ?? "", labelColor: warn }));   // 번호만 — 붙은 띠끼리 글자가 겹치지 않게(풀이는 아래 목록)
      const prim = new PriceBands(`11px ${getComputedStyle(document.body).fontFamily}`);
      series.attachPrimitive(prim);
      prim.set(bands, []);
      // 이 칸이 말하는 가격(점선 · 이름표)과 지금
      const muted = css("--muted");
      (chart.levels ?? []).forEach((v, i) => {
        if (v > 0) series.createPriceLine({ price: v, title: chart.labels?.[i] ?? "", color: muted, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
      });
      const now = frame.price ?? snap.close;
      if (now) series.createPriceLine({ price: now, title: "지금", color: css("--accent"), lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true });
      // 가까운 구역 · 칸이 말하는 가격이 보이게 축 범위에 넣는다(지금가에서 12% 넘게 먼 구역은 넣지 않는다)
      const ref = frame.price ?? snap.close;
      const close = (v: number) => !ref || Math.abs(v / ref - 1) <= 0.12;
      const near = [...frame.zones.filter((z) => z.near), ...places].flatMap((z) => [z.low, z.high]).filter(close)
        .concat((chart.levels ?? []).filter((v) => v > 0));
      if (near.length) {
        const lo = Math.min(...data.slice(-SHOWN_BARS).map((d) => d.low), ...near);
        const hi = Math.max(...data.slice(-SHOWN_BARS).map((d) => d.high), ...near);
        series.applyOptions({ autoscaleInfoProvider: () => ({ priceRange: { minValue: lo, maxValue: hi } }) });
      }
      made.timeScale().setVisibleLogicalRange({ from: Math.max(0, data.length - SHOWN_BARS), to: data.length + 1 });
    } catch {
      made?.remove();
      made = null;
    }
    const chartApi = made;
    return () => { chartApi?.remove(); };
  }, [snap, frame, tf, chart, places, scheme]);

  if (!frames.length) return null;
  return (
    <figure className="rp-candles">
      <div className="chips rp-candles-tabs" role="group" aria-label="시간봉">
        {frames.map((f) => (
          <button key={f} type="button" className={`chip chip-small${f === tf ? " chip-on" : ""}`} aria-pressed={f === tf} onClick={() => setTf(f)}>
            {FRAME_KO[f]}
          </button>
        ))}
      </div>
      <div ref={box} className="rp-candles-box" role="img"
        aria-label={`${snap.symbol} ${FRAME_KO[tf]} 캔들 차트 — 지지 · 저항 띠와 여러 시간봉 구역`} />
      {places.length > 0 && (
        <ol className="rp-places small">
          {places.map((p, i) => (
            <li key={i} className={`rp-place rp-place-${p.kind}`}>
              <span className="rp-place-num" aria-hidden>{MARKS[i]}</span> {p.label}
              <span className="muted"> ({p.distance_pct > 0 ? "+" : ""}{p.distance_pct.toFixed(1)}%)</span>
            </li>
          ))}
        </ol>
      )}
      <figcaption className="muted small">{kstDay(snap.as_of)} 기준 저장한 차트 · 띠는 그 시간봉의 가까운 선, 노란 띠는 여러 시간봉이 겹치는 곳. {snap.note}</figcaption>
    </figure>
  );
}
