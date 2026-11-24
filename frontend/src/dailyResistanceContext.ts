import type { StockSocialItem, StockSocialResponse } from "./stockSocial";

const NEWS_PRODUCTS = new Set(["SOXX", "SOXL", "^NDX", "NQ=F"]);
const PRODUCT_MENTIONS = /\bSOX[XL][\s/_:\p{Dash_Punctuation}]*USD[TC](?:\.P)?\b|\bSOX[XL]\b|\^NDX\b|\bNQ=F\b|\bNAS100(?:\.CFD)?\b/giu;
const TIMEZONE_SUFFIX = /(?:Z|[+-]\d{2}:\d{2})$/i;
const MAX_NEWS = 3;

function timestamp(value: string | null | undefined): number {
  return value && TIMEZONE_SUFFIX.test(value) ? Date.parse(value) : NaN;
}

export function canReadDailyResistanceNews(ticker: string, observedAt: string | null | undefined): boolean {
  return NEWS_PRODUCTS.has(ticker) && Number.isFinite(timestamp(observedAt));
}

export function dailyResistanceNewsUnavailable(ticker: string, data: StockSocialResponse): boolean {
  if (data?.ticker !== ticker || !Array.isArray(data.items)) return false;
  const source = Array.isArray(data.sources) ? data.sources.find((item) => item?.id === "gdelt") : undefined;
  const status = source?.status ?? data.news?.status;
  return status === "unavailable" || status === "stale";
}

export function dailyResistanceNews(
  ticker: string, observedAt: string | null | undefined, data: StockSocialResponse | null | undefined,
): StockSocialItem[] {
  if (!canReadDailyResistanceNews(ticker, observedAt) || data?.ticker !== ticker || !Array.isArray(data.items)) {
    return [];
  }
  const cutoff = timestamp(observedAt);
  const headlines: { item: StockSocialItem; discovered: number }[] = [];
  for (const item of data.items) {
    if (item?.kind !== "news" || item.relevance !== "direct" || item.source !== "gdelt" || item.time_basis !== "gdelt_seen") {
      continue;
    }
    if (typeof item.title !== "string" || !item.title.trim()) continue;
    // A direct match can still contain two tickers or an explicit ETF-linked contract.
    const symbols = item.title.match(PRODUCT_MENTIONS) ?? [];
    if (symbols.some((symbol) => symbol.toUpperCase() !== ticker)) continue;
    const discovered = timestamp(item.at);
    if (Number.isFinite(discovered) && discovered <= cutoff) headlines.push({ item, discovered });
  }
  return headlines.sort((a, b) => b.discovered - a.discovered).slice(0, MAX_NEWS).map(({ item }) => item);
}
