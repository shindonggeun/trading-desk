import { useEffect, useState } from "react";
import { api } from "../api";
import type { ExtremeFearResponse, ExtremeFearSymbol } from "../extremeFear";
import ExtremeFearPanel from "./ExtremeFearPanel";

type Result = { symbol: ExtremeFearSymbol; data: ExtremeFearResponse | null; error: string | null };

export default function ExtremeFearResearch() {
  const [symbol, setSymbol] = useState<ExtremeFearSymbol>("SOXL");
  const [result, setResult] = useState<Result | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.extremeFear(symbol, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ symbol, data, error: null });
      },
      () => {
        if (!controller.signal.aborted) setResult({ symbol, data: null, error: "검증 결과를 불러오지 못했습니다." });
      },
    );
    return () => controller.abort();
  }, [symbol, attempt]);

  const active = result?.symbol === symbol ? result : null;
  return (
    <ExtremeFearPanel
      symbol={symbol}
      onSymbolChange={(next) => {
        if (next === symbol) return;
        setResult(null);
        setSymbol(next);
      }}
      data={active?.data ?? null}
      loading={active === null}
      error={active?.error ?? null}
      onRetry={() => { setResult(null); setAttempt((value) => value + 1); }}
    />
  );
}
