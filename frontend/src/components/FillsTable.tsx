import type { ReviewRow } from "../api";
import { price } from "../format";

/** 기간 합계 — 수량 가중 매수 · 매도 평균가, 실현 손익률(매도 체결의 손익 합 ÷ 그 매입 원가 합). */
export function fillsSummary(fills: ReviewRow[]): { buyAvg: number | null; sellAvg: number | null; realizedPct: number | null } {
  const avg = (side: string) => {
    const rows = fills.filter((f) => f.side === side && f.quantity > 0);
    const q = rows.reduce((s, f) => s + f.quantity, 0);
    return q > 0 ? rows.reduce((s, f) => s + f.price * f.quantity, 0) / q : null;
  };
  let pnl = 0;
  let basis = 0;
  for (const f of fills) {
    const r = f.realized;
    if (f.side !== "SELL" || !r || r.realized_pnl == null || !r.realized_rate) continue;
    pnl += r.realized_pnl;
    basis += r.realized_pnl / (r.realized_rate / 100);
  }
  return { buyAvg: avg("BUY"), sellAvg: avg("SELL"), realizedPct: basis > 0 ? (100 * pnl) / basis : null };
}

function signedPct(v: number | null | undefined): string {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/**
 * 내 체결 표(#334) — 이 종목 · 차트에 보이는 기간만, 최근 순. 행을 누르면 차트가 그 날짜로 가고 그 봉을 강조한다.
 * 넓은 화면은 차트 옆, 좁은 화면은 차트 아래(styles.css .chart-and-fills).
 */
export default function FillsTable({ fills, picked, onPick }: {
  fills: ReviewRow[];
  picked: string | null;
  onPick: (filledAt: string) => void;
}) {
  const rows = [...fills].sort((a, b) => (a.filled_at < b.filled_at ? 1 : -1));
  const sum = fillsSummary(fills);
  return (
    <section className="fills-table" aria-label="내 체결">
      <p className="small fills-sum">
        <strong>내 체결 {fills.length}건</strong>
        {sum.buyAvg != null && <> · 매수 평균 {price(sum.buyAvg)}</>}
        {sum.sellAvg != null && <> · 매도 평균 {price(sum.sellAvg)}</>}
        {sum.realizedPct != null && (
          <> · 실현 <span className={sum.realizedPct >= 0 ? "pnl-up" : "pnl-down"}>{signedPct(sum.realizedPct)}</span></>
        )}
      </p>
      <div className="fills-scroll">
        <table className="small">
          <thead>
            <tr><th>날짜</th><th>구분</th><th>가격</th><th>수량</th><th title="그날 분석 진입가 대비">타점 대비</th></tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr
                key={f.order_id + f.filled_at}
                className={picked === f.filled_at ? "picked" : undefined}
                tabIndex={0}
                onClick={() => onPick(f.filled_at)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(f.filled_at); } }}
                title="차트에서 이 날짜 보기"
              >
                <td>{f.filled_at.slice(5, 10)}</td>
                <td className={f.side === "BUY" ? "pnl-up" : "pnl-down"}>{f.side === "BUY" ? "매수" : f.side === "SELL" ? "매도" : f.side}</td>
                <td>{price(f.price)}</td>
                <td>{f.quantity}</td>
                <td className="muted">{f.entry_gap_pct == null ? "—" : signedPct(f.entry_gap_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
