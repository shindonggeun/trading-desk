import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { api, ApiError, type Me } from "../api";
import MarketListEditor from "../components/MarketListEditor";
import InstallApp from "../components/InstallApp";
import TourSettings from "../components/TourSettings";
import PaperSettings from "../components/PaperSettings";
import PushSettings from "../components/PushSettings";
import MiniSettings from "../components/MiniSettings";
import SourceStatus from "../components/SourceStatus";
import LineWatchSettings from "../components/LineWatchSettings";
import HoldingWatchSettings from "../components/HoldingWatchSettings";

type Step =
  | { kind: "idle" }
  | { kind: "enable-password" }
  | { kind: "enable-scan"; secret: string; uri: string; qr: string }
  | { kind: "codes"; codes: string[] }
  | { kind: "disable" };

function explain(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return err.message.includes("password") ? "비밀번호가 맞지 않습니다." : "코드가 맞지 않습니다. 앱에 새로 뜬 코드를 넣어 보세요.";
    if (err.status === 409) return "이미 사용 중입니다.";
    if (err.status === 429) return `시도가 많아 잠시 막혔습니다. 약 ${Math.max(1, Math.ceil((err.retryAfter ?? 60) / 60))}분 뒤에 다시 하세요.`;
    if (err.status === 503) return "서버에 OTP 키(DESK_OTP_KEY)가 없어 지금은 쓸 수 없습니다.";
  }
  return err instanceof Error ? err.message : "처리하지 못했습니다.";
}

function download(codes: string[]) {
  const text = `Trading Desk 복구 코드 (${new Date().toLocaleString("ko-KR")})\n각 코드는 한 번만 쓸 수 있습니다.\n\n${codes.join("\n")}\n`;
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = "trading-desk-recovery-codes.txt";
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);   // 바로 풀면 일부 브라우저가 다운로드를 취소한다
}

