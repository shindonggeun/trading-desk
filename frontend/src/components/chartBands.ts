/**
 * 가격 차트 위 가로 띠 · 가는 선 그리기(#98) — 지지 · 저항 구역, 타점의 손실 · 이익 영역, 피보나치.
 * lightweight-charts 에는 가격 구간을 칠하는 시리즈가 없어서, 캔들 시리즈에 붙는 작은 플러그인으로 그린다.
 * 띠는 축 라벨을 달지 않고 왼쪽 안쪽에 작은 글자 — 오른쪽 축은 진입 · 손절 · 목표만 쓴다.
 */
import {
  type IPriceLine,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type ISeriesApi,
  type ISeriesPrimitive,
  type SeriesAttachedParameter,
  type SeriesType,
  type Time,
} from "lightweight-charts";
import type { Plan, SrLevel } from "../types";

export interface Band {
  top: number;
  bottom: number;
  fill: string;
  /** 띠 가운데 가는 선 */
  edge?: string;
  label?: string;
  labelColor?: string;
  /** 이 시각의 봉부터 오른쪽만(타점 영역은 분석한 날부터) */
  from?: Time | null;
}

export interface Mark {
  price: number;
  color: string;
  label?: string;
}

type Target = Parameters<IPrimitivePaneRenderer["draw"]>[0];

/** "#rrggbb" 에 투명도를 붙인다(토큰 색은 hex — 아니면 그대로). */
export function withAlpha(color: string, alpha: number): string {
  return /^#[0-9a-f]{6}$/i.test(color) ? color + Math.round(alpha * 255).toString(16).padStart(2, "0") : color;
}

export class PriceBands implements ISeriesPrimitive<Time> {
  private series: ISeriesApi<SeriesType> | null = null;
  private chart: SeriesAttachedParameter<Time>["chart"] | null = null;
  private request: (() => void) | null = null;
  private bands: Band[] = [];
  private marks: Mark[] = [];
  private readonly view: IPrimitivePaneView;

  constructor(private readonly font: string) {
    const renderer: IPrimitivePaneRenderer = { draw: (target) => this.draw(target) };
    // 캔들 아래(bottom)에 칠해 봉이 가려지지 않게
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

  set(bands: Band[], marks: Mark[]) {
    this.bands = bands;
    this.marks = marks;
    this.request?.();
  }

  paneViews() {
    return [this.view];
  }

  private draw(target: Target) {
    const s = this.series;
    if (!s) return;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      ctx.save();
      ctx.font = this.font.replace(/(\d+(?:\.\d+)?)px/, (_, n) => `${Number(n) * vr}px`);
      ctx.textBaseline = "top";
      for (const b of this.bands) {
        const y1 = s.priceToCoordinate(b.top);
        const y2 = s.priceToCoordinate(b.bottom);
        if (y1 == null || y2 == null) continue;
        const from = b.from != null ? this.chart?.timeScale().timeToCoordinate(b.from) ?? null : null;
        const x0 = Math.max(0, (from ?? 0) * hr);
        if (x0 >= bitmapSize.width) continue;
        const top = Math.round(Math.min(y1, y2) * vr);
        const height = Math.max(Math.round(vr), Math.round(Math.abs(y2 - y1) * vr));
        ctx.fillStyle = b.fill;
        ctx.fillRect(x0, top, bitmapSize.width - x0, height);
        if (b.edge) {
          ctx.fillStyle = b.edge;
          ctx.fillRect(x0, top + Math.round(height / 2), bitmapSize.width - x0, Math.max(1, Math.round(vr)));
        }
        if (b.label) {
          ctx.fillStyle = b.labelColor ?? b.edge ?? b.fill;
          ctx.fillText(b.label, x0 + 6 * hr, top + 2 * vr);
        }
      }
      for (const m of this.marks) {
        const y = s.priceToCoordinate(m.price);
        if (y == null) continue;
        const py = Math.round(y * vr);
        ctx.fillStyle = m.color;
        for (let x = 0; x < bitmapSize.width; x += 8 * hr) ctx.fillRect(x, py, 3 * hr, Math.max(1, Math.round(vr)));
        if (m.label) ctx.fillText(m.label, 6 * hr, py - 13 * vr);
      }
      ctx.restore();
    });
  }
}

