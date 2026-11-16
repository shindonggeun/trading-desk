/**
 * #438 분석 결과 차트의 방향 전망(#437 run outlook) — 마지막 봉 오른쪽 1주 · 1개월 원뿔과 시나리오 경로.
 * 전망은 기간 끝의 68% · 95% 범위만 준다. 그 사이 칸은 변동성이 √시간으로 퍼지는 모양으로 메운다(#300 예상도와 같은 가정).
 */
import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { OutlookPeriodId, RunOutlook } from "../types";

export const OUTLOOK_PERIODS: { id: OutlookPeriodId; label: string }[] = [
  { id: "1w", label: "1주" },
  { id: "1m", label: "1개월" },
];

/** 원뿔 한 칸 — bar 는 마지막 봉에서 몇 봉 뒤(0 = 지금) */
export interface ConeStep { bar: number; lo68: number; hi68: number; lo95: number; hi95: number }

/** 지금 가격에서 기간 끝 범위까지 √(bar/N) 로 벌어지는 원뿔. 범위가 없으면 빈 목록. */
export function outlookCone(o: RunOutlook, period: OutlookPeriodId): ConeStep[] {
  const p = o.periods[period];
  const base = o.price;
  if (!p || base == null || !(base > 0) || !(p.bars > 0)) return [];
  const n = p.bars;
  const at = (end: number, f: number) => base + (end - base) * f;
  const out: ConeStep[] = [];
  for (let bar = 0; bar <= n; bar++) {
    const f = Math.sqrt(bar / n);
    out.push({ bar, lo68: at(p.band68[0], f), hi68: at(p.band68[1], f), lo95: at(p.band95[0], f), hi95: at(p.band95[1], f) });
  }
  return out;
}

/** 경로를 기간 안(bar ≤ n)으로 자른다 — 기간을 넘는 점은 n 칸에서 선형으로 끊는다. */
export function clipPath(points: { bar: number; price: number }[], n: number): { bar: number; price: number }[] {
  const out: { bar: number; price: number }[] = [];
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    if (p.bar <= n) {
      out.push(p);
      continue;
    }
    const prev = points[i - 1];
    if (prev && prev.bar < n) out.push({ bar: n, price: prev.price + ((p.price - prev.price) * (n - prev.bar)) / (p.bar - prev.bar) });
    break;
  }
  return out;
}

const DIRECTION: Record<RunOutlook["decision"]["direction"], string> = { up: "상승", down: "하락", sideways: "횡보" };
export const directionLabel = (o: RunOutlook) => DIRECTION[o.decision.direction] ?? o.decision.direction;

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];
type Poly = { times: Time[]; lo: number[]; hi: number[]; fill: string };

/** 원뿔 음영 — 캔들 시리즈에 붙여 봉 아래에 칠한다(lightweight-charts 에는 두 선 사이를 칠하는 시리즈가 없다). */
export class ConeShade implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType> | null = null;
  private chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  private request: (() => void) | null = null;
  private polys: Poly[] = [];
  private readonly view: IPrimitivePaneView;

  constructor() {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    this.view = { zOrder: () => "bottom", renderer: () => renderer };
  }

  attached(p: SeriesAttachedParameter<Time>) {
    this.series = p.series;
    this.chart = p.chart;
    this.request = p.requestUpdate;
  }

  detached() {
    this.series = null;
    this.chart = null;
    this.request = null;
  }

  set(polys: Poly[]) {
    this.polys = polys;
    this.request?.();
  }

  paneViews() {
    return [this.view];
  }

  private draw(target: Target) {
    const s = this.series;
    const ts = this.chart?.timeScale();
    if (!s || !ts) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      for (const poly of this.polys) {
        const top: [number, number][] = [];
        const bottom: [number, number][] = [];
        poly.times.forEach((t, i) => {
          const x = ts.timeToCoordinate(t);
          const y1 = s.priceToCoordinate(poly.hi[i]);
          const y2 = s.priceToCoordinate(poly.lo[i]);
          if (x == null || y1 == null || y2 == null) return;
          top.push([x * hr, y1 * vr]);
          bottom.push([x * hr, y2 * vr]);
        });
        if (top.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(top[0][0], top[0][1]);
        for (const [x, y] of top.slice(1)) ctx.lineTo(x, y);
        for (const [x, y] of bottom.reverse()) ctx.lineTo(x, y);
        ctx.closePath();
        ctx.fillStyle = poly.fill;
        ctx.fill();
      }
    });
  }
}
