import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler, Unauthorized } from "./api";
import type { DeskConfig } from "./types";
import LoginForm from "./components/LoginForm";
import Dashboard from "./pages/Dashboard";
import RunView from "./pages/RunView";
import Review from "./pages/Review";
import Live from "./pages/Live";
import Sectors from "./pages/Sectors";
import History from "./pages/History";
import { activeNav, NAV, navigation } from "./nav";
import ChartView from "./pages/ChartView";
import Settings from "./pages/Settings";
import Backtest from "./pages/Backtest";
import WeeklyMap from "./pages/WeeklyMap";
import FuturesView from "./pages/FuturesView";
import Tips from "./pages/Tips";
import Funds from "./pages/Funds";
import Charts from "./pages/Charts";
import CalendarView from "./pages/CalendarView";
import Chat from "./pages/Chat";
import ChatWidget from "./components/ChatWidget";
import AppNotices from "./components/AppNotices";
import FlowAlertToast, { FLOW_ROUTE } from "./components/FlowAlertToast";
import FundsAlert from "./components/FundsAlert";
import { resetFavorites } from "./favorites";
import TourPrompt from "./components/TourPrompt";
import { goAndStart, startTour, tourForRoute } from "./tourState";

function safeDecode(v: string): string {
  try {
    return decodeURIComponent(v);
  } catch {
    return v;
  }
}

