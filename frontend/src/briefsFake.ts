import type { DayCallCallStats, DayCallEntry, DayCallHistory, DayCallHistoryItem, DayCallKind, DayCallMarket, DayCallProbs, DayCallStage, DayCallStats, DayCallToday, MorningBrief, MorningBriefList } from "./types";
import { DAY_CALL_KINDS, DAY_CALL_STAGES, KIND_KO, STAGE_KO } from "./dayCall";

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
    numbers: ["S&P500 6,712 (+0.85%)", "나스닥100 선물 -0.30% (전일 종가 대비 · 지금까지)", "VIX 17.2 (어제 18.1)", "엔비디아 시간외 +3.4% (실적 · 가이던스 예상 웃돎)"],
    kr_flow: "어제 코스피 3,950(+0.42%), 외국인 -1,234억 · 기관 +500억 · 개인 +700억 / 코스닥 870(-0.20%)",
    done: ["엔비디아 실적 — 매출 · 가이던스 모두 예상 위", "미 10월 소매판매 발표"],
    pending: ["FOMC 의사록(목요일 새벽)", "코스피200 옵션 만기 주간"],
    schedule: ["11/20(목) 04:00 FOMC 의사록", "11/21(금) 22:30 미 PMI 속보치", "12/11(목) 코스피200 옵션 만기"],
    good: ["반도체 실적 기대가 다시 살아나 대형 반도체 수급이 좋아질 수 있어요", "변동성 지수가 내려와 위험 선호가 조금 돌아왔어요"],
    bad: ["시간외 상승을 그대로 오늘 국장 상승으로 읽기 쉬워요 — 선물은 이미 일부 반영했어요", "외국인 순매도가 이어져 지수 상단은 무거울 수 있어요"],
    closing: "반도체가 버텨 주면 지수는 강보합, 외국인 수급이 돌아서는지가 관건이에요.",
    day_call: "오늘 국장은 약한 롱(34%) 우세, 간밤 미장 판정은 약보합(38%)이었다. (예시)",
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

const probs = (p: number[]): DayCallProbs => ({ strong_long: p[0], weak_long: p[1], flat: p[2], weak_short: p[3], strong_short: p[4] });
const STAGES = DAY_CALL_STAGES.map((key) => ({ key, label: STAGE_KO[key] }));
const top = (pr: DayCallProbs): DayCallStage => DAY_CALL_STAGES.reduce((a, b) => (pr[b] > pr[a] ? b : a));
const NOTE = [
  "판정 정의는 같은 검증 90일의 변형 비교를 보고 골라 숫자가 조금 낙관적일 수 있다",
  "야간 선물 특징은 학습이 1.4~1.8년이다",
  "2차의 전일 종가 대비 적중에는 이미 찍힌 시가 갭 몫이 크다",
];

