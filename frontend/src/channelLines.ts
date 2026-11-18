import type { ChannelState, LineBreakRate, LineRate, LinesReport, LineTf, ParallelChannel, ShortTrendline } from "./types";

/** #612 평행 채널 · 짧은 추세선(#611)을 그리는 시간봉 — 차트 주기 이름 그대로(60m · 240m) */
export const LINE_TFS: readonly LineTf[] = ["5m", "15m", "60m", "240m", "1d"];
export const isLineTf = (iv: string): iv is LineTf => (LINE_TFS as readonly string[]).includes(iv);
export const TF_KO: Record<string, string> = { "5m": "5분봉", "15m": "15분봉", "60m": "1시간봉", "240m": "4시간봉", "1d": "일봉" };
export const DIR_KO: Record<ParallelChannel["direction"], string> = { up: "상승", down: "하락", flat: "횡보" };
export const CHANNEL_STATE_KO: Record<ChannelState, string> = {
  inside: "채널 안", near_upper: "상단 가까이", near_lower: "하단 가까이", above: "채널 위로 벗어남", below: "채널 아래로 벗어남",
};
export const TREND_STATE_KO: Record<ShortTrendline["state"], string> = { intact: "유지", testing: "선 근처", broken: "이탈" };
export const TREND_KO: Record<ShortTrendline["kind"], string> = { support: "상승 추세선", resistance: "하락 추세선" };
export const NOT_ADVICE = "선은 지난 가격으로 그은 산수예요 — 앞날 예측도, 매수 · 매도 추천도 아니에요.";

export const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export const isBreakRate = (r: LineRate | LineBreakRate): r is LineBreakRate => "follow_rate" in r;

/** 그 선의 "과거에 이랬다" 한 줄 — 기준선(같은 기울기 · 거리의 아무 선)과 나란히 */
export function rateText(r: LineRate | LineBreakRate | null | undefined, tf: string): string | null {
  if (!r || !r.n) return null;
  const h = `${r.horizon_bars}봉`;
  if (isBreakRate(r)) {
    return `과거 ${TF_KO[tf] ?? tf}에서 막 이탈한 추세선 ${r.n}번 중 ${h} 안에 그 방향으로 1 ATR 더 간 것 ${pct(r.follow_rate)} · 아무 선 ${pct(r.baseline_rate)}`;
  }
  return `과거 닿은 ${r.n}번 중 ${h} 안에 반등 ${pct(r.bounce_rate)} · 이탈 ${pct(r.break_rate)} · 아무 선 반등 ${pct(r.baseline_bounce_rate)}`;
}

/** 채널 이름 — "1시간봉 하락 채널" */
export const channelTitle = (c: ParallelChannel) => `${TF_KO[c.timeframe] ?? c.timeframe} ${DIR_KO[c.direction]} 채널`;
export const trendTitle = (t: ShortTrendline) => `${TF_KO[t.timeframe] ?? t.timeframe} ${TREND_KO[t.kind]}`;

type Bar = { at: string; high: number; low: number; close: number };

/**
 * DEV 에서 /api/lines 가 없을 때(#611 머지 전) 봉으로 만든 가짜 — 화면 확인용, 운영 빌드에서는 쓰지 않는다.
 * 최근 120봉 로그 종가 회귀선 ± 잔차 극값으로 채널, 최근 60봉 저점 둘로 상승 추세선.
 */
