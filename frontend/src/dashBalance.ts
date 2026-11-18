/**
 * #636 넓은 화면 3열의 높이 맞추기. 구역마다 정해 둔 열(home)에서 시작해, 가장 긴 열의 구역 하나를 가장 짧은 열로
 * 옮기는 일을 차이가 줄어드는 동안 되풀이한다. 각 열의 첫 구역은 옮기지 않는다(열의 성격 — 왼쪽 시장, 오른쪽 수급).
 *
 * 높이는 지난번 이 브라우저에서 잰 값(펼친 채 · 내용을 다 그린 뒤)을 쓴다. 그래서 배치는 화면을 여는 순간 한 번 정해지고,
 * 보면서 스크롤하는 동안(화면 밖 구역은 보일 때 그려진다, #537) 구역이 열 사이를 뛰지 않는다.
 * 아직 잰 적이 없는 구역(처음 여는 브라우저, 끝까지 내려 보지 않은 구역)은 운영에서 잰 보통 높이(TYPICAL_PX)로 친다 —
 * #636 후속: 잰 값이 모자라면 건너뛰던 탓에 처음 여는 브라우저 · 아래를 안 본 브라우저에서는 균형이 한 번도 돌지 않았다.
 *
 * 지난번 배치가 지금 높이로도 고르면(가장 짧은 열의 KEEP 안) 그대로 둔다 — 열 폭이 달라 옮긴 구역의 높이가 조금 바뀌거나
 * 시세 · 뉴스가 늘고 줄 때마다 배치가 왔다 갔다 하지 않게.
 */
export type Col = 0 | 1 | 2;

const KEY = "desk.dashboard.heights.v1";
const COLS_KEY = "desk.dashboard.columns.v1";
const KEEP = 0.2;                   // 지난번 배치를 그대로 두는 차이(가장 짧은 열의 20%)
export const CLOSED_PX = 44;        // 접힌 구역(머리글만)
/** 운영 1440 에서 잰 구역 높이(2026-11-18, 즐겨찾기 30종목 · 접힘 8개) — 이 브라우저에서 잰 값이 없을 때만 쓴다 */
export const TYPICAL_PX: Record<string, number> = {
  market: 980, kfutures: 220, weekly: 540, macro: 530, sectornews: 200, favorites: 1100, agenda: 90, bottom: 300,
  halving: 160, recent: 360, credit: 280, regime: 175, flow: 684, portfolio: 620, stats: 270, paper: 166,
};
const UNKNOWN_PX = 300;             // 새로 생긴 구역처럼 보통 높이도 모르는 구역
const SETTLED = 0.1;                // 가장 짧은 열의 10% 안이면 그대로
const MAX_MOVES = 12;

export function loadHeights(): Record<string, number> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => typeof v === "number" && Number.isFinite(v) && v > 0 && v < 20000)) as Record<string, number>;
  } catch {
    return {};
  }
}

export function saveHeights(heights: Record<string, number>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(heights));
  } catch {
    /* 기억만 못 한다 — 다음에는 정해 둔 열 그대로 */
  }
}

export function loadColumns(): Record<string, Col> {
  try {
    const raw = JSON.parse(localStorage.getItem(COLS_KEY) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => v === 0 || v === 1 || v === 2)) as Record<string, Col>;
  } catch {
    return {};
  }
}

export function saveColumns(cols: Record<string, Col>) {
  try {
    localStorage.setItem(COLS_KEY, JSON.stringify(cols));
  } catch {
    /* 기억만 못 한다 */
  }
}

function spread<T extends string>(ids: readonly T[], cols: Record<T, Col>, h: (id: T) => number): number {
  const sums = ([0, 1, 2] as Col[]).map((c) => ids.filter((id) => cols[id] === c).reduce((a, id) => a + h(id), 0));
  const lo = Math.min(...sums);
  return lo > 0 ? (Math.max(...sums) - lo) / lo : Infinity;
}

/** 구역 → 열. 잰 적이 없는 구역(`height` 가 null)은 TYPICAL_PX(없으면 UNKNOWN_PX)로 친다.
 *  `previous`(지난번 배치)가 모든 구역을 담고 지금 높이로도 KEEP 안이면 그대로 */
export function balanceColumns<T extends string>(ids: readonly T[], home: Record<T, Col>, height: (id: T) => number | null,
  previous: Partial<Record<T, Col>> = {}): Record<T, Col> {
  const out = Object.fromEntries(ids.map((id) => [id, home[id]])) as Record<T, Col>;
  const h = (id: T) => height(id) ?? TYPICAL_PX[id] ?? UNKNOWN_PX;
  // 열마다 본래 첫 구역(왼쪽 시장 · 가운데 브리핑 · 오른쪽 수급)은 옮기지 않는다 — 옮겨 온 구역이 앞에 있어도
  const anchors = new Set(([0, 1, 2] as Col[]).map((c) => ids.find((id) => home[id] === c)).filter((id): id is T => id !== undefined));
  if (ids.every((id) => previous[id] !== undefined)) {
    const kept = Object.fromEntries(ids.map((id) => [id, previous[id] as Col])) as Record<T, Col>;
    if (spread(ids, kept, h) <= KEEP) return kept;
  }
  for (let k = 0; k < MAX_MOVES; k++) {
    const cols = ([0, 1, 2] as Col[]).map((c) => ids.filter((id) => out[id] === c));
    const sums = cols.map((list) => list.reduce((a, id) => a + h(id), 0));
    const hi = sums.indexOf(Math.max(...sums)) as Col;
    const lo = sums.indexOf(Math.min(...sums)) as Col;
    const gap = sums[hi] - sums[lo];
    if (hi === lo || gap <= SETTLED * sums[lo]) break;
    // 옮겨서 차이가 줄어드는 구역(0 < 높이 < 차이) 가운데 두 열을 가장 비슷하게 만드는 것
    let best: T | null = null;
    let bestLeft = gap;
    for (const id of cols[hi]) {
      if (anchors.has(id)) continue;
      const size = h(id);
      if (size <= 0 || size >= gap) continue;
      const left = Math.abs(gap - 2 * size);
      if (left < bestLeft) { best = id; bestLeft = left; }
    }
    if (best === null) break;
    out[best] = lo;
  }
  return out;
}
