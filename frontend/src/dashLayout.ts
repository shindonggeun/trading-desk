import type { Layout, LayoutItem } from "react-grid-layout";

/**
 * #487 대시보드 배치 편집: 화면 폭 구간별(≥1280 · 1000~1279) 12칸 격자 배치를 이 브라우저에 기억한다.
 * 저장한 배치가 없으면 대시보드는 예전(#464) 3열 · 2열 흐름 그대로 그린다 — "기본 배치로" 는 저장값을 지운다.
 *
 * #519 높이는 구역마다 자동(기본, 내용 높이를 재서 맞춤) 또는 고정(사용자가 모서리 · Shift+↑↓ 로 바꾼 구역).
 * 세로 간격은 격자 여백이 아니라 칸 아래 패딩(GAP_PX)이라 한 줄(ROW_PX)이 4px — 내용에 4px 단위로 붙는다.
 * (격자 여백으로 두면 한 줄이 줄 높이 + 간격 = 22px 단위라 구역마다 최대 21px 이 비었다)
 */
export type Band = "wide" | "mid";
export const GRID_COLS = 12;
export const ROW_PX = 4; // 격자 한 줄 높이(px) — 세로 여백이 없어 이것이 높이 단위
export const GAP_PX = 14; // 가로 여백 · 칸 아래 패딩(세로 간격)
export const MIN_W = 3; // 최소 폭 = 12칸의 1/4
/** 내용 높이(px) → 격자 줄 수(아래 간격 포함) */
export const rowsFor = (px: number) => Math.max(1, Math.ceil((px + GAP_PX) / ROW_PX));
export const HEAD_ROWS = rowsFor(30); // 접힌 구역(머리글만) — 30px
export const DEFAULT_H = rowsFor(300); // 새 구역 · 다시 보이기 · 접힌 채 잰 구역의 높이(≈ 300px)
/** 키보드 Shift+↑↓ 한 번에 바꾸는 높이(≈ 44px) */
export const KEY_H_STEP = 11;

/** fixed: 사용자가 높이를 정한 구역(나머지는 내용에 맞춘다) */
export interface BandLayout { items: Layout; hidden: string[]; fixed: string[] }
type Stored = { items: Layout; hidden?: string[]; fixed?: string[]; v?: number };
type Store = Partial<Record<Band, Stored>>;
const VERSION = 2;
// v1(#487, 키 …layout.v1): 한 줄 8px + 세로 여백 14px = 22px 단위, 높이는 모두 고정.
// v2 는 새 키에 쓴다 — 배포 전 번들이 열린 탭은 v1 키만 읽고 써서 서로의 단위를 잘못 읽지 않는다(이중 이전 없음).
// v2 키에 그 폭 구간이 없을 때 한 번만 v1 을 읽어 새 단위 · 모두 자동으로 바꿔 v2 키에 적는다.
const V1_UNIT = 22;
const KEY_V1 = "desk.dashboard.layout.v1";
const KEY = "desk.dashboard.layout.v2";

function read(key = KEY): Store {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "{}");
    return raw && typeof raw === "object" ? (raw as Store) : {};
  } catch {
    return {};
  }
}
function write(s: Store, key = KEY) {
  try { localStorage.setItem(key, JSON.stringify(s)); } catch { /* 기억만 못 한다 — 이번 화면에서는 그대로 */ }
}

const isItem = (v: unknown): v is LayoutItem => {
  const o = v as LayoutItem;
  return !!o && typeof o.i === "string" && [o.x, o.y, o.w, o.h].every((n) => Number.isFinite(n));
};

