/**
 * #550 가격 차트 이동평균선 · 보조 칸 이름표 — 봉 데이터로 프론트가 계산한다(백엔드 응답은 그대로).
 */

/** 이동평균 기간(봉 개수) — 일봉이면 5 · 20 · 60 · 120일, 주 · 월 · 분봉이면 같은 개수의 봉 */
export const MA_PERIODS = [5, 20, 60, 120] as const;
export type MaPeriod = (typeof MA_PERIODS)[number];
export type MaOn = Record<MaPeriod, boolean>;

/** 단순 이동평균 — 앞쪽 n-1 개 봉은 null */
export function sma(values: number[], n: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= n) sum -= values[i - n];
    if (i >= n - 1) out[i] = sum / n;
  }
  return out;
}

// 이동평균 선마다 켜고 끄기 — 이 브라우저에 기억(기본 모두 켬)
const MA_KEY = "desk.chart.ma";
export function loadMaOn(): MaOn {
  const all = { 5: true, 20: true, 60: true, 120: true } as MaOn;
  try {
    const raw = JSON.parse(localStorage.getItem(MA_KEY) ?? "{}");
    for (const n of MA_PERIODS) if (typeof raw[n] === "boolean") all[n] = raw[n];
  } catch {
    /* 저장소가 막혀 있으면 기본값 */
  }
  return all;
}
export function saveMaOn(on: MaOn) {
  try {
    localStorage.setItem(MA_KEY, JSON.stringify(on));
  } catch {
    /* 기억만 못 한다 */
  }
}

/** 거래량 짧게: 9,766만 · 1.2억 · 3,400 */
export function shortVolume(v: number): string {
  if (v >= 1e8) return `${(v / 1e8).toFixed(v >= 1e9 ? 0 : 1)}억`;
  if (v >= 1e4) return `${Math.round(v / 1e4).toLocaleString("ko-KR")}만`;
  return Math.round(v).toLocaleString("ko-KR");
}

export type PaneId = "vol" | "rsi" | "stoch";

/** 칸 이름표의 한 줄 뜻(넓은 화면 · 좁은 화면)과 ⓘ 를 누르면 펼치는 쉬운 설명 */
export const PANE_TEXT: Record<PaneId, { name: string; long: string; short: string; help: string[] }> = {
  vol: {
    name: "거래량",
    long: "빨강 오른 날 · 파랑 내린 날, 평소보다 크면 힘이 실림",
    short: "평소보다 크면 힘이 실림",
    help: [
      "그 봉 동안 사고판 주식 수입니다. 막대 색은 그 봉이 올랐는지(빨강) 내렸는지(파랑)입니다.",
      "평소(최근 20봉 평균)보다 훨씬 많은 거래량으로 오르거나 내리면 그 방향에 힘이 실렸다고 봅니다.",
      "거래량 없이 움직인 가격은 쉽게 되돌아가기도 합니다.",
    ],
  },
  rsi: {
    name: "RSI(14)",
    long: "30 아래는 너무 많이 빠진 구간(과매도), 70 위는 과열",
    short: "30↓ 과매도 · 70↑ 과열",
    help: [
      "최근 14봉 동안 오른 힘과 내린 힘을 0~100 으로 견준 값입니다.",
      "70 위면 짧은 기간에 많이 올라 쉬어 갈 수 있는 과열, 30 아래면 많이 빠져 반등이 나올 수 있는 과매도로 읽습니다.",
      "강한 추세에서는 과열 · 과매도가 오래 이어질 수 있어 이것만으로 사고팔지 않습니다.",
    ],
  },
  stoch: {
    name: "스토캐스틱",
    long: "20 아래 침체 · 80 위 과열, 실선이 점선을 위로 넘으면 반등 신호",
    short: "20↓ 침체 · 80↑ 과열",
    help: [
      "최근 14봉의 가장 높은 값 · 낮은 값 사이에서 지금 가격이 어디쯤인지 0~100 으로 보인 값입니다(실선 %K, 점선 %D 는 그 평균).",
      "80 위는 과열, 20 아래는 침체 구간입니다. 침체 구간에서 실선이 점선을 위로 넘으면 반등 신호, 과열 구간에서 아래로 넘으면 꺾임 신호로 봅니다.",
      "RSI 보다 빠르게 움직여 신호가 잦으니 다른 근거와 함께 봅니다.",
    ],
  },
};
