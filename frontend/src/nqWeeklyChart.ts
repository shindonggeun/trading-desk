const NEW_YORK = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
});
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const UTC_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/i;

/** Mirrors the weekly evidence contract's New York Monday labels. */
export function nqMondayLabel(stamp: string): string | null {
  let label = stamp;
  if (!DATE_ONLY.test(label)) {
    const time = Date.parse(stamp);
    if (!UTC_OFFSET.test(stamp) || !Number.isFinite(time)) return null;
    const parts = NEW_YORK.formatToParts(time);
    const part = (type: Intl.DateTimeFormatPartTypes) => parts.find(item => item.type === type)?.value;
    label = [part("year"), part("month"), part("day")].join("-");
  }
  const day = new Date(label + "T00:00:00Z");
  return day.getUTCDay() === 1 && day.toISOString().slice(0, 10) === label ? label : null;
}

export function nqMarkedRows<T>(rows: readonly T[], weeks: readonly string[], stampOf: (row: T) => string) {
  const wanted = new Set(weeks);
  const found = new Map<string, T>();
  for (const row of rows) {
    const label = nqMondayLabel(stampOf(row));
    if (label && wanted.has(label)) found.set(label, row);
  }
  return found;
}
