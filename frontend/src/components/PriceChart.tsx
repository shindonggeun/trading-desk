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
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Candles, type ReviewRow } from "../api";
import { price } from "../format";
import type { Plan, SrLevel, SupportResistance } from "../types";

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

type SrMode = "plan" | "now" | "off";
const SR_LABEL: Record<SrMode, string> = { plan: "분석 당시", now: "지금", off: "끄기" };
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
  // 지지 · 저항 — 기본은 그 분석이 에이전트에게 준 레벨(계획에 저장됨), 없으면(옛 분석) 지금 봉 기준.
  const saved = plan?.support_resistance?.levels?.length ? plan.support_resistance : null;
  const [srMode, setSrMode] = useState<SrMode | null>(null);
  const mode: SrMode = srMode ?? (saved ? "plan" : "now");
  const [nowSr, setNowSr] = useState<SupportResistance | null>(null);
  const [srFailed, setSrFailed] = useState(false);
  const [hover, setHover] = useState<{ level: SrLevel; x: number; y: number } | null>(null);
  // 매 렌더 새 배열이면 선을 계속 다시 그린다 — 근거가 바뀔 때만.
  const sr: SrLevel[] = useMemo(
    () => (mode === "plan" ? saved?.levels ?? [] : mode === "now" ? nowSr?.levels ?? [] : []),
    [mode, saved, nowSr],
  );

  useEffect(() => {
    if (mode !== "now" || nowSr) return;
    let live = true;
    setSrFailed(false);
    api.levels(ticker).then((v) => live && setNowSr(v)).catch(() => live && setSrFailed(true));
    return () => {
      live = false;
    };
  }, [mode, nowSr, ticker]);

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
      setDrawn({ chart, series, markers: createSeriesMarkers(series, []), days: rows.map((r) => r.day), lastClose: rows[rows.length - 1].bar.close, alive });
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
    const onMove = (param: { point?: { x: number; y: number } }) => {
      const pt = param.point;
      if (!pt || sr.length === 0) return setHover(null);
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

  const srSource = mode === "plan" ? saved : mode === "now" ? nowSr : null;
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
        <div className="price-chart-tools">
          <div className="review-periods" role="group" aria-label="지지 · 저항선">
            <span className="muted small">지지 · 저항</span>
            {(["plan", "now", "off"] as SrMode[]).filter((m) => m !== "plan" || saved).map((m) => (
              <button key={m} type="button" className={m === mode ? "" : "secondary"} aria-pressed={m === mode} onClick={() => setSrMode(m)}>
                {SR_LABEL[m]}
              </button>
            ))}
          </div>
          <div className="review-periods" role="group" aria-label="봉 간격">
            {(["1d", "1w"] as Interval[]).map((iv) => (
              <button key={iv} type="button" className={iv === interval ? "" : "secondary"} aria-pressed={iv === interval} onClick={() => setBarInterval(iv)}>
                {iv === "1d" ? "일봉" : "주봉"}
              </button>
            ))}
          </div>
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
          {hover && (
            <div className="sr-tip" style={tipPosition(hover, box.current)}>
              <strong>{hover.level.kind === "support" ? "지지" : "저항"} {price(hover.level.price)}</strong>{" "}
              <span className="muted">{TF_LABEL[hover.level.timeframe]} · 강도 {hover.level.strength}</span>
              <div>{hover.level.basis}</div>
            </div>
          )}
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
      {mode !== "off" && (
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
