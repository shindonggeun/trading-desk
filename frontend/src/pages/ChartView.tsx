import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "../api";
import { navigation } from "../nav";
import type { Interval } from "../components/PriceChart";
import type { Plan, Run } from "../types";

const PriceChart = lazy(() => import("../components/PriceChart"));

const INTERVALS: Interval[] = ["5m", "15m", "30m", "60m", "240m", "1d", "1w", "1M", "1y"];

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 주소로 여는 큰 차트(#97): `#/chart/<티커>?run=<분석 id>&interval=<주기>`. 분석을 주면 그 타점선까지. */
export default function ChartView({ ticker, query }: { ticker: string; query: URLSearchParams }) {
  const runId = query.get("run");
  const iv = query.get("interval");
  const interval = INTERVALS.includes(iv as Interval) ? (iv as Interval) : undefined;
  const [run, setRun] = useState<Run | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!runId) return;
    api.run(runId).then(setRun, () => setFailed(true));
  }, [runId]);

  // 닫기: 앞 화면이 있으면 돌아가고, 주소로 바로 열었으면 분석 화면 · 대시보드로
  const close = () => {
    if (navigation.moved) window.history.back();
    else window.location.replace(runId ? `#/runs/${runId}` : "#/");
  };

  const plan: Plan | null = run?.plan ?? null;
  if (runId && !run && !failed) return <main className="chart-view"><p className="muted">불러오는 중…</p></main>;
  return (
    <main className="chart-view">
      <Suspense fallback={<p className="muted">차트를 여는 중…</p>}>
        <PriceChart
          ticker={run?.ticker ?? ticker}
          tradeDate={run?.trade_date ?? today()}
          plan={plan}
          title={run?.options.name ?? undefined}
          initialInterval={interval}
          lists={false}
          startExpanded
          onClose={close}
          notice={failed ? "분석 기록을 찾지 못해 타점선 없이 보여 줍니다." : undefined}
        />
      </Suspense>
    </main>
  );
}
