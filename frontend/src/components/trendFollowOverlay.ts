import { useEffect } from "react";
import type { IChartApi, IPrimitivePaneRenderer, ISeriesApi, ISeriesPrimitive, Time } from "lightweight-charts";
import { css } from "../chartColors";
import { withAlpha } from "./chartBands";

type Chart = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  alive: { current: boolean };
};

export type TrendOverlayTarget = {
  prices: { price: number; label: string }[];
  /** 차트의 같은 시각 봉에 맞춘 앵커(시간봉이 다르면 비운다) */
  anchors: { time: Time; price: number; n: number }[];
  bar: { time: Time; high: number; low: number } | null;
};

const NUM = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });

/** #978 고른 추세 근거의 가격 · 앵커 · 확정봉 — 자동선 · 수동 작도 시리즈와 따로 그린다 */
export function useTrendFollowOverlay(drawn: Chart | null, target: TrendOverlayTarget | null) {
  useEffect(() => {
    if (!drawn?.alive.current || !target) return;
    const { chart, series } = drawn;
    const { prices, anchors, bar } = target;
    const lineColor = css("--warn");
    const anchorColor = css("--accent");
    const render: IPrimitivePaneRenderer = {
      draw(scope) {
        scope.useMediaCoordinateSpace(({ context, mediaSize }) => {
          const scale = chart.timeScale();
          context.save();
          context.font = "11px sans-serif";
          context.textBaseline = "middle";
          const edge = { top: 0, bottom: 0 };
          for (const { price, label } of prices) {
            const y = series.priceToCoordinate(price);
            const text = `${label} ${NUM.format(price)}`;
            context.fillStyle = lineColor;
            if (y == null || y < 0 || y > mediaSize.height) {
              // 축 밖 가격은 가장자리에 방향만 표시한다(축을 늘려 차트를 눌러 버리지 않는다)
              const top = y == null ? false : y < 0;
              const row = top ? edge.top++ : edge.bottom++;   // 같은 가장자리의 표시는 한 줄씩 쌓는다
              context.textAlign = "left";
              context.fillText(`${top ? "↑" : "↓"} ${text}`, 6, top ? 10 + row * 13 : mediaSize.height - 10 - row * 13);
              continue;
            }
            context.beginPath();
            context.setLineDash([6, 4]);
            context.strokeStyle = lineColor;
            context.lineWidth = 1.5;
            context.moveTo(0, y);
            context.lineTo(mediaSize.width, y);
            context.stroke();
            context.setLineDash([]);
            const w = context.measureText(text).width + 8;
            context.fillStyle = withAlpha(lineColor, .18);
            context.fillRect(6, y - 16, w, 14);
            context.fillStyle = lineColor;
            context.textAlign = "left";
            context.fillText(text, 10, y - 9);
          }
          const pts = anchors.flatMap((a) => {
            const x = scale.timeToCoordinate(a.time);
            const y = series.priceToCoordinate(a.price);
            return x != null && y != null ? [{ x, y, n: a.n }] : [];
          });
          if (pts.length >= 2) {
            context.beginPath();
            context.strokeStyle = withAlpha(anchorColor, .7);
            context.lineWidth = 1.5;
            context.moveTo(pts[0].x, pts[0].y);
            for (const p of pts.slice(1)) context.lineTo(p.x, p.y);
            context.stroke();
          }
          pts.forEach((p) => {
            context.beginPath();
            context.fillStyle = anchorColor;
            context.arc(p.x, p.y, 4, 0, Math.PI * 2);
            context.fill();
            context.textAlign = "center";
            context.fillText(String(p.n), p.x, p.y - 11);
          });
          if (bar) {
            const x = scale.timeToCoordinate(bar.time);
            const top = series.priceToCoordinate(bar.high);
            const bottom = series.priceToCoordinate(bar.low);
            if (x != null && top != null && bottom != null && x >= 0 && x <= mediaSize.width) {
              const half = Math.max(4, Math.min(scale.options().barSpacing * .42, 16));
              context.fillStyle = withAlpha(anchorColor, .15);
              context.strokeStyle = anchorColor;
              context.lineWidth = 1;
              context.fillRect(x - half, top - 3, half * 2, bottom - top + 6);
              context.strokeRect(x - half, top - 3, half * 2, bottom - top + 6);
            }
          }
          context.restore();
        });
      },
    };
    const primitive: ISeriesPrimitive<Time> = {
      paneViews: () => [{ renderer: () => render, zOrder: () => "top" }],
      autoscaleInfo(start, end) {
        // 보이는 앵커만 축에 더한다
        const visible = anchors.filter((a) => {
          const index = chart.timeScale().timeToIndex(a.time);
          return index != null && index >= start && index <= end;
        }).map((a) => a.price);
        return visible.length ? { priceRange: { minValue: Math.min(...visible), maxValue: Math.max(...visible) } } : null;
      },
    };
    series.attachPrimitive(primitive);
    return () => { if (drawn.alive.current) series.detachPrimitive(primitive); };
  }, [drawn, target]);
}

/** 앵커 · 확정봉이 화면 밖이면 보이게 옮긴다 */
export function revealTrendTimes(chart: IChartApi, times: Time[]) {
  const scale = chart.timeScale();
  const idx = times.map((t) => scale.timeToIndex(t)).filter((i): i is NonNullable<typeof i> => i != null);
  const visible = scale.getVisibleLogicalRange();
  if (!idx.length || !visible) return;
  const low = Math.min(...idx);
  const high = Math.max(...idx);
  if (low > visible.from + 1 && high < visible.to - 1) return;
  const span = Math.max(visible.to - visible.from, high - low + 8);
  const mid = (low + high) / 2;
  scale.setVisibleLogicalRange({ from: mid - span / 2, to: mid + span / 2 });
}
