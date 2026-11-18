/**
 * #636 넓은 화면 3열의 높이 맞추기. 구역마다 정해 둔 열(home)에서 시작해, 가장 긴 열의 구역 하나를 가장 짧은 열로
 * 옮기는 일을 차이가 줄어드는 동안 되풀이한다. 각 열의 첫 구역은 옮기지 않는다(열의 성격 — 왼쪽 시장, 오른쪽 수급).
 *
 * 높이는 지난번 이 브라우저에서 잰 값(펼친 채 · 내용을 다 그린 뒤)을 쓴다. 그래서 배치는 화면을 여는 순간 한 번 정해지고,
 * 보면서 스크롤하는 동안(화면 밖 구역은 보일 때 그려진다, #537) 구역이 열 사이를 뛰지 않는다. 처음 여는 브라우저처럼
 * 잰 값이 모자라면 정해 둔 열 그대로다.
 *
 * 지난번 배치가 지금 높이로도 고르면(가장 짧은 열의 KEEP 안) 그대로 둔다 — 열 폭이 달라 옮긴 구역의 높이가 조금 바뀌거나
 * 시세 · 뉴스가 늘고 줄 때마다 배치가 왔다 갔다 하지 않게.
 */
export type Col = 0 | 1 | 2;

const KEY = "desk.dashboard.heights.v1";
const COLS_KEY = "desk.dashboard.columns.v1";
const KEEP = 0.2;                   // 지난번 배치를 그대로 두는 차이(가장 짧은 열의 20%)
export const CLOSED_PX = 44;        // 접힌 구역(머리글만)
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

/** 구역 → 열. `height` 가 null 인 구역이 둘보다 많으면(잰 적이 거의 없으면) 정해 둔 열 그대로.
 *  `previous`(지난번 배치)가 모든 구역을 담고 지금 높이로도 KEEP 안이면 그대로 */
export function balanceColumns<T extends string>(ids: readonly T[], home: Record<T, Col>, height: (id: T) => number | null,
  previous: Partial<Record<T, Col>> = {}): Record<T, Col> {
  const out = Object.fromEntries(ids.map((id) => [id, home[id]])) as Record<T, Col>;
  if (ids.filter((id) => height(id) == null).length > 2) return out;
  const h = (id: T) => height(id) ?? 0;
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
    for (const id of cols[hi].slice(1)) {
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
