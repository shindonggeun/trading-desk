import { useEffect, useSyncExternalStore } from "react";
import { api } from "./api";
import type { Favorite } from "./types";

/**
 * 즐겨찾기(#135 · 서버 #134) — 화면 어디서 별을 눌러도 같은 목록을 본다.
 * 누르면 바로 바뀌고(낙관적) 서버가 거절하면 서버 목록으로 되돌린다. 쓰기는 한 줄로 차례차례.
 */
type State = { list: Favorite[] | null; error: string | null };
let state: State = { list: null, error: null };
const listeners = new Set<() => void>();
let loading: Promise<void> | null = null;
let queue: Promise<unknown> = Promise.resolve();

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
    return raw
      .filter((x) => x && typeof x.ticker === "string" && x.ticker.trim())
      .map((x) => ({ ticker: String(x.ticker).trim().toUpperCase(), name: typeof x.name === "string" ? x.name : null }))
      .slice(0, 50);
  } catch {
    return [];
  }
}
function markMigrated() {
  try {
    localStorage.setItem(MIGRATED_KEY, "1");
  } catch {
    /* 다음에 다시 시도해도 서버 목록이 있으면 건너뛴다 */
  }
}

async function fetchList(): Promise<void> {
  try {
    let list = await api.favorites();
    // 처음 한 번: 서버가 비어 있으면 이 브라우저의 예전 관심 종목을 옮긴다
    const legacy = list.length ? [] : legacyTickers();
    if (legacy.length) list = await api.replaceFavorites(legacy);
    markMigrated();
    set({ list, error: null });
  } catch {
    set({ error: "즐겨찾기를 불러오지 못했습니다.", list: state.list ?? [] });
  }
}

export function reloadFavorites(): Promise<void> {
  loading = fetchList();
  return loading;
}

function ensureLoaded() {
  if (!loading) {
    loading = fetchList();
    // 다른 기기에서 바꾼 것도 탭에 돌아오면 맞춘다
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) queue = queue.then(fetchList);
    });
  }
}

/** 쓰기를 차례로: 앞 요청이 끝난 뒤 보낸다. 실패하면 서버 목록으로 되돌린다. */
function write(apply: (list: Favorite[]) => Favorite[], send: () => Promise<Favorite[] | void>) {
  set({ list: renumber(apply(state.list ?? [])), error: null });
  queue = queue.then(async () => {
    try {
      const fresh = await send();
      if (fresh) set({ list: fresh });
    } catch (e) {
      // 409: 꽉 참(at most 50) 또는 다른 탭 · 기기의 쓰기와 부딪침 — 어느 쪽이든 서버 목록으로 맞춘다
      const full = (e as { status?: number }).status === 409 && String((e as Error).message).includes("at most");
      await fetchList();
      set({ error: full ? "즐겨찾기는 50개까지입니다." : "즐겨찾기를 저장하지 못해 서버 목록으로 되돌렸습니다." });
    }
  });
}
function renumber(list: Favorite[]): Favorite[] {
  return list.map((f, i) => (f.position === i ? f : { ...f, position: i }));
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

/** 한 칸 위(-1) · 아래(+1)로 */
export function moveFavorite(ticker: string, by: -1 | 1) {
  const list = state.list ?? [];
  const i = list.findIndex((f) => f.ticker === ticker);
  const j = i + by;
  if (i < 0 || j < 0 || j >= list.length) return;
  const next = [...list];
  [next[i], next[j]] = [next[j], next[i]];
  write(() => next, () => api.replaceFavorites(next.map((f) => ({ ticker: f.ticker, name: f.name }))));
}

export function clearFavoriteError() {
  set({ error: null });
}

/** 즐겨찾기 목록(처음엔 null = 불러오는 중)과 마지막 오류 */
export function useFavorites(): State {
  useEffect(ensureLoaded, []);
  return useSyncExternalStore(subscribe, snapshot);
}
