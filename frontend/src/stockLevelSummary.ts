import type { Candles } from "./api";
import type { BoardItem, ChartRead, ChartReadFrame, ChartReadLevel, SrLevel, SupportResistance } from "./types";
import { sameTicker } from "./ticker";

export const STOCK_LEVEL_INTERVALS = ["5m", "15m", "30m", "60m", "240m", "1d", "1w"] as const;
export type StockLevelInterval = typeof STOCK_LEVEL_INTERVALS[number];
export type StockLevelSide = "resistance" | "support";

const FRAME_ORDER: readonly StockLevelInterval[] = ["1d", "1w", "240m", "60m", "30m", "15m", "5m"];
export const STOCK_LEVEL_INTERVAL_LABEL: Record<StockLevelInterval, string> = {
  "5m": "5분봉", "15m": "15분봉", "30m": "30분봉", "60m": "1시간봉",
  "240m": "4시간봉", "1d": "일봉", "1w": "주봉",
};

interface StockQuote {
  ticker: string;
  price: number;
  changePct: number | null;
  kind: "live" | "delayed" | "close";
  at: string | null;
  atKind: "trade" | "observed" | "close";
}

interface StockLevel {
  price: number;
  side: StockLevelSide;
  name: string;
  refs?: ChartReadLevel["refs"];
  zone?: [number, number];
}

export interface StockLevelFrame {
  ticker: string;
  interval: StockLevelInterval;
  at: string | null;
  reference: number | null;
  referenceKind: "live" | "close" | "chart" | null;
  inProgress?: boolean;
  levels: StockLevel[];
  source: "chart-read" | "support-resistance";
}

function positive(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

export function stockLevelChartHref(ticker: string, interval: StockLevelInterval, price: number): string {
  return `#/charts?${new URLSearchParams({ t: ticker, iv: interval, p: String(price) })}`;
}

export function stockLevelQuote(ticker: string, item: BoardItem | null, read: ChartRead | null): StockQuote | null {
  const board = item && sameTicker(ticker, item.ticker) ? item : null;
  if (board?.state === "live" && positive(board.price) && board.quote_source !== "binance-estimate") {
    const live = ["kis", "toss", "nxt", "krx-overtime"].includes(board.quote_source ?? "");
    return { ticker: board.ticker, price: board.price, changePct: Number.isFinite(board.change_pct) ? board.change_pct : null,
      kind: live ? "live" : "delayed", at: board.traded_at ?? board.fetched_at, atKind: board.traded_at ? "trade" : "observed" };
  }

  const daily = read?.daily_reaction;
  const confirmed = daily?.status === "confirmed" && sameTicker(ticker, daily.ticker)
    && daily.bar && positive(daily.bar.close) && daily.session ? { ...daily, bar: daily.bar, session: daily.session } : null;
  const boardClose = positive(board?.close) ? board.close : null;
  const price = boardClose ?? confirmed?.bar.close;
  if (!positive(price)) return null;
  // The estimate's change describes a derivative price. Use a matching confirmed daily pair instead.
  const verified = confirmed && (boardClose == null || (
    sameTicker(confirmed.ticker, board?.ticker) && confirmed.session.trade_date === board?.close_date
    && confirmed.bar.close === price
  )) ? confirmed : null;
  const previous = verified?.previous_close?.price;
  return {
    ticker: boardClose == null ? verified?.ticker ?? ticker : board?.ticker ?? ticker,
    price,
    changePct: positive(previous) ? (price / previous - 1) * 100 : null,
    kind: "close",
    at: verified?.session.closes_at ?? board?.close_date ?? null,
    atKind: "close",
  };
}

const KRX_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
});

function dailyBarDate(ticker: string, at: string): string | null {
  if (!/^\d{4}-\d{2}-\d{2}/.test(at)) return null;
  // KRX daily candles are stamped at local midnight, 15:00 UTC the previous day.
  if (/\.(KS|KQ)$/i.test(ticker) && /(?:Z|[+-]\d{2}:\d{2})$/i.test(at)) {
    const stamp = new Date(at);
    if (!Number.isFinite(stamp.getTime())) return null;
    return KRX_DAY.format(stamp);
  }
  return at.slice(0, 10);
}

