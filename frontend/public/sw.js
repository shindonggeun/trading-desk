/* #362 서비스 워커 — 수급 전향 푸시(#361)를 알림으로 띄우고, 누르면 장중 수급 차트로 연다.
 * #451 오프라인 안내: 캐시에 두는 것은 안내 페이지(offline.html) 하나뿐이다. 화면 이동(navigate)이
 * 네트워크에서 실패할 때만 그 페이지로 대신하고, API · 자산 요청은 손대지 않는다 — 세션 · 데이터는
 * 절대 캐시하지 않는다. 설치되면 바로 이전 워커를 대신한다. */

const OFFLINE_CACHE = "desk-offline-v1";
const OFFLINE_URL = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    try {
      const cache = await caches.open(OFFLINE_CACHE);
      await cache.add(new Request(OFFLINE_URL, { cache: "reload" }));
    } catch (_e) {
      /* 안내 페이지를 못 받아도 푸시 워커는 설치한다 — 오프라인이면 브라우저 기본 화면이 뜰 뿐 */
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => event.waitUntil((async () => {
  const names = await caches.keys();
  await Promise.all(names.filter((n) => n.startsWith("desk-") && n !== OFFLINE_CACHE).map((n) => caches.delete(n)));
  await self.clients.claim();
})()));

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.mode !== "navigate" || req.method !== "GET") return;   // API · 자산 · SSE: 브라우저 그대로
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  event.respondWith((async () => {
    try {
      return await fetch(req);                                     // 늘 네트워크 — 응답은 저장하지 않는다
    } catch (err) {
      const page = await caches.match(OFFLINE_URL);
      if (page) return page;
      throw err;
    }
  })());
});

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
