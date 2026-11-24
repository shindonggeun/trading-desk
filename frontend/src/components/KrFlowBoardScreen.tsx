import { useEffect, useState } from "react";
import type { KrFlowBoardResponse, KrFlowMarket } from "../krFlowBoard";
import { krFlowLinks } from "../krFlowLinks";
import KrFlowBoardPanel from "./KrFlowBoardPanel";

export type LoadKrFlowBoard = (market: KrFlowMarket, signal: AbortSignal) => Promise<KrFlowBoardResponse>;

type Result = { market: KrFlowMarket; data: KrFlowBoardResponse | null; error: string | null };

export default function KrFlowBoardScreen({ load }: { load: LoadKrFlowBoard }) {
  const [market, setMarket] = useState<KrFlowMarket>("kospi");
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    load(market, controller.signal).then(
      (data) => {
        if (controller.signal.aborted) return;
        const valid = data?.market === market;
        setResult({ market, data: valid ? data : null, error: valid ? null : "요청한 시장과 응답이 다릅니다." });
      },
      () => {
        if (!controller.signal.aborted) setResult({ market, data: null, error: "국장 수급 현황판을 불러오지 못했습니다." });
      },
    );
    return () => controller.abort();
  }, [load, market, attempt]);

  const active = result?.market === market ? result : null;
  return (
    <KrFlowBoardPanel
      market={market}
      data={active?.data ?? null}
      loading={active === null}
      error={active?.error ?? null}
      onMarketChange={(next) => {
        if (next === market) return;
        setResult(null);
        setMarket(next);
      }}
      onRetry={() => { setResult(null); setAttempt((value) => value + 1); }}
      links={krFlowLinks}
    />
  );
}
