import { useState } from "react";
import { canPip, openMini } from "../miniWindow";

/** #582 설정의 "작은 창" — 늘 위에 뜨는 작은 시세 창을 띄운다(Chrome · Edge 는 PiP, 그 밖에는 작은 팝업) */
export default function MiniSettings() {
  const [notice, setNotice] = useState<string | null>(null);
  const open = async () => {
    const how = await openMini();
    setNotice(how === "pip" ? "작은 창을 띄웠어요. 다른 앱 위에 떠 있어요 — 이 탭을 닫으면 같이 닫혀요."
      : how === "popup" ? "작은 팝업 창으로 열었어요. 이 브라우저는 늘 위에 뜨는 창을 지원하지 않아요."
      : "팝업이 막혔어요. 주소창의 팝업 차단을 풀고 다시 눌러 주세요.");
  };
  return (
    <section className="card settings-card">
      <h2>작은 창</h2>
      <p className="muted">
        회사에서 조용히 띄워 두는 작은 창이에요. 즐겨찾기 시세 · 코스피 외인/기관 수급 · 오늘 마지막 알림을 한 줄(창이 낮으면) 또는
        세로 카드(창이 높으면)로 15초마다 보여 줘요. 창 안의 ◐ 는 조용한 모드(회색조 · ▲▼ 기호), 가 는 글자 크기예요.
      </p>
      <div className="settings-actions">
        <button type="button" onClick={() => void open()}>작은 창 띄우기</button>
      </div>
      {!canPip() && <p className="muted small">이 브라우저는 다른 앱 위에 늘 떠 있는 창을 지원하지 않아 작은 팝업 창으로 열려요(Chrome · Edge 는 지원).</p>}
      {notice && <p className="settings-notice" role="status">{notice}</p>}
    </section>
  );
}
