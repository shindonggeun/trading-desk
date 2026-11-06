import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import type { Favorite } from "./types";

/**
 * 즐겨찾기(#135 · 서버 #134) — 화면 어디서 별을 눌러도 같은 목록을 본다.
 *
 * 읽기 · 쓰기는 모두 한 줄(queue)로 차례차례 서버에 간다. 누르면 화면은 바로 바뀌고(낙관적),
 * 서버 답은 **뒤에 남은 쓰기가 없을 때만** 화면에 반영한다 — 앞 요청의 답이 그 뒤에 누른 변경을 덮지 않게.
 * 줄이 비었을 때 하나라도 실패했으면 서버 목록을 다시 읽어 맞춘다(되돌리기).
 */
type State = { list: Favorite[] | null; error: string | null };
let state: State = { list: null, error: null };
const listeners = new Set<() => void>();
let queue: Promise<unknown> = Promise.resolve();
let pending = 0;          // 줄에 선 쓰기 수
let failed: string | null = null;   // 이번 묶음에서 난 실패(줄이 비면 다시 읽고 이 문구를 보인다)
let latest: Favorite[] | null = null;   // 마지막으로 끝난 쓰기가 돌려준 서버 목록
let started = false;
let generation = 0;       // 계정이 바뀌면 이전 계정의 답은 버린다

const LEGACY_KEY = "desk.live.tickers";      // #124 시세판 관심 종목(이 브라우저에만 있던 것)
const MIGRATED_KEY = "desk.favorites.migrated";

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}
function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}
const snapshot = () => state;

function legacyTickers(): { ticker: string; name?: string | null }[] {
  try {
    if (localStorage.getItem(MIGRATED_KEY)) return [];
    const raw = JSON.parse(localStorage.getItem(LEGACY_KEY) ?? "null");
    if (!Array.isArray(raw)) return [];
    const ok = /^[A-Z0-9.^=-]{1,20}$/;   // 서버 clean_ticker 와 같은 규칙 — 하나가 틀려 전체가 거절되지 않게
    return raw
      .filter((x) => x && typeof x.ticker === "string")
      .map((x) => ({ ticker: String(x.ticker).trim().toUpperCase(), name: typeof x.name === "string" ? x.name.slice(0, 200) : null }))
      .filter((x) => ok.test(x.ticker))
      .slice(0, 50);
  } catch {
    return [];
  }
}
function markMigrated() {
  try {
    localStorage.setItem(MIGRATED_KEY, "1");
  } catch {
    /* 기억 못 하면 다음에 서버 목록이 비었을 때만 다시 옮긴다 */
  }
}

/** 서버 목록을 읽는다(줄 안에서만 부른다). 처음 한 번은 이 브라우저의 예전 관심 종목을 옮긴다.
 *  옮기기는 서버 목록이 비었을 때만 — 다른 기기에서 일부러 다 지운 뒤라면 예전 목록이 돌아올 수 있다(한 번뿐). */
async function readServer(gen: number): Promise<void> {
  try {
    let list = await api.favorites();
    const legacy = list.length ? [] : legacyTickers();
    if (legacy.length) list = await api.replaceFavorites(legacy).catch(() => list);
    markMigrated();
    if (gen === generation && pending === 0) set({ list, error: failed });
  } catch {
    if (gen !== generation) return;
    started = false;   // 다음에 화면이 붙으면 다시 읽는다
    set({ error: "즐겨찾기를 불러오지 못했습니다.", list: state.list ?? [] });
  }
  failed = null;
}

function enqueue(job: () => Promise<void>) {
  queue = queue.then(job, job);
}

function ensureLoaded() {
  if (started) return;
  started = true;
  const gen = generation;
  enqueue(() => readServer(gen));
}

// 다른 기기에서 바꾼 것도 탭에 돌아오면 맞춘다(쓰기가 줄에 없을 때만)
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && started && pending === 0) {
      const gen = generation;
      enqueue(() => readServer(gen));
    }
  });
}

