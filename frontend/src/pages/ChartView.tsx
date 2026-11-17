import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { api } from "../api";
import DeepRunConfirm from "../components/DeepRunConfirm";
import { navigation } from "../nav";
import type { Interval } from "../components/PriceChart";
import { sameTicker } from "../ticker";
import type { Plan, Run } from "../types";

const PriceChart = lazy(() => import("../components/PriceChart"));

const INTERVALS: Interval[] = ["5m", "15m", "30m", "60m", "240m", "1d", "1w", "1M", "1y"];

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/**
 * 주소로 여는 큰 차트(#97): `#/chart/<티커>?run=<분석 id>&interval=<주기>`. 분석을 주면 그 타점선까지.
 * #539 기본은 차트 분석(쿼터 없음) — 에이전트 토론 상세 분석은 위쪽 "상세 분석 시작"(확인 한 번)으로만, 같은 종목의
 * 최근 분석이 있으면 "최근 분석 보기".
 */
export default function ChartView({ ticker, query }: { ticker: string; query: URLSearchParams }) {
  const runId = query.get("run");
  const iv = query.get("interval");
  const interval = INTERVALS.includes(iv as Interval) ? (iv as Interval) : undefined;
  const [run, setRun] = useState<Run | null>(null);
  const [failed, setFailed] = useState(false);
  const [recent, setRecent] = useState<Run | null>(null);
  const [asking, setAsking] = useState(false);
  const stopAsking = useCallback(() => setAsking(false), []);

  useEffect(() => {
    if (!runId) return;
    api.run(runId).then(setRun, () => setFailed(true));
  }, [runId]);

  // 이 종목의 최근 끝난 분석(분석 기록을 주소로 열었으면 그것이 이미 보인다)
  useEffect(() => {
    if (runId) return;
    let alive = true;
    api.runs(1, 50).then(
      (p) => alive && setRecent(p.items.find((r) => sameTicker(r.ticker, ticker) && r.status === "done") ?? null),
      () => {},
    );
    return () => { alive = false; };
  }, [ticker, runId]);

  // 닫기: 앞 화면이 있으면 돌아가고, 주소로 바로 열었으면 분석 화면 · 대시보드로
  const close = () => {
    if (navigation.moved) window.history.back();
    else window.location.replace(runId ? `#/runs/${runId}` : "#/");
  };

  const plan: Plan | null = run?.plan ?? null;
  const shown = run?.ticker ?? ticker;
  // 시장 칸에서 열면 이름이 주소(name=)로 온다 — 확인 칸이 "NQ=F (NQ=F)" 가 아니라 "나스닥100 선물 (NQ=F)"
  const name = run?.options.name ?? query.get("name") ?? recent?.options.name ?? shown;
  const banner = (
    <div className="chart-view-deep">
      {!asking && (
        <button type="button" className="secondary small" onClick={() => setAsking(true)}
          title="에이전트들이 토론해 진입 · 손절 · 목표가를 냅니다(확인 한 번 · 구독 사용량 차감)">
          <span aria-hidden="true">🤖 </span>상세 분석 시작
        </button>
      )}
      {!asking && recent && <a className="small" href={`#/runs/${recent.id}`}>최근 분석({recent.trade_date}) 보기 →</a>}
      {asking && <DeepRunConfirm ticker={shown} name={name} onCancel={stopAsking} />}
    </div>
  );
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
          banner={banner}
        />
      </Suspense>
    </main>
  );
}
