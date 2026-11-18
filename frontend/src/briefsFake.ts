import type { DayCall, DayCallHistory, DayCallHistoryItem, DayCallLabel, DayCallMarket, DayCallProbs, DayCallStats, MorningBrief, MorningBriefList } from "./types";

/**
 * #573 개발 모드에서만 — 아침 시황(#571) · 오늘 방향(#572) API 가 아직 없을 때(404) 계약 모양의 예시로 화면을 그린다.
 * 운영 빌드에서는 쓰지 않는다(부르는 쪽이 import.meta.env.DEV 일 때만 부른다). 숫자는 화면 확인용 예시다.
 */
const kstToday = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
const shift = (day: string, n: number) => new Date(Date.parse(`${day}T00:00:00Z`) + n * 86400_000).toISOString().slice(0, 10);

export function fakeMorningBrief(date?: string): MorningBrief {
  const d = date ?? kstToday();
  return {
    id: `mb_sample_${d}`, date: d, created_at: `${shift(d, -1)}T22:00:41Z`, trigger: "auto", source: "ai", model: "예시",
    title: "간밤 나스닥 +1.20% — 반도체가 끌어올렸어요",
    numbers: ["S&P500 6,712 (+0.85%)", "나스닥100 선물 -0.30% (뉴욕 17시 기준)", "VIX 17.2 (어제 18.1)", "엔비디아 시간외 +3.4% (실적 · 가이던스 예상 웃돎)"],
    kr_flow: "어제 코스피 3,950(+0.42%), 외국인 -1,234억 · 기관 +500억 · 개인 +700억 / 코스닥 870(-0.20%)",
    done: ["엔비디아 실적 — 매출 · 가이던스 모두 예상 위", "미 10월 소매판매 발표"],
    pending: ["FOMC 의사록(목요일 새벽)", "코스피200 옵션 만기 주간"],
    schedule: ["11/20(목) 04:00 FOMC 의사록", "11/21(금) 22:30 미 PMI 속보치", "12/11(목) 코스피200 옵션 만기"],
    good: ["반도체 실적 기대가 다시 살아나 대형 반도체 수급이 좋아질 수 있어요", "변동성 지수가 내려와 위험 선호가 조금 돌아왔어요"],
    bad: ["시간외 상승을 그대로 오늘 국장 상승으로 읽기 쉬워요 — 선물은 이미 일부 반영했어요", "외국인 순매도가 이어져 지수 상단은 무거울 수 있어요"],
    closing: "반도체가 버텨 주면 지수는 강보합, 외국인 수급이 돌아서는지가 관건이에요.",
    day_call: "오늘 국장: 약한 롱 42% · 미장: 약보합 38% (예시)",
    missing: [], fallback: null, version: 1, generating: false, next_due: `${d}T22:00:00Z`,
  };
}

export function fakeMorningBriefs(): MorningBriefList {
  const today = kstToday();
  return {
    generating: false,
    items: Array.from({ length: 8 }, (_, i) => {
      const d = shift(today, -i);
      return { id: `mb_sample_${d}`, date: d, created_at: `${shift(d, -1)}T22:00:41Z`, title: i ? `예시 브리핑 ${d}` : fakeMorningBrief(d).title,
        source: "ai" as const, trigger: "auto" as const };
    }),
  };
}

const probs = (p: number[]): DayCallProbs => ({ strong_long: p[0], long: p[1], flat: p[2], short: p[3], strong_short: p[4] });
const LABELS: DayCallLabel[] = ["strong_long", "long", "flat", "short", "strong_short"];

export function fakeDayCall(): DayCall {
  return {
    date: kstToday(),
    kr: {
      label: "long", probs: probs([0.12, 0.42, 0.28, 0.13, 0.05]), round: 1, at: `${shift(kstToday(), -1)}T22:50:00Z`,
      reasons: ["간밤 나스닥 +1.20%, 반도체 지수 +2.1%", "코스피200 야간선물 +0.35%", "외국인 선물 순매수 1,200계약", "VKOSPI 19.8 → 18.9", "원/달러 1,385원 (-4원)"],
    },
    us: {
      label: "flat", probs: probs([0.06, 0.24, 0.38, 0.22, 0.10]), round: 1, at: `${kstToday()}T13:20:00Z`,
      reasons: ["나스닥100 선물 -0.30%", "FOMC 의사록 대기", "VIX 17.2", "엔비디아 시간외 +3.4%", "10년물 4.42% (+3bp)"],
    },
    line: "오늘 국장은 약한 롱(42%), 미장은 약보합(38%) 쪽이에요 — 예시",
  };
}

function seeded(i: number) {
  const x = Math.sin(i * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

export function fakeDayCallHistory(market: DayCallMarket, days = 90): DayCallHistory {
  const today = kstToday();
  const items: DayCallHistoryItem[] = [];
  for (let i = 0; i < Math.min(days, 60); i++) {
    const day = shift(today, -i);
    const wd = new Date(`${day}T00:00:00Z`).getUTCDay();
    if (wd === 0 || wd === 6) continue;
    const r = seeded(i + (market === "us" ? 100 : 0));
    const label = LABELS[Math.floor(r * 5)];
    const actual = i === 0 ? null : LABELS[Math.floor(seeded(i + 7) * 5)];
    const side = (l: DayCallLabel) => (l.endsWith("long") ? 1 : l === "flat" ? 0 : -1);
    items.push({
      date: day, round: 2, label, probs: probs([0.1, 0.3, 0.3, 0.2, 0.1]), actual,
      actual_pct: actual ? Math.round((seeded(i + 3) * 3 - 1.5) * 100) / 100 : null,
      hit: actual ? side(actual) === side(label) : null, exact: actual ? actual === label : null,
    });
  }
  return { market, days, items };
}

export function fakeDayCallStats(market: DayCallMarket, days = 90): DayCallStats {
  const hist = fakeDayCallHistory(market, days).items.filter((it) => it.hit !== null);
  const rate = (xs: (boolean | null)[]) => (xs.length ? xs.filter(Boolean).length / xs.length : null);
  return {
    market, days, n: hist.length, hit_rate: rate(hist.map((h) => h.hit)), exact_rate: rate(hist.map((h) => h.exact)), brier: 0.71,
    by_label: LABELS.map((label) => {
      const of = hist.filter((h) => h.label === label);
      return { label, n: of.length, hit_rate: rate(of.map((h) => h.hit)) };
    }),
    calibration: [[0, 0.2], [0.2, 0.4], [0.4, 0.6], [0.6, 0.8], [0.8, 1]].map(([lo, hi], i) => ({
      lo, hi, n: [4, 18, 22, 9, 2][i], predicted: (lo + hi) / 2, actual: i === 4 ? null : [0.12, 0.33, 0.47, 0.61][i],
    })),
  };
}
