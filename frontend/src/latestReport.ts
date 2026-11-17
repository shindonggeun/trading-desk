import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { fakeList } from "./reportsFake";
import type { ReportSummary } from "./types";

/** #496 대시보드 "오늘 볼 것" 의 최신 리포트 한 줄 — 페이지당 한 번(10분 캐시), 4일 안에 쓴 글만 */
const TTL_MS = 10 * 60_000;
const FRESH_DAYS = 4;
let cache: { at: number; p: Promise<ReportSummary | null> } | null = null;

function load(): Promise<ReportSummary | null> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.p;
  // 개발 서버에서 #495 API 가 없으면(404) 예시 글 — 운영은 조용히 없음
  const p = api.reports().then((v) => v.items[0] ?? null, (e) => (import.meta.env.DEV && e instanceof ApiError && e.status === 404 ? fakeList().items[0] ?? null : null));
  cache = { at: Date.now(), p };
  return p;
}

export function useLatestReport(): ReportSummary | null {
  const [r, setR] = useState<ReportSummary | null>(null);
  useEffect(() => {
    let alive = true;
    load().then((v) => {
      if (!alive || !v) return;
      const age = (Date.now() - Date.parse(v.created_at)) / 86_400_000;
      if (age <= FRESH_DAYS) setR(v);
    });
    return () => { alive = false; };
  }, []);
  return r;
}
