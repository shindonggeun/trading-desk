/**
 * 가격 차트 위 가로 띠 · 가는 선 그리기(#98) — 지지 · 저항 구역, 타점의 손실 · 이익 영역, 피보나치.
 * lightweight-charts 에는 가격 구간을 칠하는 시리즈가 없어서, 캔들 시리즈에 붙는 작은 플러그인으로 그린다.
 * 띠는 축 라벨을 달지 않고 왼쪽 안쪽에 작은 글자 — 오른쪽 축은 진입 · 손절 · 목표만 쓴다.
 */
import { price } from "../format";
import {
  type IChartApi,
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
  /** 실선(POC) — 없으면 점선 */
  solid?: boolean;
  /** 글자를 오른쪽 끝에(매물대 막대 위) — 없으면 왼쪽 */
  right?: boolean;
}

/** 오른쪽 끝에서 왼쪽으로 뻗는 가로 막대(매물대, #108) — size 는 0 ~ 1(가장 큰 막대 = 1). */
export interface SideBar {
  top: number;
  bottom: number;
  size: number;
  fill: string;
}

/** 매물대 막대가 차지하는 최대 폭(차트 폭 대비) */
const SIDE_BAR_SHARE = 0.22;

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
  private sideBars: SideBar[] = [];
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

  set(bands: Band[], marks: Mark[], sideBars: SideBar[] = []) {
    this.bands = bands;
    this.marks = marks;
    this.sideBars = sideBars;
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
        if (b.from != null && from == null) continue;   // 그 봉이 이 차트에 없다 — 전체 폭으로 칠하지 않는다
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
      const maxBar = bitmapSize.width * SIDE_BAR_SHARE;
      for (const b of this.sideBars) {
        const y1 = s.priceToCoordinate(b.top);
        const y2 = s.priceToCoordinate(b.bottom);
        if (y1 == null || y2 == null) continue;
        const top = Math.round(Math.min(y1, y2) * vr);
        // 이웃 막대와 1px 틈을 두어 한 덩어리로 뭉개지지 않게
        const height = Math.max(Math.round(vr), Math.round(Math.abs(y2 - y1) * vr) - Math.round(vr));
        const w = Math.max(Math.round(hr), Math.round(maxBar * Math.min(1, b.size)));
        ctx.fillStyle = b.fill;
        ctx.fillRect(bitmapSize.width - w, top, w, height);
      }
      for (const m of this.marks) {
        const y = s.priceToCoordinate(m.price);
        if (y == null) continue;
        const py = Math.round(y * vr);
        const thick = Math.max(1, Math.round(vr * (m.solid ? 1.5 : 1)));
        ctx.fillStyle = m.color;
        if (m.solid) ctx.fillRect(0, py, bitmapSize.width, thick);
        else for (let x = 0; x < bitmapSize.width; x += 8 * hr) ctx.fillRect(x, py, 3 * hr, thick);
        if (m.label) {
          const ly = Math.max(0, py - 13 * vr);   // 맨 위 글자가 잘리지 않게
          if (m.right) {
            ctx.textAlign = "right";
            ctx.fillText(m.label, bitmapSize.width - 6 * hr, ly);
            ctx.textAlign = "left";
          } else {
            ctx.fillText(m.label, 6 * hr, ly);
          }
        }
      }
      ctx.restore();
    });
  }
}

export interface VolumeProfileLike {
  bin_width: number;
  bins: { price: number; volume: number; share: number }[];
  poc: number;
  value_area: { low: number; high: number };
  nodes: { price: number; kind: "thick" | "thin"; share: number }[];
}

/**
 * 매물대(#108): 구간별 거래량 막대(가치 영역 안은 조금 진하게, 두꺼운 매물대는 강조색), 가치 영역 옅은 띠,
 * POC 실선 + 오른쪽 글자. 색은 지지 · 저항 띠와 섞이지 않게 회색 · 경고색 계열.
 */
