import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, clearApiCache, setUnauthorizedHandler, Unauthorized } from "./api";
import type { DeskConfig } from "./types";
import LoginForm from "./components/LoginForm";
import Dashboard from "./pages/Dashboard";
import SectionBoundary from "./components/SectionBoundary";
import ChunkFailed from "./components/ChunkFailed";
import { activeNav, NAV, navigation } from "./nav";
import { openMini } from "./miniWindow";
// #537 대시보드 밖 화면은 갈 때 받는다(메뉴에 올리면 미리)
import {
  Backtest, CalendarView, Charts, ChartView, Chat, DayCallRecord, FuturesView, Funds, Halving, History, Live, MiniWindow, MorningBriefPage, Paper, prefetchRoute,
  Reports, Review, RunView, Sectors, Settings, Tips, WeeklyMap,
} from "./lazyPages";
import ChatWidget from "./components/ChatWidget";
import AppNotices from "./components/AppNotices";
import ScreenHelp from "./components/ScreenHelp";
import FlowAlertToast, { FLOW_ROUTE } from "./components/FlowAlertToast";
import FundsAlert from "./components/FundsAlert";
import StatusDot from "./components/StatusDot";
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

const SESSION_CHANGE_KEY = "desk-session-change";

function announceSessionChange() {
  try {
    localStorage.setItem(SESSION_CHANGE_KEY, `${Date.now()}-${Math.random()}`);
  } catch {
    // The current tab still updates when browser storage is unavailable.
  }
}

