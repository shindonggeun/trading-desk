import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api, type Candles, type ReviewRow } from "../api";
import { price } from "../format";
import type { Plan } from "../types";

type Interval = "1d" | "1w";
// 일봉 약 6개월, 주봉 약 2년(주봉 · 200개 초과는 백엔드가 야후로 받는다).
const COUNT: Record<Interval, number> = { "1d": 130, "1w": 104 };
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

/** 봉 시각(UTC) → 한국 날짜. 국장 자정(KST) · 미장 자정(ET) 모두 그 거래일 날짜가 된다. */
function kstDay(iso: string): string {
  return new Date(new Date(iso).getTime() + 9 * 3_600_000).toISOString().slice(0, 10);
}

const ET_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });

/** 체결 시각 → 그 종목 거래일. 미장 정규장은 KST 로 자정을 넘기므로 뉴욕 날짜로 본다. */
function fillDay(filledAt: string, ticker: string): string {
  return isKorean(ticker) ? filledAt.slice(0, 10) : ET_DAY.format(new Date(filledAt));
}

/** 날짜가 들어가는 봉(그 날짜 이하의 마지막 봉). 차트 앞이면 null. */
function barFor(days: string[], day: string): string | null {
  let hit: string | null = null;
  for (const d of days) {
    if (d <= day) hit = d;
    else break;
  }
  return hit;
}

/** 날짜별 마지막 봉만 남기고 오름차순 — 야후가 진행 중 봉을 덧붙여도 setData 가 throw 하지 않게. */
function dailyBars(bars: Candles["bars"]) {
  const byDay = new Map<string, Candles["bars"][number]>();
  for (const b of bars) byDay.set(kstDay(b.at), b);
  return [...byDay.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([day, bar]) => ({ day, bar }));
}

