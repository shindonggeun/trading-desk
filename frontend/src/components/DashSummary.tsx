import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { FearItem } from "../types";
import { useTrend } from "../marketTrend";
import { TrendSummaryItems } from "./TrendChips";

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
  const trend = useTrend(); // #461 지수 지금 추세 — 시세 · 공포지수가 없어도 추세만으로 줄을 보인다

  useEffect(() => {
    let alive = true;
    // 한 번 실패해도 앞서 받은 값을 둔다(종목별로 새 값만 덮어쓴다). 뒤에 숨은 탭에서는 받지 않는다.
    const load = () => {
      if (document.hidden) return;
      Promise.all(INDICES.map((m) => api.candles(m.ticker, "1d", 5)
        .then((c): Quote | null => {
          const bars = c.bars;
          if (!bars.length) return null;
          const last = bars[bars.length - 1].close;
          const prev = bars.length > 1 ? bars[bars.length - 2].close : null;
          return { ...m, last, change: prev ? ((last - prev) / prev) * 100 : null };
        })
        .catch(() => null)))
        .then((qs) => {
          if (!alive) return;
          setQuotes((prev) => INDICES.flatMap((m) => {
            const q = qs.find((x) => x?.ticker === m.ticker) ?? prev.find((x) => x.ticker === m.ticker);
            return q ? [q] : [];
          }));
        });
      api.marketFear()
        .then((d) => {
          if (!alive) return;
          const items = FEAR_KEYS.flatMap((k) => d.items.filter((i) => i.key === k && i.value != null));
          if (items.length) setFear(items);
        })
        .catch(() => { /* 앞서 받은 공포지수를 둔다 */ });
    };
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  if (!quotes.length && !fear.length && !trend.length) return null;
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
      <TrendSummaryItems trend={trend} />
      {fear.map((f) => (
        <span key={f.key} className={`dash-sum-item${f.band ? ` fear-${f.band}` : ""}`}>
          <span className="muted">{f.key === "cnn" ? "공포탐욕" : f.name}</span> {Math.round((f.value ?? 0) * 10) / 10}
          {f.band_ko && <span className="fear-band"> {f.band_ko}</span>}
        </span>
      ))}
    </div>
  );
}