export function stockDailyClose(ticker: string, daily: Candles | null): StockQuote | null {
  if (!daily || daily.interval !== "1d" || !sameTicker(ticker, daily.ticker)) return null;
  const last = daily.bars.length - (daily.session_elapsed != null ? 2 : 1);
  const bar = daily.bars[last];
  if (!bar || !positive(bar.close)) return null;
  const previous = daily.bars[last - 1]?.close;
  return {
    ticker: daily.ticker, price: bar.close, changePct: positive(previous) ? (bar.close / previous - 1) * 100 : null,
    kind: "close", at: dailyBarDate(daily.ticker, bar.at), atKind: "close",
  };
}

function chartInterval(tf: string): StockLevelInterval | undefined {
  const normalized = tf === "1h" ? "60m" : tf === "4h" ? "240m" : tf;
  return STOCK_LEVEL_INTERVALS.find((item) => item === normalized);
}

function readLevels(frame: ChartReadFrame): StockLevel[] {
  const map = (items: ChartReadLevel[], side: StockLevelSide) =>
    items.filter(({ price }) => positive(price)).map(({ price, name, refs }) => ({ price, name, refs, side }));
  return [...map(frame.levels?.above ?? [], "resistance"), ...map(frame.levels?.below ?? [], "support")];
}

export function stockReadFrame(ticker: string, read: ChartRead | null): StockLevelFrame | null {
  if (!read || !sameTicker(ticker, read.ticker)) return null;
  for (const interval of FRAME_ORDER) {
    const frame = read.timeframes.find((item) => !item.error && chartInterval(item.timeframe) === interval);
    if (!frame) continue;
    const levels = readLevels(frame);
    if (levels.length) {
      return {
        ticker: read.ticker, interval, at: frame.as_of ?? null, reference: positive(frame.price) ? frame.price : null,
        referenceKind: positive(frame.price) ? "chart" : null, inProgress: frame.live_bar, levels, source: "chart-read",
      };
    }
  }
  return null;
}

function fallbackLevel(level: SrLevel): StockLevel {
  const { kind: side, basis: name, price, zone } = level;
  return { side, name, price, zone: zone && positive(zone[0]) && positive(zone[1]) && zone[0] < zone[1] ? zone : undefined };
}

export function stockFallbackFrame(ticker: string, support: SupportResistance | null): StockLevelFrame | null {
  if (!support || !sameTicker(ticker, support.ticker)) return null;
  for (const interval of ["1d", "1w"] as const) {
    const levels = support.levels.filter((level) => level.timeframe === interval && positive(level.price)).map(fallbackLevel);
    if (levels.length) {
      return { ticker: support.ticker, interval, at: support.as_of || null,
        reference: positive(support.reference_price) ? support.reference_price : null,
        referenceKind: positive(support.reference_price) ? support.reference : null, levels, source: "support-resistance" };
    }
  }
  return null;
}

export function stockNearestLevels(frame: StockLevelFrame, side: StockLevelSide, quote: StockQuote | null) {
  const reference = sameTicker(frame.ticker, quote?.ticker) ? quote?.price ?? frame.reference : frame.reference;
  const eligible = frame.levels.filter((level) => level.side === side && (reference == null || (
    side === "resistance" ? (level.zone?.[1] ?? level.price) >= reference : (level.zone?.[0] ?? level.price) <= reference
  )));
  // For a range containing the current price, the distance to the range is zero. The link still selects
  // the exact reference price provided by the source.
  const distance = (level: StockLevel) => {
    if (reference == null) return null;
    const nearest = level.zone ? Math.max(level.zone[0], Math.min(reference, level.zone[1])) : level.price;
    return (nearest / reference - 1) * 100;
  };
  const sorted = eligible.map((level) => ({ ...level, distancePct: distance(level) }))
    .sort((a, b) => Math.abs(a.distancePct ?? 0) - Math.abs(b.distancePct ?? 0)
      || (side === "resistance" ? a.price - b.price : b.price - a.price));
  return Array.from({ length: 3 }, (_, index) => sorted[index] ?? null);
}
