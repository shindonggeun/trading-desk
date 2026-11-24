export type DrawingAnchor = { time: string | number; stamp: string; price: number };
export type DrawingKind = "horizontal" | "trend" | "channel" | "measure";
export type ChartDrawing = { id: string; kind: DrawingKind; anchors: DrawingAnchor[] };

const ANCHORS: Record<DrawingKind, number> = { horizontal: 1, trend: 2, channel: 3, measure: 2 };
const NAMES: Record<DrawingKind, string> = {
  horizontal: "수평선", trend: "추세선", channel: "평행 채널", measure: "가격 측정",
};
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIMESTAMP = /^(\d{4}-\d{2}-\d{2})(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:?\d{2}))?$/;
const ID = /^[A-Za-z0-9_.:-]{1,96}$/;
export const MAX_DRAWINGS = 100;
const MAX_STORED_LENGTH = 65536;

export function anchorCount(kind: DrawingKind): number {
  return ANCHORS[kind];
}

function isKind(value: unknown): value is DrawingKind {
  return typeof value === "string" && Object.hasOwn(ANCHORS, value);
}

function isDate(value: string): boolean {
  if (!DATE.test(value)) return false;
  const time = Date.parse(value);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

function isTime(value: unknown): value is DrawingAnchor["time"] {
  return typeof value === "number" ? Number.isSafeInteger(value) :
    typeof value === "string" && isDate(value);
}

function isPrice(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

function isAnchor(value: unknown): value is DrawingAnchor {
  if (typeof value !== "object" || value === null) return false;
  const { time, stamp, price } = value as Record<string, unknown>;
  if (typeof stamp !== "string") return false;
  const matched = TIMESTAMP.exec(stamp);
  return isTime(time) && isPrice(price) && matched !== null &&
    isDate(matched[1]) && Number.isFinite(Date.parse(stamp));
}

export function validateAnchors(kind: DrawingKind, anchors: readonly DrawingAnchor[]): boolean {
  if (!isKind(kind) || !Array.isArray(anchors) || anchors.length !== anchorCount(kind) ||
    ![...anchors].every(isAnchor)) return false;
  const [a, b, c] = anchors;
  if (anchors.some(anchor => typeof anchor.time !== typeof a.time)) return false;
  if ((kind === "trend" || kind === "channel") && a.time === b.time) return false;
  return kind !== "channel" || ![a, b].some(base => base.time === c.time && base.price === c.price);
}

export function measurePrice(a: number, b: number): { difference: number; percent: number } | null {
  if (!isPrice(a) || !isPrice(b)) return null;
  const difference = b - a;
  const percent = (difference / a) * 100;
  return Number.isFinite(percent) ? { difference, percent } : null;
}

function storageKey(ticker: string, interval: string): string {
  return `desk.chart-drawings.v1:${JSON.stringify([ticker.trim().toUpperCase(), interval])}`;
}

function isDrawing(value: unknown): value is ChartDrawing {
  if (typeof value !== "object" || value === null) return false;
  const { id, kind, anchors } = value as Record<string, unknown>;
  return typeof id === "string" && ID.test(id) && isKind(kind) &&
    Array.isArray(anchors) && validateAnchors(kind, anchors);
}

function collectDrawings(values: unknown[]): ChartDrawing[] {
  const ids = new Set<string>();
  const drawings: ChartDrawing[] = [];
  for (const value of values) {
    if (!isDrawing(value) || ids.has(value.id)) continue;
    ids.add(value.id);
    // 저장 자료를 공개 계약의 필드만 있는 값으로 읽는다.
    const { id, kind, anchors } = value;
    drawings.push({ id, kind, anchors: anchors.map(({ time, stamp, price }) => ({ time, stamp, price })) });
    if (drawings.length === MAX_DRAWINGS) break;
  }
  return drawings;
}

export function readDrawings(ticker: string, interval: string, storage?: Storage): ChartDrawing[] {
  try {
    const raw = (storage ?? localStorage).getItem(storageKey(ticker, interval));
    if (raw === null || raw.length > MAX_STORED_LENGTH) return [];
    const values: unknown = JSON.parse(raw);
    return Array.isArray(values) ? collectDrawings(values) : [];
  } catch {
    return [];
  }
}

export function writeDrawings(
  ticker: string, interval: string, drawings: ChartDrawing[], storage?: Storage,
): boolean {
  if (drawings.length > MAX_DRAWINGS) return false;
  try {
    const values = collectDrawings(drawings);
    if (values.length !== drawings.length) return false;
    const saved = storage ?? localStorage;
    const key = storageKey(ticker, interval);
    if (values.length) saved.setItem(key, JSON.stringify(values));
    else saved.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function formatDrawingAnchors(drawing: ChartDrawing, ticker: string, interval: string, currency: string): string {
  return [
    `${ticker} · ${interval} · ${NAMES[drawing.kind]}`,
    ...drawing.anchors.map(({ stamp, price }, index) =>
      `${String.fromCharCode(65 + index)} ${stamp} · ${price} ${currency}`),
  ].join("\n");
}
