import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler, Unauthorized } from "./api";
import type { DeskConfig } from "./types";
import LoginForm from "./components/LoginForm";
import Dashboard from "./pages/Dashboard";
import RunView from "./pages/RunView";
import Review from "./pages/Review";
import Live from "./pages/Live";
import History from "./pages/History";
import { navigation } from "./nav";
import ChartView from "./pages/ChartView";
import Settings from "./pages/Settings";

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
    const onChange = () => {
      navigation.moved = true;
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

  useEffect(() => {
    if (user) api.config().then(setConfig).catch(() => setConfig(null));
    else setConfig(null);
  }, [user]);

  const logout = useCallback(async () => {
    await api.logout().catch(() => {});
    setUser(null);
    window.location.hash = "#/";
  }, []);

  const runMatch = route.match(/^#\/runs\/([\w-]+)/);
  const onSettings = route.startsWith("#/settings");
  const onReview = route.startsWith("#/review");
  const onLive = route.startsWith("#/live");
  const onHistory = route.startsWith("#/history");
  // 큰 차트(#97): #/chart/<티커>?run=…&interval=…
  const chartMatch = route.match(/^#\/chart\/([^?]+)(?:\?(.*))?$/);

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">TD</span> Trading Desk
        </a>
        {user && (
          <nav aria-label="메뉴">
            <a href="#/" className={!runMatch && !onSettings && !onReview && !onLive && !onHistory ? "active" : ""}>대시보드</a>
            <a href="#/history" className={onHistory ? "active" : ""}>분석 기록</a>
            <a href="#/live" className={onLive ? "active" : ""}>실시간</a>
            <a href="#/review" className={onReview ? "active" : ""}>매매 복기</a>
            <a href="#/settings" className={onSettings ? "active" : ""}>설정</a>
          </nav>
        )}
        <div className="topbar-right">
          {config?.fake_runs && <span className="pill pill-warn">FAKE 모드</span>}
          {config && <span className="pill pill-broker">broker: {config.broker}</span>}
          {offline && <span className="pill pill-bad">API 연결 안 됨</span>}
          {user && (
            <>
              <span className="pill pill-user" title={user}>{user}</span>
              <button type="button" className="logout" onClick={logout}>로그아웃</button>
            </>
          )}
        </div>
      </header>

      {user === undefined ? null : user === null ? (
        <LoginForm onLoggedIn={(name) => { setOffline(false); setUser(name); }} />
      ) : chartMatch ? (
        <ChartView key={route} ticker={safeDecode(chartMatch[1])} query={new URLSearchParams(chartMatch[2] ?? "")} />
      ) : onHistory ? (
        <History />
      ) : onLive ? (
        <Live />
      ) : onReview ? (
        <Review />
      ) : onSettings ? (
        <Settings />
      ) : runMatch ? (
        <RunView key={runMatch[1]} runId={runMatch[1]} />
      ) : (
        <Dashboard />
      )}
    </div>
  );
}
