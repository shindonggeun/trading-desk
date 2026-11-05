import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import MarketBoard from "../components/MarketBoard";
import MarketFlowPanel from "../components/MarketFlowPanel";
import NewRunForm from "../components/NewRunForm";
import PlanStatsCard from "../components/PlanStatsCard";
import PortfolioPanel from "../components/PortfolioPanel";
import RecentRuns from "../components/RecentRuns";
import { time } from "../format";
import type { RegimeSnapshot } from "../types";

function RegimePanel() {
  const [data, setData] = useState<RegimeSnapshot | null | undefined>(undefined);
  useEffect(() => {
    api.regime().then(setData).catch(() => setData(null));
  }, []);
  if (data === undefined) return <p className="empty-state">불러오는 중…</p>;
  if (!data?.enabled) return <p className="empty-state">semibot 레짐 데이터가 연결되지 않았습니다.</p>;
  return (
    <section className="card">
      <h3>semibot 레짐 <span className="muted small">실시간 층</span></h3>
      {data.error && <p className="error small">{data.error}</p>}
      <div className="regimes">
        {data.regimes?.map((r) => (
          <div key={r.market} className="regime">
            <div className="muted small">{r.market}</div>
            <div className="regime-label">{r.label}</div>
            <div className="small">{r.score.toFixed(0)}점 · {time(r.ts)}</div>
          </div>
        ))}
      </div>
      {!!data.triggers?.length && (
        <ul className="triggers">
          {data.triggers.slice(0, 8).map((t) => (
            <li key={`${t.ts}-${t.symbol}-${t.kind}`}>
              <span className={t.direction === "UP" ? "up" : "down"}>{t.direction === "UP" ? "▲" : "▼"}</span>
              <strong>{t.symbol}</strong> {t.kind} <span className="muted small">{time(t.ts)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

type SectionId = "market" | "portfolio" | "recent" | "stats" | "flow" | "regime";
/** 구역 — 순서는 이슈 #100 의 기본값. wide 는 넓은 화면에서 한 줄을 다 쓴다. */
const SECTIONS: Record<SectionId, { title: string; wide?: boolean; render: () => ReactNode }> = {
  market: { title: "시장 · 지수 · 선물", wide: true, render: () => <MarketBoard /> },
  portfolio: { title: "내 투자", render: () => <PortfolioPanel /> },
  recent: { title: "최근 분석", render: () => <RecentRuns /> },
  stats: { title: "적중률", render: () => <PlanStatsCard /> },
  flow: { title: "시장 수급", render: () => <MarketFlowPanel /> },
  regime: { title: "레짐", render: () => <RegimePanel /> },
};
const DEFAULT_ORDER = Object.keys(SECTIONS) as SectionId[];

// 구역 순서 · 접힘은 이 브라우저에 기억한다(손상값 · 새로 생긴 구역은 기본값으로)
const PREFS_KEY = "desk.dashboard.v1";
type Prefs = { order: SectionId[]; closed: SectionId[] };
function loadPrefs(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}");
    const known = (v: unknown): v is SectionId => typeof v === "string" && v in SECTIONS;
    const saved = Array.isArray(raw.order) ? raw.order.filter(known) : [];
    const order = [...new Set<SectionId>([...saved, ...DEFAULT_ORDER])];
    const closed = Array.isArray(raw.closed) ? raw.closed.filter(known) : [];
    return { order, closed };
  } catch {
    return { order: DEFAULT_ORDER, closed: [] };
  }
}
function savePrefs(prefs: Prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* 기억만 못 한다 */
  }
}

/** 대시보드(#100): 맨 위 검색 한 줄 → 분석, 아래는 접고 순서를 바꾸는 구역들. */
export default function Dashboard() {
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  // 휴대폰에서 검색 줄을 위 메뉴(두 줄, 고정) 바로 밑에 붙이려고 메뉴 높이를 CSS 로 넘긴다
  useLayoutEffect(() => {
    const bar = document.querySelector<HTMLElement>(".topbar");
    if (!bar) return;
    const set = () => document.documentElement.style.setProperty("--topbar-h", `${Math.round(bar.getBoundingClientRect().height)}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(bar);
    return () => ro.disconnect();
  }, []);
  const update = (next: Prefs) => {
    savePrefs(next);
    setPrefs(next);
  };
  const flip = (id: SectionId) =>
    update({ ...prefs, closed: prefs.closed.includes(id) ? prefs.closed.filter((c) => c !== id) : [...prefs.closed, id] });
  const move = (id: SectionId, by: -1 | 1) => {
    const order = [...prefs.order];
    const i = order.indexOf(id);
    const j = i + by;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    update({ ...prefs, order });
    // 옮긴 구역의 같은 버튼에 초점을 남겨 연달아 옮길 수 있게
    window.setTimeout(() => document.getElementById(`dash-move-${id}-${by}`)?.focus(), 0);
  };

  return (
    <main className="dashboard dashboard-v2">
      <div className="dash-search">
        <NewRunForm />
      </div>
      <div className="dash-sections">
        {prefs.order.map((id, i) => {
          const s = SECTIONS[id];
          const open = !prefs.closed.includes(id);
          const bodyId = `dash-${id}`;
          return (
            <section key={id} className={`dash-section${s.wide ? " dash-wide" : ""}${open ? "" : " dash-closed"}`} aria-labelledby={`${bodyId}-title`}>
              <header className="dash-head">
                <button type="button" className="dash-toggle" aria-expanded={open} aria-controls={bodyId} onClick={() => flip(id)}>
                  <span className="dash-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                  <h2 id={`${bodyId}-title`}>{s.title}</h2>
                </button>
                <span className="dash-order" role="group" aria-label={`${s.title} 순서`}>
                  <button type="button" id={`dash-move-${id}--1`} className="dash-move" disabled={i === 0}
                    aria-label={`${s.title} 위로`} onClick={() => move(id, -1)}>↑</button>
                  <button type="button" id={`dash-move-${id}-1`} className="dash-move" disabled={i === prefs.order.length - 1}
                    aria-label={`${s.title} 아래로`} onClick={() => move(id, 1)}>↓</button>
                </span>
              </header>
              {open && <div id={bodyId} className="dash-body">{s.render()}</div>}
            </section>
          );
        })}
      </div>
    </main>
  );
}
