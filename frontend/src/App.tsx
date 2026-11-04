import { useCallback, useEffect, useState } from "react";
import { api, setUnauthorizedHandler, Unauthorized } from "./api";
import type { DeskConfig } from "./types";
import LoginForm from "./components/LoginForm";
import Dashboard from "./pages/Dashboard";
import RunView from "./pages/RunView";
function useHashRoute(): string {
  const [hash, setHash] = useState(() => window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
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

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">TD</span> Trading Desk
        </a>
        {user && (
          <nav>
            <a href="#/" className={!runMatch ? "active" : ""}>대시보드</a>
          </nav>
        )}
        <div className="topbar-right">
          {config?.fake_runs && <span className="pill pill-warn">FAKE 모드</span>}
          {config && <span className="pill">broker: {config.broker}</span>}
          {offline && <span className="pill pill-bad">API 연결 안 됨</span>}
          {user && (
            <>
              <span className="pill">{user}</span>
              <button type="button" className="logout" onClick={logout}>로그아웃</button>
            </>
          )}
        </div>
      </header>

      {user === undefined ? null : user === null ? (
        <LoginForm onLoggedIn={(name) => { setOffline(false); setUser(name); }} />
      ) : runMatch ? (
        <RunView key={runMatch[1]} runId={runMatch[1]} />
      ) : (
        <Dashboard />
      )}
    </div>
  );
}
