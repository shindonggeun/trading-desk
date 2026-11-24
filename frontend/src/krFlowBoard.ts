import type { FlowRecord } from "./types";

export type KrFlowMarket = "kospi" | "kosdaq";
export type KrFlowStatus = "available" | "partial" | "no_data" | "source_unavailable" | "stale"
  | "not_published" | "scope_mismatch" | "unit_mismatch" | "missing_input" | "zero_denominator" | "inconsistent_amounts";

export interface KrFlowPublication {
  status: KrFlowStatus;
  as_of: string | null;
  source: string | null;
  published_at: string | null;
}

export interface KrFlowTurnover extends KrFlowPublication {
  venue: string | null;
  unit: string | null;
  value_krw: number | null;
}

export interface KrFlowForeignFlow extends KrFlowPublication {
  venue: string | null;
  unit: string | null;
  buy_krw: number | null;
  sell_krw: number | null;
  net_krw: number | null;
}

export interface KrFlowOwnership extends KrFlowPublication {
  unit: "percent";
  ratio_pct: number | null;
}

export interface KrFlowDay {
  date: string;
  turnover: KrFlowTurnover;
  foreign_flow: KrFlowForeignFlow;
  ownership: KrFlowOwnership;
  participation_pct: number | null;
  buy_sell_imbalance: number | null;
  participation_status: KrFlowStatus;
  imbalance_status: KrFlowStatus;
}

export interface KrFlowRankedItem {
  ticker: string;
  name: string | null;
  rank: number;
  latest: KrFlowDay;
  latest_ownership: KrFlowOwnership;
}

export interface KrFlowFavorite {
  ticker: string;
  name: string | null;
  history: KrFlowDay[];
  latest_ownership: KrFlowOwnership;
}

type MarketObservation = Omit<KrFlowPublication, "status"> & { unit: string };

export interface KrFlowBoardResponse {
  market: KrFlowMarket;
  venue: "krx";
  status: KrFlowStatus;
  latest_session: string;
  sessions: string[];
  ranking: KrFlowPublication & { items: KrFlowRankedItem[] };
  favorites: { status: KrFlowStatus; items: KrFlowFavorite[] };
  market_summary: {
    scope: "market";
    status: KrFlowStatus;
    flow: (MarketObservation & { investors: FlowRecord["investors"] }) | null;
    index: (MarketObservation & { value: number; change_pct?: number | null }) | null;
  };
  sources: Record<"turnover" | "foreign_flow" | "ownership", { status: KrFlowStatus }>;
}

export type KrFlowBoardLinks = (ticker: string) => { chart: string; analysis: string; shortSale: string };
export type KrFlowMetric = "participation" | "imbalance" | "ownership";

const valid = (value: number | null, min: number, max: number): value is number =>
  value !== null && Number.isFinite(value) && value >= min && value <= max;

export const KR_FLOW_METRICS: Record<KrFlowMetric, string> = {
  participation: "외국인 참여율", imbalance: "매수·매도 우위", ownership: "외국인 지분율",
};

export const KR_FLOW_STATE: Record<KrFlowStatus, string> = {
  available: "확인", partial: "일부 자료", no_data: "자료 없음", source_unavailable: "원천 미연결", stale: "이전 자료",
  not_published: "미공표", scope_mismatch: "시장 범위 불일치", unit_mismatch: "단위 불일치",
  missing_input: "매수·매도 자료 불완전", zero_denominator: "분모 0으로 계산 불가", inconsistent_amounts: "금액 불일치",
};

export function krFlowPercent(value: number | null, signed = false) {
  if (value === null || !Number.isFinite(value)) return "자료 없음";
  const magnitude = Math.abs(value);
  const sign = value < 0 ? "−" : signed && value > 0 ? "+" : "";
  if (magnitude > 0 && magnitude < 0.000001) return `${sign}<0.000001%`;
  const maximumFractionDigits = magnitude < 0.01 ? 6 : 2;
  return `${sign}${magnitude.toLocaleString("ko-KR", { maximumFractionDigits })}%`;
}

export function krFlowMetricStatus(day: KrFlowDay, metric: KrFlowMetric): KrFlowStatus {
  if (metric === "participation") return day.participation_status;
  if (metric === "imbalance") return day.imbalance_status;
  return day.ownership.status;
}

export function krFlowMetricValue(day: KrFlowDay, metric: KrFlowMetric): number | null {
  if (krFlowMetricStatus(day, metric) !== "available") return null;
  if (metric === "participation") return valid(day.participation_pct, 0, 100) ? day.participation_pct : null;
  if (metric === "imbalance") return valid(day.buy_sell_imbalance, -1, 1) ? day.buy_sell_imbalance * 100 : null;
  return valid(day.ownership.ratio_pct, 0, 100) ? day.ownership.ratio_pct : null;
}

export function krFlowScatter(items: readonly KrFlowRankedItem[]) {
  const points = items.flatMap((item) => {
    const participation = krFlowMetricValue(item.latest, "participation");
    const imbalance = krFlowMetricValue(item.latest, "imbalance");
    const turnover = item.latest.turnover.value_krw;
    if (participation === null || imbalance === null || !valid(turnover, 0, Infinity)
      || item.latest.turnover.status !== "available") return [];
    return [{ item, participation, imbalance, turnover }];
  });
  const values = points.map(({ participation }) => participation).sort((a, b) => a - b);
  const middle = Math.floor(values.length / 2);
  const median = !values.length ? null : values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2;
  const maxX = Math.min(100, Math.max(10, Math.ceil(Math.max(0, ...values) / 10) * 10));
  const maxTurnover = Math.max(0, ...points.map(({ turnover }) => turnover));
  return { points, median, maxX, maxTurnover };
}

export interface KrFlowSeriesPoint {
  index: number;
  sample: KrFlowDay;
  value: number;
}

/** The axis is the complete session list. Missing observations split the line. */
export function krFlowHistorySegments(sessions: readonly string[], history: readonly KrFlowDay[], metric: KrFlowMetric) {
  const byDate = new Map(history.map((sample) => [sample.date, sample]));
  const segments: KrFlowSeriesPoint[][] = [];
  sessions.forEach((date, index) => {
    const sample = byDate.get(date);
    const value = sample ? krFlowMetricValue(sample, metric) : null;
    if (!sample || value === null) return;
    const previous = segments.at(-1);
    const point = { index, sample, value };
    if (previous && previous[previous.length - 1].index === index - 1) previous.push(point);
    else segments.push([point]);
  });
  return segments;
}
