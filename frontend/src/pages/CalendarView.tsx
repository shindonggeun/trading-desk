import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { addMonths, analyzeLink, closures, earnDetail, earningsNotice, FLAG, IMPORTANCE, kstTime, nextDay, shortTicker, todayKst, WHEN } from "../calendar";
import type { CalDay, MarketMonth } from "../types";
import EarningsPreviewCard from "../components/EarningsPreviewCard";

/**
 * #246 국장 · 미장 월간 캘린더(#243 GET /api/calendar) — 휴장 · 조기 폐장, 경제 발표(한국 시간), 만기, 실적.
 * 달력은 날짜 칸에 휴장 · 중요 발표 점 · 실적 수, 누르면 그날 목록. 휴대폰은 목록형이 기본.
 * 주소 `#/calendar?m=2026-11&d=2026-11-13` 로 달 · 고른 날이 남는다.
 */

type Filters = { kr: boolean; us: boolean; earningsOnly: boolean; topOnly: boolean };
type View = "grid" | "list";
const FILTER_KEY = "desk.calendar.filters";
const VIEW_KEY = "desk.calendar.view";
const DEFAULT_FILTERS: Filters = { kr: true, us: true, earningsOnly: false, topOnly: false };
// #309 주 시작은 일요일
const WEEK = ["일", "월", "화", "수", "목", "금", "토"];
const dayTone = (w: string) => (w === "일" ? " cal-sun" : w === "토" ? " cal-sat" : "");

function load<T>(key: string, fallback: T, ok: (v: unknown) => v is T): T {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "null");
    return ok(raw) ? raw : fallback;
  } catch {
    return fallback;
  }
}
function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: the choice is not kept */
  }
}
const isFilters = (v: unknown): v is Filters =>
  !!v && typeof v === "object" && ["kr", "us", "earningsOnly", "topOnly"].every((k) => typeof (v as Record<string, unknown>)[k] === "boolean");
const isView = (v: unknown): v is View => v === "grid" || v === "list";
const phone = () => typeof window !== "undefined" && window.matchMedia("(max-width: 760px)").matches;

/** 필터를 적용한 그날 — 휴장 줄 · 발표 · 실적 */
export function shownDay(d: CalDay, f: Filters) {
  const market = (m: "kr" | "us") => (m === "kr" ? f.kr : f.us);
  const closed = closures(d).filter((c) => market(c.market));
  const events = f.earningsOnly ? [] : d.events.filter((e) => market(e.market) && (!f.topOnly || e.importance >= 2));
  const earnings = d.earnings.filter((r) => market(r.market) && (!f.topOnly || r.watched));
  return { closed, events, earnings, empty: !closed.length && !events.length && !earnings.length };
}

function go(month: string, day: string | null) {
  const q = new URLSearchParams({ m: month });
  if (day) q.set("d", day);
  window.history.replaceState(null, "", `#/calendar?${q}`);
}

