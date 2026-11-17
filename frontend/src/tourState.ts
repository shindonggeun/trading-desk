/** #491 투어를 봤는지 · 다시 보지 않기 — 첫 화면에 필요한 작은 부분만(투어 문구 · driver.js 는 tour.ts 에서 지연 로드) */
export type TourId = "dashboard" | "analysis" | "tips" | "funds";

const SEEN_KEY = "desk.tour.seen";
const NEVER_KEY = "desk.tour.never";

function readSet(key: string): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(key) ?? "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}
function addTo(key: string, id: string) {
  try { localStorage.setItem(key, JSON.stringify([...new Set([...readSet(key), id])])); } catch { /* 기억 못 해도 투어는 돈다 */ }
}
export const tourSeen = (id: TourId) => readSet(SEEN_KEY).includes(id) || readSet(NEVER_KEY).includes(id);
export const neverTour = (id: TourId) => addTo(NEVER_KEY, id);
export const markSeen = (id: TourId) => addTo(SEEN_KEY, id);

/** 지금 주소에 맞는 투어(설정 · ? 버튼) */
export function tourForRoute(hash: string): TourId {
  if (hash.startsWith("#/runs/")) return "analysis";
  if (hash.startsWith("#/tips")) return "tips";
  if (hash.startsWith("#/funds")) return "funds";
  return "dashboard";
}

/** 투어 모듈을 그때 불러와 시작 — 대상이 하나도 없으면 false */
export async function startTour(id: TourId): Promise<boolean> {
  return (await import("./tour")).startTour(id);
}
export async function goAndStart(id: TourId): Promise<void> {
  return (await import("./tour")).goAndStart(id);
}
