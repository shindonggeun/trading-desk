import React from "react";
import { createRoot } from "react-dom/client";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css"; // 글꼴은 번들에 — 외부 CDN 없이(#96)
import App from "./App";
import { registerServiceWorker } from "./push";
import "./styles.css";

registerServiceWorker(); // #362 푸시 알림을 받을 서비스 워커 — 권한은 설정에서 켤 때 묻는다
// 서비스 워커가 navigate 를 못 하는 브라우저: 알림 클릭 주소를 탭에 보내 준다
navigator.serviceWorker?.addEventListener("message", (event) => {
  const url = event.data?.type === "open" ? String(event.data.url ?? "") : "";
  if (url.startsWith(window.location.origin)) window.location.href = url;
});

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