/** 저장한 배치(손상값은 버림). 없는 구역은 빼고, 새로 생긴 구역은 맨 아래에 4칸 폭으로 붙인다. */
export function loadBand(band: Band, known: string[]): BandLayout | null {
  const cur = read()[band];
  const b = cur ?? read(KEY_V1)[band];
  if (!b || !Array.isArray(b.items)) return null;
  const moved = !cur;   // v1 키에서 옮겨 온다
  // #524 가 먼저 배포됐다면 v1 키에 이미 새 단위(v:2)로 적혀 있다 — 그건 단위를 바꾸지 않고 그대로 옮긴다
  const v1 = moved && b.v !== VERSION;
  const items = b.items.filter(isItem).filter((it) => known.includes(it.i))
    .map((it) => ({
      i: it.i, x: clamp(it.x, 0, GRID_COLS - MIN_W), w: clamp(it.w, MIN_W, GRID_COLS),
      y: Math.max(0, v1 ? Math.round((it.y * V1_UNIT) / ROW_PX) : it.y),
      // v1 의 높이는 내용을 잴 때까지만 쓰는 첫 짐작(자동)이다
      h: Math.max(HEAD_ROWS, v1 ? Math.ceil((it.h * V1_UNIT) / ROW_PX) : it.h),
    }));
  const hidden = Array.isArray(b.hidden) ? b.hidden.filter((h) => known.includes(h)) : [];
  const fixed = !v1 && Array.isArray(b.fixed) ? b.fixed.filter((f) => typeof f === "string" && items.some((it) => it.i === f)) : [];
  const bottom = items.reduce((m, it) => Math.max(m, it.y + it.h), 0);
  const missing = known.filter((k) => !items.some((it) => it.i === k) && !hidden.includes(k));
  missing.forEach((k, n) => items.push({ i: k, x: (n % 3) * 4, y: bottom + Math.floor(n / 3) * DEFAULT_H, w: 4, h: DEFAULT_H }));
  const out = items.length ? { items, hidden, fixed } : null;
  if (moved && out) saveBand(band, out);   // 이전은 한 번만 — 다음부터는 v2 키에서 읽는다
  return out;
}

export function saveBand(band: Band, layout: BandLayout) {
  const s = read();
  s[band] = { v: VERSION, items: layout.items.map(({ i, x, y, w, h }) => ({ i, x, y, w, h })), hidden: layout.hidden, fixed: layout.fixed };
  write(s);
}

/** 보기 화면에서 잰 자동 높이를 저장 배치에 적어 둔다 — 다음에 열 때 첫 그림이 덜 흔들리게(고정 구역 · 다른 값은 그대로) */
export function saveAutoHeights(band: Band, heights: Record<string, number>) {
  const s = read();
  const b = s[band];
  if (!b || b.v !== VERSION || !Array.isArray(b.items)) return;
  const fixed = b.fixed ?? [];
  let changed = false;
  const items = b.items.map((it) => {
    const h = heights[it.i];
    if (h == null || fixed.includes(it.i) || it.h === h) return it;
    changed = true;
    return { ...it, h };
  });
  if (changed) { s[band] = { ...b, items }; write(s); }
}

export function resetBand(band: Band) {
  // v1 키도 지운다 — 남겨 두면 다음에 다시 이전되어 "기본 배치로" 가 듣지 않는다
  for (const key of [KEY, KEY_V1]) {
    const s = read(key);
    if (s[band]) { delete s[band]; write(s, key); }
  }
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
    out.push({ i: id, x, y: Math.max(0, Math.round((r.top - box.top) / ROW_PX)), w, h: Math.max(HEAD_ROWS, rowsFor(r.height)) });
  }
  return out;
}

/** 저장 배치에서 숨긴 구역을 다시 보이게(loadBand 가 맨 아래에 붙인다). 바꿨으면 true — #362 알림 클릭용 */
export function unhideSection(band: Band, id: string): boolean {
  // 아직 이전 전이면(v2 키에 그 구간이 없으면) v1 키에서 — 이전할 때 그대로 따라온다
  const key = read()[band] ? KEY : KEY_V1;
  const s = read(key);
  const b = s[band];
  if (!b || !Array.isArray(b.hidden) || !b.hidden.includes(id)) return false;
  s[band] = { ...b, hidden: b.hidden.filter((h) => h !== id) };
  write(s, key);
  return true;
}
