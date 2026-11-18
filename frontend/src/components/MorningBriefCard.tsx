import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { fakeMorningBrief, fakeMorningBriefs } from "../briefsFake";
import { kstStamp } from "../dayCall";
import type { MorningBrief, MorningBriefItem } from "../types";

const dev404 = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;

function List({ title, items, mark, cls }: { title: string; items: string[]; mark?: string; cls?: string }) {
  if (!items.length) return null;
  return (
    <div className={`brief-block ${cls ?? ""}`}>
      <h4>{mark && <span className="brief-mark" aria-hidden="true">{mark}</span>}{title}</h4>
      <ul>{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  );
}

/**
 * #573 아침 시황(#571) — 오늘 브리핑의 헤드라인 · 핵심 숫자, 펼치면 수급 · 끝난/남은 재료 · 일정 · 좋게/잘못 읽기 쉬운 것 · 마무리.
 * 지난 브리핑은 날짜로 고른다. `open` 이면 처음부터 펼친다(알림으로 연 화면).
 */
export default function MorningBriefCard({ date: wanted, open = false }: { date?: string; open?: boolean }) {
  const [date, setDate] = useState<string | undefined>(wanted);
  const [brief, setBrief] = useState<MorningBrief | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "none" | "failed">("loading");
  const [list, setList] = useState<MorningBriefItem[]>([]);
  const [expanded, setExpanded] = useState(open);

  useEffect(() => {
    let alive = true;
    setState("loading");
    api.morningBrief(date)
      .then((b) => { if (alive) { setBrief(b); setState("ok"); } })
      .catch((e: unknown) => {
        if (!alive) return;
        if (dev404(e)) { setBrief(fakeMorningBrief(date)); setState("ok"); }
        else if (e instanceof ApiError && e.status === 404) setState("none");
        else setState("failed");
      });
    return () => { alive = false; };
  }, [date]);

  useEffect(() => {
    let alive = true;
    api.morningBriefs(20).then((l) => alive && setList(l.items))
      .catch((e: unknown) => { if (alive && dev404(e)) setList(fakeMorningBriefs().items); });
    return () => { alive = false; };
  }, []);

  const picker = list.length > 1 && (
    <label className="brief-pick small">
      <span className="muted">지난 브리핑</span>
      <select value={date ?? brief?.date ?? ""} onChange={(e) => setDate(e.target.value || undefined)}>
        {list.map((it) => <option key={it.id} value={it.date}>{it.date}</option>)}
      </select>
    </label>
  );

  if (state === "loading") return <p className="muted small">불러오는 중…</p>;
  if (state === "failed") return <p className="muted small">아침 시황을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>;
  if (state === "none" || !brief) {
    return (
      // #618 비었을 때는 한 줄 — 넓은 화면 맨 위 줄은 이 표시(morning-brief-empty)로 오늘 방향에 폭을 넘긴다
      <div className="morning-brief morning-brief-empty">
        <p className="muted small">{date ? `${date} 아침 시황이 없어요.` : "아직 아침 시황이 없어요. 평일 오전 7시쯤 만들어져요."}</p>
        {picker}
      </div>
    );
  }
  const b = brief;
  return (
    <article className="morning-brief" aria-label={`${b.date} 아침 시황`}>
      <div className="brief-head">
        <span className="muted small">
          {b.date} · {kstStamp(b.created_at)} 작성{b.source === "rules" ? " · 데스크 문장(모델 없이)" : ""}{b.generating ? " · 새로 만드는 중…" : ""}
        </span>
        {picker}
      </div>
      <h3 className="brief-title">{b.title}</h3>
      {b.numbers.length > 0 && <ul className="brief-numbers">{b.numbers.slice(0, expanded ? undefined : 3).map((n, i) => <li key={i}>{n}</li>)}</ul>}
      {b.day_call && <p className="brief-daycall small"><span className="muted">오늘 방향</span> {b.day_call}</p>}
      {expanded && (
        <div className="brief-body">
          {b.kr_flow && <div className="brief-block"><h4>전날 국장 수급</h4><p>{b.kr_flow}</p></div>}
          <List title="끝난 재료" items={b.done} />
          <List title="남은 재료" items={b.pending} />
          <List title="앞으로의 일정" items={b.schedule} />
          <List title="좋게 읽을 것" mark="🟢 ＋" items={b.good} cls="brief-good" />
          <List title="잘못 읽기 쉬운 것" mark="🔴 ！" items={b.bad} cls="brief-bad" />
          {b.closing && <p className="brief-closing">{b.closing}</p>}
          {b.missing.length > 0 && <p className="muted small">받지 못한 자료: {b.missing.join(", ")} — 그 부분은 빠져 있어요.</p>}
        </div>
      )}
      <button type="button" className="link small" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
        {expanded ? "접기" : "전체 읽기"}
      </button>
    </article>
  );
}
