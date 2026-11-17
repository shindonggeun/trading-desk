import { useState } from "react";
import { neverTour, startTour, tourSeen } from "../tourState";

// #491 처음 로그인한 브라우저에 대시보드 둘러보기를 한 번 제안한다. "나중에"는 이번 창에서만, "다시 보지 않기"는 기억.
const LATER_KEY = "desk.tour.later";

function later(): boolean {
  try { return sessionStorage.getItem(LATER_KEY) === "1"; } catch { return false; }
}

export default function TourPrompt() {
  const [hidden, setHidden] = useState(() => tourSeen("dashboard") || later());
  if (hidden) return null;
  const close = () => setHidden(true);
  return (
    <div className="tour-prompt" role="region" aria-label="화면 안내 제안">
      <span>처음이신가요? 대시보드를 1분 안에 둘러보세요.</span>
      <span className="tour-prompt-actions">
        <button type="button" onClick={() => { close(); void startTour("dashboard"); }}>둘러보기</button>
        <button type="button" className="link small" onClick={() => { try { sessionStorage.setItem(LATER_KEY, "1"); } catch { /* 이번 화면에서만 */ } close(); }}>나중에</button>
        <button type="button" className="link small" onClick={() => { neverTour("dashboard"); close(); }}>다시 보지 않기</button>
      </span>
    </div>
  );
}
