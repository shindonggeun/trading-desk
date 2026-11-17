import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import ExplainArticle from "../components/ExplainArticle";
import { clearLatestReport } from "../latestReport";
import { FAKE_REPORTS, fakeList } from "../reportsFake";
import type { ReportDetail, ReportList, ReportSummary } from "../types";

// #496 리포트(#/reports · #/reports/{id}): 2~3일마다 데스크가 쓰는 초보자용 분석 글(#495)을 블로그 글처럼 읽고 지난 리포트를 고른다.
// 알림(웹 푸시)의 url 이 /#/reports/{id} 다. 본문 모양은 "쉽게 보기"(#494)와 같은 ExplainArticle.

const fmtDay = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 10) : d.toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "short" });
};
const KIND_KO = { index: "지수", favorite: "즐겨찾기" } as const;
// 개발 서버에서 #495 API 가 없으면(404) 예시로 화면을 본다 — 운영은 오류를 그대로
const fakeable = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;

function Subject({ r }: { r: ReportSummary }) {
  return <span className="muted small">{r.subject.name} <span className="rp-sym">{r.subject.symbol}</span> · {KIND_KO[r.subject.kind] ?? r.subject.kind}</span>;
}

// 같은 글이 두 번 붙지 않게 id 로 합친다
const union = (a: ReportSummary[], b: ReportSummary[]) => [...a, ...b.filter((x) => !a.some((y) => y.id === x.id))];

function useReportList() {
  const [list, setList] = useState<ReportList | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [fake, setFake] = useState(false);
  /** 성공이면 목록, 예시로 바꿨으면 null, 실패면 false */
  const load = (before?: string): Promise<ReportList | null | false> => api.reports(before)
    .then((v) => {
      setFake(false); setFailed(null);
      setList((cur) => {
        if (!cur) return v;
        if (before) return { ...v, items: union(cur.items, v.items) };
        // 첫 페이지를 다시 읽음(만드는 중 폴링) — "더 보기"로 붙여 둔 지난 글은 그대로 두고 새 글만 앞에
        return { ...v, items: union(v.items, cur.items), next_before: cur.items.length > v.items.length ? cur.next_before : v.next_before };
      });
      return v;
    })
    .catch((e) => {
      if (fakeable(e)) { setFake(true); setList(fakeList()); return null; }
      setFailed(e instanceof Error ? e.message : "불러오지 못했습니다");
      return false;
    });
  return { list, failed, fake, load, setList };
}

