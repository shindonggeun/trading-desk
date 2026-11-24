import type { ScreenHelpId } from "./screenHelp";

/** 이 탭에서 앱 안 이동(hashchange)이 있었나 — 큰 차트의 "닫기"가 앱 밖으로 뒤로 가지 않게(#97). */
export const navigation: { moved: boolean; prev: string | null } = { moved: false, prev: null };  // prev: 직전 앱 안 주소(#435)

/** 목적별 메뉴. 페이지와 알림이 사용하는 기존 hash를 그대로 가리킨다. */
export type NavItem = { id: ScreenHelpId; label: string; href: string; match: (route: string) => boolean };
export type NavGroup = { id: string; label: string; items: NavItem[] };

const starts = (...prefixes: string[]) => (route: string) => prefixes.some((p) => route === p || route.startsWith(`${p}?`) || route.startsWith(`${p}/`));
const item = (id: ScreenHelpId, label: string, href: string, ...aliases: string[]): NavItem =>
  ({ id, label, href, match: starts(href, ...aliases) });
export const MARKET_FLOW_MENU = "#/?flow=intraday&view=flow";
export const MARKET_WATCHBOARD_ROUTE = /^#\/market-watchboard(?:\?|$)/;
export const isMarketFlowMenuRoute = (route: string) =>
  route.startsWith("#/?") && /[?&]flow=intraday(?:&|$)/.test(route) && /[?&]view=flow(?:&|$)/.test(route);

export const NAV: NavGroup[] = [
  { id: "dash", label: "대시보드", items: [{
    id: "dashboard", label: "대시보드", href: "#/",
    match: (route) => !isMarketFlowMenuRoute(route) && (route === "" || route === "#" || route === "#/" || route.startsWith("#/?")),
  }] },
  {
    id: "market", label: "시장", items: [
      item("live", "실시간 시세", "#/live"),
      { id: "watchboard", label: "시장 모니터", href: "#/market-watchboard", match: (route) => MARKET_WATCHBOARD_ROUTE.test(route) },
      { id: "flow", label: "수급·포지셔닝", href: MARKET_FLOW_MENU, match: isMarketFlowMenuRoute },
      item("futures", "선물 시세", "#/futures"),
      item("sectors", "섹터", "#/sectors"),
      item("weekly", "주간 지도", "#/weekly"),
      item("halving", "반감기 사이클", "#/halving"),
      item("calendar", "시장 일정", "#/calendar"),
    ],
  },
  {
    id: "analysis", label: "분석", items: [
      item("history", "분석 기록", "#/history", "#/runs"),
      item("backtest", "백테스트", "#/backtest"),
      item("tips", "매매 팁", "#/tips"),
      item("funds", "기관 포트폴리오", "#/funds"),
      item("reports", "리포트", "#/reports"),
      item("morning", "아침 시황", "#/briefs/morning"),
    ],
  },
  { id: "chart", label: "차트", items: [item("charts", "차트", "#/charts", "#/chart")] },
  {
    id: "operations", label: "운영", items: [
      item("review", "매매 복기", "#/review"),
      item("paper", "모의 투자", "#/paper"),
      item("dayCall", "오늘 방향 기록", "#/day-call"),
      item("settings", "설정", "#/settings"),
    ],
  },
];

/** 지금 주소의 대메뉴 · 하위 메뉴. 독립 화면은 null이다. */
export function activeNav(route: string): { group: NavGroup; item: NavItem } | null {
  for (const group of NAV) {
    const item = group.items.find((i) => i.match(route));
    if (item) return { group, item };
  }
  return null;
}
