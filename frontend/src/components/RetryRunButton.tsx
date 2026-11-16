import { useState } from "react";
import { api } from "../api";
import type { Run } from "../types";

/** #431 실패한 분석(배포 재기동 등)을 같은 종목 · 같은 설정으로 새로 제출한다 — 기존 POST /api/runs */
export default function RetryRunButton({ run }: { run: Run }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const o = run.options;
  async function retry() {
    setBusy(true);
    setError(null);
    try {
      const next = await api.createRun({
        ticker: run.ticker,
        name: o.name ?? undefined,
        profile: o.profile ?? undefined,
        horizon: o.horizon ?? undefined,
        trade_date: run.trade_date,
        analysts: o.analysts,
        use_portfolio: o.use_portfolio,
        output_language: o.output_language ?? undefined,
      });
      window.location.hash = `#/runs/${next.id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }
  return (
    <div className="run-retry">
      <button type="button" onClick={retry} disabled={busy}>
        {busy ? "제출 중…" : "다시 분석"}
      </button>
      <span className="muted small">같은 종목 · 같은 설정으로 새로 돌립니다</span>
      {error && <p className="error small">다시 제출하지 못했습니다 — {error}</p>}
    </div>
  );
}
