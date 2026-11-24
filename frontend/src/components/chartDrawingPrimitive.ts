import {
  MismatchDirection,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type Time,
} from "lightweight-charts";
import type { ChartDrawing, DrawingAnchor, DrawingKind } from "../chartDrawingModel";

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];
type Point = { x: number; y: number };
type Draft = { kind: DrawingKind; anchors: DrawingAnchor[] };
type Segment = [Point, Point];

const LINE_DASH: Record<DrawingKind, number[]> = {
  horizontal: [9, 5], trend: [9, 5], channel: [9, 5], measure: [2, 4],
};

/** 평행선도 가격축이 로그이면 로그 화면 좌표에서 같은 기울기를 유지한다. */
function extendLine(a: Point, b: Point, left: number, right: number, offset = 0): Segment | null {
  if (left > right || a.x === b.x) return null;
  const slope = (b.y - a.y) / (b.x - a.x);
  return [
    { x: left, y: a.y + (left - a.x) * slope + offset },
    { x: right, y: a.y + (right - a.x) * slope + offset },
  ];
}

function channelOffset(a: Point, b: Point, c: Point): number {
  const baselineY = a.y + (c.x - a.x) * (b.y - a.y) / (b.x - a.x);
  return c.y - baselineY;
}

function stroke(ctx: CanvasRenderingContext2D, segment: Segment | null) {
  if (!segment) return;
  const [a, b] = segment;
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();
}

function dot(ctx: CanvasRenderingContext2D, point: Point, radius: number) {
  ctx.beginPath();
  ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
}

export class ChartDrawingPrimitive implements ISeriesPrimitive<Time> {
  private attachment: SeriesAttachedParameter<Time> | null = null;
  private drawings: ChartDrawing[] = [];
  private selectedId: string | null = null;
  private draft: Draft | undefined;
  private guide: DrawingAnchor | undefined;
  private readonly views: readonly IPrimitivePaneView[];

  constructor() {
    const renderer: IPrimitivePaneRenderer = { draw: target => this.draw(target) };
    this.views = [{ zOrder: () => "top", renderer: () => renderer }];
  }

  attached(params: SeriesAttachedParameter<Time>) {
    this.attachment = params;
  }

  detached() {
    this.attachment = null;
  }

  paneViews() {
    return this.views;
  }

  setDrawings(drawings: ChartDrawing[], selectedId: string | null, draft?: Draft, guide?: DrawingAnchor) {
    this.drawings = drawings;
    this.selectedId = selectedId;
    this.draft = draft;
    this.guide = guide;
    this.attachment?.requestUpdate();
  }

  /** 채널의 반대편 앵커가 기준선과 같은 화면상의 위치인지 검사한다. */
  canDrawChannel(anchors: readonly DrawingAnchor[]): boolean {
    if (anchors.length !== 3) return false;
    const [a, b, c] = anchors.map(anchor => this.point(anchor));
    return !!(a && b && c && a.x !== b.x && Math.abs(channelOffset(a, b, c)) > 1e-6);
  }

  private point(anchor: DrawingAnchor): Point | null {
    if (!this.attachment) return null;
    const { chart, series } = this.attachment;
    const scale = chart.timeScale();
    const time = anchor.time as Time;
    const index = scale.timeToIndex(time);
    if (index === null || !series.dataByIndex(index, MismatchDirection.None)) return null;
    const x = scale.timeToCoordinate(time);
    const y = series.priceToCoordinate(anchor.price);
    return x !== null && y !== null && Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
  }

  private drawDrawing(
    ctx: CanvasRenderingContext2D, width: number, { kind, anchors }: Draft, showAnchors: boolean,
  ) {
    const points = anchors.map(anchor => this.point(anchor));
    const [a, b, c] = points;
    ctx.setLineDash(LINE_DASH[kind]);
    if (kind === "horizontal" && anchors[0]) {
      // 수평선의 가격은 기준 봉이 현재 불러온 범위 밖이어도 유효하다.
      const y = this.attachment?.series.priceToCoordinate(anchors[0].price);
      if (y != null && Number.isFinite(y)) stroke(ctx, [{ x: 0, y }, { x: width, y }]);
    } else if (kind === "measure" && a && b) {
      stroke(ctx, [a, b]);
    } else if ((kind === "trend" || kind === "channel") && a && b && (!anchors[2] || c)) {
      const left = Math.max(0, Math.min(a.x, b.x, c?.x ?? Infinity));
      stroke(ctx, extendLine(a, b, left, width));
      if (c && a.x !== b.x) {
        const offset = channelOffset(a, b, c);
        if (Math.abs(offset) > 1e-6) stroke(ctx, extendLine(a, b, left, width, offset));
      }
    }
    if (showAnchors) {
      ctx.setLineDash([]);
      for (const point of points) {
        if (point) dot(ctx, point, 3.5);
      }
    }
  }

  private draw(target: Target) {
    if (!this.attachment || (!this.drawings.length && !this.draft && !this.guide)) return;
    const styles = getComputedStyle(this.attachment.chart.chartElement());
    const line = styles.getPropertyValue("--text").trim() || "#637081";
    const background = styles.getPropertyValue("--surface").trim() || "#ffffff";
    const hint = styles.getPropertyValue("--warn").trim() || "#a36918";
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, mediaSize.width, mediaSize.height);
      ctx.clip();
      ctx.strokeStyle = line;
      ctx.fillStyle = background;
      for (const drawing of this.drawings) {
        const selected = drawing.id === this.selectedId;
        ctx.globalAlpha = selected ? 1 : 0.76;
        ctx.lineWidth = selected ? 2.25 : 1.5;
        this.drawDrawing(ctx, mediaSize.width, drawing, selected);
      }
      ctx.globalAlpha = 1;
      ctx.lineWidth = 2;
      if (this.draft) this.drawDrawing(ctx, mediaSize.width, this.draft, true);
      const guide = this.guide && this.point(this.guide);
      if (guide) {
        ctx.strokeStyle = hint;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.arc(guide.x, guide.y, 10, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = hint;
        ctx.setLineDash([]);
        dot(ctx, guide, 2.5);
      }
      ctx.restore();
    });
  }
}
