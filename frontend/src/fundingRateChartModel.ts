import type { BusinessDay, LineData } from "lightweight-charts";
import type { FundingDay, FundingSettlement } from "./fundingRateTypes";

const UTC_DAY_MS = 86_400_000;

export function fundingDayTime(date: string): BusinessDay {
  const [year, month, day] = date.split("-").map(Number);
  return { year, month, day };
}

export type FundingChartRow = FundingDay & { mark: number | null };

export function fundingChartRows(daily: FundingDay[], settlements: FundingSettlement[]): FundingChartRow[] {
  const latest = new Map<string, FundingSettlement>();
  for (const settlement of settlements) {
    const timestamp = Date.parse(settlement.settled_at);
    if (!Number.isFinite(timestamp)) continue;
    const day = new Date(timestamp).toISOString().slice(0, 10);
    const previous = latest.get(day);
    const previousTime = previous ? Date.parse(previous.settled_at) : -Infinity;
    // Regular and special settlements may share a timestamp. Keep a reported mark at that time.
    const preferMarkAtSameTime = settlement.mark_price !== null
      && (previous?.mark_price === null || (settlement.rate_type === "regular" && previous?.rate_type === "special"));
    if (timestamp > previousTime || (timestamp === previousTime && preferMarkAtSameTime)) {
      latest.set(day, settlement);
    }
  }
  return daily.map(day => ({ ...day, mark: latest.get(day.date)?.mark_price ?? null }));
}

export function fundingLineRuns(rows: FundingChartRow[], valueOf: (row: FundingChartRow) => number | null): LineData<BusinessDay>[][] {
  const runs: LineData<BusinessDay>[][] = [];
  let run: LineData<BusinessDay>[] = [];
  let previous = NaN;
  for (const row of rows) {
    const value = valueOf(row);
    const stamp = Date.parse(row.date);
    if (value === null || stamp - previous !== UTC_DAY_MS) {
      if (run.length) runs.push(run);
      run = [];
    }
    if (value !== null) run.push({ time: fundingDayTime(row.date), value });
    previous = stamp;
  }
  if (run.length) runs.push(run);
  return runs;
}
