import type { FundHolding } from "./types";

type SourceStatus = "available" | "stale" | "missing" | "error" | "disabled";
type Direction = "positive" | "negative" | "neutral";
type RegimeState = "downtrend" | "rebound_candidate" | "recovery_watch" | "undetermined";

export interface MarketOutlookSource {
  status: SourceStatus;
  reason: string | null;
  source?: string;
  checked_at?: string | null;
}

export interface MarketOutlookView {
  id: string;
  market: "us";
  expert: string;
  organization: string;
  source: string;
  source_url: string;
  date_source_url: string | null;
  spoken_on: string | null;
  published_on: string | null;
  stance: string;
  horizon: string;
  summary: string;
  basis: string;
  change: string | null;
  positions: { asset: string; side: string; status: string; as_of: string | null }[];
  age_days: number | null;
  status: "available" | "stale" | "unverified";
}

export interface MarketOutlookFiling {
  fund_id: string;
  name: string;
  cik: string;
  quarter: string;
  period_of_report: string;
  filed_at: string;
  form: string;
  accession: string;
  source_url: string;
  publication_lag_days: number;
  holdings_status: "available" | "missing";
  previous_quarter: string | null;
  holdings: FundHolding[];
}

export interface MarketOutlookEvidence {
  id: string;
  unit: string;
  direction: Direction | null;
  details: Partial<Record<string, number>>;
  source: string | null;
  observed_at: string | null;
  fetched_at: string | null;
  age_days: number | null;
  status: SourceStatus;
  reason: string | null;
  value: number | null;
}

export interface MarketOutlookRegime {
  state: RegimeState;
  supporting: string[];
  opposing: string[];
  next_checks: string[];
  evidence: MarketOutlookEvidence[];
}

export interface MarketOutlookResponse {
  market: "us";
  as_of: string;
  views: MarketOutlookView[];
  filings: MarketOutlookFiling[];
  regime: MarketOutlookRegime;
  sources: { views: MarketOutlookSource; filings: MarketOutlookSource };
}

export const OUTLOOK_STATUS = {
  available: "확인됨",
  stale: "오래된 자료",
  missing: "자료 없음",
  error: "원천 오류",
  disabled: "원천 미연결",
  unverified: "날짜 미확인",
};

export const REGIME_LABEL: Record<RegimeState, string> = {
  downtrend: "하락세 지속",
  rebound_candidate: "단기 반등 후보",
  recovery_watch: "추세 회복 관찰",
  undetermined: "판정 유보",
};

const REASON_LABEL: Partial<Record<string, string>> = {
  source_error: "원천에서 응답을 가져오지 못했습니다",
  no_views: "등록된 공개 관점이 없습니다",
  no_filing: "확인된 공시가 없습니다",
  refresh_failed: "SEC 최신 조회에 실패했습니다",
  refresh_incomplete: "SEC 최신 조회가 완료되지 않았습니다",
  check_overdue: "SEC 최신 확인 시점이 48시간을 넘었습니다",
  latest_session_missing: "최신 마감 자료가 없습니다",
  no_closed_bars: "마감 일봉이 없습니다",
  insufficient_history: "연속 거래일 자료가 부족합니다",
  source_unconfigured: "데이터 공급자가 설정되지 않았습니다",
  timeout: "원천 응답 시간이 초과됐습니다",
  no_observation: "관측 자료가 없습니다",
  invalid_observation: "관측 값을 확인할 수 없습니다",
  publication_lag: "최신 판정에 쓰기에는 관측일이 오래됐습니다",
};

const EVIDENCE_LABEL: Partial<Record<string, string>> = {
  index_rebound: "단기 반등 · 5거래일 수익률",
  index_trend: "중기 추세 · 20·50일 이동평균",
  index_drawdown: "고점 대비 낙폭 · 최근 60거래일",
  volatility: "변동성 · VIX",
  breadth: "시장 폭",
  credit_spread: "신용 스프레드",
};

const STANCE: Partial<Record<string, { label: string; tone: string }>> = {
  bullish: { label: "상승 전망", tone: "up" },
  bearish: { label: "하락 전망", tone: "down" },
  cautious: { label: "신중 전망", tone: "warn" },
  neutral: { label: "중립 전망", tone: "" },
};

export function outlookReason(reason: string | null): string | undefined {
  return reason ? REASON_LABEL[reason] : undefined;
}

export function evidenceLabel(id: string): string {
  return EVIDENCE_LABEL[id] ?? id;
}

export function outlookStance(stance: string): { label: string; tone: string } {
  return STANCE[stance] ?? { label: "방향 미확인", tone: "" };
}

export function sourceLink(url: string | null): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    if ((parsed.protocol === "http:" || parsed.protocol === "https:") && !parsed.username && !parsed.password) {
      return parsed.href;
    }
  } catch {
    // A source without a valid absolute URL remains readable as text.
  }
  return undefined;
}

const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function outlookTime(iso: string): string | undefined {
  const value = Date.parse(iso);
  return Number.isFinite(value) ? `${KST_TIME.format(value)} KST` : undefined;
}
