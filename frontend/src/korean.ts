/**
 * 판단 용어 한글 풀이.
 *
 * TradingAgents 는 보고서 본문을 한국어로 쓰지만, 구조화된 항목명(**Action**, **Entry Price** …)과
 * 등급 값(Buy · Overweight · Underweight …)은 영어로 고정한다(백엔드 plan.py 가 그 라벨을 읽는다).
 * 화면에서만 한글로 바꿔 보여 준다 — 원문은 DB 에 그대로 있다.
 */
import type { Bias } from "./types";

/**
 * 시세 출처(backend prices.Quote.source · 봉의 source) — 실시간인지 지연 시세인지 헷갈리지 않게 한 곳에서.
 * kis = 한국투자증권 웹소켓 체결가(#125), toss = 토스증권 조회, yahoo = 야후(15분 이상 지연될 수 있다).
 */
export const QUOTE_SOURCE: Record<string, string> = {
  kis: "실시간(한투)",
  toss: "토스",
  yahoo: "야후(지연 가능)",
  mock: "모의",
  nxt: "NXT",
  "krx-overtime": "KRX 시간외",
};

/** #238 미국 장외 세션: 프리마켓 · 애프터마켓 · 주간거래(한국 낮, 미국 밤) — 실제 체결이다. */
export const US_SESSION: Record<string, string> = { pre: "프리", post: "애프터", overnight: "주간거래" };

/** #251 거래 구간(#250 session) — 정규 · 프리 · 애프터 · 주간거래(미장, 한국 낮) */
export const SESSION_KO: Record<string, string> = { ...US_SESSION, regular: "정규", closed: "장 마감" };

/** #251 타일에 작게 쓰는 가격 출처 */
export const QUOTE_SOURCE_SHORT: Record<string, string> = {
  kis: "한투", toss: "토스", nxt: "NXT", "krx-overtime": "KRX 시간외", yahoo: "야후(지연 가능)", "binance-estimate": "바이낸스 추정", mock: "모의",
};

type BoardLike = { state: "live" | "estimate" | "unavailable"; session?: string | null; quote_source?: string | null;
  overtime?: string | null; traded_at?: string | null };

/** #283 KRX 시간외 거래 — 시간외 종가(15:40~16:00) · 시간외 단일가(16:00~18:00, 10분마다 체결) */
export const OVERTIME_KO: Record<string, string> = { closing: "시간외 종가", single: "시간외 단일가" };

/**
 * 시세판 · 즐겨찾기 타일 배지(#251): `실시간 · 프리` / `실시간 · 정규` / `실시간 · 애프터` / `실시간 · 주간거래` /
 * `NXT`(국장 장외) / `추정(바이낸스)` / `추정 불가`. 구간을 모르는 예전 응답이면 `실시간` 만.
 */
export function boardBadge(it: BoardLike): string {
  if (it.state === "live") {
    if (it.quote_source === "nxt") return it.session === "pre" || it.session === "post" ? `NXT · ${SESSION_KO[it.session]}` : "NXT";
    if (it.quote_source === "krx-overtime") return OVERTIME_KO[it.overtime ?? ""] ?? "시간외";
    const s = it.session && it.session !== "closed" ? SESSION_KO[it.session] : null;
    return s ? `실시간 · ${s}` : "실시간";
  }
  return it.state === "estimate" ? "추정(바이낸스)" : "추정 불가";
}

/** 배지 색 — 상태(qb-live · qb-estimate · qb-unavailable)와 실시간이면 구간(qb-s-…) */
export function boardBadgeClass(it: BoardLike | undefined): string {
  if (!it) return "qb-badge qb-wait";
  if (it.state !== "live") return `qb-badge qb-${it.state}`;
  const s = it.quote_source === "nxt" || it.quote_source === "krx-overtime" ? "nxt" : it.session && it.session !== "closed" ? it.session : "regular";
  return `qb-badge qb-live qb-s-${s}`;
}

