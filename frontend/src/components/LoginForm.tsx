import { useState } from "react";
import { api, ApiError } from "../api";

export default function LoginForm({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(username.trim(), password);
      const me = await api.me();
      onLoggedIn(me.username);
    } catch (err) {
      setPassword("");
      if (err instanceof ApiError && err.status === 429) {
        const minutes = Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60));
        setError(`로그인 실패가 많아 잠시 막혔습니다. 약 ${minutes}분 뒤에 다시 시도하세요.`);
      } else if (err instanceof ApiError && err.status === 401) {
        setError("아이디 또는 비밀번호가 맞지 않습니다.");
      } else {
        setError(err instanceof Error ? err.message : "로그인하지 못했습니다.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card login-card" onSubmit={submit}>
      <h2>로그인</h2>
      <label>
        <span className="muted">아이디</span>
        <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus required />
      </label>
      <label>
        <span className="muted">비밀번호</span>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {error && <p className="login-error" role="alert">{error}</p>}
      <button type="submit" disabled={busy || !username.trim() || !password}>
        {busy ? "확인 중…" : "로그인"}
      </button>
    </form>
  );
}