function ListView() {
  const { list, failed, fake, load, setList } = useReportList();
  const [note, setNote] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const wasGenerating = useRef(false);
  useEffect(() => { void load(); return () => window.clearTimeout(timer.current); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // 만드는 중이면 몇 초마다 다시 읽어 끝나면 맨 위에 새 글을 보인다. 한 번 읽기에 실패해도(순단 · 재배포) 계속 다시 읽는다
  useEffect(() => {
    window.clearTimeout(timer.current);
    const generating = !!list?.generating;
    if (wasGenerating.current && !generating) clearLatestReport();   // 새 글이 나왔으니 "오늘 볼 것" 한 줄도 다시 읽게
    wasGenerating.current = generating;
    if (!generating) return;
    const poll = () => {
      timer.current = window.setTimeout(() => void load().then((v) => { if (v === false) poll(); }), 5000);
    };
    poll();
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps
  const loadMore = (before: string) => { setMore(true); void load(before).finally(() => setMore(false)); };
  const make = () => {
    setNote(null);
    api.createReport()
      .then(() => setList((cur) => (cur ? { ...cur, generating: true } : cur)))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 409) { setNote("이미 만드는 중이에요 — 끝나면 맨 위에 보입니다."); setList((cur) => (cur ? { ...cur, generating: true } : cur)); return; }
        // 429: 직전 리포트 뒤 10분 안 — 만드는 중이 아니므로 폴링하지 않고 서버 문구를 그대로
        if (e instanceof ApiError && e.status === 429) { setNote(e.message || "방금 만든 리포트가 있어요. 10분 뒤에 다시 만들 수 있어요"); return; }
        setNote(e instanceof Error ? e.message : "만들기를 시작하지 못했습니다");
      });
  };
  return (
    <>
      <header className="rp-head">
        <h1>리포트 <span className="muted small">2~3일마다 데스크가 쓰는 초보자용 분석 글</span></h1>
        <div className="rp-make">
          <button type="button" onClick={make} disabled={!list || list.generating || fake}>{list?.generating ? "만드는 중…" : "지금 하나 만들기"}</button>
          {list?.next_due && !list.generating && <span className="muted small">다음 자동 작성 {fmtDay(list.next_due)}</span>}
        </div>
      </header>
      {list?.generating && <p className="rp-generating" role="status">새 리포트를 쓰는 중입니다 — 보통 수십 초, 설명 그림까지 그리면 몇 분(최대 3분쯤) 걸려요. 다 되면 알림이 갑니다.</p>}
      {note && <p className="muted small" role="status">{note}</p>}
      {fake && <p className="app-notice notice-warn">예시 데이터 — 개발 서버에서 #495 API 없이 화면을 보기 위한 글이며 실제 리포트가 아닙니다.</p>}
      {failed && <p className="error">리포트를 불러오지 못했습니다: {failed}</p>}
      {!list && !failed && <p className="muted">불러오는 중…</p>}
      {list && list.items.length === 0 && <p className="muted">아직 리포트가 없습니다. "지금 하나 만들기"로 첫 글을 받아 보세요.</p>}
      <ul className="rp-list">
        {(list?.items ?? []).map((r) => (
          <li key={r.id}>
            <a className="card rp-item" href={`#/reports/${encodeURIComponent(r.id)}`}>
              <span className="rp-date muted small">{fmtDay(r.created_at)}</span>
              <strong className="rp-title">{r.title}</strong>
              <span className="rp-sum">{r.summary}</span>
              <Subject r={r} />
            </a>
          </li>
        ))}
      </ul>
      {list?.next_before && <button type="button" className="link" disabled={more} onClick={() => loadMore(list.next_before as string)}>{more ? "불러오는 중…" : "지난 리포트 더 보기"}</button>}
    </>
  );
}

function DetailView({ id }: { id: string }) {
  const [r, setR] = useState<ReportDetail | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [fake, setFake] = useState(false);
  const [older, setOlder] = useState<ReportSummary | null>(null);
  const [newer, setNewer] = useState<ReportSummary | null>(null);
  useEffect(() => {
    let alive = true;
    setR(null); setFailed(null);
    api.report(id)
      .then((v) => alive && (setR(v), setFake(false)))
      .catch((e) => {
        if (!alive) return;
        const f = fakeable(e) ? FAKE_REPORTS.find((x) => x.id === id) : undefined;
        if (f) { setR(f); setFake(true); return; }
        setFailed(e instanceof ApiError && e.status === 404 ? "이 리포트를 찾지 못했습니다." : e instanceof Error ? e.message : "불러오지 못했습니다");
      });
    window.scrollTo({ top: 0 });
    return () => { alive = false; };
  }, [id]);
  // 지난 글은 이 글 바로 앞 페이지(before=id)의 첫 글 — 첫 페이지(20편) 밖의 옛 글 · 알림 링크에서도 이어 간다.
  // 다음(더 새) 글은 첫 페이지에 이 글이 있을 때만(대개 최근 글이라 충분하다)
  useEffect(() => {
    let alive = true;
    const fakeAt = () => { const items = fakeList().items; return { items, at: items.findIndex((x) => x.id === id) }; };
    api.reports(id)
      .then((v) => alive && setOlder(v.items[0] ?? null))
      .catch((e) => { if (alive && fakeable(e)) { const { items, at } = fakeAt(); setOlder(at >= 0 ? items[at + 1] ?? null : null); } });
    api.reports()
      .then((v) => { const at = v.items.findIndex((x) => x.id === id); if (alive) setNewer(at > 0 ? v.items[at - 1] : null); })
      .catch((e) => { if (alive && fakeable(e)) { const { items, at } = fakeAt(); setNewer(at > 0 ? items[at - 1] : null); } });
    return () => { alive = false; };
  }, [id]);
  const nav = (
    <nav className="rp-nav" aria-label="다른 리포트">
      {older ? <a href={`#/reports/${encodeURIComponent(older.id)}`}>← 지난 리포트<span className="muted small"> {older.title}</span></a> : <span />}
      {newer ? <a href={`#/reports/${encodeURIComponent(newer.id)}`} className="rp-newer">다음 리포트 →<span className="muted small"> {newer.title}</span></a> : <span />}
    </nav>
  );
  return (
    <>
      <a href="#/reports" className="link small">← 리포트 목록</a>
      {failed && <p className="error">{failed}</p>}
      {!r && !failed && <p className="muted">불러오는 중…</p>}
      {r && (
        <>
          {fake && <p className="app-notice notice-warn">예시 데이터 — 실제 리포트가 아닙니다.</p>}
          <header className="rp-article-head">
            <span className="muted small">{fmtDay(r.created_at)}{r.as_of ? ` (${r.as_of} 종가 기준)` : ""} · <Subject r={r} /></span>
            <h1>{r.title}</h1>
          </header>
          <ExplainArticle data={r} className="easy-view rp-article"
            afterHook={r.why && (
              <aside className="rp-why">
                <strong>왜 이 주제?</strong> {r.why.text}
                {r.why.candidates.length > 1 && (
                  <span className="muted small"> 다른 후보: {r.why.candidates.filter((c) => c.symbol !== r.subject.symbol).map((c) => `${c.name}(${c.reasons.join(" · ")})`).join(", ")}</span>
                )}
              </aside>
            )}
            beforeStages={r.similar.length > 0 && (
              <section className="ev-section rp-similar">
                <h3>과거 비슷한 장면</h3>
                <ul>{r.similar.map((s, i) => <li key={i}><strong>{s.date}</strong> {s.what} → {s.after}</li>)}</ul>
                <p className="muted small">데스크 기록에서 찾은 장면이에요. 그때와 같다는 보장은 없어요.</p>
              </section>
            )}
            footer={<p className="muted small">숫자는 모두 데스크가 계산한 값이고, 문장은 {r.source === "ai" ? "AI 가 쉽게 풀어 쓴 것" : "데스크 규칙으로 쓴 것"}입니다. 예측이 아니라 조건과 대응입니다.</p>} />
          {nav}
        </>
      )}
    </>
  );
}

export default function Reports({ id }: { id: string | null }) {
  return <main className="reports-page">{id ? <DetailView key={id} id={id} /> : <ListView />}</main>;
}
