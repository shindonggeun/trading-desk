import type { Halving, HalvingSeries } from "./types";

// #515 개발 서버 전용 예시 — #514 API 가 없을 때(404)만 쓴다. 숫자는 모양을 보이려고 대강 맞춘 값이라 실제와 다르다(화면에 "예시" 표시).

const DAY = 86_400_000;
const addDays = (iso: string, n: number) => new Date(Date.parse(`${iso}T00:00:00Z`) + n * DAY).toISOString().slice(0, 10);

/** (반감기 뒤 일수, 가격) 기준점 사이를 로그로 잇고 잔물결을 얹은 가짜 일봉 */
function priceAt(anchors: [number, number][], d: number): number {
  let k = anchors.findIndex(([x]) => x >= d);
  if (k <= 0) k = k === 0 ? 1 : anchors.length - 1;
  const [x0, y0] = anchors[k - 1];
  const [x1, y1] = anchors[k];
  const t = x1 === x0 ? 0 : Math.min(1, Math.max(0, (d - x0) / (x1 - x0)));
  return Math.exp(Math.log(y0) + (Math.log(y1) - Math.log(y0)) * t) * (1 + 0.025 * Math.sin(d / 5.3) + 0.02 * Math.sin(d / 17));
}

interface Spec { halving: string; end: number; from: number; anchors: [number, number][]; peak: number | null; bottom: number | null }
const SPECS: Spec[] = [
  { halving: "2012-11-28", end: 1318, from: 658, peak: null, bottom: 777,
    anchors: [[658, 457], [700, 380], [777, 178], [900, 230], [1100, 420], [1250, 580], [1318, 650]] },
  { halving: "2016-07-09", end: 1401, from: 0, peak: 526, bottom: 889,
    anchors: [[0, 650], [150, 600], [300, 1000], [450, 4000], [526, 19497], [600, 8000], [700, 7000], [800, 6300], [889, 3237], [1000, 4000], [1100, 8000], [1250, 7300], [1401, 8600]] },
  { halving: "2020-05-11", end: 1439, from: 0, peak: 546, bottom: 924,
    anchors: [[0, 8601], [150, 10800], [240, 19000], [330, 57000], [400, 40000], [460, 58000], [546, 67566], [600, 47000], [700, 41000], [800, 20000], [924, 15787], [1000, 21000], [1100, 27000], [1200, 37000], [1300, 43000], [1439, 64000]] },
  { halving: "2024-04-20", end: 941, from: 0, peak: 534, bottom: null,
    anchors: [[0, 64994], [100, 58000], [200, 67000], [250, 95000], [300, 98000], [360, 84000], [420, 104000], [500, 117000], [534, 124700], [600, 110000], [700, 92000], [800, 76000], [880, 52900], [941, 57100]] },
];

function series(s: Spec, zero: number, base: number): HalvingSeries {
  const points: [number, number][] = [];
  for (let d = s.from; d <= s.end; d += 7) points.push([d - zero, +(priceAt(s.anchors, d) / base).toFixed(4)]);
  return { halving: s.halving, ...(s.from > 0 ? { base_date: addDays(s.halving, s.from) } : {}), points };
}

export function fakeHalving(): Halving {
  const [c12, c16, c20, cur] = SPECS;
  const p = (s: Spec, d: number) => +priceAt(s.anchors, d).toFixed(1);
  const cycle = (s: Spec) => {
    const peak = s.peak == null ? null : { date: addDays(s.halving, s.peak), price: p(s, s.peak), days_from_halving: s.peak };
    const bp = s.bottom == null ? 0 : p(s, s.bottom);
    return {
      halving: s.halving, end: addDays(s.halving, s.end), data_from: addDays(s.halving, s.from), partial: s.from > 0, peak,
      bottom: s.bottom == null ? null : {
        date: addDays(s.halving, s.bottom), price: bp, days_from_halving: s.bottom,
        days_from_peak: peak ? s.bottom - peak.days_from_halving : null, drawdown_pct: peak ? +((bp / peak.price - 1) * 100).toFixed(1) : null,
      },
      note: s.from > 0 ? "2014-09-17 이전 가격이 없어 고점은 자료 부족이에요." : null,
    };
  };
  const cycles = [c12, c16, c20].map(cycle);
  const highP = p(cur, 534); const lowP = p(cur, 880); const nowP = p(cur, 941);
  const dd = cycles.flatMap((c) => (c.bottom?.drawdown_pct != null ? [c.bottom.drawdown_pct] : []));
  const dp = cycles.flatMap((c) => (c.bottom?.days_from_peak != null ? [c.bottom.days_from_peak] : []));
  return {
    as_of: "2026-11-17",
    source: "예시 데이터(개발 서버) — 실제 값 아님",
    halvings: [
      { n: 1, block: 210000, date: "2012-11-28", reward_after: 25, estimated: false },
      { n: 2, block: 420000, date: "2016-07-09", reward_after: 12.5, estimated: false },
      { n: 3, block: 630000, date: "2020-05-11", reward_after: 6.25, estimated: false },
      { n: 4, block: 840000, date: "2024-04-20", reward_after: 3.125, estimated: false },
      { n: 5, block: 1050000, date: "2028-03-30", reward_after: 1.5625, estimated: true },
    ],
    cycles,
    current: {
      halving: cur.halving, days_since_halving: 941, price: nowP,
      high: { date: addDays(cur.halving, 534), price: highP, days_from_halving: 534 },
      days_since_high: 941 - 534, from_high_pct: +((nowP / highP - 1) * 100).toFixed(1),
      low_after_high: { date: addDays(cur.halving, 880), price: lowP, days_from_halving: 880, drawdown_pct: +((lowP / highP - 1) * 100).toFixed(1) },
      from_low_pct: +((nowP / lowP - 1) * 100).toFixed(1),
    },
    ranges: {
      bottom_days_from_halving: { min: 777, max: 924, cycles: cycles.map((c) => c.halving) },
      bottom_days_from_peak: { min: Math.min(...dp), max: Math.max(...dp), cycles: [c16.halving, c20.halving] },
      bottom_drawdown_pct: { min: Math.min(...dd), max: Math.max(...dd), cycles: [c16.halving, c20.halving] },
      now: { days_since_halving: "after", days_since_high: "after", from_high_pct: "shallower" },
    },
    series: {
      by_halving: SPECS.map((s) => series(s, 0, s.from > 0 ? priceAt(s.anchors, s.from) : priceAt(s.anchors, 0))),
      by_peak: SPECS.filter((s) => s.peak != null).map((s) => series({ ...s, from: 0 }, s.peak!, priceAt(s.anchors, s.peak!))),
    },
    notes: [
      "바닥은 지나고 나서야 알 수 있어요. 지금까지의 최저가 바닥이라는 뜻이 아니에요.",
      "비교할 지난 사이클이 3개뿐이라 범위는 참고용이에요.",
      "2012 사이클은 2014-09-17 이전 가격이 없어 고점은 자료 부족이고, 바닥은 있는 자료 안에서 찾은 값이에요.",
    ],
  };
}