/** 추정 · 추정 불가의 이유(툴팁 · 탭하면 보이는 한 줄) */
export function boardReason(it: BoardLike): string | null {
  if (it.state === "estimate") {
    return !it.session || it.session === "closed"
      ? "주말 · 휴장 · 장 마감 뒤라 바이낸스 무기한 선물로 낸 추정가"
      : `${SESSION_KO[it.session] ?? "장외"} 시간인데 체결이 없어 바이낸스 무기한 선물로 낸 추정가`;
  }
  if (it.state === "unavailable") return "실시간 체결도 추정할 계약도 없어 가격을 낼 수 없습니다";
  if (it.quote_source === "krx-overtime") {
    const at = it.traded_at
      ? new Date(it.traded_at).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" })
      : null;
    const what = it.overtime === "closing" ? "시간외 종가(15:40~16:00)" : "시간외 단일가(16:00~18:00, 10분마다 체결)";
    return `NXT 에 없는 종목이라 KRX ${what}의 마지막 체결가${at ? ` · ${at} 체결` : ""}`;
  }
  return null;
}

export function quoteSourceShort(source: string | null | undefined): string {
  if (!source) return "";
  return QUOTE_SOURCE_SHORT[source] ?? QUOTE_SOURCE[source] ?? source;
}

export function quoteSourceText(source: string | null | undefined): string {
  if (!source) return "";
  return QUOTE_SOURCE[source] ?? source;
}

export interface Term {
  ko: string;
  desc: string;
}

/** 포트폴리오 매니저의 최종 등급(5단계). desc 는 레버리지 공격형 관점의 행동까지 적는다. */
export const RATING: Record<string, Term> = {
  Buy: { ko: "매수", desc: "상승 판단이 강합니다. 새로 사거나 크게 늘립니다. 공격형이면 레버리지 롱 진입 후보입니다." },
  Overweight: { ko: "비중 확대", desc: "상승 쪽이 우세합니다. 평소보다 더 담되 분할로 들어갑니다." },
  Hold: { ko: "보유 · 관망", desc: "방향이 뚜렷하지 않습니다. 지금 비중을 유지하고 제시된 가격에 닿을 때 움직입니다." },
  Underweight: { ko: "비중 축소", desc: "하락 쪽이 우세합니다. 보유분을 줄입니다. 공격형이면 인버스 · 숏 진입 후보입니다." },
  Sell: { ko: "매도", desc: "하락 판단이 강합니다. 정리하거나 숏 · 인버스로 대응합니다." },
  REVIEW: { ko: "판단 보류", desc: "등급을 읽지 못했습니다. 아래 대화 원문을 확인하세요." },
};

/** 트레이더의 행동(3단계). */
export const ACTION: Record<string, Term> = {
  Buy: { ko: "매수", desc: "롱(상승) 진입" },
  Hold: { ko: "관망", desc: "진입하지 않고 기다림" },
  Sell: { ko: "매도", desc: "정리 또는 숏(하락) 진입" },
};

export const BIAS: Record<Bias, Term> = {
  long: { ko: "롱", desc: "상승에 베팅" },
  short: { ko: "숏 · 인버스", desc: "하락에 베팅 (또는 비중 축소)" },
  flat: { ko: "관망", desc: "방향 없음" },
  review: { ko: "판단 보류", desc: "등급 확인 필요" },
};

export function term(table: Record<string, Term>, value: string | null | undefined): Term | null {
  if (!value) return null;
  const key = Object.keys(table).find((k) => value.trim().toLowerCase().startsWith(k.toLowerCase()));
  return key ? table[key] : null;
}

/** 에이전트 메시지 속 영어 항목명 → 한글. */
const LABEL: Record<string, string> = {
  Action: "행동",
  Reasoning: "근거",
  "Entry Price": "진입가",
  "Stop Loss": "손절가",
  "Position Sizing": "포지션 크기",
  Rating: "등급",
  "Executive Summary": "요약",
  "Investment Thesis": "투자 논리",
  "Price Target": "목표가",
  "Time Horizon": "보유 기간",
  Recommendation: "권고",
  Rationale: "근거",
  "Strategic Actions": "실행 계획",
};
const VALUE_TABLES: Record<string, Record<string, Term>> = { Action: ACTION, Rating: RATING, Recommendation: RATING };

