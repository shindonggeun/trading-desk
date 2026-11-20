import { useEffect } from "react";
import type { IPrimitivePaneRenderer, ISeriesPrimitive, ISeriesApi, IChartApi, Time } from "lightweight-charts";
import type { AccumulationLegs, AccumulationView } from "../types";
import { PriceBands, withAlpha } from "./chartBands";

type AccumulationChart = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  alive: { current: boolean };
  interval: string;
};

export function useAccumulationOverlay(
  drawn: AccumulationChart | null,
  view: AccumulationView | null | undefined,
  legs: AccumulationLegs | null | undefined,
  timeAt: (iso: string) => Time | null,
  colors: { accent: string; muted: string; surface: string },
  font: string,
) {
  const { accent, muted, surface } = colors;
  useEffect(() => {
    if (!drawn?.alive.current || !view || view.error !== undefined) return;
    const { series, chart } = drawn;
    const bands = new PriceBands(font);
    bands.set([...view.below, ...view.above].map((band) => {
      const candidate = band.role === "accumulation";
      const color = candidate ? accent : muted;
      return {
        top: band.high,
        bottom: band.low,
        fill: withAlpha(color, 0.14),
        edge: withAlpha(color, 0.55),
        labelColor: color,
        label: candidate ? `↑ 매집 후보(${view.label})` : `매물 저항(${view.label})`,
      };
    }), []);
    series.attachPrimitive(bands);

    const legsToDraw = view.timeframe === drawn.interval
      ? (legs?.items ?? []).flatMap((leg) => {
          const start = leg.from.at == null ? null : timeAt(leg.from.at);
          const end = leg.to.at == null ? null : timeAt(leg.to.at);
          return start == null || end == null ? [] : [{ leg, start, end }];
        })
      : [];
    const renderer: IPrimitivePaneRenderer = {
      draw(target) {
        target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
          const scale = chart.timeScale();
          ctx.save();
          ctx.font = font;
          ctx.textBaseline = "middle";
          for (const { leg, start, end } of legsToDraw) {
            const x0 = scale.timeToCoordinate(start);
            const x1 = scale.timeToCoordinate(end);
            const y0 = series.priceToCoordinate(leg.from.price);
            const y1 = series.priceToCoordinate(leg.to.price);
            if (x0 == null || x1 == null || y0 == null || y1 == null || x1 <= x0 || x1 < 0 || x0 > mediaSize.width) continue;
            const color = leg.direction === "up" ? accent : muted;
            ctx.strokeStyle = color;
            ctx.setLineDash(leg.live ? [4, 3] : []);
            ctx.beginPath();
            ctx.moveTo(x0, y0);
            ctx.lineTo(x1, y1);
            ctx.stroke();
            const duration = leg.days == null ? `${leg.bars}봉` : `${leg.days}일`;
            const label = `${leg.pct > 0 ? "+" : ""}${leg.pct}% · ${duration}`;
            const width = ctx.measureText(label).width + 10;
            const x = Math.max(0, Math.min((x0 + x1 - width) / 2, mediaSize.width - width));
            const y = Math.max(0, Math.min((y0 + y1) / 2 - 9, mediaSize.height - 18));
            ctx.fillStyle = surface;
            ctx.fillRect(x, y, width, 18);
            ctx.fillStyle = color;
            ctx.fillText(label, x + 5, y + 9);
          }
          ctx.restore();
        });
      },
    };
    const primitive: ISeriesPrimitive<Time> = { paneViews: () => [{ renderer: () => renderer, zOrder: () => "top" }] };
    if (legsToDraw.length) series.attachPrimitive(primitive);
    return () => {
      if (!drawn.alive.current) return;
      series.detachPrimitive(bands);
      if (legsToDraw.length) series.detachPrimitive(primitive);
    };
  }, [drawn, view, legs, timeAt, accent, muted, surface, font]);
}