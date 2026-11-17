import { useEffect, useState } from "react";
import { dismissInstallHint, installHintDismissed, promptInstall, useInstallState } from "../install";
import IosInstallGuide from "./IosInstallGuide";

function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine !== false);
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  return online;
}

/** 손가락으로 쓰는 기기에서만 설치 배너를 띄운다 — PC 는 설정의 "앱으로 설치" 칸으로 충분하다. */
const touch = () => window.matchMedia?.("(pointer: coarse)").matches === true;

/**
 * #451 상단 안내 두 가지.
 * - 연결 없음: 기기가 오프라인이면(navigator.onLine) 화면 위에 알린다. 다시 연결되면 사라진다.
 * - 앱으로 설치: 휴대폰에서 아직 설치하지 않았으면 한 번 권한다. 닫으면 이 브라우저에서 다시 안 띄운다.
 *   설치 창이 있는 브라우저(안드로이드 Chrome 등)는 배너의 "설치" 버튼, iOS Safari 는 그림 안내 카드(IosInstallGuide).
 */
export default function AppNotices() {
  const online = useOnline();
  const install = useInstallState();
  const [hidden, setHidden] = useState(installHintDismissed);

  const close = () => {
    dismissInstallHint();
    setHidden(true);
  };
  const offerInstall = !hidden && touch() && install === "available";

  return (
    <>
      {/* 알림 영역은 늘 두고 안쪽만 바꾼다 — 새로 생긴 live region 은 읽지 않는 스크린리더가 있다 */}
      <div role="status" aria-live="polite">
        {!online && (
          <div className="app-notice notice-warn">
            <b>연결 없음</b> — 인터넷에 다시 연결되면 이어서 불러옵니다. 그 전의 숫자는 오래된 값일 수 있습니다.
          </div>
        )}
      </div>
      {offerInstall && (
        <div className="app-notice notice-info install-hint" role="region" aria-label="앱으로 설치">
          <div className="install-hint-row">
            <span>홈 화면에 데스크를 앱으로 설치할 수 있습니다.</span>
            <span className="install-hint-actions">
              <button type="button" onClick={() => promptInstall().then((ok) => ok && close()).catch(() => {})}>설치</button>
              <button type="button" className="link" onClick={close}>닫기</button>
            </span>
          </div>
        </div>
      )}
      <IosInstallGuide />
    </>
  );
}
