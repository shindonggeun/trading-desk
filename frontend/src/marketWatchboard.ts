export type WatchboardGroup = "semiconductors" | "crypto" | "gold_miners";
export type WatchboardMarket = "kr" | "cme" | "us" | "jp" | "tw" | "crypto";
export type WatchboardMarketState = "open" | "break" | "closed" | "holiday" | "unknown";
export type WatchboardKind = "index" | "future" | "stock" | "adr" | "crypto" | "etf";
export type WatchboardRelation = "benchmark" | "related" | "reference";
export type WatchboardStatus = "available" | "delayed" | "missing";
export type WatchboardReason =
  | "source_error" | "wrong_instrument" | "source_unverified" | "timestamp_unverified"
  | "no_completed_bar" | "session_unverified" | "latest_session_missing";

export interface WatchboardBar {
  trade_date: string;
  at: string;
  closed_at: string;
  close: number;
}

export interface WatchboardItem {
  ticker: string;
  name: string;
  market: WatchboardMarket;
  timezone: string;
  currency: string;
  unit: "points" | "currency";
  kind: WatchboardKind;
  relation: WatchboardRelation;
  chart_href?: string | null;
  market_state: WatchboardMarketState;
  status: WatchboardStatus;
  reason: WatchboardReason | null;
  source: string | null;
  fetched_at: string | null;
  source_delay_min: number | null;
  latest: WatchboardBar | null;
  change_pct: number | null;
  history: WatchboardBar[];
}

export interface WatchboardPoint {
  trade_date: string;
  return_pct: number;
}

export interface WatchboardComparison {
  status: "available" | "partial" | "unavailable";
  base_date: string | null;
  series: { ticker: string; points: WatchboardPoint[] }[];
  excluded: string[];
}

export interface WatchboardResponse {
  group: WatchboardGroup;
  days: number;
  as_of: string;
  items: WatchboardItem[];
  comparison: WatchboardComparison;
}

/** Only the advertised daily chart for this exact instrument can become an app link. */
export function watchboardChartHref(item: Pick<WatchboardItem, "ticker" | "chart_href">): string | null {
  const href = item.chart_href?.startsWith("/#") ? item.chart_href.slice(1) : item.chart_href;
  const prefix = "#/charts?";
  if (!href?.startsWith(prefix) || !item.ticker) return null;
  const query = new URLSearchParams(href.slice(prefix.length));
  if (query.size !== 2 || query.get("t") !== item.ticker || query.get("iv") !== "1d") return null;
  return `${prefix}${new URLSearchParams({ t: item.ticker, iv: "1d" })}`;
}

const DAY_MS = 86_400_000;

export interface CalendarPoint {
  date: string;
  day: number;
  value: number;
}

/** Use actual civil dates in place of row indexes so absent sessions leave space in the plot. */
export function watchboardCalendarPoints(rows: readonly { date: string; value: number }[]): CalendarPoint[] {
  const points = new Map<number, CalendarPoint>();
  for (const { date, value } of rows) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(value)) continue;
    const stamp = Date.parse(`${date}T00:00:00Z`);
    if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 10) !== date) continue;
    const day = stamp / DAY_MS;
    points.set(day, { date, day, value });
  }
  return [...points.values()].sort((left, right) => left.day - right.day);
}

/** An absent civil date ends the line; its value is never interpolated or filled. */
export function watchboardSegments(points: readonly CalendarPoint[]): CalendarPoint[][] {
  const segments: CalendarPoint[][] = [];
  for (const point of points) {
    const current = segments.at(-1);
    if (current && current.at(-1)!.day + 1 === point.day) current.push(point);
    else segments.push([point]);
  }
  return segments;
}
