import type { Layout, LayoutItem } from "react-grid-layout";

/**
 * #487 대시보드 배치 편집: 화면 폭 구간별(≥1280 · 1000~1279) 12칸 격자 배치를 이 브라우저에 기억한다.
 * 저장한 배치가 없으면 대시보드는 예전(#464) 3열 · 2열 흐름 그대로 그린다 — "기본 배치로" 는 저장값을 지운다.
 */
export type Band = "wide" | "mid";
export const GRID_COLS = 12;
export const ROW_PX = 8; // 격자 한 줄 높이(px) — 높이를 촘촘히 조절하도록 작게
export const GAP_PX = 14;
export const HEAD_ROWS = 5; // 접힌 구역(머리글만)의 높이
export const MIN_W = 3; // 최소 폭 = 12칸의 1/4

export interface BandLayout { items: Layout; hidden: string[] }
type Store = Partial<Record<Band, BandLayout>>;
const KEY = "desk.dashboard.layout.v1";

function read(): Store {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return raw && typeof raw === "object" ? (raw as Store) : {};
  } catch {
    return {};
  }
}
function write(s: Store) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* 기억만 못 한다 — 이번 화면에서는 그대로 */ }
}

const isItem = (v: unknown): v is LayoutItem => {
  const o = v as LayoutItem;
  return !!o && typeof o.i === "string" && [o.x, o.y, o.w, o.h].every((n) => Number.isFinite(n));
};

/** 저장한 배치(손상값은 버림). 없는 구역은 빼고, 새로 생긴 구역은 맨 아래에 4칸 폭으로 붙인다. */
export function loadBand(band: Band, known: string[]): BandLayout | null {
  const b = read()[band];
  if (!b || !Array.isArray(b.items)) return null;
  const items = b.items.filter(isItem).filter((it) => known.includes(it.i))
    .map((it) => ({ i: it.i, x: clamp(it.x, 0, GRID_COLS - MIN_W), y: Math.max(0, it.y), w: clamp(it.w, MIN_W, GRID_COLS), h: Math.max(HEAD_ROWS, it.h) }));
  const hidden = Array.isArray(b.hidden) ? b.hidden.filter((h) => known.includes(h)) : [];
  const bottom = items.reduce((m, it) => Math.max(m, it.y + it.h), 0);
  const missing = known.filter((k) => !items.some((it) => it.i === k) && !hidden.includes(k));
  missing.forEach((k, n) => items.push({ i: k, x: (n % 3) * 4, y: bottom + Math.floor(n / 3) * 30, w: 4, h: 30 }));
  return items.length ? { items, hidden } : null;
}

export function saveBand(band: Band, layout: BandLayout) {
  const s = read();
  s[band] = { items: layout.items.map(({ i, x, y, w, h }) => ({ i, x, y, w, h })), hidden: layout.hidden };
  write(s);
}

export function resetBand(band: Band) {
  const s = read();
  delete s[band];
  write(s);
}

function clamp(n: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, Math.round(n)));
}

/** 지금 화면(예전 흐름 배치)의 구역 위치 · 크기를 재서 격자 배치로 — "처음엔 지금 배치로" */
export function measureLayout(container: HTMLElement, ids: string[]): Layout {
  const box = container.getBoundingClientRect();
  const colPx = (box.width + GAP_PX) / GRID_COLS;
  const out: LayoutItem[] = [];
  for (const id of ids) {
    const el = container.querySelector<HTMLElement>(`[data-section="${id}"]`);
    if (!el) continue;
    const r = el.getBoundingClientRect();
    const x = clamp((r.left - box.left) / colPx, 0, GRID_COLS - MIN_W);
    const w = clamp((r.width + GAP_PX) / colPx, MIN_W, GRID_COLS - x);
    out.push({ i: id, x, y: Math.max(0, Math.round((r.top - box.top) / (ROW_PX + GAP_PX))), w, h: Math.max(HEAD_ROWS, Math.ceil((r.height + GAP_PX) / (ROW_PX + GAP_PX))) });
  }
  return out;
}