function DayItems({ day, f, onPreview }: { day: CalDay; f: Filters; onPreview: (ticker: string, name: string) => void }) {
  const s = shownDay(day, f);
  if (s.empty) return <p className="muted small">일정이 없습니다.</p>;
  return (
    <ul className="cal-items">
      {s.closed.map((c) => (
        <li key={c.text} className={`cal-item cal-closed cal-${c.market}`}>
          <span className="cal-time">{c.early ? "조기 폐장" : "휴장"}</span>
          <span>{FLAG[c.market]} {c.text}</span>
        </li>
      ))}
      {s.events.map((e) => (
        <li key={`${e.id}-${e.at}`} className={`cal-item${e.importance >= 2 ? " cal-top" : ""}`}>
          <span className="cal-time num">{kstTime(e.time_kst)}{nextDay(e)}</span>
          <span>
            {FLAG[e.market]} {e.label}
            <span className="muted small"> · 중요도 {IMPORTANCE[e.importance] ?? "하"}{e.market === "us" ? ` · 현지 ${e.time_local}` : ""}</span>
          </span>
        </li>
      ))}
      {s.earnings.map((r) => (
        <li key={`${r.ticker}-${r.date}`} className={`cal-item cal-earn${r.watched ? " cal-watched" : ""}`}>
          <span className="cal-time">실적 · {WHEN[r.when]}</span>
          <span>
            {FLAG[r.market]} <strong>{r.name || shortTicker(r.ticker)}</strong>{r.name && <span className="muted small"> {shortTicker(r.ticker)}</span>}
            {r.stale && <span className="muted small" title="이번 회차에 새로 받지 못해 이전 자료를 보입니다"> · 이전 자료</span>}
            {" "}<button type="button" className="link small" onClick={() => onPreview(r.ticker, r.name || r.ticker)}>프리뷰</button>
            {r.watched && <> <span className="pill">관심</span> <a className="small" href={analyzeLink(r)}>바로 분석 →</a></>}
            {earnDetail(r).length > 0 && <span className="cal-earn-detail muted small">{earnDetail(r).join(" · ")}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function CalendarView({ query }: { query: URLSearchParams }) {
  const today = todayKst();
  const qm = query.get("m");
  const [month, setMonth] = useState(qm && /^\d{4}-\d{2}$/.test(qm) ? qm : today.slice(0, 7));
  const qd = query.get("d");
  const [picked, setPicked] = useState<string | null>(qd && /^\d{4}-\d{2}-\d{2}$/.test(qd) ? qd : null);
  const [filters, setFilters] = useState<Filters>(() => load(FILTER_KEY, DEFAULT_FILTERS, isFilters));
  const [view, setView] = useState<View>(() => load<View>(VIEW_KEY, phone() ? "list" : "grid", isView));
  const [data, setData] = useState<MarketMonth | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ ticker: string; name: string } | null>(null);
  const openPreview = (ticker: string, name: string) => setPreview({ ticker, name });

  useEffect(() => {
    let alive = true;
    setData(null);
    setFailed(null);
    api.marketMonth(month).then((d) => alive && setData(d), (e) => alive && setFailed(e instanceof Error ? e.message : "불러오지 못했습니다."));
    return () => { alive = false; };
  }, [month]);

  const day = picked ?? (today.startsWith(month) ? today : null);
  useEffect(() => go(month, picked), [month, picked]);

  const setF = (next: Partial<Filters>) => {
    const f = { ...filters, ...next };
    if (!f.kr && !f.us) return; // 둘 다 끄면 볼 것이 없다
    setFilters(f);
    save(FILTER_KEY, f);
  };
  const setV = (v: View) => {
    setView(v);
    save(VIEW_KEY, v);
  };
  const move = (n: number) => {
    setMonth(addMonths(month, n));
    setPicked(null);
  };
  // 서버는 지금부터 ±12개월만 준다
  const span = (m: string) => {
    const [y, mo] = m.split("-").map(Number);
    const [ty, tm] = today.slice(0, 7).split("-").map(Number);
    return (y - ty) * 12 + (mo - tm);
  };

  const cells = useMemo(() => {
    if (!data) return [];
    const lead = WEEK.indexOf(data.days[0]?.weekday ?? "일");
    return [...Array<null>(Math.max(lead, 0)).fill(null), ...data.days];
  }, [data]);
  const selected = data?.days.find((d) => d.date === day) ?? null;
  const [y, m] = month.split("-");

  return (
    <main className="page cal-page">
      <section className="card cal-bar">
        <div className="cal-nav">
          <button type="button" className="secondary" onClick={() => move(-1)} disabled={span(month) <= -12} aria-label="이전 달">◀</button>
          <h2 className="cal-month">{y}년 {Number(m)}월</h2>
          <button type="button" className="secondary" onClick={() => move(1)} disabled={span(month) >= 12} aria-label="다음 달">▶</button>
          {month !== today.slice(0, 7) && (
            <button type="button" className="link small" onClick={() => { setMonth(today.slice(0, 7)); setPicked(null); }}>이번 달</button>
          )}
          <div className="seg seg-small cal-view" role="group" aria-label="보기">
            <button type="button" className={view === "grid" ? "on" : ""} aria-pressed={view === "grid"} onClick={() => setV("grid")}>달력</button>
            <button type="button" className={view === "list" ? "on" : ""} aria-pressed={view === "list"} onClick={() => setV("list")}>목록</button>
          </div>
        </div>
        <div className="cal-filters small" role="group" aria-label="필터">
          <label><input type="checkbox" checked={filters.kr} onChange={(e) => setF({ kr: e.target.checked })} /> 국장</label>
          <label><input type="checkbox" checked={filters.us} onChange={(e) => setF({ us: e.target.checked })} /> 미장</label>
          <label><input type="checkbox" checked={filters.earningsOnly} onChange={(e) => setF({ earningsOnly: e.target.checked })} /> 실적만</label>
          <label><input type="checkbox" checked={filters.topOnly} onChange={(e) => setF({ topOnly: e.target.checked })} /> 중요도 상만</label>
        </div>
      </section>

      {failed && <p className="error" role="alert">캘린더를 불러오지 못했습니다. {failed}</p>}
      {!data && !failed && <p className="muted">일정을 받는 중…</p>}

      {data && view === "grid" && (
        <section className="card cal-grid-card">
          <div className="cal-grid" role="grid" aria-label={`${y}년 ${Number(m)}월 달력`}>
            {WEEK.map((w) => <div key={w} className={`cal-wd${w === "토" || w === "일" ? " cal-weekend" : ""}${dayTone(w)}`} role="columnheader">{w}</div>)}
            {cells.map((d, i) => {
              if (!d) return <div key={`pad-${i}`} className="cal-cell cal-pad" />;
              const s = shownDay(d, filters);
              const top = s.events.filter((e) => e.importance >= 2).length;
              const rest = s.events.length - top;
              const watched = s.earnings.filter((r) => r.watched).length;
              return (
                <button key={d.date} type="button" role="gridcell"
                  className={`cal-cell${d.date === day ? " cal-on" : ""}${d.date === today ? " cal-today" : ""}${d.weekday === "토" || d.weekday === "일" ? " cal-weekend" : ""}${dayTone(d.weekday)}`}
                  aria-selected={d.date === day} onClick={() => setPicked(d.date)}
                  aria-label={`${Number(d.date.slice(8))}일 ${d.weekday}요일${s.closed.length ? ` · ${s.closed.map((c) => c.text).join(", ")}` : ""}${s.events.length ? ` · 발표 ${s.events.length}건` : ""}${s.earnings.length ? ` · 실적 ${s.earnings.length}건` : ""}`}>
                  <span className="cal-num">{Number(d.date.slice(8))}</span>
                  {s.closed.map((c) => (
                    <span key={c.text} className={`cal-tag cal-tag-${c.market}${c.early ? " cal-tag-early" : ""}`}>
                      {c.market === "kr" ? "국" : "미"} {c.early ? "조기" : "휴장"}
                    </span>
                  ))}
                  <span className="cal-marks" aria-hidden>
                    {Array.from({ length: Math.min(top, 3) }, (_, k) => <span key={`t${k}`} className="cal-dot cal-dot-top" />)}
                    {Array.from({ length: Math.min(rest, 3) }, (_, k) => <span key={`r${k}`} className="cal-dot" />)}
                    {s.earnings.length > 0 && <span className={`cal-earn-n${watched ? " cal-watched" : ""}`}>실적 {s.earnings.length}</span>}
                  </span>
                  {s.earnings.length > 0 && (
                    <span className={`cal-earn-names${watched ? " cal-watched" : ""}`} aria-hidden>
                      {s.earnings.slice(0, 2).map((r) => <span key={r.ticker} className="cal-earn-name">{r.name || shortTicker(r.ticker)}</span>)}
                      {s.earnings.length > 2 && <span className="cal-earn-more">+{s.earnings.length - 2}</span>}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <p className="muted small cal-legend">
            <span className="cal-dot cal-dot-top" /> 중요도 상 · <span className="cal-dot" /> 중 · <span className="cal-tag cal-tag-kr">국</span> 국장 ·{" "}
            <span className="cal-tag cal-tag-us">미</span> 미장 휴장 · 조기 폐장
          </p>
        </section>
      )}

      {data && view === "grid" && (
        <section className="card cal-day" aria-live="polite">
          {selected ? (
            <>
              <h3>{Number(selected.date.slice(5, 7))}월 {Number(selected.date.slice(8))}일 ({selected.weekday})</h3>
              <DayItems day={selected} f={filters} onPreview={openPreview} />
            </>
          ) : <p className="muted small">날짜를 누르면 그날 일정이 나옵니다.</p>}
        </section>
      )}

      {data && view === "list" && (
        <section className="card cal-list">
          {data.days.filter((d) => !shownDay(d, filters).empty).map((d) => (
            <div key={d.date} id={`cal-${d.date}`} className={`cal-list-day${d.date === today ? " cal-today" : ""}`}>
              <h3>{Number(d.date.slice(5, 7))}/{Number(d.date.slice(8))} ({d.weekday}){d.date === today && <span className="pill">오늘</span>}</h3>
              <DayItems day={d} f={filters} onPreview={openPreview} />
            </div>
          ))}
          {data.days.every((d) => shownDay(d, filters).empty) && <p className="muted small">이 달에 보여 줄 일정이 없습니다.</p>}
        </section>
      )}

      {data && (
        <section className="muted small cal-notes">
          {data.sources.economic.error && <p className="error small">경제 일정 일부를 받지 못했습니다 — {data.sources.economic.error}</p>}
          {(() => {
            const n = earningsNotice(data.sources.earnings, data.days.reduce((k, d) => k + d.earnings.length, 0));
            return n && <p className={n.red ? "error small" : "warn-line small"} role="status">{n.text}</p>;
          })()}
          <ul>{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </section>
      )}
      {preview && <EarningsPreviewCard ticker={preview.ticker} name={preview.name} onClose={() => setPreview(null)} />}
    </main>
  );
}
