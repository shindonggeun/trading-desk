import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { FearItem } from "../types";

// #464 휴대폰 대시보드 맨 위 요약 줄: 지수 등락 3개 + 공포지수 2개. 구역은 접힌 머리글로 시작하니
// 펼치지 않아도 시장 분위기를 한 줄로 본다. 실패한 항목은 조용히 뺀다(아래 구역에서 자세히 본다).

const INDICES = [
  { ticker: "^KS11", name: "코스피" },
  { ticker: "^KQ11", name: "코스닥" },
  { ticker: "NQ=F", name: "나스닥100 선물" },
];
const FEAR_KEYS: FearItem["key"][] = ["cnn", "vix"];
const REFRESH_MS = 60_000;

type Quote = { ticker: string; name: string; last: number; change: number | null };

export default function DashSummary() {
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [fear, setFear] = useState<FearItem[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () => {
      Promise.all(INDICES.map((m) => api.candles(m.ticker, "1d", 5)
        .then((c): Quote | null => {
          const bars = c.bars;
          if (!bars.length) return null;
          const last = bars[bars.length - 1].close;
          const prev = bars.length > 1 ? bars[bars.length - 2].close : null;
          return { ...m, last, change: prev ? ((last - prev) / prev) * 100 : null };
        })
        .catch(() => null)))
        .then((qs) => alive && setQuotes(qs.filter((q): q is Quote => q !== null)));
      api.marketFear()
        .then((d) => alive && setFear(FEAR_KEYS.flatMap((k) => d.items.filter((i) => i.key === k && i.value != null))))
        .catch(() => alive && setFear([]));
    };
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  if (!quotes.length && !fear.length) return null;
  return (
    <div className="dash-summary" role="group" aria-label="시장 요약">
      {quotes.map((q) => (
        <span key={q.ticker} className="dash-sum-item">
          <span className="muted">{q.name}</span> {price(q.last)}
          {q.change != null && (
            <span className={q.change >= 0 ? "up" : "down"}> {q.change >= 0 ? "+" : ""}{q.change.toFixed(2)}%</span>
          )}
        </span>
      ))}
      {fear.map((f) => (
        <span key={f.key} className={`dash-sum-item${f.band ? ` fear-${f.band}` : ""}`}>
          <span className="muted">{f.key === "cnn" ? "공포탐욕" : f.name}</span> {Math.round((f.value ?? 0) * 10) / 10}
          {f.band_ko && <span className="fear-band"> {f.band_ko}</span>}
        </span>
      ))}
    </div>
  );
}
