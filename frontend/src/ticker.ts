/**
 * #477: a bare KRX code (005930) and the same code with its market suffix (005930.KS · 005930.KQ) are
 * one instrument. The server adds the suffix (desk.api.clean_ticker), so what comes back can differ
 * from what was typed or put in the address: compare with this, not with ===.
 */
const KR_CODE = /^[0-9][0-9A-Z]{5}$/;
const KR_SUFFIXED = /^([0-9][0-9A-Z]{5})\.(KS|KQ)$/;

function norm(t: string | null | undefined): string {
  return (t ?? "").trim().toUpperCase();
}

/** Equal, or one is the other's bare KRX code. Two different suffixes stay different. */
export function sameTicker(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = norm(a);
  const y = norm(b);
  if (!x || !y) return false;
  if (x === y) return true;
  const sx = KR_SUFFIXED.exec(x);
  const sy = KR_SUFFIXED.exec(y);
  return (!!sx && KR_CODE.test(y) && sx[1] === y) || (!!sy && KR_CODE.test(x) && sy[1] === x);
}

/** The answer's items keyed by the ticker that was asked for: a stored bare code (005930, saved before the
 * server added the suffix) asked, its 005930.KS answered - the page reads it back under 005930. */
export function byAsked<T extends { ticker: string }>(asked: readonly string[], items: readonly T[]): Record<string, T> {
  const out: Record<string, T> = {};
  for (const it of items) out[askedOf(asked, it.ticker)] = it;
  return out;
}

/** The asked ticker an answered one belongs to (the answer's own when none matches). */
export function askedOf(asked: readonly string[], ticker: string): string {
  return asked.includes(ticker) ? ticker : (asked.find((a) => sameTicker(a, ticker)) ?? ticker);
}

/** Exactly ``ticker`` first, else the one that names the same instrument - a list can hold both a bare
 * code saved before #477 and its suffixed row; pick the one asked for. */
export function findTicker<T extends { ticker: string }>(list: readonly T[], ticker: string | null | undefined): T | undefined {
  const t = (ticker ?? "").trim().toUpperCase();
  return list.find((x) => x.ticker === t) ?? list.find((x) => sameTicker(x.ticker, t));
}