export default function App() {
  const route = useHashRoute();
  // undefined = still asking the server; null = signed out.
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [config, setConfig] = useState<DeskConfig | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    let stale = false;
    const onSessionChange = (event: StorageEvent) => {
      if (event.key !== SESSION_CHANGE_KEY || !event.newValue) return;
      stale = true;
      clearApiCache();
      resetFavorites();
      setUser(undefined);
      window.location.reload();
    };
    window.addEventListener("storage", onSessionChange);
    setUnauthorizedHandler(() => { if (!stale) setUser(null); });
    api
      .me()
      .then((me) => { if (!stale) setUser(me.username); })
      .catch((err) => {
        if (stale || err instanceof Unauthorized) return;
        setOffline(true);
        setUser(null);
      });
    return () => {
      stale = true;
      window.removeEventListener("storage", onSessionChange);
    };
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
    await api.logout().then(announceSessionChange).catch(() => {});
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
  const reportsMatch = route.match(/^#\/reports(?:\/([\w-]+))?(?:\?.*)?$/);   // #496 초보자 리포트(알림 url /#/reports/{id})
  const halvingMatch = route.match(/^#\/halving(?:\?.*)?$/);   // #515 비트코인 반감기 사이클
  const onPaper = route === "#/paper" || route.startsWith("#/paper?");   // #528 모의 투자
  const onDayCall = route === "#/day-call" || route.startsWith("#/day-call?");   // #573 오늘 방향 적중 기록
  const morningMatch = route.match(/^#\/briefs\/morning(?:\?(.*))?$/);   // #573 아침 시황(알림 url, #571)
  const futuresMatch = route.match(/^#\/futures(?:\?(.*))?$/);   // #444 코스피200 선물 시세
  // 큰 차트(#97): #/chart/<티커>?run=…&interval=…
  const chartMatch = route.match(/^#\/chart\/([^?]+)(?:\?(.*))?$/);

  const here = activeNav(route);
  // #561 휴대폰 하위 메뉴: 고른 항목을 보이는 곳으로 옮기고, 줄 밖에 더 있으면 그 끝을 흐린다
  const subNav = useRef<HTMLElement>(null);
  const [subMore, setSubMore] = useState({ left: false, right: false });
  const measureSub = useCallback(() => {
    const el = subNav.current;
    if (!el) return;
    const left = el.scrollLeft > 1, right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setSubMore((m) => (m.left === left && m.right === right ? m : { left, right }));
  }, []);
  useEffect(() => {
    const el = subNav.current;
    const active = el?.querySelector<HTMLElement>("a.active");
    if (el && active && el.scrollWidth > el.clientWidth) {
      const target = active.offsetLeft - (el.clientWidth - active.offsetWidth) / 2;
      el.scrollLeft = Math.max(0, Math.min(target, el.scrollWidth - el.clientWidth));
    }
    measureSub();
  }, [route, measureSub]);
  useEffect(() => {
    window.addEventListener("resize", measureSub);
    return () => window.removeEventListener("resize", measureSub);
  }, [measureSub]);

  // #582 작은 창(팝업으로 연 #/mini) — 머리글 · 메뉴 없이 그 화면만. 세션이 끝나면 화면 안에서 안내한다
  if (route.startsWith("#/mini")) return <Suspense fallback={null}><MiniWindow /></Suspense>;

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
                onPointerEnter={() => prefetchRoute(g.items[0].href)} onFocus={() => prefetchRoute(g.items[0].href)}
                aria-current={here?.group.id === g.id ? "page" : undefined}>{g.label}</a>
            ))}
          </nav>
        )}
        {user && here && here.group.items.length > 1 && (
          <nav aria-label={`${here.group.label} 하위 메뉴`} className={`nav-sub${subMore.left ? " more-left" : ""}${subMore.right ? " more-right" : ""}`}
            ref={subNav} onScroll={measureSub}>
            {here.group.items.map((i) => (
              <a key={i.href} href={i.href} className={here.item === i ? "active" : ""}
                onPointerEnter={() => prefetchRoute(i.href)} onFocus={() => prefetchRoute(i.href)}
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
                onClick={() => { void startTour(tourForRoute(route)).then((r) => { if (r === "empty") void goAndStart("dashboard"); }); }}>?</button>
              {/* #624 늦거나 멈춘 데이터 원천이 있을 때만 — 누르면 설정의 상태 칸 */}
              <StatusDot />
              {/* #582 회사에서 조용히 띄워 두는 작은 창 */}
              <button type="button" className="topbar-mini" aria-label="작은 창 띄우기" title="작은 창 띄우기(늘 위)"
                onClick={() => { void openMini(); }}>▭</button>
              <a href="#/settings" className={`topbar-settings${onSettings ? " active" : ""}`} aria-label="설정"
                onPointerEnter={() => prefetchRoute("#/settings")} onFocus={() => prefetchRoute("#/settings")}
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
      {user && here && !chartMatch && <ScreenHelp id={here.item.id} />}

      {/* #537 화면 코드를 받는 동안은 짧은 안내 — 머리줄 · 메뉴는 그대로 */}
      {/* 배포 뒤 오래 열어 둔 탭은 옛 화면 조각이 없어 받기에 실패한다 — 앱 전체가 내려가지 않게 이 자리만 안내(화면을 바꾸면 다시 시도) */}
      <SectionBoundary key={route.split("?")[0]} fallback={<main className="page-loading"><ChunkFailed /></main>}>
      <Suspense fallback={<main className="page-loading"><p className="empty-state">화면 불러오는 중…</p></main>}>
      {user === undefined ? null : user === null ? (
        <LoginForm onLoggedIn={(name) => {
          setOffline(false);
          setUser(name);
          announceSessionChange();
        }} />
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
      ) : reportsMatch ? (
        <Reports id={reportsMatch[1] ?? null} />
      ) : halvingMatch ? (
        <Halving />
      ) : onPaper ? (
        <Paper />
      ) : onDayCall ? (
        <DayCallRecord />
      ) : morningMatch ? (
        <MorningBriefPage query={new URLSearchParams(morningMatch[1] ?? "")} />
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
      </Suspense>
      </SectionBoundary>
      {/* #340 어디서든 오른쪽 아래에서 여는 AI 채팅(채팅 크게 보기 화면에선 숨김) */}
      {user && <ChatWidget />}
      {/* #362 장중 수급 전향을 앱 안 토스트로 */}
      {user && <FlowAlertToast />}
      {/* #484 새 13F 공시 */}
      {user && <FundsAlert />}
    </div>
  );
}
