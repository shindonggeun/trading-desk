import { useEffect, useState } from "react";
import { api, ApiError } from "../api";

// 서버의 challenge 수명 · 시도 횟수(desk/otp.py CHALLENGE_SEC · CHALLENGE_TRIES)와 같다. 넘으면 비밀번호부터 다시.
const CHALLENGE_MS = 5 * 60 * 1000;
const CHALLENGE_TRIES = 5;

function message(err: unknown, wrong: string): string {
  if (err instanceof ApiError && err.status === 429) {
    const minutes = Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60));
    return `실패가 많아 잠시 막혔습니다. 약 ${minutes}분 뒤에 다시 시도하세요.`;
  }
  if (err instanceof ApiError && err.status === 401) return wrong;
  if (err instanceof ApiError && err.status === 503) return "서버에서 OTP 를 확인할 수 없습니다. 관리자 설정(DESK_OTP_KEY)을 확인하세요.";
  return err instanceof Error ? err.message : "로그인하지 못했습니다.";
}

/**
 * 로그인. OTP 를 켠 계정은 2단계다: 이메일 · 비밀번호 → (서버가 otp_required 를 주면) 6자리 코드.
 * OTP 를 끈 계정은 1단계에서 바로 들어간다(docs/security.md §6).
 */
export default function LoginForm({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [fails, setFails] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 5분이 지나면 서버가 challenge 를 버린다 — 화면도 비밀번호 단계로 돌아간다.
  useEffect(() => {
    if (!challenge) return;
    const timer = window.setTimeout(() => {
      setChallenge(null);
      setCode("");
      setError("시간이 지났습니다. 비밀번호부터 다시 입력하세요.");
    }, CHALLENGE_MS);
    return () => window.clearTimeout(timer);
  }, [challenge]);

  async function finish() {
    const me = await api.me();
    onLoggedIn(me.username);
  }

  async function submitPassword(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // 계정은 소문자 이메일로 만든다(desk.cli create-user). 대소문자 · 앞뒤 공백 차이로 실패하지 않게 맞춘다
      const step = await api.login(email.trim().toLowerCase(), password);
      setPassword("");
      if (step?.otp_required) {
        setChallenge(step.challenge);
        setFails(0);
        setCode("");
        setUseRecovery(false);
        return;
      }
      await finish();
    } catch (err) {
      setPassword("");
      setError(message(err, "이메일 또는 비밀번호가 맞지 않습니다."));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      await api.loginCode(challenge, code.trim());
      await finish();
    } catch (err) {
      setCode("");
      if (err instanceof ApiError && err.status === 401) {
        // 같은 challenge 는 서버에서 5번까지다. 다 쓰면 맞는 코드도 401 이니, 헛시도로 IP 가 막히기 전에 처음으로 돌린다.
        if (fails + 1 >= CHALLENGE_TRIES) {
          setChallenge(null);
          setError("코드를 여러 번 틀렸습니다. 비밀번호부터 다시 입력하세요.");
          return;
        }
        setFails(fails + 1);
        setError(useRecovery ? "복구 코드가 맞지 않거나 이미 쓴 코드입니다." : "코드가 맞지 않습니다. 앱에 새로 뜬 코드를 넣어 보세요.");
      } else {
        setError(message(err, "코드가 맞지 않습니다."));
      }
    } finally {
      setBusy(false);
    }
  }

  const restart = () => {
    setChallenge(null);
    setCode("");
    setError(null);
  };

  if (challenge) {
    const codeOk = useRecovery ? code.replace(/[\s-]/g, "").length >= 10 : /^\d{6}$/.test(code);
    return (
      <form className="card login-card" onSubmit={submitCode}>
        <h2>2단계 인증</h2>
        <p className="muted small">
          {useRecovery
            ? "복구 코드 하나를 입력하세요. 한 번 쓴 코드는 다시 쓸 수 없습니다."
            : "OTP 앱(Google Authenticator 등)에 뜬 6자리 코드를 입력하세요."}
        </p>
        <label>
          <span className="muted">{useRecovery ? "복구 코드" : "인증 코드"}</span>
          <input
            key={useRecovery ? "recovery" : "totp"}
            inputMode={useRecovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={useRecovery ? 16 : 6}
            pattern={useRecovery ? undefined : "\\d{6}"}
            placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
            value={code}
            onChange={(e) => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))}
            autoFocus
            required
          />
        </label>
        {error && <p className="login-error" role="alert">{error}</p>}
        <button type="submit" disabled={busy || !codeOk}>
          {busy ? "확인 중…" : "확인"}
        </button>
        <div className="login-links">
          <button type="button" className="link" onClick={() => { setUseRecovery(!useRecovery); setCode(""); setError(null); }}>
            {useRecovery ? "OTP 앱 코드로 입력" : "휴대폰이 없나요? 복구 코드 사용"}
          </button>
          <button type="button" className="link" onClick={restart}>
            처음으로
          </button>
        </div>
      </form>
    );
  }

  return (
    <form className="card login-card" onSubmit={submitPassword}>
      <h2>로그인</h2>
      <label>
        <span className="muted">이메일</span>
        <input
          type="email"
          inputMode="email"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoFocus
          required
        />
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
      <button type="submit" disabled={busy || !email.trim() || !password}>
        {busy ? "확인 중…" : "로그인"}
      </button>
    </form>
  );
}
