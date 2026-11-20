import { api, type HoldingLine, type HoldingRegime } from "./api";
import { price } from "./format";
import { usePoll } from "./poll";

/** #714 보유 종목 손절 · 익절 알림(#694)의 장세 · 선을 화면에 — 서버가 장중 1분마다 다시 판정한다 */
export const REGIME_KO: Record<HoldingRegime, string> = {
  trend_up: "상승 추세", trend_down: "하락 추세", range: "박스권", turning: "전환", unknown: "판단 보류",
};
const REFRESH_MS = 60_000;
const NEAR_PCT = 2;            // 손절선까지 이만큼 안이면 눈에 띄게

/** 손절선까지 거리, 예: "손절선까지 −4.2%" · "손절선 1.3% 아래" */
export function stopDistance(line: HoldingLine): string {
  const pct = Math.abs(line.to_stop_pct).toFixed(1);
  return line.to_stop_pct > 0 ? `손절선까지 −${pct}%` : `손절선 ${pct}% 아래`;
}

/** 근거 한 줄씩(툴팁) — 손절선의 근거, 익절 후보, 장세 근거 */
export function lineTitle(line: HoldingLine): string {
  return [
    `손절선: ${line.stop_basis}${line.stop != null ? ` (${price(line.stop)})` : ""}`,
    line.take_profit != null && line.tp_basis ? `익절 후보: ${line.tp_basis}` : null,
    ...line.basis,
  ].filter(Boolean).join("\n");
}

/** ticker → 그 보유 종목의 지난 판정. 감시가 꺼져 있거나 서버에 #694 가 없으면 비어 있다 */
export function useHoldingLines(): Map<string, HoldingLine> {
  const { data } = usePoll("holding-watch", () => api.holdingWatch(), REFRESH_MS);
  return new Map((data?.enabled ? data.holdings : []).map((l) => [l.ticker, l]));
}

/** 장세 배지 + 손절선까지 % (+ 박스권이면 익절 후보) */
export function HoldingBadge({ line }: { line: HoldingLine }) {
  return (
    <div className="hw-line small" title={lineTitle(line)}>
      <span className={`hw-regime hw-regime-${line.regime}`}>{REGIME_KO[line.regime] ?? line.label}</span>
      <span className={line.to_stop_pct <= NEAR_PCT ? "hw-stop hw-stop-near" : "hw-stop"}>{stopDistance(line)}</span>
      {line.take_profit != null && <span className="hw-tp">익절 후보 {price(line.take_profit)}</span>}
    </div>
  );
}
