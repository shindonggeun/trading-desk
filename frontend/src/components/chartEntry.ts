/**
 * #313 차트 "타점 보기" — 지금가 기준 최적 타점(#312 GET /api/entry-now)을 가격 칸에 그린다.
 * 분석(run) 없이도 보인다. 가장 나은 쪽의 구역 띠 · 1차 · 손절 · 목표선, 반대쪽 구역은 흐리게.
 * 관망이면(stand_aside) 가장 나은 쪽도 흐린 참고 띠만 — 선은 긋지 않는다.
 * PriceChart 의 다른 그리기와 섞이지 않게 자기 PriceBands 와 가격선을 붙이고 정리 함수를 돌려준다.
 */
import { LineStyle, type IPriceLine, type ISeriesApi, type SeriesType } from "lightweight-charts";
import { useEffect, useState } from "react";
import { entryNow } from "../api";
import type { EntryCandidate, EntryNow } from "../types";
import { type Band, PriceBands, withAlpha } from "./chartBands";

/** 서버가 지평(horizon)의 TTL 동안 같은 값을 준다 — 1분마다 다시 묻는다(실시간 가격으로 상태가 바뀐다) */
const REFRESH_MS = 60_000;

export function useEntryNow(ticker: string, on: boolean): { entry: EntryNow | null; failed: boolean } {
  const [entry, setEntry] = useState<EntryNow | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setEntry(null);
    setFailed(false);
    if (!on) return;
    let alive = true;
    const load = () =>
      entryNow(ticker).then(
        (e) => { if (alive) { setEntry(e); setFailed(false); } },
        () => { if (alive) setFailed(true); },
      );
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, [ticker, on]);
  return { entry, failed };
}

export function pctText(v: number | null | undefined): string {
  if (v == null) return "";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;
}

/** 지금가 대비 % (선 라벨용) */
function fromNow(p: number, now: number | null): string {
  return now ? ` (${pctText((p / now - 1) * 100)})` : "";
}

export interface EntryColors { long: string; short: string; loss: string; profit: string; muted: string }

interface Line { price: number; title: string; color: string; style: LineStyle; axis: boolean }

/** 띠 · 선 목록(순수 함수 — 테스트 · 확인이 쉽게) */
export function entryDrawing(e: EntryNow, c: EntryColors, fmt: (v: number) => string): { bands: Band[]; lines: Line[] } {
  const bands: Band[] = [];
  const lines: Line[] = [];
  const now = e.price_now;
  const zoneBand = (k: EntryCandidate, alpha: number, prefix: string) => {
    const [lo, hi] = k.zone;
    if (lo == null || hi == null) return;
    const color = k.side === "long" ? c.long : c.short;
    bands.push({ top: hi, bottom: lo, fill: withAlpha(color, alpha), edge: withAlpha(color, alpha * 2.5),
      label: `${prefix}${k.side_ko} ${k.kind === "now" ? "지금 구역 안" : "구역"} ${fmt(lo)} ~ ${fmt(hi)}${fromNow(k.first, now)}`,
      labelColor: withAlpha(color, Math.min(1, alpha * 6)) });
  };
  if (e.best) {
    const act = !e.stand_aside;
    zoneBand(e.best, act ? 0.18 : 0.07, act ? "타점 · " : "참고(관망) · ");
    if (act) {
      const color = e.best.side === "long" ? c.long : c.short;
      lines.push({ price: e.best.first, title: `${e.best.side_ko} 1차${fromNow(e.best.first, now)}`, color, style: LineStyle.Solid, axis: true });
      lines.push({ price: e.best.stop, title: `손절${fromNow(e.best.stop, now)}`, color: c.loss, style: LineStyle.Solid, axis: true });
      e.best.targets.slice(0, 2).forEach((t, i) =>
        lines.push({ price: t.price, title: `목표${i + 1}${fromNow(t.price, now)}`, color: c.profit,
          style: i === 0 ? LineStyle.Solid : LineStyle.Dashed, axis: i === 0 }));
    }
  }
  if (e.other) zoneBand(e.other, 0.05, "반대 · ");
  return { bands, lines };
}

/** 붙이고, 떼는 함수를 돌려준다(차트가 이미 사라졌으면 alive() 가 false). */
export function drawEntry(series: ISeriesApi<SeriesType>, e: EntryNow, c: EntryColors, fmt: (v: number) => string,
  font: string, alive: () => boolean): () => void {
  const { bands, lines } = entryDrawing(e, c, fmt);
  const prim = new PriceBands(font);
  series.attachPrimitive(prim);
  prim.set(bands, [], []);
  const made: IPriceLine[] = lines.map((l) =>
    series.createPriceLine({ price: l.price, title: l.title, color: l.color, lineWidth: 2, lineStyle: l.style, axisLabelVisible: l.axis }));
  return () => {
    if (!alive()) return;
    for (const line of made) series.removePriceLine(line);
    series.detachPrimitive(prim);
  };
}