function css(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

type Drawn = {
  series: ISeriesApi<"Candlestick">;
  markers: ISeriesMarkersPluginApi<Time>;
  days: string[];
  lastClose: number;
  /** chart.remove() 뒤에는 false — 지운 차트에 선을 지우면 다시 그리기가 예약돼 throw 한다. */
  alive: { current: boolean };
};

/** 분석 화면의 가격 차트 — 일봉/주봉 캔들 · 거래량, 진입 · 손절 · 목표선, 분석일, 내 체결(#18). */
export default function PriceChart({ ticker, tradeDate, plan }: { ticker: string; tradeDate: string; plan: Plan | null }) {
  const [interval, setBarInterval] = useState<Interval>("1d");
  const [candles, setCandles] = useState<Candles | null>(null);
  const [fills, setFills] = useState<ReviewRow[]>([]);
  const [failed, setFailed] = useState(false);
  const [drawn, setDrawn] = useState<Drawn | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    setCandles(null);
    setFailed(false);
    api
      .candles(ticker, interval, COUNT[interval])
      .then((c) => live && setCandles(c))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [ticker, interval]);

  // 이 종목 체결만. 브로커가 체결 내역을 안 주거나 실패하면 마커 없이 그린다.
  useEffect(() => {
    let live = true;
    recentFills().then((rows) => live && setFills(rows.filter((f) => baseCode(f.ticker) === baseCode(ticker))));
    return () => {
      live = false;
    };
  }, [ticker]);

  // 차트와 봉 — 봉이 바뀔 때만 새로 만든다(계획 · 체결이 늦게 와도 확대 · 이동 상태가 유지된다).
  useEffect(() => {
    const el = box.current;
    if (!el || !candles || candles.bars.length === 0) return;
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
        },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
        rightPriceScale: { borderColor: grid },
        timeScale: { borderColor: grid, lockVisibleTimeRangeOnResize: true }, // 창 크기를 바꿔도 보던 기간 유지
        localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v) },
      });
      const rows = dailyBars(candles.bars);
      const series = chart.addSeries(CandlestickSeries, {
        upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
      });
      series.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.24 } }); // 아래는 거래량 자리
      series.setData(rows.map(({ day, bar }) => ({ time: day as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
      const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volume.setData(rows.map(({ day, bar }) => ({ time: day as Time, value: bar.volume, color: (bar.close >= bar.open ? up : down) + "55" })));
      chart.timeScale().fitContent();
      setDrawn({ series, markers: createSeriesMarkers(series, []), days: rows.map((r) => r.day), lastClose: rows[rows.length - 1].bar.close, alive });
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

  // 계획선 · 마커 · 축 범위 — 차트를 다시 만들지 않고 갈아 끼운다.
  useEffect(() => {
    if (!drawn) return;
    const { series, markers, days, lastClose } = drawn;
    const lines: [number | null | undefined, string, string, LineStyle][] = [
      [plan?.entry_price, "진입", css("--accent", "#4f8cff"), LineStyle.Solid],
      [plan?.stop_loss, "손절", css("--short", "#e5534b"), LineStyle.Dashed],
      [plan?.price_target, "목표", css("--long", "#2ea043"), LineStyle.Dashed],
    ];
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
    const list: SeriesMarker<Time>[] = [];
    const analysed = barFor(days, tradeDate);
    if (analysed) list.push({ time: analysed as Time, position: "aboveBar", shape: "arrowDown", color: css("--warn", "#d29922"), text: "분석" });
    for (const f of fills) {
      const at = barFor(days, fillDay(f.filled_at, ticker));
      if (!at) continue;
      const buy = f.side === "BUY";
      list.push({ time: at as Time, position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown", color: buy ? up : down, text: `${buy ? "매수" : "매도"} ${price(f.price)}` });
    }
    list.sort((a, b) => (String(a.time) < String(b.time) ? -1 : String(a.time) > String(b.time) ? 1 : 0));
    markers.setMarkers(list);
    return () => {
      // 봉이 바뀌면 차트 effect 가 먼저 chart.remove() 한다 — 그때는 지울 선도 이미 없다.
      if (drawn.alive.current) for (const line of made) series.removePriceLine(line);
    };
  }, [drawn, plan, fills, tradeDate, ticker]);

  const currency = isKorean(ticker) ? "원" : "달러";
  const levels = plan ? [plan.entry_price, plan.stop_loss, plan.price_target] : [];
  const hasLevels = levels.some((v) => v != null);
  const outOfRange = !!drawn && levels.some((v) => v != null && Math.abs(v - drawn.lastClose) / drawn.lastClose > AUTOSCALE_MAX_GAP);
  return (
    <section className="card price-chart">
      <div className="price-chart-head">
        <h3>
          가격 차트 <span className="muted small">{currency}{candles ? ` · ${candles.source}` : ""}</span>
        </h3>
        <div className="review-periods" role="group" aria-label="봉 간격">
          {(["1d", "1w"] as Interval[]).map((iv) => (
            <button key={iv} type="button" className={iv === interval ? "" : "secondary"} aria-pressed={iv === interval} onClick={() => setBarInterval(iv)}>
              {iv === "1d" ? "일봉" : "주봉"}
            </button>
          ))}
        </div>
      </div>
      {failed ? (
        <p className="muted small">시세를 불러오지 못했습니다.</p>
      ) : candles && candles.bars.length === 0 ? (
        <p className="muted small">표시할 봉이 없습니다.</p>
      ) : (
        <div
          ref={box}
          className="price-chart-box"
          role="img"
          aria-label={`${ticker} ${interval === "1d" ? "일봉" : "주봉"} 가격 차트${drawn ? `, 마지막 종가 ${price(drawn.lastClose)}` : ""}`}
          aria-describedby={hasLevels ? "price-chart-legend" : undefined}
        >
          {!candles && <p className="muted small">불러오는 중…</p>}
        </div>
      )}
      {plan && hasLevels && (
        <p id="price-chart-legend" className="muted small price-chart-legend">
          <span className="lg lg-entry" /> 진입 {price(plan.entry_price)} · <span className="lg lg-stop" /> 손절 {price(plan.stop_loss)} ·{" "}
          <span className="lg lg-target" /> 목표 {price(plan.price_target)}
          {fills.length > 0 && ` · 내 체결 ${fills.length}건`}
          {outOfRange && " · 현재가와 너무 먼 선은 축 범위에서 뺐습니다"}
        </p>
      )}
    </section>
  );
}
