import { lazy } from "react";
import { MARKET_WATCHBOARD_ROUTE } from "./nav";

/**
 * #537 대시보드 밖 화면은 그 화면에 갈 때 받는다 — 첫 화면은 대시보드 · 로그인만 싣는다.
 * 메뉴에 마우스를 올리거나(데스크톱) 포커스하면 prefetchRoute 로 미리 받아 두어, 누를 때 기다리지 않는다.
 */
const load = {
  RunView: () => import("./pages/RunView"),
  Review: () => import("./pages/Review"),
  Live: () => import("./pages/Live"),
  MarketWatchboard: () => import("./pages/MarketWatchboard"),
  Sectors: () => import("./pages/Sectors"),
  History: () => import("./pages/History"),
  ChartView: () => import("./pages/ChartView"),
  Settings: () => import("./pages/Settings"),
  Paper: () => import("./pages/Paper"),
  MiniWindow: () => import("./pages/MiniWindow"),
  DayCallRecord: () => import("./pages/DayCallRecord"),
  MorningBriefPage: () => import("./pages/MorningBriefPage"),
  Backtest: () => import("./pages/Backtest"),
  WeeklyMap: () => import("./pages/WeeklyMap"),
  FuturesView: () => import("./pages/FuturesView"),
  Tips: () => import("./pages/Tips"),
  Funds: () => import("./pages/Funds"),
  Reports: () => import("./pages/Reports"),
  Halving: () => import("./pages/Halving"),
  Charts: () => import("./pages/Charts"),
  CalendarView: () => import("./pages/CalendarView"),
  Chat: () => import("./pages/Chat"),
};

export const RunView = lazy(load.RunView);
export const Review = lazy(load.Review);
export const Live = lazy(load.Live);
export const MarketWatchboard = lazy(load.MarketWatchboard);
export const Sectors = lazy(load.Sectors);
export const History = lazy(load.History);
export const ChartView = lazy(load.ChartView);
export const Settings = lazy(load.Settings);
export const Paper = lazy(load.Paper);
export const MiniWindow = lazy(load.MiniWindow);
export const DayCallRecord = lazy(load.DayCallRecord);
export const MorningBriefPage = lazy(load.MorningBriefPage);
export const Backtest = lazy(load.Backtest);
export const WeeklyMap = lazy(load.WeeklyMap);
export const FuturesView = lazy(load.FuturesView);
export const Tips = lazy(load.Tips);
export const Funds = lazy(load.Funds);
export const Reports = lazy(load.Reports);
export const Halving = lazy(load.Halving);
export const Charts = lazy(load.Charts);
export const CalendarView = lazy(load.CalendarView);
export const Chat = lazy(load.Chat);

/** 주소 앞부분 → 그 화면(App.tsx 의 라우트와 같은 순서 · 규칙) */
const ROUTES: [RegExp, keyof typeof load][] = [
  [/^#\/chart\//, "ChartView"],
  [/^#\/history/, "History"],
  [/^#\/live/, "Live"],
  [MARKET_WATCHBOARD_ROUTE, "MarketWatchboard"],
  [/^#\/sectors/, "Sectors"],
  [/^#\/review/, "Review"],
  [/^#\/charts/, "Charts"],
  [/^#\/chat/, "Chat"],
  [/^#\/calendar/, "CalendarView"],
  [/^#\/tips/, "Tips"],
  [/^#\/funds/, "Funds"],
  [/^#\/reports/, "Reports"],
  [/^#\/halving/, "Halving"],
  [/^#\/paper/, "Paper"],
  [/^#\/day-call/, "DayCallRecord"],
  [/^#\/briefs\/morning/, "MorningBriefPage"],
  [/^#\/futures/, "FuturesView"],
  [/^#\/weekly/, "WeeklyMap"],
  [/^#\/backtest/, "Backtest"],
  [/^#\/settings/, "Settings"],
  [/^#\/runs\//, "RunView"],
];

const started = new Set<string>();

/** 그 주소의 화면 코드를 미리 받는다(한 번만, 실패는 조용히 — 실제로 갈 때 다시 받는다) */
export function prefetchRoute(href: string) {
  const name = ROUTES.find(([re]) => re.test(href))?.[1];
  if (!name || started.has(name)) return;
  started.add(name);
  load[name]().catch(() => started.delete(name));
}
