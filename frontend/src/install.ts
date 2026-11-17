/**
 * #451 홈 화면 설치(PWA). Chrome · Edge(안드로이드 · PC)는 설치할 수 있을 때 `beforeinstallprompt` 를
 * 한 번 보낸다 — 앱 시작 때 잡아 두었다가 설정 · 배너의 "앱으로 설치" 버튼이 띄운다.
 * iOS 는 이 이벤트가 없어 공유 → "홈 화면에 추가" 를 안내한다. 이미 설치한 앱(독립 실행)이면 숨긴다.
 */
import { useEffect, useState } from "react";
import { isIOS, isStandalone } from "./push";

type InstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export type InstallState =
  | "installed"   // 독립 실행 중이거나 방금 설치했다
  | "available"   // 브라우저가 설치 창을 띄울 수 있다
  | "ios"         // iOS Safari — 공유 → 홈 화면에 추가
  | "ios-other"   // iOS 의 다른 브라우저 · 앱 안 브라우저 — Safari 로 열어야 한다
  | "manual";     // 설치 이벤트가 없다(아직 조건 미충족 · Firefox 등) — 브라우저 메뉴로

/** iOS 의 Safari 인가. iOS 의 Chrome · Firefox · Edge 와 카카오톡 · 네이버 같은 앱 안 브라우저는 UA 에 Safari 가 있어도 뺀다. */
export function isIOSSafari(): boolean {
  if (!isIOS()) return false;
  const ua = navigator.userAgent;
  return /Safari\//.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS|GSA\/|KAKAOTALK|NAVER|DaumApps|Instagram|FBAN|FBAV|Line\//.test(ua);
}

let deferred: InstallPrompt | null = null;
let installed = false;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

/** main.tsx 에서 화면을 그리기 전에 한 번 — 이벤트는 페이지를 연 직후에 오고 다시 오지 않는다. */
export function captureInstallPrompt(): void {
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();          // 브라우저 기본 미니 안내 대신 데스크 버튼으로
    deferred = event as InstallPrompt;
    notify();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    notify();
  });
}

export function installState(): InstallState {
  if (installed || isStandalone()) return "installed";
  if (deferred) return "available";
  if (isIOS()) return isIOSSafari() ? "ios" : "ios-other";
  return "manual";
}

export function onInstallChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 지금 설치 상태 — 설치 이벤트 · 설치 완료가 오면 다시 그린다. */
export function useInstallState(): InstallState {
  const [state, setState] = useState<InstallState>(installState);
  useEffect(() => {
    const off = onInstallChange(() => setState(installState()));
    setState(installState());        // 첫 그림과 구독 사이에 온 설치 이벤트도 반영
    return off;
  }, []);
  return state;
}

/** 설치 창을 띄운다. 설치했으면 true. 한 번 쓴 이벤트는 다시 못 쓴다. */
export async function promptInstall(): Promise<boolean> {
  const event = deferred;
  if (!event) return false;
  deferred = null;
  try {
    await event.prompt();
    const { outcome } = await event.userChoice;
    return outcome === "accepted";
  } finally {
    notify();                        // 실패해도 다 쓴 이벤트의 버튼은 내린다
  }
}

const HINT_KEY = "desk.installHint.dismissed";

/** 상단 설치 배너를 닫았는지(이 브라우저에 기억). 저장소를 못 쓰면 닫지 않은 것으로 본다. */
export function installHintDismissed(): boolean {
  try {
    return window.localStorage.getItem(HINT_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissInstallHint(): void {
  try {
    window.localStorage.setItem(HINT_KEY, "1");
  } catch {
    /* 저장 못 하면 이번 화면에서만 닫힌다 */
  }
}

const IOS_GUIDE_KEY = "desk.iosInstallGuide.dismissed";

/** iOS Safari 그림 안내 카드를 닫았는지(이 브라우저에 기억). 저장소를 못 쓰면 닫지 않은 것으로 본다. */
export function iosGuideDismissed(): boolean {
  try {
    return window.localStorage.getItem(IOS_GUIDE_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissIosGuide(): void {
  try {
    window.localStorage.setItem(IOS_GUIDE_KEY, "1");
  } catch {
    /* 저장 못 하면 이번 화면에서만 닫힌다 */
  }
}
