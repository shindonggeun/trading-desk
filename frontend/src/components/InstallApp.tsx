import { useState } from "react";
import { promptInstall, useInstallState } from "../install";
import { IosStepsPictured } from "./IosInstallGuide";

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

      {state === "ios" && <IosStepsPictured />}

      {state === "ios-other" && (
        <p className="settings-notice notice-info" role="status">
          iOS 는 <b>Safari</b> 에서만 홈 화면에 추가할 수 있습니다. 이 주소를 Safari 로 열어 공유 → ‘홈 화면에 추가’ 를 쓰세요.
        </p>
      )}

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
