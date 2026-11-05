export type Team = "analyst" | "research" | "trading" | "risk" | "portfolio" | "system";

export interface AgentInfo {
  id: string;
  name: string;
  short: string;
  team: Team;
  /** Only some runs have it (the leverage analyst speaks on leveraged products): listed once it speaks. */
  onlyIfSpoken?: boolean;
}

// Pipeline order: the rail on the run page lists agents in this order.
export const AGENTS: AgentInfo[] = [
  { id: "market_analyst", name: "기술적 분석가", short: "MK", team: "analyst" },
  { id: "sentiment_analyst", name: "심리 분석가", short: "SE", team: "analyst" },
  { id: "news_analyst", name: "뉴스 분석가", short: "NW", team: "analyst" },
  { id: "fundamentals_analyst", name: "재무 분석가", short: "FD", team: "analyst" },
  { id: "bull_researcher", name: "강세 리서처", short: "BU", team: "research" },
  { id: "bear_researcher", name: "약세 리서처", short: "BE", team: "research" },
  { id: "research_manager", name: "리서치 매니저", short: "RM", team: "research" },
  { id: "trader", name: "트레이더", short: "TR", team: "trading" },
  { id: "aggressive_analyst", name: "공격형 리스크", short: "AG", team: "risk" },
  { id: "conservative_analyst", name: "보수형 리스크", short: "CO", team: "risk" },
  { id: "neutral_analyst", name: "중립형 리스크", short: "NE", team: "risk" },
  { id: "portfolio_manager", name: "포트폴리오 매니저", short: "PM", team: "portfolio" },
  { id: "leverage_analyst", name: "레버리지 분석가", short: "LV", team: "portfolio", onlyIfSpoken: true },
];

export const TEAM_LABEL: Record<Team, string> = {
  analyst: "애널리스트",
  research: "리서치 토론",
  trading: "트레이딩",
  risk: "리스크 토론",
  portfolio: "최종 결정",
  system: "시스템",
};

// The analyst ids the API takes ("social" is the sentiment analyst's config name).
export const ANALYST_CHOICES: { key: string; agent: string; label: string }[] = [
  { key: "market", agent: "market_analyst", label: "기술적" },
  { key: "social", agent: "sentiment_analyst", label: "심리" },
  { key: "news", agent: "news_analyst", label: "뉴스" },
  { key: "fundamentals", agent: "fundamentals_analyst", label: "재무" },
];

export function agentInfo(id: string | null): AgentInfo {
  return AGENTS.find((a) => a.id === id) ?? { id: id ?? "system", name: id ?? "시스템", short: "··", team: "system" };
}
