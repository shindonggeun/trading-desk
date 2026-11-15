/**
 * #380 실시간 차트의 봉 간격 — 1분 흐름(/api/live/stream)을 고른 간격의 봉으로 접는다.
 * 경계는 서버(/api/candles, #379)와 같다: 정규장 시작(국장 09:00 KST · 미장 09:30 뉴욕)부터 N분씩,
 * 국장 15:30 종가 단일가는 마지막 봉에 넣는다. 장 전 · 장 뒤(NXT · 프리 · 애프터)는 그 구간 시작부터 따로 센다.
 */
import type { CandleInterval } from "./api";

export const LIVE_INTERVALS: { iv: CandleInterval; label: string; minutes: number }[] = [
  { iv: "1m", label: "1분", minutes: 1 },
  { iv: "5m", label: "5분", minutes: 5 },
  { iv: "15m", label: "15분", minutes: 15 },
  { iv: "30m", label: "30분", minutes: 30 },
  { iv: "60m", label: "1시간", minutes: 60 },
  { iv: "120m", label: "2시간", minutes: 120 },
  { iv: "240m", label: "4시간", minutes: 240 },
];

const KEY = "desk.live.interval";

export function loadLiveInterval(): CandleInterval {
  try {
    const v = localStorage.getItem(KEY);
    return LIVE_INTERVALS.some((i) => i.iv === v) ? (v as CandleInterval) : "1m";
  } catch {
    return "1m";
  }
}

export function saveLiveInterval(iv: CandleInterval) {
  try {
    localStorage.setItem(KEY, iv);
  } catch {
    /* 기억만 못 한다 */
  }
}

export function minutesOf(iv: CandleInterval): number {
  return LIVE_INTERVALS.find((i) => i.iv === iv)?.minutes ?? 1;
}

const isKrx = (ticker: string) => /\.(KS|KQ)$/i.test(ticker);

const nyClock = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

/** 그 시장 시계로 자정부터 몇 분인가 */
function minuteOfDay(ms: number, ticker: string): number {
  if (isKrx(ticker)) {
    const kst = new Date(ms + 9 * 3600_000);
    return kst.getUTCHours() * 60 + kst.getUTCMinutes();
  }
  const parts = nyClock.formatToParts(new Date(ms));
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0);
  return get("hour") * 60 + get("minute");
}

/** 정규장 [시작, 끝] (분) — 끝 분은 마지막 봉에 들어간다(국장 15:30 종가) */
function session(ticker: string): [number, number, number] {
  // [장 전 구간 시작, 정규장 시작, 정규장 끝]
  return isKrx(ticker) ? [8 * 60, 9 * 60, 15 * 60 + 30] : [4 * 60, 9 * 60 + 30, 16 * 60];
}

/** ms 가 든 봉의 시작 시각(ms). 1분이면 그 분의 시작. */
export function bucketStart(ms: number, ticker: string, minutes: number): number {
  const minuteStart = Math.floor(ms / 60_000) * 60_000;
  if (minutes <= 1) return minuteStart;
  const m = minuteOfDay(minuteStart, ticker);
  const [pre, open, close] = session(ticker);
  let anchor: number;
  let at = m;
  if (m >= open && m <= close) {
    anchor = open;
    at = Math.min(m, close - 1); // 종가 단일가는 마지막 봉에
  } else if (m > close) anchor = close;
  else anchor = m >= pre ? pre : 0;
  const start = anchor + Math.floor((at - anchor) / minutes) * minutes;
  return minuteStart - (m - start) * 60_000;
}

export type Bar = { at: string; open: number; high: number; low: number; close: number; volume: number };

/**
 * 진행 중인 봉 하나. 처음 읽은 이력의 봉(base)이 있으면 시가는 그것을 두고, 그 뒤 흐름의 1분봉(같은 분은 마지막 것)과
 * 고가 · 저가를 합친다. 거래량은 둘 중 큰 쪽 — 서로 어느 분까지 담았는지 모르므로(서버 #379 와 같은 규칙).
 */
export class LiveBucket {
  readonly start: number;
  private base: Bar | null;
  private mins = new Map<number, Bar>();
  private tickHigh = -Infinity;
  private tickLow = Infinity;
  private lastPrice: number | null = null;

  constructor(start: number, base: Bar | null) {
    this.start = start;
    this.base = base;
  }

  addMinute(ms: number, bar: Bar) {
    this.mins.set(ms, bar);
    this.lastPrice = null; // 새 1분봉의 종가가 더 새 값이다
  }

  tick(p: number) {
    this.lastPrice = p;
    this.tickHigh = Math.max(this.tickHigh, p);
    this.tickLow = Math.min(this.tickLow, p);
  }

  bar(): Bar | null {
    const mins = [...this.mins.entries()].sort((a, b) => a[0] - b[0]).map(([, b]) => b);
    const first = this.base ?? mins[0];
    if (!first) return null;
    const all = this.base ? [this.base, ...mins] : mins;
    const last = mins.length ? mins[mins.length - 1] : this.base!;
    const close = this.lastPrice ?? last.close;
    return {
      at: new Date(this.start).toISOString(),
      open: first.open,
      high: Math.max(...all.map((b) => b.high), this.tickHigh, close),
      low: Math.min(...all.map((b) => b.low), this.tickLow, close),
      close,
      volume: Math.max(this.base?.volume ?? 0, mins.reduce((s, b) => s + b.volume, 0)),
    };
  }
}
