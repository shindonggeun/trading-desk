import { useEffect } from "react";
import type { IChartApi, IPrimitivePaneRenderer, ISeriesApi, ISeriesPrimitive, Time } from "lightweight-charts";
import { css } from "../chartColors";
import { withAlpha } from "./chartBands";

type Chart = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  alive: { current: boolean };
};

export type DailyOverlayTarget = { time: Time; high: number; low: number; resistancePrice: number | null };

/** Keeps the historical candle and projected price separate from the live and manual drawing series. */
export function useDailyResistanceOverlay(drawn: Chart | null, selection: DailyOverlayTarget | null) {
  useEffect(() => {
    if (!drawn?.alive.current || !selection) return;
    const { chart, series } = drawn;
    const { time, high, low, resistancePrice } = selection;
    const candleColor = css("--accent");
    const resistanceColor = css("--warn");
    const render: IPrimitivePaneRenderer = {
      draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
          const scale = chart.timeScale();
          const x = scale.timeToCoordinate(time);
          const top = series.priceToCoordinate(high);
          const bottom = series.priceToCoordinate(low);
          if (x == null || top == null || bottom == null || x < 0 || x > mediaSize.width) return;
          const half = Math.max(4, Math.min(scale.options().barSpacing * .42, 16));
          context.save();
          context.fillStyle = withAlpha(candleColor, .15);
          context.strokeStyle = candleColor;
          context.lineWidth = 1;
          context.fillRect(x - half, top - 3, half * 2, bottom - top + 6);
          context.strokeRect(x - half, top - 3, half * 2, bottom - top + 6);
          const y = resistancePrice !== null ? series.priceToCoordinate(resistancePrice) : null;
          if (y != null && y >= 0 && y <= mediaSize.height) {
            context.beginPath();
            context.setLineDash([5, 3]);
            context.strokeStyle = resistanceColor;
            context.lineWidth = 2;
            context.moveTo(Math.max(0, x - 36), y);
            context.lineTo(Math.min(mediaSize.width, x + 36), y);
            context.stroke();
            context.fillStyle = resistanceColor;
            context.beginPath();
            context.arc(x, y, 3, 0, Math.PI * 2);
            context.fill();
          }
          context.restore();
        });
      },
    };
    const primitive: ISeriesPrimitive<Time> = {
      paneViews: () => [{ renderer: () => render, zOrder: () => "top" }],
      autoscaleInfo(start, end) {
        // 선택한 봉이 보이는 동안만 저항 가격을 축에 더한다.
        const index = chart.timeScale().timeToIndex(time);
        return resistancePrice !== null && index != null && index >= start && index <= end
          ? { priceRange: { minValue: resistancePrice, maxValue: resistancePrice } }
          : null;
      },
    };
    series.attachPrimitive(primitive);
    return () => { if (drawn.alive.current) series.detachPrimitive(primitive); };
  }, [drawn, selection]);
}

export function revealDailyResistanceBar(chart: IChartApi, time: Time) {
  const scale = chart.timeScale();
  const index = scale.timeToIndex(time);
  const visible = scale.getVisibleLogicalRange();
  if (index == null || !visible || (index > visible.from + 2 && index < visible.to - 2)) return;
  const half = Math.max(6, (visible.to - visible.from) / 2);
  scale.setVisibleLogicalRange({ from: index - half, to: index + half });
}
