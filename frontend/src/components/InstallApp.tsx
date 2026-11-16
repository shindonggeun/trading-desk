import { useEffect, useState } from "react";
import { installState, onInstallChange, promptInstall, type InstallState } from "../install";

/** iOS 공유 버튼 모양(네모 위로 화살표) — 안내 문장 안에서 어떤 버튼인지 알아보게 */
export function ShareIcon() {
  return (
    <svg className="install-share" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v12" /><path d="M8 7l4-4 4 4" /><path d="M6 11H5a1 1 0 0 0-1 1v8a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-8a1 1 0 0 0-1-1h-1" />
    </svg>
  );
}

export function useInstallState(): InstallState {
  const [state, setState] = useState<InstallState>(installState);
  useEffect(() => {
    const off = onInstallChange(() => setState(installState()));
    setState(installState());        // 첫 그림과 구독 사이에 온 설치 이벤트도 반영
    return off;
  }, []);
  return state;
}

/** iOS: 공유 → 홈 화면에 추가 → 추가 */
export function IosSteps({ id }: { id?: string }) {
  return (
    <ol className="install-steps" id={id}>
      <li>Safari 아래(아이패드는 위)의 공유 버튼 <ShareIcon /><span className="sr-only">(공유)</span> 을 누릅니다.</li>
      <li>목록을 내려 <b>홈 화면에 추가</b>를 고릅니다.</li>
      <li>오른쪽 위 <b>추가</b>를 누르면 홈 화면에 데스크 아이콘이 생깁니다.</li>
    </ol>
  );
}

/** #451 설정의 "앱으로 설치" 칸 — 설치 버튼(안드로이드 · PC Chrome) · iOS 안내 · 그 밖의 브라우저 안내. */
export default function InstallApp() {
  const state = useInstallState();
  const [notice, setNotice] = useState<string | null>(null);

  const install = async () => {
    setNotice(null);
    try {
      const done = await promptInstall();
      setNotice(done ? "설치했습니다. 홈 화면 · 앱 목록의 데스크 아이콘으로 여세요." : "설치를 취소했습니다.");
    } catch {
      setNotice("설치 창을 띄우지 못했습니다. 브라우저 메뉴의 '앱 설치'를 써 보세요.");
    }
  };

  return (
    <section className="card settings-card install-app" id="install">
      <h2>앱으로 설치</h2>
      <p className="muted">홈 화면에 아이콘을 두고 주소창 없는 창으로 엽니다. 로그인은 브라우저와 같이 유지되고,
        iOS 는 설치한 앱에서만 푸시 알림을 받습니다.</p>

      {state === "installed" && <p className="settings-notice" role="status">앱으로 실행 중이거나 이미 설치했습니다.</p>}

      {state === "available" && (
        <div className="settings-actions">
          <button type="button" onClick={install}>앱으로 설치</button>
        </div>
      )}

      {state === "ios" && <IosSteps />}

      {state === "manual" && (
        <p className="settings-notice notice-info" role="status">
          이미 설치했다면 홈 화면 · 앱 목록의 데스크 아이콘으로 여세요. 아니면 이 브라우저는 지금 설치 버튼을 주지 않습니다. Chrome · Edge 는 주소창 오른쪽의 설치 아이콘이나 메뉴의
          ‘앱 설치’ · ‘홈 화면에 추가’를 쓰세요(페이지를 잠시 써야 나타나기도 합니다). Firefox 데스크톱은 설치를 지원하지 않습니다.
        </p>
      )}

      {notice && <p className="settings-notice" role="status">{notice}</p>}
    </section>
  );
}