function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || "#/");
  useEffect(() => {
    const onChange = (e: HashChangeEvent) => {
      navigation.moved = true;
      navigation.prev = new URL(e.oldURL).hash || "#/";
      setHash(window.location.hash || "#/");
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const route = useHashRoute();
  // undefined = still asking the server; null = signed out.
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [config, setConfig] = useState<DeskConfig | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api
      .me()
      .then((me) => setUser(me.username))
      .catch((err) => {
        if (err instanceof Unauthorized) return; // the handler already showed the login screen
        setOffline(true);
        setUser(null);
      });
  }, []);

  // 로그아웃 · 세션 만료면 즐겨찾기를 비운다 — 다음 로그인(다른 계정이어도)이 처음부터 읽는다(#135).
  // 로그인 쪽에서 비우면 이미 읽기 시작한 화면(자식 효과가 먼저 돈다)의 읽기를 버리게 된다.
  useEffect(() => {
    if (user === null) resetFavorites();
  }, [user]);

  useEffect(() => {
    if (user) api.config().then(setConfig).catch(() => setConfig(null));
    else setConfig(null);
  }, [user]);

  const logout = useCallback(async () => {
    await api.logout().catch(() => {});
    setUser(null);
    window.location.hash = "#/";
  }, []);

  // #362 알림 · 토스트의 "#/market" → 대시보드 시장 수급 카드의 "오늘 장중" 탭
  useEffect(() => {
    if (route === FLOW_ROUTE) window.location.replace("#/?flow=intraday");
  }, [route]);

  const runMatch = route.match(/^#\/runs\/([\w-]+)/);
  const onSettings = route.startsWith("#/settings");
  const onReview = route.startsWith("#/review");
  const onLive = route.startsWith("#/live");
  const onHistory = route.startsWith("#/history");
  const onSectors = route.startsWith("#/sectors");
  const onBacktest = route.startsWith("#/backtest");
  const weeklyMatch = route.match(/^#\/weekly(?:\?(.*))?$/);
  const chartsMatch = route.match(/^#\/charts(?:\?(.*))?$/);
  const calendarMatch = route.match(/^#\/calendar(?:\?(.*))?$/);
  const chatMatch = route.match(/^#\/chat(?:\?(.*))?$/);
  const tipsMatch = route.match(/^#\/tips(?:\?(.*))?$/);
  const fundsMatch = route.match(/^#\/funds(?:\?(.*))?$/);   // #484 기관 포트폴리오(13F)
  const futuresMatch = route.match(/^#\/futures(?:\?(.*))?$/);   // #444 코스피200 선물 시세
  // 큰 차트(#97): #/chart/<티커>?run=…&interval=…
  const chartMatch = route.match(/^#\/chart\/([^?]+)(?:\?(.*))?$/);

  const here = activeNav(route);

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">TD</span> Trading Desk
        </a>
        {user && (
          <nav aria-label="메뉴" className="nav-main">
            {NAV.map((g) => (
              <a key={g.id} href={g.items[0].href} className={here?.group.id === g.id ? "active" : ""}
                aria-current={here?.group.id === g.id ? "page" : undefined}>{g.label}</a>
            ))}
          </nav>
        )}
        {user && here && here.group.items.length > 1 && (
          <nav aria-label={`${here.group.label} 하위 메뉴`} className="nav-sub">
            {here.group.items.map((i) => (
              <a key={i.href} href={i.href} className={here.item === i ? "active" : ""}
                aria-current={here.item === i ? "page" : undefined}>{i.label}</a>
            ))}
          </nav>
        )}
        <div className="topbar-right">
          {config?.fake_runs && <span className="pill pill-warn">FAKE 모드</span>}
          {offline && <span className="pill pill-bad">API 연결 안 됨</span>}
          {user && (
            <>
              <span className="pill pill-user" title={user}>{user}</span>
              {/* #491 지금 화면의 안내 투어 */}
              <button type="button" className="topbar-help" aria-label="이 화면 안내" title="이 화면 안내"
                onClick={() => { void startTour(tourForRoute(route)).then((ok) => { if (!ok) void goAndStart("dashboard"); }); }}>?</button>
              <a href="#/settings" className={`topbar-settings${onSettings ? " active" : ""}`} aria-label="설정"
                aria-current={onSettings ? "page" : undefined} title="설정">⚙<span className="topbar-settings-label"> 설정</span></a>
              <button type="button" className="logout" onClick={logout}>로그아웃</button>
            </>
          )}
        </div>
      </header>
      {/* #491 처음 로그인한 브라우저에 대시보드 둘러보기 제안 */}
      {user && (route === "" || route === "#/" || route.startsWith("#/?")) && <TourPrompt />}
      {/* #451 연결 없음 · 앱으로 설치 안내 */}
      <AppNotices />

      {user === undefined ? null : user === null ? (
        <LoginForm onLoggedIn={(name) => { setOffline(false); setUser(name); }} />
      ) : chartMatch ? (
        <ChartView key={route} ticker={safeDecode(chartMatch[1])} query={new URLSearchParams(chartMatch[2] ?? "")} />
      ) : onHistory ? (
        <History />
      ) : onLive ? (
        <Live />
      ) : onSectors ? (
        <Sectors />
      ) : onReview ? (
        <Review />
      ) : chartsMatch ? (
        <Charts query={new URLSearchParams(chartsMatch[1] ?? "")} />
      ) : chatMatch ? (
        <Chat key={route.split("thread=")[1] ?? ""} query={new URLSearchParams(chatMatch[1] ?? "")} />
      ) : calendarMatch ? (
        <CalendarView query={new URLSearchParams(calendarMatch[1] ?? "")} />
      ) : tipsMatch ? (
        <Tips key={route} query={new URLSearchParams(tipsMatch[1] ?? "")} />
      ) : fundsMatch ? (
        <Funds query={new URLSearchParams(fundsMatch[1] ?? "")} />
      ) : futuresMatch ? (
        <FuturesView query={new URLSearchParams(futuresMatch[1] ?? "")} />
      ) : weeklyMatch ? (
        <WeeklyMap query={new URLSearchParams(weeklyMatch[1] ?? "")} />
      ) : onBacktest ? (
        <Backtest />
      ) : onSettings ? (
        <Settings />
      ) : runMatch ? (
        <RunView key={runMatch[1]} runId={runMatch[1]} />
      ) : (
        <Dashboard />
      )}
      {/* #340 어디서든 오른쪽 아래에서 여는 AI 채팅(채팅 크게 보기 화면에선 숨김) */}
      {user && <ChatWidget />}
      {/* #362 장중 수급 전향을 앱 안 토스트로 */}
      {user && <FlowAlertToast />}
      {/* #484 새 13F 공시 */}
      {user && <FundsAlert />}
    </div>
  );
}
