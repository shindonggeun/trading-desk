import { useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { FearItem } from "../types";
import { useTrend } from "../marketTrend";
import { TrendSummaryItems } from "./TrendChips";
import { useHalving, WHERE_DAYS_SHORT } from "../halving";
import { staleText, usePoll } from "../poll";

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
  // #540 비트코인 반감기 뒤 일수 — 반감기 구역 카드와 같은 요청(10분 캐시)을 나눠 쓴다. 지금 사이클 값이 없으면 숨긴다
  const hv = useHalving().data;
  const hvNow = hv?.current ?? null;
  const hvWhere = hv?.ranges.now.days_since_halving ?? null;

  // #626 1분마다(가려진 탭은 쉰다). 한 번 실패해도 앞서 받은 값을 둔다(종목별로 새 값만 덮어쓴다)
  const indexPoll = usePoll("dash-summary:indices", () => Promise.all(INDICES.map((m) => api.candles(m.ticker, "1d", 5)
    .then((c): Quote | null => {
      const bars = c.bars;
      if (!bars.length) return null;
      const last = bars[bars.length - 1].close;
      const prev = bars.length > 1 ? bars[bars.length - 2].close : null;
      return { ...m, last, change: prev ? ((last - prev) / prev) * 100 : null };
    })
    .catch(() => null)))
    // 하나도 못 받았으면 실패로 — 다음 차례를 늦추고 카드에 "마지막 갱신 · 다시 시도 중" 을 적는다
    .then((qs) => { if (qs.every((q) => q == null)) throw new Error("지수를 받지 못함"); return qs; }), REFRESH_MS, {
    onData: (qs) => setQuotes((prev) => INDICES.flatMap((m) => {
      const q = qs.find((x) => x?.ticker === m.ticker) ?? prev.find((x) => x.ticker === m.ticker);
      return q ? [q] : [];
    })),
  });
  // 공포지수는 대시보드 공포지수 띠(FearIndexStrip)와 같은 요청 하나를 함께 쓴다. 실패하면 앞서 받은 값을 둔다
  usePoll("market-fear", () => api.marketFear(), REFRESH_MS, {
    onData: (d) => {
      const items = FEAR_KEYS.flatMap((k) => d.items.filter((i) => i.key === k && i.value != null));
      if (items.length) setFear(items);
    },
  });

  if (!quotes.length && !fear.length && !trend.length && !hvNow) return null;
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
      {/* #626 지수를 다시 받지 못하는 동안: 마지막으로 받은 시각과 다시 시도 중 */}
      {staleText(indexPoll) && <span className="dash-sum-item muted small" role="status">{staleText(indexPoll)}</span>}
      {fear.map((f) => (
        <span key={f.key} className={`dash-sum-item${f.band ? ` fear-${f.band}` : ""}`}>
          <span className="muted">{f.key === "cnn" ? "공포탐욕" : f.name}</span> {Math.round((f.value ?? 0) * 10) / 10}
          {f.band_ko && <span className="fear-band"> {f.band_ko}</span>}
        </span>
      ))}
      {hvNow && (
        <a className="dash-sum-item dash-sum-link" href="#/halving"
          title="비트코인 반감기 뒤 일수 — 누르면 사이클 비교 화면">
          <span className="muted">반감기</span> {hvNow.days_since_halving}일째
          {hvWhere && <span className="muted"> · 과거 바닥 {WHERE_DAYS_SHORT[hvWhere]}</span>}
        </a>
      )}
    </div>
  );
}
