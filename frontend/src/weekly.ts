import type { WeeklyMap } from "./types";

/**
 * #205 레버리지 · 섹터 종목 → 주간 지도(#202)의 기초 지수. backend desk/weekly_map.UNDERLYING 과 같은 표 —
 * 한쪽을 바꾸면 다른 쪽도 바꾼다(분석 노트와 화면 링크가 같은 지수를 가리키게).
 */
export const WEEKLY_UNDERLYING: Record<string, string> = {
  SOXL: "SOXX", SOXS: "SOXX", USD: "SOXX", SMH: "SOXX", SOXX: "SOXX",
  NVDA: "SOXX", AMD: "SOXX", AVGO: "SOXX", MU: "SOXX", TSM: "SOXX", INTC: "SOXX",
  TQQQ: "QQQ", SQQQ: "QQQ", QLD: "QQQ", QID: "QQQ", QQQ: "QQQ",
  SPXL: "^GSPC", UPRO: "^GSPC", SPXS: "^GSPC", SSO: "^GSPC", SPY: "^GSPC", VOO: "^GSPC",
  "122630.KS": "^KS200", "252670.KS": "^KS200", "069500.KS": "^KS200", "114800.KS": "^KS200",
};

export const WEEKLY_NAMES: Record<string, string> = {
  "NQ=F": "나스닥100 선물", QQQ: "나스닥100 ETF(QQQ)", SOXX: "반도체 ETF(SOXX)", "^GSPC": "S&P 500", "^KS200": "코스피200",
};

/** 서버(desk/weekly_map.place)의 위치 — #508 부터 "반등 시도"(rebound)도 서버가 나눈다 */
export type WeeklyZone = WeeklyMap["position"];

/**
 * #490 구간마다 쉬운 이름 · 한 줄 뜻 · 지금 할 일 — 대시보드 요약(WeeklyMapBrief) · 주간 지도 화면 · 차트 탭이 같이 쓴다.
 * 검정선 = 20주 이동평균, 빨간선 = 60주 이동평균(#202). 처음 나올 때 괄호로 풀어 쓴다.
 */
export const WEEKLY_WORDS: Record<WeeklyZone, { name: string; where: string; meaning: string; todo: string }> = {
  above_black: {
    name: "상승 흐름 유지", where: "검정선 위",
    meaning: "주가가 검정선(20주 평균) 위 — 몇 달 흐름이 위쪽이다.",
    todo: "눌림(검정선 근처)에서 나눠 사고, 주봉 종가로 검정선이 깨지면 줄인다.",
  },
  rebound: {
    name: "반등 시도", where: "검정선 위 · 빨간선 아래",
    meaning: "긴 하락 뒤라 검정선(20주 평균)이 빨간선(60주 평균) 아래에 있고, 주가는 그 사이 — 검정선은 되찾았지만 1년 흐름은 아직 아래다.",
    todo: "아직 상승 전환이 아니다. 산다면 적게 · 손절은 검정선 아래로 짧게, 빨간선을 주봉 종가로 넘는지 본다.",
  },
  between: {
    name: "쉬어 가는 구간", where: "선 사이",
    meaning: "검정선(20주 평균) 아래 · 빨간선(60주 평균) 위 — 흐름이 식는 중이다.",
    todo: "서두르지 말고 빨간선 근처에서 반등을 확인한 뒤 소량, 검정선을 되찾으면 더한다.",
  },
  below_red: {
    name: "긴 하락 흐름", where: "빨간선 아래",
    meaning: "빨간선(60주 평균) 아래 — 1년 넘는 흐름이 아래쪽이다.",
    todo: "새로 사기는 쉬고, 산다면 아주 적게 · 손절은 짧게. 빨간선을 주봉 종가로 되찾을 때까지 기다린다.",
  },
};

export const weeklyPct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);

interface WeeklyLike {
  position: WeeklyZone;
  close: number;
  lines: { black: { price: number }; red: { price: number } };
  scenario: { down: { price: number; pct: number | null }; up: { price: number; pct: number | null } };
  range_52w: { high: number; low: number };
  record?: {
    black_break: { n: number; reached_red: number; within_weeks: number };
    bb_lower_touch: { n: number; rebounded: number; after_weeks: number };
  };
}

/** ``text`` = 선 이름 + 거리(요약 줄), ``pctText`` = 거리만(이름을 따로 보이는 카드의 숫자 칸) */
export interface NextLine { label: "아래 받침" | "위 저항"; what: string; price: number; pct: number | null; text: string; pctText: string }

