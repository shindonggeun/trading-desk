import { todayKst } from "../calendar";
import { percent, tone } from "../format";
import type { MarketFlow } from "../types";

type ExpectedIndex = {
  price: number;
  change: number | null;
  rate: number | null;
  at: string;
};

export type MarketFlowWithExpected = MarketFlow & {
  expected?: { index: ExpectedIndex | null } | null;
};

const INDEX_NUMBER = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export default function PremarketExpected({ index }: { index: ExpectedIndex | null | undefined }) {
  // KIS stamps the received value in KST. Keep the last pre-open reading through the same day.
  if (!index || index.at.slice(0, 10) !== todayKst()) return null;

  const change = index.change === null ? null : `${index.change > 0 ? "+" : ""}${INDEX_NUMBER.format(index.change)}p`;

  return (
    <div className="flow-period small" role="group" aria-label="프리장 예상체결">
      <span className="pill flow-pill">프리장</span>
      <span className="flow-readout-item">
        <span className="muted">예상 지수</span>
        <strong>{INDEX_NUMBER.format(index.price)}</strong>
      </span>
      {(change !== null || index.rate !== null) && (
        <span className="flow-readout-item">
          <span className="muted">예상 등락</span>
          {change !== null && <strong className={tone(index.change)}>{change}</strong>}
          {index.rate !== null && <strong className={tone(index.rate)}>{percent(index.rate)}</strong>}
        </span>
      )}
    </div>
  );
}