export function volumeProfileDrawing(vp: VolumeProfileLike, colors: { bar: string; thick: string; poc: string; label: string },
  fmt: (v: number) => string): { bands: Band[]; marks: Mark[]; bars: SideBar[] } {
  const max = Math.max(...vp.bins.map((b) => b.share), 0) || 1;
  const half = vp.bin_width / 2;
  const thick = vp.nodes.filter((n) => n.kind === "thick").map((n) => n.price);
  const isThick = (p: number) => thick.some((t) => Math.abs(t - p) <= half);
  const inVa = (p: number) => p >= vp.value_area.low && p <= vp.value_area.high;
  return {
    bands: [{ top: vp.value_area.high, bottom: vp.value_area.low, fill: withAlpha(colors.bar, 0.05) }],
    marks: [{ price: vp.poc, color: withAlpha(colors.poc, 0.9), label: `POC ${fmt(vp.poc)}`, solid: true, right: true }],
    bars: vp.bins.map((b) => ({
      top: b.price + half,
      bottom: b.price - half,
      size: b.share / max,
      fill: isThick(b.price) ? withAlpha(colors.thick, 0.45) : withAlpha(colors.bar, inVa(b.price) ? 0.3 : 0.16),
    })),
  };
}

interface PlaybookSideLike {
  setup: string;
  zone: { low: number; high: number } | null;
  entries: { step: number; price: number }[];
  /** 분석의 트리거 또는 #149 실시간 트리거(신호 전이면 price null) */
  trigger?: { price: number | null } | null;
  stop: number | null;
  targets: { role: string; price: number }[];
}

/**
 * 플레이북(#117): 롱 구역은 옅은 빨강, 숏 구역은 옅은 파랑 띠, 사다리 · 트리거 · 손절 · T1/T2 는 오른쪽 글자가 붙은 가는 선
 * ("롱 1차", "숏 손절") — 왼쪽은 지지 · 저항 글자 자리라 오른쪽에.
 */
export function playbookDrawing(pb: { long: PlaybookSideLike; short: PlaybookSideLike }, colors: { long: string; short: string },
  fmt: (v: number) => string): { bands: Band[]; marks: Mark[] } {
  const bands: Band[] = [];
  const marks: Mark[] = [];
  for (const [dir, side] of [["long", pb.long], ["short", pb.short]] as const) {
    if (side.setup === "none") continue;
    const c = colors[dir];
    const name = dir === "long" ? "롱" : "숏";
    if (side.zone) {
      bands.push({ top: side.zone.high, bottom: side.zone.low, fill: withAlpha(c, 0.12), label: `${name} 구역 ${fmt(side.zone.low)} ~ ${fmt(side.zone.high)}`, labelColor: withAlpha(c, 0.95) });
    }
    // 같은 가격(숏 1차 = 트리거, 롱 1차 = 구역 위쪽 끝 등)은 글자가 겹치니 한 선으로 합친다
    const own: Mark[] = [];
    const add = (p: number, alpha: number, what: string) => {
      const same = own.find((m) => Math.abs(m.price - p) < 1e-9);
      if (same) same.label = `${same.label} · ${what}`;
      else own.push({ price: p, color: withAlpha(c, alpha), label: `${name} ${what}`, right: true });
    };
    for (const e of side.entries) add(e.price, 0.75, `${e.step}차`);
    if (side.trigger?.price != null) add(side.trigger.price, 0.9, "트리거");
    if (side.stop != null) add(side.stop, 0.9, "손절");
    for (const t of side.targets) add(t.price, 0.75, t.role.toUpperCase());
    marks.push(...own);
  }
  return { bands, marks };
}

/** 지지 · 저항 구역의 반폭 — 서버가 구역을 묶는 폭(반 ATR)의 절반에 가깝게, ATR 이 없으면 가격의 0.15%(근사). */
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

