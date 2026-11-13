/** 이 탭에서 앱 안 이동(hashchange)이 있었나 — 큰 차트의 "닫기"가 앱 밖으로 뒤로 가지 않게(#97). */
export const navigation = { moved: false };

/**
 * #341 상단 메뉴 — 대메뉴(대시보드 · 시장 · 분석 · 캘린더) 아래 하위 메뉴. 주소는 예전 그대로(#/live, #/review …)라
 * 링크 · 즐겨찾기가 깨지지 않는다. 설정은 오른쪽 위, 채팅은 오른쪽 아래 위젯(#340).
 */
export type NavItem = { label: string; href: string; match: (route: string) => boolean };
export type NavGroup = { id: string; label: string; items: NavItem[] };

const starts = (...prefixes: string[]) => (route: string) => prefixes.some((p) => route === p || route.startsWith(`${p}?`) || route.startsWith(`${p}/`));

export const NAV: NavGroup[] = [
  { id: "dash", label: "대시보드", items: [{ label: "대시보드", href: "#/", match: (r) => r === "" || r === "#" || r === "#/" || r.startsWith("#/?") }] },
  {
    id: "market", label: "시장", items: [
      { label: "시세판", href: "#/live", match: starts("#/live") },
      { label: "차트", href: "#/charts", match: starts("#/charts", "#/chart") },
      { label: "섹터", href: "#/sectors", match: starts("#/sectors") },
      { label: "주간 지도", href: "#/weekly", match: starts("#/weekly") },
    ],
  },
  {
    id: "analysis", label: "분석", items: [
      { label: "분석 기록", href: "#/history", match: starts("#/history", "#/runs") },
      { label: "매매 복기", href: "#/review", match: starts("#/review") },
      { label: "백테스트", href: "#/backtest", match: starts("#/backtest") },
      { label: "매매 팁", href: "#/tips", match: starts("#/tips") },
    ],
  },
  { id: "calendar", label: "캘린더", items: [{ label: "캘린더", href: "#/calendar", match: starts("#/calendar") }] },
];

/** 지금 주소의 대메뉴 · 하위 메뉴(설정 · 채팅처럼 메뉴에 없는 화면이면 null) */
export function activeNav(route: string): { group: NavGroup; item: NavItem } | null {
  for (const group of NAV) {
    const item = group.items.find((i) => i.match(route));
    if (item) return { group, item };
  }
  return null;
}