/**
 * "**Action**: Sell" → "**행동**: Sell (매도 · 정리 또는 숏(하락) 진입)".
 * 마지막 "FINAL TRANSACTION PROPOSAL: **SELL**" 줄은 위 행동과 같은 말이라 "최종 제안: 매도" 로 줄인다.
 */
export function koreanizeLabels(markdown: string): string {
  return markdown
    .replace(/\*\*([A-Za-z][A-Za-z ]+?)\*\*(\s*:\s*)([^\n]*)/g, (whole, label: string, sep: string, rest: string) => {
      const ko = LABEL[label.trim()];
      if (!ko) return whole;
      const table = VALUE_TABLES[label.trim()];
      const t = table ? term(table, rest) : null;
      const value = t ? `${rest.trim()} (${t.ko} · ${t.desc})` : rest;
      return `**${ko}**${sep}${value}`;
    })
    .replace(/FINAL TRANSACTION PROPOSAL:\s*\*\*(BUY|HOLD|SELL)\*\*/g, (_w, a: string) => {
      const t = term(ACTION, a.charAt(0) + a.slice(1).toLowerCase());
      return `**최종 제안**: ${t ? t.ko : a}`;
    });
}

/** 매매 시간축(backend desk/horizons.py). 서버 config 가 안 오면 이것으로 그린다. */
export const HORIZON: Record<string, { ko: string; holding: string }> = {
  scalp: { ko: "스캘핑", holding: "수 분~1시간" },
  intraday: { ko: "단타", holding: "당일" },
  short_swing: { ko: "2~3일", holding: "2~3 거래일" },
  swing: { ko: "스윙", holding: "1~3주" },
};

/** 판단 레이어(#64 desk/verdict.py)의 의견. 등급(rating)과 따로, 고확률 필터를 거친 결과다. */
export const OPINION: Record<string, Term> = {
  buy: { ko: "매수", desc: "고확률 필터를 통과한 롱 판단입니다." },
  sell: { ko: "매도", desc: "고확률 필터를 통과한 숏 · 축소 판단입니다." },
  hold: { ko: "관망", desc: "방향이 없거나 승률 · 손익비 · 근거가 기준에 못 미칩니다. 아래 진입 조건에 닿을 때 움직입니다." },
  review: { ko: "확인 필요", desc: "등급 또는 승률 · 체크리스트 줄을 읽지 못했습니다. 대화 원문을 보고 직접 판단하세요." },
};

export const FACTOR: Record<string, string> = {
  trend: "추세",
  location: "위치",
  momentum: "모멘텀",
  volume: "거래량",
  catalyst: "촉매",
  regime: "시장 국면",
};

/** 채점 결과(#64 desk/outcome.py). */
export const OUTCOME: Record<string, Term> = {
  target: { ko: "목표 도달", desc: "체결 뒤 손절보다 목표에 먼저 닿았습니다." },
  stop: { ko: "손절", desc: "체결 뒤 목표보다 손절에 먼저 닿았습니다." },
  both: { ko: "같은 봉에 둘 다", desc: "한 봉 안에서 목표와 손절이 모두 닿아 순서를 모릅니다 — 실패로 셉니다." },
  expired: { ko: "기간 만료", desc: "보유 기간 안에 목표 · 손절 어디에도 닿지 않았습니다. R 은 만료 시점 종가 기준입니다." },
  unfilled: { ko: "미체결", desc: "진입 유효 시간 안에 진입가에 닿지 않았습니다." },
  invalid: { ko: "채점 불가 · 레벨", desc: "진입 · 손절 · 목표가 맞지 않거나 이미 지나 있었습니다." },
  unscorable: { ko: "채점 불가", desc: "과거 날짜 분석이거나 봉 데이터가 닿지 않습니다." },
  open: { ko: "보유 중", desc: "체결됐고 보유 기간이 아직 남았습니다." },
  waiting: { ko: "체결 대기", desc: "아직 진입가에 닿지 않았습니다." },
  error: { ko: "채점 오류", desc: "봉 데이터를 받지 못했습니다. 다음 채점 때 다시 봅니다." },
};
