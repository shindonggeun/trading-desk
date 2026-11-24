import { useId, useRef } from "react";
import { KR_FLOW_METRICS, krFlowPercent, type krFlowScatter, type KrFlowMetric, type KrFlowSeriesPoint } from "../krFlowBoard";

const LEFT = 47, RIGHT = 421, TOP = 31, BOTTOM = 216;
const Y_TICKS = [100, 50, 0, -50, -100];

export function KrFlowScatterPlot({
  scatter, selectedTicker, select,
}: {
  scatter: ReturnType<typeof krFlowScatter>;
  selectedTicker: string | null;
  select: (ticker: string) => void;
}) {
  const id = useId();
  const buttons = useRef(new Map<string, SVGGElement>());
  const { points, maxX, maxTurnover, median } = scatter;
  if (!points.length) return <p className="kr-flow-empty">참여율과 매수·매도 우위를 함께 확인할 수 있는 종목이 없습니다.</p>;
  const x = (value: number) => LEFT + (value / maxX) * (RIGHT - LEFT);
  const y = (value: number) => TOP + ((100 - value) / 200) * (BOTTOM - TOP);
  const xTicks = [0, maxX / 4, maxX / 2, maxX * .75, maxX];
  const selection = points.find(({ item }) => item.ticker === selectedTicker);
  const focusableTicker = selection?.item.ticker ?? points[0].item.ticker;
  const ordered = [...points].sort((a, b) => b.turnover - a.turnover);
  const radius = (turnover: number) => Math.sqrt(22 + (maxTurnover > 0 ? turnover / maxTurnover : 0) * 144);

  return (
    <figure className="kr-flow-scatter">
      <svg viewBox="0 0 440 261" role="group" aria-labelledby={id}>
        <title id={id}>외국인 참여율과 매수·매도 우위 산점도. 방향키로 순위에 따라 점을 선택합니다.</title>
        <g aria-hidden="true">
          <rect className="kr-flow-plot-bg" x={LEFT} y={TOP} width={RIGHT - LEFT} height={BOTTOM - TOP} />
          {Y_TICKS.map((tick) => (
            <g key={tick}>
              <line className={tick === 0 ? "kr-flow-zero" : "kr-flow-gridline"} x1={LEFT} x2={RIGHT} y1={y(tick)} y2={y(tick)} />
              <text x={LEFT - 11} y={y(tick)} textAnchor="end" dominantBaseline="middle">{tick}</text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <g key={tick}>
              <line className="kr-flow-gridline" x1={x(tick)} x2={x(tick)} y1={TOP} y2={BOTTOM} />
              <text x={x(tick)} y={BOTTOM + 17} textAnchor="middle">{tick}</text>
            </g>
          ))}
          {median !== null && <line className="kr-flow-median" x1={x(median)} x2={x(median)} y1={TOP} y2={BOTTOM} />}
          <text className="kr-flow-axis-label" x={LEFT} y={16}>매수·매도 우위 (%)</text>
          <text className="kr-flow-axis-label" x={RIGHT} y={BOTTOM + 38} textAnchor="end">외국인 참여율 (%)</text>
        </g>
        {ordered.map(({ item, participation, imbalance, turnover }) => {
          const isSelected = selectedTicker === item.ticker;
          const size = radius(turnover);
          return (
            <g key={item.ticker} role="button" tabIndex={item.ticker === focusableTicker ? 0 : -1}
              ref={(element) => {
                if (element) buttons.current.set(item.ticker, element);
                else buttons.current.delete(item.ticker);
              }}
              aria-label={`${item.name ?? item.ticker} (${item.ticker}) 산점도 선택`} aria-pressed={isSelected}
              className="kr-flow-dot" data-tone={imbalance > 0 ? "up" : imbalance < 0 ? "down" : "neutral"}
              transform={`translate(${x(participation)},${y(imbalance)})`}
              onClick={() => select(item.ticker)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  select(item.ticker);
                } else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
                  event.preventDefault();
                  const here = points.findIndex((point) => point.item.ticker === item.ticker);
                  const direction = event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1;
                  const index = event.key === "Home" ? 0 : event.key === "End" ? points.length - 1
                    : Math.max(0, Math.min(points.length - 1, here + direction));
                  const target = points[index].item.ticker;
                  select(target);
                  buttons.current.get(target)?.focus();
                }
              }}>
              <circle className="kr-flow-dot-hit" r={Math.max(size, 11)} />
              <circle className="kr-flow-dot-value" r={size} />
            </g>
          );
        })}
        {selection && <circle className="kr-flow-selected-ring" aria-hidden="true" pointerEvents="none"
          cx={x(selection.participation)} cy={y(selection.imbalance)} r={radius(selection.turnover) + 2} />}
      </svg>
      <figcaption className="kr-flow-plot-caption">
        <span><i className="kr-flow-key-line" />우위 0%</span>
        <span><i className="kr-flow-key-line is-median" />표시 표본 참여율 중앙값 {krFlowPercent(median)}</span>
        <span><i className="kr-flow-key-dot" />점 면적은 거래대금</span>
      </figcaption>
    </figure>
  );
}

export function KrFlowHistoryPlot({ segments, sessionCount, metric, ticker }: {
  segments: KrFlowSeriesPoint[][];
  sessionCount: number;
  metric: KrFlowMetric;
  ticker: string;
}) {
  const points = segments.flat();
  if (!points.length) return <span className="kr-flow-history-empty">표시할 이력 없음</span>;
  const values = points.map((point) => point.value);
  if (metric === "imbalance") values.push(0);
  const low = Math.min(...values), high = Math.max(...values);
  const x = (index: number) => 3 + (index / Math.max(1, sessionCount - 1)) * 126;
  const y = (value: number) => high === low ? 20 : 4 + ((high - value) / (high - low)) * 32;
  const last = points[points.length - 1];
  return (
    <svg className="kr-flow-history-plot" viewBox="0 0 132 40" role="img"
      aria-label={`${ticker} ${KR_FLOW_METRICS[metric]} 이력, 관측 ${points.length}일, 최근 관측 ${last.sample.date} ${krFlowPercent(last.value, metric === "imbalance")}`}>
      {metric === "imbalance" && <line className="kr-flow-gridline" x1={3} x2={129} y1={y(0)} y2={y(0)} />}
      {segments.map((segment) => segment.length > 1
        ? <polyline key={segment[0].index} points={segment.map(({ index, value }) => `${x(index)},${y(value)}`).join(" ")} />
        : null)}
      {points.map(({ index, value }) => <circle key={index} cx={x(index)} cy={y(value)} r={index === last.index ? 2.5 : 1.45} />)}
    </svg>
  );
}