/** 구역 하단 · 상단 — 서버가 준 구역(#321)이 있으면 그것, 없으면(예전 run) 가격 ± 반폭. */
export function zoneEdges(level: SrLevel, atr: number | null | undefined): [number, number] {
  if (level.zone && level.zone[0] <= level.zone[1]) return level.zone;
  const hw = zoneHalfWidth(level, atr);
  return [level.price - hw, level.price + hw];
}

/** 반응 기록이 이만큼 안 되면 근거 부족 — 띠를 흐리게(desk/sr_zones.MIN_TOUCHES 와 같게) */
const MIN_TOUCHES = 3;
const FLIP_KO = { resistance_to_support: "저항→지지", support_to_resistance: "지지→저항" } as const;

/** 띠 라벨 — 반응 기록이 있으면 "지지 150.2~152.8 · 반등 6/8 · 저항→지지", 없으면(예전 run) 주기 · 강도. */
export function srLabel(lv: SrLevel, fmt: (v: number) => string): string {
  if (!lv.zone) return `${KIND_KO[lv.kind]} · ${TF_KO[lv.timeframe] ?? lv.timeframe} · 강도 ${lv.strength}`;
  const parts = [`${KIND_KO[lv.kind]} ${fmt(lv.zone[0])}~${fmt(lv.zone[1])}`];
  if (lv.touches) parts.push(`${lv.kind === "support" ? "반등" : "저지"} ${lv.held ?? 0}/${lv.touches}`);
  if (lv.flipped) parts.push(FLIP_KO[lv.flipped]);
  if (lv.buy_zone?.ok) parts.push("저가매수 구간");
  return parts.join(" · ");
}

/** 지지 · 저항 구역 띠 — 강도가 셀수록 진하게, 주봉 근거는 가운데 선, 반응 기록이 적으면 흐리게(#321). */
export function srBands(levels: SrLevel[], atr: number | null | undefined, colors: { support: string; resistance: string },
                        fmt: (v: number) => string = String): Band[] {
  return levels.map((lv) => {
    const [bottom, top] = zoneEdges(lv, atr);
    const base = colors[lv.kind];
    const thin = lv.zone != null && (lv.touches ?? 0) < MIN_TOUCHES && !lv.flipped;
    const alpha = (0.07 + Math.min(lv.strength, 8) * 0.018) * (thin ? 0.55 : 1);
    return {
      top,
      bottom,
      fill: withAlpha(base, alpha),
      edge: lv.timeframe === "1w" ? withAlpha(base, thin ? 0.4 : 0.7) : undefined,
      label: srLabel(lv, fmt),
      labelColor: withAlpha(base, thin ? 0.6 : 0.95),
    };
  });
}

/** 저가매수 구간(#321 buy_zone) — 근거가 된 것만 초록 띠(이름은 지지 라벨에, 손절 · 목표는 설명에). */
export function buyZoneDrawing(levels: SrLevel[], color: string, fmt: (v: number) => string): { bands: Band[]; marks: Mark[] } {
  const bands: Band[] = [];
  const marks: Mark[] = [];
  for (const lv of levels) {
    const bz = lv.buy_zone;
    if (!bz?.ok) continue;
    // 이름은 그 지지 띠 라벨에 붙인다(같은 높이에 라벨 둘이면 휴대폰에서 겹친다, #98)
    bands.push({ top: bz.hi, bottom: bz.lo, fill: withAlpha(color, 0.13), edge: withAlpha(color, 0.55) });
    // 손절은 마우스를 올린 설명에만 — 지지가 몰린 곳에 선 · 글자를 더하면 휴대폰에서 읽을 수 없다
    void fmt;
  }
  return { bands, marks };
}