const near = (a: number, b: number) => Math.abs(a - b) <= Math.max(Math.abs(b) * 0.001, 0.01);

function line(label: NextLine["label"], what: string, short: string, price: number, pct: number | null): NextLine {
  return { label, what, price, pct, text: `${short}까지 ${weeklyPct(pct)}`, pctText: weeklyPct(pct) };
}

/**
 * ↓ · ↑ 숫자가 무엇까지의 거리인지 — 서버(desk/weekly_map)가 고르는 규칙 그대로:
 * 검정선 위 · 선 사이의 아래 받침은 빨간선, 빨간선 아래면 52주 저점 · 50주 볼린저 하단 중 가까운 쪽.
 * 위 저항은 선 사이면 검정선, 빨간선 아래면 두 선 중 가까운 쪽(보통 빨간선, 역배열이면 검정선),
 * 검정선 위면 52주 고점 · 50주 볼린저 상단 중 가까운 쪽. 반등 시도는 받침 = 검정선, 저항 = 빨간선.
 * 화면은 ``text``(선 이름 + 거리)를 그대로 보여 준다 — 터치 기기에서도 무엇까지인지 보이게.
 */
export function nextLines(m: WeeklyLike): { down: NextLine; up: NextLine } {
  const { down, up } = m.scenario;
  switch (m.position) {
    case "rebound":
      return {
        down: line("아래 받침", "검정선(20주 평균)", "검정선", down.price, down.pct),
        up: line("위 저항", "빨간선(60주 평균)", "빨간선", up.price, up.pct),
      };
    case "below_red": {
      const low = near(down.price, m.range_52w.low);
      const black = m.lines.black.price < m.lines.red.price && near(up.price, m.lines.black.price);
      return {
        down: line("아래 받침", low ? "52주 저점" : "50주 볼린저 하단", low ? "52주 저점" : "볼린저 하단", down.price, down.pct),
        up: black ? line("위 저항", "검정선(20주 평균)", "검정선", up.price, up.pct)
          : line("위 저항", "빨간선(60주 평균)", "빨간선", up.price, up.pct),
      };
    }
    case "between":
      return {
        down: line("아래 받침", "빨간선(60주 평균)", "빨간선", down.price, down.pct),
        up: line("위 저항", "검정선(20주 평균)", "검정선", up.price, up.pct),
      };
    default: {
      const high = near(up.price, m.range_52w.high);
      const top = line("위 저항", high ? "52주 고점" : "50주 볼린저 상단", high ? "52주 고점" : "볼린저 상단", up.price, up.pct);
      // 52주 고점을 종가로 새로 쓴 주 — "+0.0% 저항" 이 아니라 고점 갱신
      if (high && up.price <= m.close) Object.assign(top, { text: "52주 고점 갱신 중", pctText: "갱신 중" });
      return { down: line("아래 받침", "빨간선(60주 평균)", "빨간선", down.price, down.pct), up: top };
    }
  }
}

/** 할 일 문장의 근거 — 서버가 준 지난 기록(없으면 null). 언제의 기록인지 · 예측이 아님을 함께 말한다. */
export function weeklyEvidence(m: WeeklyLike): string | null {
  const rec = m.record;
  if (!rec) return null;
  const zone = m.position;
  if (zone === "rebound") return null;
  if (zone === "below_red") {
    const t = rec.bb_lower_touch;
    return t.n ? `참고로 과거 50주 볼린저 하단에 닿았던 ${t.n}번 중 ${t.rebounded}번은 ${t.after_weeks}주 뒤 더 높았다. `
      + "하단에 닿았을 때의 기록이고 예측이 아니다." : null;
  }
  const b = rec.black_break;
  // #508 역배열(검정선이 빨간선 아래)이면 검정선을 깰 때 이미 빨간선 아래 — 이 기록은 근거가 못 된다
  if (!b.n || m.lines.black.price < m.lines.red.price) return null;
  const fact = `과거 검정선을 깬 ${b.n}번 중 ${b.reached_red}번은 ${b.within_weeks}주 안에 빨간선에 닿았다`;
  return zone === "between" ? `${fact} — 지난 기록이지 예측이 아니다.`
    : `검정선이 깨지면 참고 — ${fact}. 지난 기록이지 예측이 아니다.`;
}

/** #235 그 종목의 차트 탭을 주봉으로 */
export const chartsLink = (symbol: string) => `#/charts?t=${encodeURIComponent(symbol)}&iv=1w`;

export const weeklyLink = (symbol: string) => `#/weekly?symbol=${encodeURIComponent(symbol)}`;
