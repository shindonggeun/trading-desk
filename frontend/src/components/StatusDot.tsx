import { STATE_TEXT, TROUBLE, useDeskStatus } from "../deskStatus";

/** #624 상단바의 작은 점 — 늦음 · 오류 · 멈춤인 원천이 있을 때만 보인다(글자로도 몇 개인지). 누르면 설정 → 데이터 원천 상태 */
export default function StatusDot() {
  const { data } = useDeskStatus();
  const bad = (data?.jobs ?? []).filter((j) => TROUBLE.includes(j.state));
  if (!bad.length) return null;
  const worst = bad.some((j) => j.state !== "stale") ? "st-bad" : "st-warn";
  const names = bad.map((j) => `${j.label} ${STATE_TEXT[j.state].text}`).join(", ");
  return (
    <a href="#/settings?status" className={`topbar-status ${worst}`} title={`데이터 원천: ${names}`}
      aria-label={`데이터 원천 ${bad.length}곳 늦음 · 오류 — 상태 보기`}>
      <span aria-hidden="true">●</span><span className="topbar-status-n">{bad.length}</span>
    </a>
  );
}
