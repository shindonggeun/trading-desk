import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { fakeHalving } from "./halvingFake";
import type { Halving } from "./types";

/**
 * #515 비트코인 반감기 사이클(#514 GET /api/crypto/halving, 서버가 하루 캐시) — 화면 · 대시보드 카드가 같이 쓴다.
 * 페이지당 한 번(10분) 읽는다. 개발 서버에서 API 가 없으면(404) 예시 — 운영은 오류 그대로.
 */
const TTL_MS = 10 * 60_000;
type Got = { data: Halving; fake: boolean };
let cache: { at: number; p: Promise<Got> } | null = null;

function load(): Promise<Got> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.p;
  const p = api.halving().then((data) => ({ data, fake: false }), (e: unknown) => {
    if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) return { data: fakeHalving(), fake: true };
    throw e;
  });
  cache = { at: Date.now(), p };
  p.catch(() => { if (cache?.p === p) cache = null; });   // 실패는 기억하지 않는다(다음에 다시)
  return p;
}

export function useHalving(): { data: Halving | null; fake: boolean; error: string | null } {
  const [got, setGot] = useState<Got | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    load().then((g) => alive && setGot(g), (e: unknown) => alive && setError(e instanceof Error ? e.message : "불러오지 못했습니다"));
    return () => { alive = false; };
  }, []);
  return { data: got?.data ?? null, fake: got?.fake ?? false, error };
}

// ── 표기 ──
export const usd = (v: number) => `$${Math.round(v).toLocaleString("en-US")}`;
export const pct = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
export const range = (a: number, b: number, unit = "") => (a === b ? `${a}${unit}` : `${a}~${b}${unit}`);
/** 하락률 범위는 얕은 쪽부터(−76.6 ~ −83.4%) */
export const ddRange = (a: number, b: number) => {
  const [hi, lo] = a >= b ? [a, b] : [b, a];
  return hi === lo ? pct(hi) : `${pct(hi)} ~ ${pct(lo)}`;
};
export const WHERE_DAYS = { before: "아직 그 앞이에요", within: "그 범위 안이에요", after: "그 범위를 지났어요" } as const;
export const WHERE_DAYS_SHORT = { before: "아직 전", within: "범위 안", after: "지남" } as const;
export const WHERE_DD = { shallower: "그보다 얕아요", within: "그 범위 안이에요", deeper: "그보다 깊어요" } as const;
/** 반감기 날짜 → "2016~20 사이클" */
export function cycleName(halving: string, end?: string | null): string {
  const y = halving.slice(0, 4);
  return end ? `${y}~${end.slice(2, 4)}` : `${y}~ 지금`;
}
