/**
 * #362 웹 푸시 — 서비스 워커(public/sw.js) 등록, 이 기기 구독 · 해지, 상태 판정.
 * 서버(#361)는 VAPID 공개키(/api/push/key)를 주고, 구독은 계정마다 따로 저장된다.
 * iOS 는 홈 화면에 추가한 PWA 에서만 푸시가 된다(Safari 탭에서는 PushManager 가 없다).
 */
import { api, type PushKinds } from "./api";

export type PushState =
  | "unsupported"   // 이 브라우저는 서비스 워커 · 푸시가 없다
  | "ios-browser"   // iOS Safari 탭 — 홈 화면에 추가해야 한다
  | "server-off"    // 서버에 VAPID 키가 없다(푸시 꺼짐)
  | "denied"        // 사용자가 알림을 막았다
  | "off"           // 쓸 수 있지만 이 기기는 구독 안 함
  | "on";           // 이 기기 구독 중

export const SW_URL = "/sw.js";

export function isIOS(): boolean {
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Macintosh") && navigator.maxTouchPoints > 1);
}

export function isStandalone(): boolean {
  return window.matchMedia?.("(display-mode: standalone)").matches || (navigator as any).standalone === true;
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/** 앱 시작 때 한 번 — 등록만 하고 권한은 묻지 않는다(설정에서 켤 때 묻는다). */
export function registerServiceWorker(): void {
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;
  navigator.serviceWorker.register(SW_URL).catch(() => {
    /* 등록 실패는 푸시만 못 쓸 뿐 — 화면은 그대로 */
  });
}

function urlBase64ToBytes(base64: string): Uint8Array {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(padded);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration> {
  const existing = await navigator.serviceWorker.getRegistration(SW_URL);
  return existing ?? navigator.serviceWorker.register(SW_URL);
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration(SW_URL);
  return reg ? reg.pushManager.getSubscription() : null;
}

/** 지금 이 기기의 상태(서버 키 확인 포함). */
export async function pushState(signal?: AbortSignal): Promise<PushState> {
  if (!pushSupported()) return isIOS() && !isStandalone() ? "ios-browser" : "unsupported";
  const key = await api.pushKey(signal).catch(() => ({ enabled: false, public_key: null }));
  if (!key.enabled || !key.public_key) return "server-off";
  if (Notification.permission === "denied") return "denied";
  return (await currentSubscription()) ? "on" : "off";
}

export function deviceName(): string {
  const ua = navigator.userAgent;
  const os = /Android/.test(ua) ? "Android" : isIOS() ? "iOS" : /Windows/.test(ua) ? "Windows" : /Mac/.test(ua) ? "Mac" : "기기";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return `${os}${browser ? ` · ${browser}` : ""}${isStandalone() ? " (앱)" : ""}`.slice(0, 80);
}

/** 켜기: 권한을 묻고 구독해 서버에 저장한다. 돌아온 상태로 화면을 바꾼다. */
export async function enablePush(kinds: PushKinds): Promise<PushState> {
  const key = await api.pushKey();
  if (!key.enabled || !key.public_key) return "server-off";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "denied" : "off";
  const reg = await registration();
  await navigator.serviceWorker.ready;
  const sub = (await reg.pushManager.getSubscription())
    ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToBytes(key.public_key) }));
  await api.pushSubscribe(sub.toJSON(), deviceName(), kinds);
  return "on";
}

/** 이 기기 구독의 알림 종류를 바꾼다(같은 endpoint 로 다시 저장). 구독이 없으면 아무것도 안 한다. */
export async function updateKinds(kinds: PushKinds): Promise<void> {
  const sub = await currentSubscription();
  if (sub) await api.pushSubscribe(sub.toJSON(), deviceName(), kinds);
}

/** 끄기: 서버에서 지우고 브라우저 구독도 푼다. */
export async function disablePush(): Promise<PushState> {
  const sub = await currentSubscription();
  if (sub) {
    await api.pushUnsubscribe(sub.endpoint).catch(() => undefined);
    await sub.unsubscribe().catch(() => false);
  }
  return "off";
}

/** 상태마다 사용자가 할 일(한국어). */
export const STATE_TEXT: Record<PushState, string> = {
  unsupported: "이 브라우저는 웹 푸시를 지원하지 않습니다. PC · Android 의 Chrome · Edge 를 쓰세요. 앱을 열어 둔 동안은 화면 위 토스트로 알려 드립니다.",
  "ios-browser": "iPhone · iPad 는 Safari 의 공유 버튼 → '홈 화면에 추가'로 설치한 앱에서만 알림을 받을 수 있습니다. 설치한 앱에서 이 화면을 다시 여세요.",
  "server-off": "서버의 푸시가 꺼져 있습니다(VAPID 키 미설정). 관리자가 서버 설정을 켜야 합니다. 앱을 열어 둔 동안은 토스트로 알려 드립니다.",
  denied: "이 기기에서 알림을 차단했습니다. 브라우저 주소창의 자물쇠(사이트 설정) → 알림 → 허용으로 바꾼 뒤 다시 켜세요.",
  off: "이 기기는 알림을 받지 않고 있습니다.",
  on: "이 기기로 수급 전향 알림을 받습니다.",
};
