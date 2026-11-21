import { todayKst } from "../calendar";
import { percent, price, tone } from "../format";
import type { MarketFlow } from "../types";

type ExpectedQuote = {
  price: number;
  change: number | null;
  rate: number | null;
  at: string;
};

type ExpectedStock = ExpectedQuote & { code: string; name: string };

export type MarketFlowWithExpected = MarketFlow & {
  expected?: { index: ExpectedQuote | null; stocks?: ExpectedStock[] } | null;
};

const INDEX_NUMBER = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export default function PremarketExpected({ index, stocks }: {
  index?: ExpectedQuote | null;
  stocks?: ExpectedStock[];
}) {
  // KIS stamps the received value in KST. Keep the last pre-open reading through the same day.
  const today = todayKst();
  const todayIndex = index?.at.slice(0, 10) === today ? index : null;
  const todayStocks = stocks?.filter((stock) => stock.at.slice(0, 10) === today) ?? [];
  if (!todayIndex && todayStocks.length === 0) return null;

  const indexChange = todayIndex?.change ?? null;
  const change = indexChange === null ? null : `${indexChange > 0 ? "+" : ""}${INDEX_NUMBER.format(indexChange)}p`;

  return (
    <div className="flow-period small" role="group" aria-label="프리장 예상체결">
      <span className="pill flow-pill">프리장</span>
      {todayIndex && (
        <span className="flow-readout-item">
          <span className="muted">예상 지수</span>
          <strong>{INDEX_NUMBER.format(todayIndex.price)}</strong>
        </span>
      )}
      {todayIndex && (change !== null || todayIndex.rate !== null) && (
        <span className="flow-readout-item">
          <span className="muted">예상 등락</span>
          {change !== null && <strong className={tone(indexChange)}>{change}</strong>}
          {todayIndex.rate !== null && <strong className={tone(todayIndex.rate)}>{percent(todayIndex.rate)}</strong>}
        </span>
      )}
      {todayStocks.map((stock) => (
        <span className="flow-readout-item" key={stock.code}>
          <span className="muted">NXT {stock.name}</span>
          <strong>{price(stock.price)}원</strong>
          {stock.rate !== null && <strong className={tone(stock.rate)}>{percent(stock.rate)}</strong>}
        </span>
      ))}
    </div>
  );
}
