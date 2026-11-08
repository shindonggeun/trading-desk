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

/** 주간 지도의 위치 이름(#205 카드 · #235 차트 탭) */
export const WEEKLY_POSITION: Record<"above_black" | "between" | "below_red", string> = {
  above_black: "검정선 위", between: "검정선 아래 · 빨간선 위", below_red: "빨간선 아래",
};

/** #235 그 종목의 차트 탭을 주봉으로 */
export const chartsLink = (symbol: string) => `#/charts?t=${encodeURIComponent(symbol)}&iv=1w`;

export const weeklyLink = (symbol: string) => `#/weekly?symbol=${encodeURIComponent(symbol)}`;
