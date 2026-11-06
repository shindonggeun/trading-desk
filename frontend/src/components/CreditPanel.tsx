import { useEffect, useState } from "react";
import { api } from "../api";
import type { CreditView } from "../types";
import { CreditSummary } from "./PositioningView";

const REFRESH_MS = 30 * 60_000;   // 금융투자협회는 하루 한 번(다음 영업일) 올린다

function Trend({ c }: { c: CreditView }) {
  const rows = c.series;
  if (rows.length < 2) return null;
  const vals = rows.map((r) => r.ratio);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const w = 100;
  const h = 40;
  const x = (i: number) => (i / (rows.length - 1)) * w;
  const y = (v: number) => (hi === lo ? h / 2 : h - 2 - ((v - lo) / (hi - lo)) * (h - 4));
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  return (
    <figure className="credit-trend">
      <svg className="spark spark-flat" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" role="img"
        aria-label={`빚투 비율 ${rows[0].date}부터 ${rows[rows.length - 1].date}까지 ${vals[0]}%에서 ${vals[vals.length - 1]}%`}>
        <path d={d} />
      </svg>
      <figcaption className="muted small">빚투 비율 1년 추이 · 최저 {lo}% · 최고 {hi}%</figcaption>
    </figure>
  );
}

/** 대시보드 "빚투 · 예탁금"(#158): 신용융자 잔고 · 투자자예탁금 · 빚투 비율과 1년 위치, 1년 추이 선. */
export default function CreditPanel() {
  const [c, setC] = useState<CreditView | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    const load = () => api.credit(365).then((v) => alive && setC(v), () => alive && setC((cur) => cur ?? null));
    load();
    const t = window.setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  return (
    <section className="card credit-panel" aria-label="빚투 · 예탁금">
      <CreditSummary c={c} />
      {c?.summary && <Trend c={c} />}
    </section>
  );
}
