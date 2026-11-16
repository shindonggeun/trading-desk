import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  LineSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import { crosshairTime } from "./chartFormat";
import type { WeeklyMap } from "../types";

/**
 * #205 주봉 차트 — 검정선(20주) · 빨간선(60주) · 50주 볼린저밴드(2σ)를 주봉에서 서버(desk/weekly_map.py)와
 * 같은 식으로 다시 그린다. 지지 · 저항은 가격선, 과거 "V자 예외"(검정선 이탈 뒤 바로 되찾음)와
 * "밴드 하단 뒤 더 낮았던" 주는 마커.
 */

const COUNT = 260;          // 약 5년 — 60주선이 앞쪽부터 그려지게
const KST_MS = 9 * 3600 * 1000;
const NARROW_WEEKS = 104;   // 좁은 화면에 처음 보이는 주봉 수(약 2년)

function css(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}
/** 주봉의 그 시장 날짜(한국 지수는 서울 자정 = UTC 전날 15시) */
const dayOf = (iso: string) => new Date(new Date(iso).getTime() + KST_MS).toISOString().slice(0, 10);

function sma(xs: number[], n: number, i: number): number | null {
  if (i + 1 < n) return null;
  let s = 0;
  for (let k = i - n + 1; k <= i; k++) s += xs[k];
  return s / n;
}
function pstdev(xs: number[], n: number, i: number): number | null {
  const m = sma(xs, n, i);
  if (m == null) return null;
  let s = 0;
  for (let k = i - n + 1; k <= i; k++) s += (xs[k] - m) ** 2;
  return Math.sqrt(s / n);
}

export default function WeeklyMapChart({ map }: { map: WeeklyMap }) {
  const box = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const symbol = map.source_symbol ?? map.symbol;

  useEffect(() => {
    let alive = true;
    let chart: IChartApi | null = null;
    setFailed(false);
    api.candles(symbol, "1w", COUNT).then((c) => {
      const el = box.current;
      if (!alive || !el || c.bars.length < 2) {
        if (alive && c.bars.length < 2) setFailed(true);
        return;
      }
      try {
        const grid = css("--border", "#2a2f37");
        const up = css("--up", "#e5484d");
        const down = css("--down", "#3e7bfa");
        const text = css("--text", "#e6e9ee");
        const muted = css("--muted", "#8c939d");
        chart = createChart(el, {
          autoSize: true,
          layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: muted,
            fontFamily: getComputedStyle(document.body).fontFamily },
          grid: { vertLines: { color: grid }, horzLines: { color: grid } },
          rightPriceScale: { borderColor: grid },
          timeScale: { borderColor: grid },
          localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v), timeFormatter: crosshairTime },
          handleScroll: { vertTouchDrag: false },        // 휴대폰: 세로로 밀면 페이지가 내려가게
        });
        const rows = c.bars.map((b) => ({ ...b, day: dayOf(b.at), utc: b.at.slice(0, 10) }));
        const t = (i: number) => rows[i].day as Time;
        const candles = chart.addSeries(CandlestickSeries, {
          upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
        });
        candles.setData(rows.map((b, i) => ({ time: t(i), open: b.open, high: b.high, low: b.low, close: b.close })));
        const closes = rows.map((b) => b.close);
        const line = (values: (number | null)[], color: string, width: 1 | 2, style: LineStyle, title: string) => {
          const s = chart!.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, priceLineVisible: false,
            lastValueVisible: false, crosshairMarkerVisible: false, title });
          s.setData(values.flatMap((v, i) => (v == null ? [] : [{ time: t(i), value: v }])));
        };
        const bb = closes.map((_, i) => {
          const m = sma(closes, map.bollinger.weeks, i);
          const sd = pstdev(closes, map.bollinger.weeks, i);
          return m == null || sd == null ? null : { up: m + 2 * sd, low: m - 2 * sd };
        });
        line(bb.map((x) => x?.up ?? null), muted, 1, LineStyle.Dashed, "");
        line(bb.map((x) => x?.low ?? null), muted, 1, LineStyle.Dashed, "볼린저 하단");
        line(closes.map((_, i) => sma(closes, map.lines.red.weeks, i)), up, 2, LineStyle.Solid, "빨간선");
        line(closes.map((_, i) => sma(closes, map.lines.black.weeks, i)), text, 2, LineStyle.Solid, "검정선");
        for (const p of map.supports.slice(0, 2)) {
          candles.createPriceLine({ price: p, color: `${down}99`, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "지지" });
        }
        for (const p of map.resistances.slice(0, 2)) {
          candles.createPriceLine({ price: p, color: `${up}99`, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "저항" });
        }
        // 서버의 사례 날짜는 UTC 날짜 — 같은 주봉을 찾아 그 시장 날짜에 찍는다
        const at = new Map(rows.map((b, i) => [b.utc, i]));
        const markers: SeriesMarker<Time>[] = [];
        for (const d of map.record.black_break.v_exceptions) {
          const i = at.get(d);
          if (i != null) markers.push({ time: t(i), position: "belowBar", color: text, shape: "arrowUp", text: "V자" });
        }
        for (const d of map.record.bb_lower_touch.lower_after) {
          const i = at.get(d);
          if (i != null) markers.push({ time: t(i), position: "belowBar", color: down, shape: "circle", text: "하단 뒤 더 하락" });
        }
        markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
        createSeriesMarkers(candles, markers);
        // 휴대폰 폭에는 5년치 주봉이 너무 빽빽하다 — 최근 2년만 보이고 밀면 앞이 나온다
        if (el.clientWidth < 520 && rows.length > NARROW_WEEKS) {
          chart.timeScale().setVisibleLogicalRange({ from: rows.length - NARROW_WEEKS, to: rows.length + 1 });
        } else {
          chart.timeScale().fitContent();
        }
      } catch {
        chart?.remove();
        chart = null;
        if (alive) setFailed(true);
      }
    }, () => alive && setFailed(true));
    return () => {
      alive = false;
      chart?.remove();
    };
  }, [symbol, map]);

  if (failed) return <p className="muted small">주봉 차트를 불러오지 못했습니다.</p>;
  return (
    <div className="wm-chart-box">
      <div ref={box} className="wm-chart" role="img"
        aria-label={`${map.name} 주봉 차트 — 검정선 ${price(map.lines.black.price)}, 빨간선 ${price(map.lines.red.price)}, 50주 볼린저 ${price(map.bollinger.lower)}~${price(map.bollinger.upper)}`} />
      <p className="wm-legend muted small">
        <span className="wm-key wm-key-black" /> 검정선(20주) <span className="wm-key wm-key-red" /> 빨간선(60주){" "}
        <span className="wm-key wm-key-bb" /> 50주 볼린저 · 점선 = 지지 · 저항 · ↑ V자 예외 · ● 밴드 하단 뒤 더 하락
      </p>
    </div>
  );
}
