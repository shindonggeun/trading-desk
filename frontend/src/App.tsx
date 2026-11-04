import { useEffect, useState } from "react";
import { api, setToken, Unauthorized } from "./api";
import type { Health } from "./types";
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
  const [health, setHealth] = useState<Health | null>(null);
  const [needToken, setNeedToken] = useState(false);
  const [tokenInput, setTokenInput] = useState("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    // /api/health is open; probe a guarded route to learn whether the token is right.
    api.runs().catch((e) => e instanceof Unauthorized && setNeedToken(true));
  }, []);

  const runMatch = route.match(/^#\/runs\/([\w-]+)/);

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" href="#/">
          <span className="brand-mark">TD</span> Trading Desk
        </a>
        <nav>
          <a href="#/" className={!runMatch ? "active" : ""}>대시보드</a>
        </nav>
        <div className="topbar-right">
          {health?.fake_runs && <span className="pill pill-warn">FAKE 모드</span>}
          {health && <span className="pill">broker: {health.broker}</span>}
          {!health && <span className="pill pill-bad">API 연결 안 됨</span>}
        </div>
      </header>

      {needToken ? (
        <form
          className="card token-gate"
          onSubmit={(e) => {
            e.preventDefault();
            setToken(tokenInput.trim());
            window.location.reload();
          }}
        >
          <h2>API 토큰</h2>
          <p className="muted">서버의 DESK_API_TOKEN 값을 입력하세요. 이 브라우저에만 저장됩니다.</p>
          <input type="password" value={tokenInput} onChange={(e) => setTokenInput(e.target.value)} autoFocus />
          <button type="submit">저장</button>
        </form>
      ) : runMatch ? (
        <RunView key={runMatch[1]} runId={runMatch[1]} />
      ) : (
        <Dashboard />
      )}
    </div>
  );
}
