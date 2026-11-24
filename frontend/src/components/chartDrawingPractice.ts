import type { DrawingAnchor, DrawingKind } from "../chartDrawingModel";

export type DrawingCandle = {
  time: DrawingAnchor["time"];
  stamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

export type PracticeKind = "horizontal" | "rising" | "falling" | "channel";
type Step = { anchor: DrawingAnchor; instruction: string };
export type DrawingPractice = {
  kind: DrawingKind;
  steps: Step[];
  review: string;
  reviewAnchor?: DrawingAnchor;
};

type Pivot = { candle: DrawingCandle; index: number; price: number; side: "high" | "low" };
const anchor = ({ candle, price }: Pivot): DrawingAnchor => ({ time: candle.time, stamp: candle.stamp, price });
const near = (a: number, b: number) => Math.abs(a - b) <= Math.max(a, b) * 0.009;

function pivots(candles: DrawingCandle[]): Pivot[] {
  return candles.flatMap((candle, index) => {
    if (index < 1 || index >= candles.length - 1) return [];
    const prev = candles[index - 1];
    const next = candles[index + 1];
    const result: Pivot[] = [];
    for (const side of ["high", "low"] as const) {
      const value = candle[side];
      const a = prev[side];
      const b = next[side];
      const confirmed = side === "high" ? value >= a && value >= b && (value > a || value > b)
        : value <= a && value <= b && (value < a || value < b);
      if (confirmed && Number.isFinite(value) && value > 0) result.push({ candle, index, price: value, side });
    }
    return result;
  });
}

function trendPair(points: Pivot[], rising: boolean): [Pivot, Pivot] | null {
  const side = rising ? "low" : "high";
  for (let i = points.length - 1; i >= 1; i--) {
    const later = points[i];
    if (later.side !== side) continue;
    for (let j = i - 1; j >= 0; j--) {
      const first = points[j];
      const spacing = later.index - first.index;
      if (spacing > 42) break;
      if (first.side !== side || spacing < 3) continue;
      if (rising ? later.price > first.price : later.price < first.price) return [first, later];
    }
  }
  return null;
}

function trendSteps(pair: [Pivot, Pivot], rising: boolean): Step[] {
  return [
    { anchor: anchor(pair[0]), instruction: rising ? "먼저 확정된 저점의 꼬리 끝을 선택하세요." : "먼저 확정된 고점의 꼬리 끝을 선택하세요." },
    { anchor: anchor(pair[1]), instruction: rising ? "나중에 나온 더 높은 저점의 꼬리를 선택하세요." : "나중에 나온 더 낮은 고점의 꼬리를 선택하세요." },
  ];
}

export function findDrawingPractice(candles: DrawingCandle[], requested: PracticeKind, logarithmic: boolean): DrawingPractice | null {
  // 마지막 봉의 진행 여부와 무관하게 양옆의 봉으로 확인된 반응을 사용한다.
  const recent = candles.slice(-72, -1);
  const points = pivots(recent);
  if (requested === "horizontal") {
    for (let i = points.length - 1; i >= 1; i--) {
      const level = points[i];
      const repeat = points.slice(0, i).reverse().find(point => point.side === level.side
        && level.index - point.index >= 3 && near(level.price, point.price));
      if (!repeat) continue;
      const side = level.side === "high" ? "저항" : "지지";
      return {
        kind: "horizontal",
        steps: [{ anchor: anchor(level), instruction: "이전 " + repeat.candle.stamp.slice(0, 10)
          + " 봉과 비슷한 " + side + " 가격입니다. 표시된 꼬리에서 수평선을 시작하세요." }],
        review: "두 봉이 같은 가격대에 반응했는지 수평선과 비교하세요. 꼬리끼리 비교하면 몸통 종가와 혼동하지 않습니다.",
        reviewAnchor: anchor(repeat),
      };
    }
    return null;
  }

  const rising = requested !== "falling";
  const pair = trendPair(points, rising) ?? (requested === "channel" ? trendPair(points, false) : null);
  if (!pair) return null;
  const actualRising = pair[0].side === "low";
  const steps = trendSteps(pair, actualRising);
  if (requested !== "channel") {
    return { kind: "trend", steps, review: "두 기준봉을 시간순으로 확인하세요. 꼬리 대신 몸통을 쓰려면 두 점 모두 몸통 기준으로 수정하세요." };
  }

  const [first, second] = pair;
  const scale = logarithmic ? Math.log : (p: number) => p;
  const unscale = logarithmic ? Math.exp : (p: number) => p;
  const priceAt = (i: number) => scale(first.price) + (scale(second.price) - scale(first.price))
    * (i - first.index) / (second.index - first.index);
  const opposite = points.find(point => point.side !== first.side && point.index > first.index
    && point.index !== second.index && (actualRising ? scale(point.price) > priceAt(point.index)
      : scale(point.price) < priceAt(point.index)));
  if (!opposite) return null;
  steps.push({ anchor: anchor(opposite), instruction: "기준선 반대편의 표시된 꼬리를 찍어 채널 너비를 정하세요." });
  const offset = scale(opposite.price) - priceAt(opposite.index);
  const later = points.filter(point => point.index > Math.max(second.index, opposite.index));
  const touch = later.find(point => near(point.price, unscale(priceAt(point.index)
    + (point.side === first.side ? 0 : offset))));
  const breakIndex = recent.findIndex((candle, index) => index > Math.max(second.index, opposite.index)
    && (scale(candle.close) > Math.max(priceAt(index), priceAt(index) + offset)
      || scale(candle.close) < Math.min(priceAt(index), priceAt(index) + offset)));
  const review = (touch ? "표시한 뒤쪽 반응이 기준선이나 반대편 경계에 닿는지 확인하세요. "
    : "뒤쪽에 채널과 가까운 반응점이 부족합니다. 새로 확정된 봉의 세 번째 반응을 확인하세요. ")
    + (breakIndex >= 0 ? recent[breakIndex].stamp.slice(0, 10) + " 봉은 채널 밖에서 마감했습니다. "
      : "확인 구간에서 채널 밖 종가가 발견되지 않았습니다. ")
    + "꼬리 돌출과 종가 이탈을 구분하세요.";
  return { kind: "channel", steps, review, reviewAnchor: touch ? anchor(touch) : undefined };
}
