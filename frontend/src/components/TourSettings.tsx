import { goAndStart, type TourId } from "../tourState";

// #491 설정의 "화면 안내" 칸 — 언제든 다시 보기
const LIST: { id: TourId; label: string }[] = [
  { id: "dashboard", label: "대시보드" },
  { id: "tips", label: "매매 팁" },
  { id: "funds", label: "기관 포트폴리오" },
];

export default function TourSettings() {
  return (
    <section className="card settings-card" id="tour">
      <h2>화면 안내</h2>
      <p className="muted">실제 화면 위에 말풍선을 띄워 순서대로 알려 줍니다. ← → 로 이동, Esc 로 닫습니다.</p>
      <div className="settings-actions">
        {LIST.map((t) => <button key={t.id} type="button" onClick={() => void goAndStart(t.id)}>{t.label} 둘러보기</button>)}
      </div>
      <p className="muted small">분석 · 차트 안내는 분석 화면 오른쪽 위 "?" 버튼으로 봅니다.</p>
    </section>
  );
}