/** 설정 — OTP 사용 · 사용 안 함. 켜면 지금 세션은 그대로, 다음 로그인부터 코드를 묻는다. */
export default function Settings() {
  const [me, setMe] = useState<Me | null>(null);
  const [step, setStep] = useState<Step>({ kind: "idle" });
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = () => api.me().then(setMe).catch(() => {});

  // 클립보드에 복구 코드를 오래 두지 않는다(Win+V 기록 · 클라우드 동기화). 60초 뒤 덮어쓴다.
  const copyCodes = async (codes: string[]) => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setNotice("복사했습니다. 비밀번호 관리자 등에 붙여 넣은 뒤 클립보드를 비우세요 — 60초 뒤 자동으로 지웁니다.");
      window.setTimeout(() => navigator.clipboard.writeText("").catch(() => {}), 60_000);
    } catch {
      setNotice("복사하지 못했습니다(브라우저 권한). 직접 옮겨 적거나 파일로 저장하세요.");
    }
  };

  useEffect(() => {
    reload();
  }, []);

  const go = (next: Step) => {
    setStep(next);
    setPassword("");
    setCode("");
    setError(null);
  };

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(explain(err));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  const startEnable = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const { secret, uri } = await api.otpSetup(password);
      const qr = await QRCode.toDataURL(uri, { margin: 1, width: 220 });
      go({ kind: "enable-scan", secret, uri, qr });
    });
  };

  const confirmEnable = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const { recovery_codes } = await api.otpEnable(code.trim());
      go({ kind: "codes", codes: recovery_codes });
      setNotice("OTP 를 켰습니다. 다음 로그인부터 비밀번호 다음에 코드를 묻습니다. 다른 브라우저의 로그인은 끊겼습니다.");
      reload();
    });
  };

  const confirmDisable = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      await api.otpDisable(password, code.trim());
      go({ kind: "idle" });
      setNotice("OTP 를 껐습니다. 다음 로그인부터 비밀번호만 묻습니다. 다른 브라우저의 로그인은 끊겼습니다.");
      reload();
    });
  };

  if (!me) return <div className="settings"><p className="muted">불러오는 중…</p></div>;

  return (
    <div className="settings">
      <section className="card settings-card">
        <h2>설정</h2>
        <div className="settings-row">
          <div>
            <strong>2단계 인증 (OTP)</strong>
            <p className="muted small">
              로그인할 때 비밀번호 다음에 OTP 앱(Google Authenticator 등)의 6자리 코드를 한 번 더 묻습니다.
            </p>
          </div>
          <span className={`pill ${me.otp_enabled ? "pill-on" : ""}`}>{me.otp_enabled ? "사용 중" : "사용 안 함"}</span>
        </div>
        {me.otp_enabled && me.recovery_codes_left !== null && (
          <p className="muted small">
            남은 복구 코드 {me.recovery_codes_left}개
            {me.recovery_codes_left <= 3 && " — 얼마 남지 않았습니다. 껐다가 다시 켜면 새로 10개를 받습니다."}
          </p>
        )}
        {notice && <p className="settings-notice" role="status">{notice}</p>}

        {step.kind === "idle" && (
          <div className="settings-actions">
            {me.otp_enabled ? (
              <button type="button" className="secondary" onClick={() => { setNotice(null); go({ kind: "disable" }); }}>
                사용 안 함으로 바꾸기
              </button>
            ) : me.otp_available ? (
              <button type="button" onClick={() => { setNotice(null); go({ kind: "enable-password" }); }}>
                사용하기
              </button>
            ) : (
              <p className="login-error">서버에 OTP 키(DESK_OTP_KEY)가 없어 아직 켤 수 없습니다.</p>
            )}
          </div>
        )}

        {step.kind === "enable-password" && (
          <form className="settings-form" onSubmit={startEnable}>
            <p className="small">1/3 · 본인 확인을 위해 비밀번호를 입력하세요.</p>
            <input type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} autoFocus required />
            {error && <p className="login-error" role="alert">{error}</p>}
            <div className="settings-actions">
              <button type="submit" disabled={busy || !password}>{busy ? "확인 중…" : "다음"}</button>
              <button type="button" className="link" onClick={() => go({ kind: "idle" })}>취소</button>
            </div>
          </form>
        )}

        {step.kind === "enable-scan" && (
          <form className="settings-form" onSubmit={confirmEnable}>
            <p className="small">2/3 · OTP 앱에서 이 QR 을 찍으세요. 10분 안에 아래에 앱의 6자리 코드를 넣어야 켜집니다.</p>
            <img className="otp-qr" src={step.qr} alt="OTP 등록 QR 코드" width={220} height={220} />
            <details>
              <summary className="small">QR 을 못 찍으면 — 직접 입력할 키</summary>
              <code className="otp-secret">{step.secret.match(/.{1,4}/g)?.join(" ")}</code>
            </details>
            <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} pattern="\d{6}" placeholder="123456"
              value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus required />
            {error && <p className="login-error" role="alert">{error}</p>}
            <div className="settings-actions">
              <button type="submit" disabled={busy || !/^\d{6}$/.test(code)}>{busy ? "확인 중…" : "켜기"}</button>
              <button type="button" className="link" onClick={() => go({ kind: "idle" })}>취소</button>
            </div>
          </form>
        )}

        {step.kind === "codes" && (
          <div className="settings-form">
            <p className="small">
              3/3 · <strong>복구 코드 10개</strong> — 휴대폰을 잃었을 때 코드 대신 하나씩 씁니다.
              <strong> 지금 한 번만 보여 줍니다.</strong> 비밀번호 관리자 등 안전한 곳에 보관하세요.
            </p>
            <ol className="recovery-codes">
              {step.codes.map((c) => <li key={c}><code>{c}</code></li>)}
            </ol>
            <div className="settings-actions">
              <button type="button" className="secondary" onClick={() => copyCodes(step.codes)}>복사</button>
              <button type="button" className="secondary" onClick={() => {
                download(step.codes);
                setNotice("파일로 저장했습니다. 다운로드 폴더에 두지 말고 안전한 곳으로 옮기거나, 옮겨 적은 뒤 지우세요.");
              }}>파일로 저장</button>
              <button type="button" onClick={() => go({ kind: "idle" })}>보관했습니다</button>
            </div>
          </div>
        )}

        {step.kind === "disable" && (
          <form className="settings-form" onSubmit={confirmDisable}>
            <p className="small">끄려면 비밀번호와 지금 OTP 코드(또는 복구 코드)가 필요합니다.</p>
            <input type="password" autoComplete="current-password" placeholder="비밀번호" value={password}
              onChange={(e) => setPassword(e.target.value)} autoFocus required />
            <input autoComplete="one-time-code" autoCapitalize="none" spellCheck={false} placeholder="6자리 코드 또는 복구 코드"
              value={code} onChange={(e) => setCode(e.target.value)} required />
            {error && <p className="login-error" role="alert">{error}</p>}
            <div className="settings-actions">
              <button type="submit" disabled={busy || !password || !code.trim()}>{busy ? "확인 중…" : "사용 안 함으로 바꾸기"}</button>
              <button type="button" className="link" onClick={() => go({ kind: "idle" })}>취소</button>
            </div>
          </form>
        )}
      </section>
      <InstallApp />
      {/* #491 화면 안내 다시 보기 */}
      <PaperSettings />
      <TourSettings />
      <PushSettings />
      <LineWatchSettings />
      <HoldingWatchSettings />
      <MiniSettings />
      <MarketListEditor />
      {/* #624 데이터 원천 상태 — 상단바 점(#/settings?status)이 여기로 */}
      <SourceStatus />
    </div>
  );
}
