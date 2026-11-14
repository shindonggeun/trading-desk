/* #362 서비스 워커 — 수급 전향 푸시(#361)를 알림으로 띄우고, 누르면 장중 수급 차트로 연다.
 * 캐시는 하지 않는다(오프라인 앱이 아니다): 설치되면 바로 이전 워커를 대신한다. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

/** 서버 payload: {title, body, tag, url} — url 은 같은 사이트 안의 경로만 쓴다. */
function sameSite(url) {
  try {
    const u = new URL(url || "/", self.location.origin);
    return u.origin === self.location.origin ? u.href : self.location.origin + "/";
  } catch (_e) {
    return self.location.origin + "/";
  }
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_e) {
    data = { body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "트레이딩 데스크";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    tag: data.tag || "desk",
    renotify: true,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    data: { url: sameSite(data.url || "/#/market") },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || self.location.origin + "/#/market";
  event.waitUntil((async () => {
    const tabs = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    // 이미 열린 데스크 탭이 있으면 그 탭을 그 주소로 옮기고 앞으로
    for (const tab of tabs) {
      if (new URL(tab.url).origin === self.location.origin) {
        await tab.focus();
        if ("navigate" in tab) {
          try {
            await tab.navigate(target);
          } catch (_e) {
            tab.postMessage({ type: "open", url: target });
          }
        } else {
          tab.postMessage({ type: "open", url: target });
        }
        return;
      }
    }
    await self.clients.openWindow(target);
  })());
});
