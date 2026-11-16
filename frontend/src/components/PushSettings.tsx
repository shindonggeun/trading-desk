import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { disablePush, enablePush, isIOS, isStandalone, pushState, STATE_TEXT, updateKinds, type PushState } from "../push";
import { loadToastMarkets, pushKindsOf, saveToastMarkets, TOAST_MARKETS, type ToastMarkets } from "./FlowAlertToast";

/** #362 설정의 "알림" 칸 — 이 기기 푸시 켜기 · 끄기, 시험 알림, 앱 안 토스트에 보일 시장. */
export default function PushSettings() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [markets, setMarkets] = useState<ToastMarkets>(loadToastMarkets);

  useEffect(() => {
    let alive = true;
    pushState().then((s) => alive && setState(s)).catch(() => alive && setState("unsupported"));
    return () => { alive = false; };
  }, []);

  const run = async (work: () => Promise<PushState | void>, done?: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const next = await work();
      if (next) setState(next);
      if (done) setNotice(done);
    } catch (e) {
      setNotice(e instanceof ApiError ? `실패 — ${e.message}` : "실패 — 잠시 뒤 다시 해 주세요");
    } finally {
      setBusy(false);
    }
  };

  const toggleMarket = (key: keyof ToastMarkets) => {
    const next = { ...markets, [key]: !markets[key] };
    setMarkets(next);
    saveToastMarkets(next);
    // 이 기기가 푸시를 받는 중이면 서버의 알림 종류(#361)도 같이
    if (state === "on") run(() => updateKinds(pushKindsOf(next)), "이 기기의 알림 시장을 바꿨습니다.");
  };

  return (
    <section className="card settings-card push-settings">
      <h2>알림</h2>
      <p className="muted">외국인 수급이 순매수 · 순매도로 돌아서면(전향) 알려 드립니다.</p>

      <div className="settings-row">
        <span>이 기기 푸시</span>
        <span className={`pill${state === "on" ? " pill-on" : state === "denied" || state === "server-off" ? " pill-warn" : ""}`}>
          {state === null ? "확인 중…" : state === "on" ? "켜짐" : state === "off" ? "꺼짐" : "쓸 수 없음"}
        </span>
      </div>
      {state && <p className="settings-notice" role="status">{STATE_TEXT[state]}</p>}

      <div className="settings-actions">
        {state === "off" && (
          <button type="button" disabled={busy} onClick={() => run(() => enablePush(pushKindsOf(markets)), "이 기기에서 알림을 받습니다.")}>
            이 기기 알림 켜기
          </button>
        )}
        {state === "on" && (
          <>
            <button type="button" disabled={busy}
              onClick={() => run(async () => { const r = await api.pushTest(); return r.sent ? undefined : "off"; },
                "시험 알림을 보냈습니다. 몇 초 안에 오지 않으면 브라우저 · OS 의 알림 설정을 확인하세요.")}>
              시험 알림 보내기
            </button>
            <button type="button" className="secondary" disabled={busy} onClick={() => run(disablePush, "이 기기 알림을 껐습니다.")}>
              이 기기 알림 끄기
            </button>
          </>
        )}
      </div>
      {notice && <p className="settings-notice" role="status">{notice}</p>}

      <h3>알림 받을 시장</h3>
      <p className="muted small">이 기기의 푸시와 앱 안 토스트에 같이 적용됩니다. 앱을 열어 둔 장중에는 푸시가 꺼진 기기에도 화면 위에 알려 드립니다.</p>
      <div className="push-markets">
        {TOAST_MARKETS.map(({ key, label }) => (
          <button key={key} type="button" className={`chip ${markets[key] ? "chip-on" : ""}`} aria-pressed={markets[key]}
            disabled={busy} onClick={() => toggleMarket(key)}>
            {label}
          </button>
        ))}
      </div>

      {isIOS() && !isStandalone() && (
        <p className="muted small">iPhone · iPad: Safari 공유 버튼 → "홈 화면에 추가" 로 설치한 뒤, 설치한 앱에서 이 화면을 열어 알림을 켜세요(iOS 16.4 이상).</p>
      )}
    </section>
  );
}
