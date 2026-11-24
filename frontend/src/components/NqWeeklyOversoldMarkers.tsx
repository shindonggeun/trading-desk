import { useEffect, useState } from "react";
import type { IChartApi, IPrimitivePaneRenderer, ISeriesApi, ISeriesPrimitive, Time } from "lightweight-charts";
import "./NqWeeklyOversoldMarkers.css";

type DrawingChart = {
  chart: IChartApi;
  series: ISeriesApi<"Candlestick">;
  alive: { current: boolean };
};
type Marker = { week: string; time: Time; low: number };
type Position = { week: string; x: number; y: number };
type Frame = { drawn: DrawingChart; markers: readonly Marker[]; positions: Position[] };

function equalPositions(a: Position[], b: Position[]) {
  return a.length === b.length && a.every((p, i) => p.week === b[i].week && p.x === b[i].x && p.y === b[i].y);
}

/** Reposition interactive markers only when the chart redraws or its viewport changes. */
export default function NqWeeklyOversoldMarkers({ drawn, markers, selected, disabled, onSelect }: {
  drawn: DrawingChart | null;
  markers: readonly Marker[];
  selected: string | null;
  disabled: boolean;
  onSelect: (week: string) => void;
}) {
  const [frame, setFrame] = useState<Frame | null>(null);
  useEffect(() => {
    if (!drawn?.alive.current || markers.length === 0) return;
    const { chart, series, alive } = drawn;
    const scale = chart.timeScale();
    let raf = 0;
    let previous: Position[] = [];
    const place = (width: number, height: number) => {
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        raf = 0;
        if (!alive.current) return;
        const positions = markers.flatMap(marker => {
          const x = scale.timeToCoordinate(marker.time);
          const y = series.priceToCoordinate(marker.low);
          return x != null && y != null && x >= 0 && x <= width && y >= 0 && y <= height
            ? [{ week: marker.week, x: Math.round(x), y: Math.round(Math.min(y + 12, height - 12)) }]
            : [];
        });
        if (equalPositions(previous, positions)) return;
        previous = positions;
        setFrame({ drawn, markers, positions });
      });
    };
    const renderer: IPrimitivePaneRenderer = {
      draw(target) {
        target.useMediaCoordinateSpace(({ mediaSize }) => place(mediaSize.width, mediaSize.height));
      },
    };
    const primitive: ISeriesPrimitive<Time> = { paneViews: () => [{ renderer: () => renderer, zOrder: () => "top" }] };
    series.attachPrimitive(primitive);
    // 다시 켤 때 차트의 데이터나 범위가 그대로면 새 그리기가 없어도 바로 복원한다.
    place(scale.width(), chart.panes()[0]?.getHeight() ?? 0);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (alive.current) series.detachPrimitive(primitive);
    };
  }, [drawn, markers]);

  if (!frame || frame.drawn !== drawn || frame.markers !== markers) return null;
  return frame.positions.map(({ week, x, y }) => (
    <button key={week} type="button" className="nq-weekly-marker"
      style={{ left: x, top: y }} aria-label={week + " 주봉 과매도 겹침"}
      title={week + " · 과매도 겹침 근거 보기"} aria-pressed={selected === week}
      disabled={disabled} onClick={() => onSelect(week)}>
      <span aria-hidden="true">◆</span>
    </button>
  ));
}