/** 계획 손절이 지지 구역 안쪽이면 그 구역과 제안 손절(구역 하단 밖) — 롱 손절만(진입 아래). */
export function stopInsideZone(stop: number | null | undefined, entry: number | null | undefined, levels: SrLevel[]):
  { level: SrLevel; suggest: number } | null {
  if (stop == null || entry == null || stop >= entry) return null;
  for (const lv of levels) {
    if (lv.kind !== "support" || !lv.zone) continue;
    const [lo, hi] = lv.zone;
    if (stop > lo && stop <= hi) return { level: lv, suggest: lv.buy_zone?.stop ?? lo };
  }
  return null;
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

/**
 * 오른쪽 축의 가까운 라벨은 하나로 — 위 라벨에 이름(값이 다르면 "진입 8.03" 처럼 제 가격까지)을 합치고 아래 것은 축 라벨을 끈다. 좌표는 줌 · 이동 · 새 봉 ·
 * 늦게 온 데이터로 바뀌니, 원래 제목 · 표시를 기억해 두고 보는 범위 · 크기가 바뀔 때와 1초마다 다시 계산한다
 * (바뀐 것만 적용 — 같은 값이면 다시 그리지 않는다). 돌려준 함수로 멈춘다(차트가 살아 있을 때만 구독을 푼다).
 * #585 `alive` 가 false 면(차트를 지웠는데 이 효과의 정리가 아직 안 돌았을 때 — 봉이 바뀌어 차트를 새로 만들 때 한 번의 렌더 동안)
 * 예약된 다시 계산은 아무것도 건드리지 않는다. 지운 시리즈 · 선을 읽으면 "Object is disposed" 가 난다.
 */
export function keepAxisLabelsApart(chart: IChartApi, series: ISeriesApi<SeriesType>, lines: IPriceLine[], minGapPx = 16,
  alive: () => boolean = () => true) {
  const original = lines.map((line) => ({ line, title: line.options().title, visible: line.options().axisLabelVisible }));
  let frame = 0;
  const run = () => {
    frame = 0;
    if (!alive()) return;
    const want = new Map(original.map((o) => [o.line, { title: o.title, visible: o.visible }]));
    const placed = original
      .filter((o) => o.visible)
      .map((o) => ({ o, y: (series.priceToCoordinate(o.line.options().price) ?? NaN) as number }))
      .filter((p) => Number.isFinite(p.y))
      .sort((a, b) => a.y - b.y);
    let keep: (typeof placed)[number] | null = null;
    for (const p of placed) {
      if (keep && p.y - keep.y < minGapPx) {
        const k = want.get(keep.o.line)!;
        // #436 축 라벨의 숫자는 위 선의 가격 — 값이 다른 선은 제 가격을 붙여 한 값처럼 읽히지 않게
        const own = p.o.line.options().price;
        const tag = p.o.title && price(own) !== price(keep.o.line.options().price) ? `${p.o.title} ${price(own)}` : p.o.title;
        k.title = [k.title, tag].filter(Boolean).join(" · ");
        want.set(p.o.line, { title: "", visible: false });
      } else {
        keep = p;
      }
    }
    for (const [line, w] of want) {
      const cur = line.options();
      if (cur.title !== w.title || cur.axisLabelVisible !== w.visible) line.applyOptions({ title: w.title, axisLabelVisible: w.visible });
    }
  };
  const schedule = () => {
    if (!frame && alive()) frame = requestAnimationFrame(run);
  };
  schedule();
  const scale = chart.timeScale();
  scale.subscribeVisibleLogicalRangeChange(schedule);
  scale.subscribeSizeChange(schedule);
  const timer = window.setInterval(schedule, 1000);   // 새 봉 · 자동 축척은 범위 이벤트 없이도 좌표를 바꾼다
  return (alive: boolean) => {
    cancelAnimationFrame(frame);
    window.clearInterval(timer);
    if (alive) {
      scale.unsubscribeVisibleLogicalRangeChange(schedule);
      scale.unsubscribeSizeChange(schedule);
    }
  };
}