function seeded(i: number) {
  const x = Math.sin(i * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

function entry(market: DayCallMarket, kind: DayCallKind, day: string, round: number, pr: DayCallProbs, scored: boolean, i = 0): DayCallEntry {
  const stage = top(pr);
  const longP = pr.strong_long + pr.weak_long;
  const shortP = pr.weak_short + pr.strong_short;
  const direction = longP >= shortP ? "long" : "short";
  const verdict = kind === "open" && Math.abs(longP - shortP) < 10 ? "hold" : direction;
  const reasons: DayCallEntry["reasons"] = kind === "prev_close"
    ? [
      { key: "nq_overnight", label: "야간 나스닥100 선물", value: market === "kr" ? "+0.6%" : "-0.3%", effect: market === "kr" ? "long" : "short", weight: 0.34, text: market === "kr" ? "간밤 나스닥100 선물 +0.6%" : "프리마켓 선물이 약보합" },
      { key: "us_prev_ret", label: "전일 S&P500", value: "+0.9%", effect: "long", weight: 0.22, text: "전일 S&P500 +0.9% 상승" },
      { key: "vix", label: "VIX", value: "17.2", effect: "calm", weight: 0.1, text: "변동성 지수가 평소 수준" },
      { key: "usdkrw", label: "원/달러", value: "1,385원(-4원)", effect: "long", weight: 0.08, text: "원화 강세" },
      { key: "foreign_futures", label: "외국인 선물(전일)", value: "-3,200계약", effect: "short", weight: 0.07, text: "전일 외국인 선물 순매도는 부담" },
    ]
    : [
      { key: "prev_ret", label: "전일 등락", value: "+0.4%", effect: "long", weight: 0.2, text: "전일 지수 +0.4%" },
      { key: "atr", label: "변동폭(ATR)", value: "1.1%", effect: "volatile", weight: 0.15, text: "평소보다 조금 큰 변동폭" },
      { key: "ma20", label: "20일선", value: "위", effect: "long", weight: 0.1, text: "지수가 20일선 위" },
    ];
  const actual = DAY_CALL_STAGES[Math.floor(seeded(i + 7) * 5)];
  const sideOf = (st: DayCallStage) => (st.endsWith("long") ? "long" : st.endsWith("short") ? "short" : "flat");
  return {
    call: kind, call_label: KIND_KO[kind], measured_from: kind, fallback: kind === "prev_close" && market === "kr" && i % 9 === 4,
    round, round_label: `${round}차`, called_at: `${day}T${market === "kr" ? (round === 1 ? "22:30" : "00:30") : round === 1 ? "14:00" : "15:00"}:10+00:00`,
    stage, label: STAGE_KO[stage], direction, verdict, probs: pr, edges_pct: { flat: 0.38, strong: 1.27 }, reasons,
    context: [{ key: "night_futures", label: "야간선물", value: "+0.4%", in_model: false }],
    model_version: "dc-예시", source: i > 20 ? "backfill" : "live",
    outcome: scored ? {
      stage: actual, label: STAGE_KO[actual], open: 2710.5, close: 2713.1, return_pct: Math.round((seeded(i + 3) * 3 - 1.5) * 100) / 100,
      ret_atr: 0.08, hit_direction: verdict === "hold" ? null : sideOf(actual) === direction, hit_stage: actual === stage, brier: 0.71,
      scored_at: `${day}T07:30:00+00:00`,
    } : null,
    record: kind === "prev_close"
      ? { n: market === "kr" ? 56 : 63, direction_hit_rate: market === "kr" ? 0.661 : 0.762, always_long: market === "kr" ? 0.464 : 0.429,
        always_short: market === "kr" ? 0.536 : 0.571, brier_skill: market === "kr" ? 0.167 : 0.165, basis: "backtest" }
      : { n: market === "kr" ? 29 : 63, direction_hit_rate: market === "kr" ? 0.655 : 0.46, always_long: market === "kr" ? 0.379 : 0.476,
        always_short: market === "kr" ? 0.621 : 0.524, brier_skill: market === "kr" ? 0.023 : -0.009, basis: "backtest" },
  };
}

export function fakeDayCall(market: DayCallMarket): DayCallToday {
  const today = kstToday();
  const day = market === "kr" ? today : shift(today, -1);
  const round = market === "kr" ? 2 : 1;
  const prev = entry(market, "prev_close", day, round, market === "kr" ? probs([8.1, 34.2, 31.0, 21.3, 5.4]) : probs([5.2, 20.1, 33.0, 29.4, 12.3]), false);
  const open = entry(market, "open", day, round, market === "kr" ? probs([6.0, 26.0, 38.0, 22.0, 8.0]) : probs([10.4, 30.1, 30.2, 22.0, 7.3]), false);
  return { market, date: today, stages: STAGES, calls: { prev_close: prev, open }, rounds: { prev_close: [prev], open: [open] }, note: NOTE };
}

export function fakeDayCallHistory(market: DayCallMarket, days = 90): DayCallHistory {
  const today = kstToday();
  const items: DayCallHistoryItem[] = [];
  for (let i = 0; i < Math.min(days, 45); i++) {
    const day = shift(today, -i);
    const wd = new Date(`${day}T00:00:00Z`).getUTCDay();
    if (wd === 0 || wd === 6) continue;
    for (const kind of DAY_CALL_KINDS) {
      const r = seeded(i + (market === "us" ? 100 : 0) + (kind === "open" ? 50 : 0));
      const pr = probs([10 + r * 10, 30 - r * 10, 25 + r * 5, 25 - r * 5, 10].map((x) => Math.round(x * 10) / 10));
      items.push({ date: day, ...entry(market, kind, day, 2, pr, i > 0, i) });
    }
  }
  return { market, days, items };
}

export function fakeDayCallStats(market: DayCallMarket, days = 90): DayCallStats {
  const all = fakeDayCallHistory(market, days).items.filter((it) => it.outcome);
  const ratio = (xs: boolean[]) => (xs.length ? xs.filter(Boolean).length / xs.length : null);
  const one = (kind: DayCallKind): DayCallCallStats => {
    const scored = all.filter((h) => h.call === kind);
    const decided = scored.filter((h) => h.outcome!.hit_direction !== null);
    return {
      n: scored.length, missed: 1,
      direction_hit_rate: ratio(decided.map((h) => !!h.outcome!.hit_direction)), stage_hit_rate: ratio(scored.map((h) => h.outcome!.hit_stage)),
      brier: kind === "prev_close" ? 0.62 : 0.74,
      baselines: { always_long: { direction_hit_rate: 0.43 }, always_short: { direction_hit_rate: 0.57 }, follow_yesterday: { direction_hit_rate: 0.49 }, climatology: { brier: 0.75 } },
      per_stage: DAY_CALL_STAGES.map((stage) => {
        const called = scored.filter((h) => h.stage === stage);
        return { stage, label: STAGE_KO[stage], called: called.length, called_hit: called.filter((h) => h.outcome!.hit_stage).length,
          actual: scored.filter((h) => h.outcome!.stage === stage).length };
      }),
      calibration: Array.from({ length: 10 }, (_, k) => ({ lo: k * 10, hi: k * 10 + 10, n: [60, 48, 52, 40, 22, 9, 3, 1, 0, 0][k],
        mean_prob: k < 8 ? k * 10 + 5.3 : null, actual_rate: k < 7 ? [7.7, 14.2, 23.0, 36.1, 41.0, 52.2, 66.7][k] : null })),
      by_round: { "1": { n: 20, direction_hit_rate: 0.52 }, "2": { n: scored.length, direction_hit_rate: 0.55 } },
      by_source: { live: { n: 8 }, backfill: { n: scored.length - 8 } },
      ...(kind === "open" ? { hold_rate: scored.length ? 1 - decided.length / scored.length : null, called: { n: decided.length, direction_hit_rate: ratio(decided.map((h) => !!h.outcome!.hit_direction)) } } : {}),
    };
  };
  return { market, days, calls: { prev_close: one("prev_close"), open: one("open") }, note: NOTE };
}
