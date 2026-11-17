import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { money, percent, tone } from "../format";
import { sampleSummary } from "../paperSample";
import type { PaperSummary } from "../types";

/** #528 대시보드의 작은 모의 투자 카드 — 모의 잔고 · 총손익 · 이번 달. 누르면 #/paper. 실제 주문 없음 */
export default function PaperCard() {
  const [s, setS] = useState<PaperSummary | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.paperSummary().then(setS).catch((e: unknown) => {
      if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) setS(sampleSummary());   // API(#527) 전 개발 모드
      else setFailed(true);
    });
  }, []);
  if (failed) return <p className="muted small">모의 투자 결과를 불러오지 못했어요. <a href="#/paper">모의 투자 화면</a></p>;
  if (!s) return <p className="muted small">계산하는 중…</p>;
  return (
    <a className="paper-card" href="#/paper" aria-label="모의 투자 화면 열기">
      <span className="paper-badge">모의 · 실제 주문 없음</span>
      <span className="paper-card-row"><span className="muted small">모의 잔고</span>
        <strong className={tone(s.total_pnl)}>{money(s.balance, "KRW")}</strong></span>
      <span className="paper-card-row"><span className="muted small">시작 대비</span>
        <span className={tone(s.total_pnl)}>{money(s.total_pnl, "KRW", true)} ({percent(s.total_pnl_pct)})</span></span>
      <span className="paper-card-row"><span className="muted small">이번 달</span>
        <span className={tone(s.this_month.pnl)}>{money(s.this_month.pnl, "KRW", true)}</span></span>
      <span className="muted small">끝난 {s.trades.closed}건{s.win_rate != null ? ` · 수익 낸 비율 ${Math.round(s.win_rate * 100)}%` : ""} · 과거 성적, 보장 아님</span>
    </a>
  );
}
