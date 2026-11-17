import { useState } from "react";
import { dismissIosGuide, iosGuideDismissed, useInstallState } from "../install";

// #451 iOS Safari 홈 화면 추가 안내: 세 단계를 그림 + 문구로. iOS 는 설치 API 가 없어 사람이 직접 눌러야 한다.
// 그림은 실제 화면을 흉내 낸 단순한 선 그림이고, 눌러야 할 곳만 강조색(.hl)으로 칠한다.

function StepShare() {
  return (
    <svg viewBox="0 0 96 120" aria-hidden="true">
      <rect className="ln" x="8" y="4" width="80" height="112" rx="12" />
      <rect className="mute" x="16" y="16" width="64" height="8" rx="3" />
      <rect className="mute" x="16" y="30" width="48" height="6" rx="3" />
      <rect className="mute" x="16" y="42" width="56" height="6" rx="3" />
      <line className="ln" x1="8" y1="92" x2="88" y2="92" />
      <path className="ln" d="M20 104l-4-4 4-4M76 96l4 4-4 4" />
      <circle className="hl-bg" cx="48" cy="104" r="10" />
      <g className="hl">
        <path d="M48 97v9" /><path d="M45 100l3-3 3 3" />
        <path d="M44 103h-1v7h10v-7h-1" />
      </g>
    </svg>
  );
}

function StepAdd() {
  return (
    <svg viewBox="0 0 96 120" aria-hidden="true">
      <rect className="ln" x="8" y="4" width="80" height="112" rx="12" />
      <rect className="mute" x="8" y="40" width="80" height="76" rx="0" />
      <rect className="row" x="14" y="48" width="68" height="14" rx="3" />
      <rect className="hl-bg" x="14" y="66" width="68" height="16" rx="3" />
      <rect className="hl" x="20" y="69" width="10" height="10" rx="2" />
      <path className="hl" d="M25 71.5v5M22.5 74h5" />
      <rect className="hl-fill" x="35" y="72" width="40" height="4" rx="2" />
      <rect className="row" x="14" y="86" width="68" height="14" rx="3" />
    </svg>
  );
}

function StepConfirm() {
  return (
    <svg viewBox="0 0 96 120" aria-hidden="true">
      <rect className="ln" x="8" y="4" width="80" height="112" rx="12" />
      <rect className="mute" x="16" y="14" width="18" height="6" rx="3" />
      <rect className="hl-bg" x="60" y="10" width="24" height="14" rx="4" />
      <rect className="hl-fill" x="65" y="15" width="14" height="4" rx="2" />
      <line className="ln" x1="8" y1="30" x2="88" y2="30" />
      <rect className="ln" x="18" y="40" width="20" height="20" rx="5" />
      <rect className="mute" x="44" y="46" width="34" height="6" rx="3" />
      <rect className="mute" x="18" y="70" width="60" height="6" rx="3" />
    </svg>
  );
}

/** 세 단계 그림 + 문구. 설정 화면과 안내 카드가 같이 쓴다. */
export function IosStepsPictured({ id }: { id?: string }) {
  return (
    <ol className="ios-guide-steps" id={id}>
      <li>
        <StepShare />
        <span><b>1</b> Safari 아래(아이패드는 위) <b>공유</b> 버튼을 누릅니다. 안 보이면 ⋯ → 공유.</span>
      </li>
      <li>
        <StepAdd />
        <span><b>2</b> 목록을 내려 <b>홈 화면에 추가</b>를 고릅니다.</span>
      </li>
      <li>
        <StepConfirm />
        <span><b>3</b> 오른쪽 위 <b>추가</b>를 누르면 홈 화면에 아이콘이 생깁니다.</span>
      </li>
    </ol>
  );
}

/**
 * iOS Safari 에서만, 아직 홈 화면 앱(standalone)으로 열지 않았을 때 한 번 띄우는 안내 카드.
 * 닫으면 이 브라우저에 기억한다(localStorage, 못 쓰면 이번 화면에서만 닫힌다).
 */
export default function IosInstallGuide() {
  const state = useInstallState();
  const [hidden, setHidden] = useState(iosGuideDismissed);
  if (state !== "ios" || hidden) return null;
  const close = () => {
    dismissIosGuide();
    setHidden(true);
  };
  return (
    <section className="app-notice notice-info ios-guide" aria-labelledby="ios-guide-title">
      <div className="ios-guide-head">
        <h2 id="ios-guide-title">홈 화면에 데스크 추가하기</h2>
        <button type="button" className="link" onClick={close}>닫기</button>
      </div>
      <p className="muted small">앱처럼 주소창 없이 열리고, iOS 는 홈 화면에 추가한 앱에서만 푸시 알림을 받습니다.</p>
      <IosStepsPictured />
    </section>
  );
}