export function fakeLines(bars: Bar[], tf: LineTf, ticker: string): LinesReport | null {
  const n = Math.min(120, bars.length);
  if (n < 40) return null;
  const xs = bars.slice(-n);
  const ys = xs.map((b) => Math.log(b.close));
  const mx = (n - 1) / 2;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  ys.forEach((y, i) => { num += (i - mx) * (y - my); den += (i - mx) ** 2; });
  const slope = num / den;
  const fit = (i: number) => my + slope * (i - mx);
  const hi = Math.max(...xs.map((b, i) => Math.log(b.high) - fit(i)));
  const lo = Math.min(...xs.map((b, i) => Math.log(b.low) - fit(i)));
  const w = hi - lo;
  const seg = (off: number) => ({
    start: { at: xs[0].at, price: Math.exp(fit(0) + off) }, end: { at: xs[n - 1].at, price: Math.exp(fit(n - 1) + off) },
  });
  const last = xs[n - 1].close;
  const position = (Math.log(last) - fit(n - 1) - lo) / w;
  const atr = xs.slice(-14).reduce((a, b) => a + (b.high - b.low), 0) / 14;
  const dir: ParallelChannel["direction"] = Math.abs(slope * n) < w * 0.25 ? "flat" : slope > 0 ? "up" : "down";
  const state: ChannelState = position > 1 ? "above" : position < 0 ? "below" : position > 0.85 ? "near_upper" : position < 0.15 ? "near_lower" : "inside";
  const rate = (b: number): LineRate => ({ n: 120, bounce_rate: b, break_rate: 1 - b - 0.02, baseline_bounce_rate: 0.42, horizon_bars: 8 });
  const channel: ParallelChannel = {
    id: `${tf}-channel-fake`, timeframe: tf, direction: dir, base: "lower", start_at: xs[0].at,
    upper: seg(hi), lower: seg(lo), mid: seg((hi + lo) / 2), outer_upper: seg(hi + w), outer_lower: seg(lo - w),
    slope_pct_per_bar: (Math.exp(slope) - 1) * 100, width_pct: (Math.exp(w) - 1) * 100, width_atr: (last * (Math.exp(w) - 1)) / atr,
    position, state, broke_at: null, touches: { upper: [xs[10].at], lower: [xs[30].at] }, body_breaks: 0, score: 5,
    explain: `(가짜) ${TF_KO[tf]} ${DIR_KO[dir]} 채널 — 화면 확인용으로 봉에서 만든 선이에요.`,
    hit_rate: { lower: rate(0.4), upper: rate(0.47) },
  };
  // 상승 추세선: 최근 60봉을 둘로 나눠 각 구간 저점을 잇는다
  const tail = bars.slice(-60);
  const minAt = (a: number, b: number) => tail.slice(a, b).reduce((m, x) => (x.low < m.low ? x : m));
  const p0 = minAt(0, 30);
  const p1 = minAt(30, 55);
  const i0 = tail.indexOf(p0);
  const i1 = tail.indexOf(p1);
  const trend: ShortTrendline[] = [];
  if (i1 > i0 && p1.low > p0.low) {
    const s = Math.log(p1.low / p0.low) / (i1 - i0);
    const endPrice = p0.low * Math.exp(s * (tail.length - 1 - i0));
    const broken = tail[tail.length - 1].close < endPrice - 0.3 * atr;
    const brokeBar = broken ? tail.slice(i1 + 1).find((b, k) => b.close < p0.low * Math.exp(s * (i1 + 1 + k - i0)) - 0.3 * atr) ?? null : null;
    trend.push({
      id: `${tf}-trend-support-fake`, timeframe: tf, kind: "support", anchors: [{ at: p0.at, price: p0.low }, { at: p1.at, price: p1.low }],
      end: { at: tail[tail.length - 1].at, price: endPrice }, touches: [p0.at, p1.at], slope_pct_per_bar: (Math.exp(s) - 1) * 100,
      state: broken ? "broken" : "intact", broken_at: brokeBar?.at ?? null, broken_price: brokeBar?.close ?? null, fresh_break: broken,
      distance_atr: (tail[tail.length - 1].close - endPrice) / atr,
      explain: `(가짜) ${TF_KO[tf]} 상승추세${broken ? " 이탈" : ""} — 화면 확인용으로 봉에서 만든 선이에요.`,
      hit_rate: broken ? { n: 40, follow_rate: 0.45, baseline_rate: 0.6, horizon_bars: 8 } : rate(0.36),
    });
  }
  return {
    ticker, interval: tf, timeframe: tf, source: "fake", as_of: xs[n - 1].at, reference_price: last, atr,
    channels: [channel], trendlines: trend, events: [], note: NOT_ADVICE, backtest: null, fake: true,
  };
}
