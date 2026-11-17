import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import { FAKE_CHANGES } from "./fundsFake";
import type { FundChangeItem } from "./types";

/**
 * #484 즐겨찾기 · "오늘 볼 것" 기관 배지: 내 종목이 유명 기관 최신 13F 에서 신규 · 증가 · 감소 · 전량 매도면.
 * 같은 종목 묶음은 한 페이지에서 한 번만 받는다(/api/funds/changes, 최대 30개). 공시는 분기마다라 30분 캐시로 충분하다.
 */
const TTL_MS = 30 * 60_000;
const cache = new Map<string, { at: number; p: Promise<Record<string, FundChangeItem[]>> }>();

function load(tickers: string[]): Promise<Record<string, FundChangeItem[]>> {
  const asked = [...tickers].sort().slice(0, 30); // 캐시 키와 실제 요청이 같은 30개
  const key = asked.join(",");
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL_MS) return hit.p;
  const p = api.fundChanges(asked)
    .then((v) => v.items ?? {})
    .catch((e) => {
      cache.delete(key); // 실패는 붙잡지 않는다 — 다음 화면에서 다시 받는다
      return import.meta.env.DEV && e instanceof ApiError && (e.status === 404 || e.status === 503) ? FAKE_CHANGES.items : {};
    });
  cache.set(key, { at: Date.now(), p });
  return p;
}

export function useFundChanges(tickers: string[]): Record<string, FundChangeItem[]> {
  const key = tickers.join(",");
  const [items, setItems] = useState<Record<string, FundChangeItem[]>>({});
  useEffect(() => {
    if (!key) return;
    let alive = true;
    load(key.split(",")).then((v) => alive && setItems(v));
    return () => { alive = false; };
  }, [key]);
  return items;
}

const VERB: Record<FundChangeItem["change"], string | null> = {
  new: "신규 매수", increase: "비중 확대", decrease: "비중 축소", sold: "전량 매도", unchanged: null,
};

/** "퍼싱스퀘어 신규 매수(3분기)" — 유지는 배지를 달지 않는다 */
export function fundBadgeText(c: FundChangeItem): string | null {
  const verb = VERB[c.change];
  if (!verb) return null;
  const q = /Q([1-4])$/.exec(c.quarter)?.[1];
  return `${c.name} ${verb}${q ? `(${q}분기)` : ""}`;
}

export function fundHref(c: FundChangeItem): string {
  return `#/funds?fund=${encodeURIComponent(c.fund)}&q=${encodeURIComponent(c.quarter)}`;
}
