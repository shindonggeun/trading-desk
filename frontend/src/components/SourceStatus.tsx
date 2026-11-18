import { useEffect, useRef } from "react";
import type { StatusJob } from "../api";
import { relTime, STATE_TEXT, useDeskStatus } from "../deskStatus";

const ORDER: StatusJob["state"][] = ["error", "stopped", "stale", "starting", "running", "fresh", "off"];

/** 한 줄: 원천 · 상태(기호 + 글자, 색만으로 구분하지 않음) · 마지막 성공 · 다음 회차 · 마지막 오류(종류 이름) */
function Row({ j, part }: { j: StatusJob; part?: boolean }) {
  const s = STATE_TEXT[j.state] ?? STATE_TEXT.off;
  const late = j.state === "stale" && j.next_due;
  return (
    <li className={`src-row${part ? " src-part" : ""}${j.state === "off" ? " src-off" : ""}`}>
      <span className="src-name">
        {part && <span aria-hidden="true">└ </span>}{j.label}
        <span className="muted small src-source"> · {j.source}</span>
      </span>
      <span className={`src-state ${s.cls}`}><span aria-hidden="true">{s.mark} </span>{s.text}</span>
      <span className="src-time small">
        {j.state === "off" ? <span className="muted">이 서버에서 쓰지 않음</span> : (
          <>
            <span className="muted">성공 </span>{j.state === "running" && !j.last_ok ? "확인 안 함" : relTime(j.last_ok)}
            {j.next_due && <span className="muted"> · 다음 {late ? <strong>{relTime(j.next_due)} 예정이었음</strong> : relTime(j.next_due)}</span>}
          </>
        )}
      </span>
      {j.last_error && j.state !== "fresh" && j.state !== "off" && (
        <span className="src-error small">오류 {relTime(j.last_error_at)} · {j.last_error}{j.errors > 1 ? ` (${j.errors}번)` : ""}</span>
      )}
    </li>
  );
}

/** #624 설정 → 데이터 원천 상태(#623 GET /api/status). 화면 숫자가 이상할 때 어느 피드가 늦은지 본다 */
export default function SourceStatus() {
  const { data, error, reload } = useDeskStatus();
  const box = useRef<HTMLElement>(null);

  // 상단바 점(#/settings?status)으로 왔으면 이 칸으로 — 표가 그려진 뒤 한 번(위 칸들이 먼저 자라도 다시 맞춘다)
  const scrolled = useRef(false);
  useEffect(() => {
    if (!data || scrolled.current || !/[?&]status(?:[=&]|$)/.test(window.location.hash)) return;
    scrolled.current = true;
    box.current?.scrollIntoView({ block: "start" });
    const again = window.setTimeout(() => box.current?.scrollIntoView({ block: "start" }), 800);
    return () => window.clearTimeout(again);
  }, [data]);

  const tops = (data?.jobs ?? []).filter((j) => !j.parent)
    .sort((a, b) => ORDER.indexOf(a.state) - ORDER.indexOf(b.state));
  const on = tops.filter((j) => j.state !== "off");
  const off = tops.filter((j) => j.state === "off");
  const group = (j: StatusJob) => (
    <li key={j.name} className="src-group">
      <ul className="src-sub">
        <Row j={j} />
        {partsOf(j.name).map((p) => <Row key={p.name} j={p} part />)}
      </ul>
    </li>
  );
  const partsOf = (name: string) => (data?.jobs ?? []).filter((j) => j.parent === name);
  const cooling = (data?.cooldowns ?? []).filter((c) => c.until);
  return (
    <section ref={box} id="status" className="card settings-card source-status">
      <h2>데이터 원천 상태</h2>
      <p className="muted small">
        서버가 뒤에서 받아 오는 자료의 마지막 성공 시각이에요. 화면 숫자가 이상하면 여기서 늦은 피드를 찾아요.
        오류는 종류 이름만 보여요(자세한 내용은 서버 로그).
      </p>
      {error && !data && <p className="muted small">상태를 {error === "준비 중" ? "아직 볼 수 없어요(서버 준비 중)" : "불러오지 못했어요"}.</p>}
      {!data && !error && <p className="muted small">불러오는 중…</p>}
      {data && (
        <>
          <ul className="src-list" aria-label="원천별 상태">{on.map(group)}</ul>
          {off.length > 0 && (
            // 스위치 · 키가 없어 이 서버에서 돌지 않는 작업 — 접어 둔다
            <details className="src-off-list">
              <summary className="muted small">이 서버에서 쓰지 않는 작업 {off.length}개</summary>
              <ul className="src-list">{off.map(group)}</ul>
            </details>
          )}
          <h3 className="src-cool-title">속도 제한(쿨다운)</h3>
          {cooling.length === 0 ? <p className="muted small">지금 기다리는 쿨다운이 없어요.</p> : (
            <ul className="src-cool">
              {cooling.map((c) => (
                <li key={c.name}><span className="st-warn"><span aria-hidden="true">⏳ </span>{c.label}</span>{" "}
                  <span className="small">{relTime(c.until)} 풀림</span></li>
              ))}
            </ul>
          )}
          <div className="settings-actions">
            <button type="button" className="link small" onClick={reload}>다시 읽기</button>
            <span className="muted small">{relTime(data.now)} 기준 · 2분마다 다시 읽어요</span>
          </div>
        </>
      )}
    </section>
  );
}
