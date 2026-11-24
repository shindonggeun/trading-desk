const listing = /^\d{6}\.(KS|KQ)$/;

export function krFlowLinks(ticker: string) {
  const symbol = encodeURIComponent(ticker);
  return {
    chart: `#/charts?t=${symbol}&iv=1d`,
    analysis: `#/?ticker=${symbol}`,
    shortSale: `#/?flow=short&shortTicker=${symbol}`,
  };
}

export function shortSaleFlowTicker(route: string): string | null {
  if (!route.startsWith("#/?")) return null;
  const params = new URLSearchParams(route.slice(3));
  const ticker = params.get("shortTicker");
  return params.get("flow") === "short" && ticker && listing.test(ticker) ? ticker : null;
}

export function opensMarketFlow(route: string): boolean {
  return /[?&]flow=intraday(?:&|$)/.test(route) || shortSaleFlowTicker(route) !== null;
}