/** 로그인 계정이 바뀌면(로그아웃 · 다른 계정) 처음부터 다시 */
export function resetFavorites() {
  generation += 1;
  started = false;
  pending = 0;
  failed = null;
  latest = null;
  set({ list: null, error: null });
}

/** 쓰기 하나를 줄에 세운다. apply 는 화면에 바로, send 는 차례가 오면 그때의 목록으로 보낸다. */
function write(apply: (list: Favorite[]) => Favorite[], send: () => Promise<Favorite[] | void>) {
  if (state.list !== null) set({ list: renumber(apply(state.list)), error: null });
  pending += 1;
  const gen = generation;
  enqueue(async () => {
    if (gen !== generation) return;
    try {
      const fresh = await send();
      latest = fresh ?? latest;
    } catch (e) {
      const full = (e as { status?: number }).status === 409 && String((e as Error).message).includes("at most");
      failed = full ? "즐겨찾기는 50개까지입니다." : "즐겨찾기를 저장하지 못해 서버 목록으로 되돌렸습니다.";
    }
    if (gen !== generation) return;
    pending -= 1;
    if (pending > 0) return;
    // 줄이 비었다: 실패가 있었거나 아직 목록이 없으면 서버를 다시 읽고, 아니면 마지막 답(이름이 채워진)을 쓴다.
    // 마지막 쓰기가 빼기(204)였다면 앞 답에 그 종목이 남아 있으므로 지금 화면(낙관적)이 곧 서버 목록이다.
    const last = latest;
    latest = null;
    if (failed || state.list === null) await readServer(gen);
    else if (last && sameTickers(last, state.list)) set({ list: last });
  });
}
function renumber(list: Favorite[]): Favorite[] {
  return list.map((f, i) => (f.position === i ? f : { ...f, position: i }));
}
function sameTickers(a: Favorite[], b: Favorite[]): boolean {
  return a.length === b.length && a.every((f, i) => f.ticker === b[i].ticker);
}

export function isFavorite(ticker: string): boolean {
  return !!state.list?.some((f) => f.ticker === ticker.toUpperCase());
}

export function addFavorite(ticker: string, name?: string | null) {
  const t = ticker.trim().toUpperCase();
  if (!t || isFavorite(t)) return;
  write((l) => [...l, { ticker: t, name: name ?? null, position: l.length }], () => api.addFavorite(t, name ?? undefined));
}

export function removeFavorite(ticker: string) {
  const t = ticker.toUpperCase();
  write((l) => l.filter((f) => f.ticker !== t), () => api.removeFavorite(t));
}

export function toggleFavorite(ticker: string, name?: string | null) {
  if (isFavorite(ticker)) removeFavorite(ticker);
  else addFavorite(ticker, name);
}

/** 한 칸 앞(-1) · 뒤(+1)로. 보낼 때의 화면 목록 전체를 보낸다(그사이 더하고 뺀 것까지). */
export function moveFavorite(ticker: string, by: -1 | 1) {
  const list = state.list ?? [];
  const i = list.findIndex((f) => f.ticker === ticker);
  const j = i + by;
  if (i < 0 || j < 0 || j >= list.length) return;
  write((l) => {
    const next = [...l];
    const a = next.findIndex((f) => f.ticker === ticker);
    const b = a + by;
    if (a >= 0 && b >= 0 && b < next.length) [next[a], next[b]] = [next[b], next[a]];
    return next;
  }, () => api.replaceFavorites((state.list ?? []).map((f) => ({ ticker: f.ticker, name: f.name }))));
}

export function clearFavoriteError() {
  set({ error: null });
}

/** 즐겨찾기 목록(처음엔 null = 불러오는 중)과 마지막 오류 */
export function useFavorites(): State {
  useEffect(ensureLoaded, []);
  return useSyncExternalStore(subscribe, snapshot);
}