/** 지지 · 저항 구역의 반폭 — 서버가 구역을 묶은 폭(반 ATR)의 절반, ATR 이 없으면 가격의 0.15%. */
export function zoneHalfWidth(level: SrLevel, atr: number | null | undefined): number {
  return Math.max((atr ?? 0) * 0.25, level.price * 0.0015);
}

const KIND_KO = { support: "지지", resistance: "저항" } as const;
const TF_KO: Record<string, string> = { "1d": "일봉", "1w": "주봉" };

/** 가격에서 가까운 지지 · 저항 n 개씩(all 이면 전부). */
export function nearestLevels(levels: SrLevel[], reference: number, n: number, all: boolean): SrLevel[] {
  if (all) return levels;
  const side = (kind: SrLevel["kind"]) =>
    levels.filter((l) => l.kind === kind).sort((a, b) => Math.abs(a.price - reference) - Math.abs(b.price - reference)).slice(0, n);
  return [...side("resistance"), ...side("support")];
}

/** 지지 · 저항 구역 띠 — 강도가 셀수록 진하게, 주봉 근거는 가운데 선. */
export function srBands(levels: SrLevel[], atr: number | null | undefined, colors: { support: string; resistance: string }): Band[] {
  return levels.map((lv) => {
    const hw = zoneHalfWidth(lv, atr);
    const base = colors[lv.kind];
    const alpha = 0.07 + Math.min(lv.strength, 8) * 0.018;
    return {
      top: lv.price + hw,
      bottom: lv.price - hw,
      fill: withAlpha(base, alpha),
      edge: lv.timeframe === "1w" ? withAlpha(base, 0.7) : undefined,
      label: `${KIND_KO[lv.kind]} · ${TF_KO[lv.timeframe] ?? lv.timeframe} · 강도 ${lv.strength}`,
      labelColor: withAlpha(base, 0.95),
    };
  });
}

/** 타점의 손실(손절 ~ 진입) · 이익(진입 ~ 목표) 영역 — 트레이딩뷰 포지션 도구처럼, 분석한 날부터. */
export function planBands(plan: Plan | null, from: Time | null, colors: { loss: string; profit: string }): Band[] {
  const e = plan?.entry_price;
  const s = plan?.stop_loss;
  const t = plan?.price_target;
  if (e == null || s == null || t == null || (t - e) * (e - s) <= 0) return []; // 진입을 사이에 두지 않으면 거래가 아니다
  return [
    { top: Math.max(s, e), bottom: Math.min(s, e), fill: withAlpha(colors.loss, 0.1), from },
    { top: Math.max(t, e), bottom: Math.min(t, e), fill: withAlpha(colors.profit, 0.1), from },
  ];
}

/** 오른쪽 축의 가까운 라벨은 하나로 — 위 라벨에 이름을 합치고 아래 것은 축 라벨을 끈다. */
export function mergeAxisLabels(series: ISeriesApi<SeriesType>, lines: IPriceLine[], minGapPx = 16) {
  const placed = lines
    .map((line) => ({ line, y: (series.priceToCoordinate(line.options().price) ?? NaN) as number }))
    .filter((p) => Number.isFinite(p.y) && p.line.options().axisLabelVisible)
    .sort((a, b) => a.y - b.y);
  let keep: { line: IPriceLine; y: number } | null = null;
  for (const p of placed) {
    if (keep && p.y - keep.y < minGapPx) {
      const title = [keep.line.options().title, p.line.options().title].filter(Boolean).join(" · ");
      keep.line.applyOptions({ title });
      p.line.applyOptions({ axisLabelVisible: false, title: "" });
    } else {
      keep = p;
    }
  }
}
