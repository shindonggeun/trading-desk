import { useEffect, useState } from "react";
import { api } from "./api";
import type { TrendRow } from "./types";

/**
 * #461 지수별 대세 국면(/api/tips 의 trend[], ticker 없이) — 한 페이지에서 한 번만 받아 배지 · 칩이 같이 쓴다.
 * 국면은 그 지수의 "지금 추세 설명"이지 종목의 앞날 예측이 아니다(백테스트상 하락 국면 뒤가 꼭 나쁘지 않았다).
 */
const TTL_MS = 10 * 60_000;
let cache: { at: number; rows: Promise<TrendRow[]> } | null = null;

export function loadTrend(): Promise<TrendRow[]> {
  if (cache && Date.now() - cache.at < TTL_MS) return cache.rows;
  const rows = api.tips().then((v) => v.trend ?? [], () => [] as TrendRow[]);
  cache = { at: Date.now(), rows };
  // 실패 · "불러오는 중"(trend 빈 배열)은 오래 붙잡지 않는다 — 1분 뒤 다시 받는다
  rows.then((r) => { if (!r.length && cache?.rows === rows) cache = { at: Date.now() - TTL_MS + 60_000, rows }; });
  return rows;
}

export function useTrend(): TrendRow[] {
  const [rows, setRows] = useState<TrendRow[]>([]);
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    // 배포 직후 등 서버가 아직 계산 중이면 trend 가 비어 온다 — 화면을 연 채로도 1분마다 다시 받아 채운다
    const load = () => loadTrend().then((r) => {
      if (!alive) return;
      setRows(r);
      if (!r.length) timer = window.setTimeout(load, 60_000);
    });
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);
  return rows;
}

// 필라델피아 반도체(SOX)를 함께 보일 반도체 종목 · ETF(미국 상장)
const SEMIS = new Set([
  "NVDA", "AMD", "AVGO", "MU", "TSM", "INTC", "QCOM", "ASML", "AMAT", "LRCX", "KLAC", "MRVL", "ARM", "SMCI", "TXN", "ADI",
  "NXPI", "ON", "MCHP", "MPWR", "TER", "ENTG", "WOLF", "SOXL", "SOXS", "SOXX", "SMH", "USD", "NVDL", "NVDU", "AMDL", "MUU",
]);

/** 이 종목에 붙일 시장 지수: 한국 → 코스피(추세 자료에 코스닥이 없다), 미국 → 나스닥100(+ 반도체면 SOX) */
export function marketsFor(ticker: string): TrendRow["market"][] {
  const t = ticker.toUpperCase();
  if (/\.(KS|KQ)$/.test(t) || /^\d{6}$/.test(t)) return ["kospi"];
  if (t.startsWith("^") || /=[FX]$/.test(t) || /-USD$/.test(t)) return []; // 지수 · 선물 · 환율 · 코인은 붙이지 않는다
  return SEMIS.has(t) ? ["nasdaq100", "sox"] : ["nasdaq100"];
}

export const TREND_CLASS: Record<TrendRow["state"], string> = {
  bull: "trend-bull", bear: "trend-bear", range: "trend-range", turning: "trend-turning",
};
