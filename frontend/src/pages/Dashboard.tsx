import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import FavoritesPanel from "../components/FavoritesPanel";
import FuturesQuoteCard from "../components/FuturesQuoteCard";
import CreditPanel from "../components/CreditPanel";
import MacroBrief from "../components/MacroBrief";
import MarketBoard from "../components/MarketBoard";
import MarketFlowPanel from "../components/MarketFlowPanel";
import WeekAgenda from "../components/WeekAgenda";
import SectorNews from "../components/SectorNews";
import NewRunForm from "../components/NewRunForm";
import PlanStatsCard from "../components/PlanStatsCard";
import PortfolioPanel from "../components/PortfolioPanel";
import RecentRuns from "../components/RecentRuns";
import WeeklyMapBrief from "../components/WeeklyMapBrief";
import BottomZoneBrief from "../components/BottomZoneBrief";
import TodayStrip from "../components/TodayStrip";
import DashSummary from "../components/DashSummary";
import { time } from "../format";
import type { DeskRegime, RegimeSnapshot } from "../types";

// #410 데스크 자체 국면(#409) — semibot 이 없는 서버에서도 카드가 빈 문구가 아니게
const RATES_KO = { hiking: "금리 인상기", cutting: "금리 인하기", hold: "금리 동결" } as const;
const YIELD_KO = { rising: "10년물 상승", falling: "10년물 하락", flat: "10년물 보합" } as const;
const DOLLAR_KO = { strong: "달러 강세", weak: "달러 약세", flat: "달러 보합" } as const;
const day = (iso: string | null) => (iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : "");

function DeskRegimeView({ d }: { d: DeskRegime }) {
  const r = d.rates;
  const rateParts = r ? [
    r.rates && RATES_KO[r.rates], r.yield10 && YIELD_KO[r.yield10], r.yield_spike ? "10년물 급등" : null,
    r.dollar && DOLLAR_KO[r.dollar], r.sideways == null ? null : r.sideways ? "나스닥 횡보" : "나스닥 추세",
  ].filter(Boolean) : [];
  return (
    <>
      {d.markets.length > 0 && (
        <div className="regimes">
          {d.markets.map((m) => (
            <div key={m.market} className={`regime regime-${m.phase ?? "unknown"}`}>
              <div className="muted small">{m.market === "us" ? "미장" : "국장"} 시장 위험</div>
              <div className="regime-label">{m.label ?? "판단 불가"}</div>
              <div className="small">{m.score == null ? "—" : `${Math.round(m.score)}점`}{m.as_of ? ` · ${day(m.as_of)} 기준` : ""}</div>
            </div>
          ))}
        </div>
      )}
      {rateParts.length > 0 && (
        <p className="regime-rates small">
          {rateParts.join(" · ")}{r?.as_of ? <span className="muted"> · {day(r.as_of)} 기준</span> : null}{" "}
          <a href="#/tips">국면별 매매 팁 →</a>
        </p>
      )}
      {d.error && <p className="muted small">{d.error}</p>}
    </>
  );
}

function RegimePanel() {
  const [data, setData] = useState<RegimeSnapshot | "error" | undefined>(undefined);
  useEffect(() => {
    api.regime().then(setData).catch(() => setData("error"));
  }, []);
  if (data === undefined) return <p className="empty-state">불러오는 중…</p>;
  if (data === "error") return <p className="empty-state">레짐을 불러오지 못했습니다. 잠시 뒤 새로 고쳐 주세요.</p>;
  const desk = data.desk && (data.desk.markets.length > 0 || data.desk.rates) ? data.desk : null;
  if (!data.enabled) {
    // 서버(semibot 없음): 데스크 자체 국면만. 옛 응답(desk 없음)이면 받을 자료가 없다고만
    return desk
      ? <section className="card"><DeskRegimeView d={desk} /></section>
      : <p className="empty-state">국면 자료를 아직 받지 못했습니다.{data.desk?.error ? ` ${data.desk.error}` : ""}</p>;
  }
  return (
    <section className="card">
      {desk && <DeskRegimeView d={desk} />}
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

type SectionId = "macro" | "sectornews" | "favorites" | "market" | "kfutures" | "agenda" | "weekly" | "bottom" | "portfolio" | "recent" | "stats" | "flow" | "credit" | "regime";
/** 구역 — 순서는 이슈 #100 의 기본값. wide 는 넓은 화면에서 한 줄을 다 쓴다. */
const SECTIONS: Record<SectionId, { title: string; wide?: boolean; render: () => ReactNode }> = {
  macro: { title: "오늘의 거시 · 정책 브리핑", wide: true, render: () => <MacroBrief /> },
  // #245 섹터 · 인물 이슈(#244) — 브리핑 바로 아래
  sectornews: { title: "섹터 이슈", wide: true, render: () => <SectorNews /> },
  favorites: { title: "즐겨찾기 시세", wide: true, render: () => <FavoritesPanel /> },
  market: { title: "시장 · 지수 · 선물", wide: true, render: () => <MarketBoard embedded /> },
  // #444 코스피200 선물 주간 · 야간 시세(#443) — 누르면 #/futures
  kfutures: { title: "코스피200 선물", render: () => <FuturesQuoteCard /> },
  // #246 오늘부터 7일 안의 휴장 · 중요 발표 · 만기 · 관심 종목 실적 5개
  agenda: { title: "이번 주 일정", render: () => <WeekAgenda /> },
  // #227 주요 지수의 주간 지도(#202) 위치 · 다음 선 한 줄씩
  weekly: { title: "주간 지도", render: () => <WeeklyMapBrief /> },
  bottom: { title: "인생 타점 · 장기 바닥권", render: () => <BottomZoneBrief /> },
  portfolio: { title: "내 투자", render: () => <PortfolioPanel /> },
  recent: { title: "최근 분석", render: () => <RecentRuns /> },
  stats: { title: "적중률", render: () => <PlanStatsCard /> },
  flow: { title: "시장 수급", render: () => <MarketFlowPanel /> },
  credit: { title: "빚투 · 예탁금", render: () => <CreditPanel /> },
  regime: { title: "레짐", render: () => <RegimePanel /> },
};
const BASE_ORDER = Object.keys(SECTIONS) as SectionId[];

// #464 넓은 화면(≥1280px) 3열: 왼쪽 시장 · 지수 · 공포 · 선물, 가운데 즐겨찾기 · 오늘 볼 것, 오른쪽 수급 · 내 투자.
// 열 안의 순서는 저장된 순서를 따르고, ↑↓ 는 같은 열 안의 이웃과 바꾼다.
const COLUMN: Record<SectionId, 0 | 1 | 2> = {
  market: 0, kfutures: 0, weekly: 0, regime: 0, credit: 0,
  favorites: 1, macro: 1, sectornews: 1, agenda: 1, recent: 1, bottom: 1,
  flow: 2, portfolio: 2, stats: 2,
};
type Layout = "wide" | "normal" | "phone";
const WIDE_Q = "(min-width: 1280px)";
const PHONE_Q = "(max-width: 480px)";
function currentLayout(): Layout {
  const mq = (q: string) => window.matchMedia?.(q).matches === true;
  return mq(WIDE_Q) ? "wide" : mq(PHONE_Q) ? "phone" : "normal";
}
function useLayout(): Layout {
  const [layout, setLayout] = useState<Layout>(currentLayout);
  useEffect(() => {
    const on = () => setLayout(currentLayout());
    const lists = [WIDE_Q, PHONE_Q].map((q) => window.matchMedia?.(q)).filter((m): m is MediaQueryList => !!m);
    lists.forEach((m) => m.addEventListener("change", on));
    return () => lists.forEach((m) => m.removeEventListener("change", on));
  }, []);
  return layout;
}

// 구역 순서 · 접힘은 이 브라우저에 기억한다(손상값 · 새로 생긴 구역은 기본값으로).
// #464 휴대폰은 접힌 머리글로 시작하고, 펼친 구역(mobileOpen)을 데스크톱 접힘(closed)과 따로 기억한다.
const PREFS_KEY = "desk.dashboard.v1";
// rev 464: 넓은 화면 오른쪽 열에서 시장 수급을 내 투자 · 적중률 위로(한 번만 — 그 뒤 사용자가 옮긴 순서는 그대로)
const PREFS_REV = 464;
type Prefs = { order: SectionId[]; closed: SectionId[]; mobileOpen: SectionId[]; rev?: number };
function flowFirstInColumn(order: SectionId[]): SectionId[] {
  const rest: SectionId[] = order.filter((s) => s !== "flow");
  const at = rest.findIndex((s) => COLUMN[s] === COLUMN.flow);
  if (at < 0) return order;
  rest.splice(at, 0, "flow");
  return rest;
}
// 기본 순서: 구역 정의 순서에서 시장 수급만 오른쪽 열 맨 위로(새로 생긴 구역도 이 자리 기준으로 끼운다)
const DEFAULT_ORDER = flowFirstInColumn(BASE_ORDER);
function loadPrefs(): Prefs {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}");
    const known = (v: unknown): v is SectionId => typeof v === "string" && Object.hasOwn(SECTIONS, v);
    const saved = Array.isArray(raw.order) ? raw.order.filter(known) : [];
    // 새로 생긴 구역(예: #118 브리핑은 맨 위)은 저장된 순서 끝이 아니라 기본 자리(몇 번째)에 끼운다
    const order = [...new Set<SectionId>(saved)];
    DEFAULT_ORDER.forEach((id, i) => {
      if (!order.includes(id)) order.splice(Math.min(i, order.length), 0, id);
    });
    const closed = Array.isArray(raw.closed) ? raw.closed.filter(known) : [];
    const mobileOpen = Array.isArray(raw.mobileOpen) ? raw.mobileOpen.filter(known) : [];
    const rev = typeof raw.rev === "number" ? raw.rev : 0;
    return { order: rev >= PREFS_REV ? order : flowFirstInColumn(order), closed, mobileOpen, rev: PREFS_REV };
  } catch {
    return { order: DEFAULT_ORDER, closed: [], mobileOpen: [], rev: PREFS_REV };
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
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--topbar-h");
    };
  }, []);
  const update = (next: Prefs) => {
    savePrefs(next);
    setPrefs(next);
  };
  // #362 알림 클릭(#/?flow=intraday): 시장 수급 구역을 접어 두었으면 펴서 패널이 "오늘 장중" 탭을 열게 한다.
  // 해시는 패널이 읽고 지운다 — 여기서는 구역만 편다.
  useEffect(() => {
    const reveal = () => {
      if (!/[?&]flow=intraday(?:&|$)/.test(window.location.hash)) return;
      const cur = loadPrefs();               // 저장은 updater 밖에서(StrictMode 이중 실행에도 한 번)
      if (!cur.closed.includes("flow") && cur.mobileOpen.includes("flow")) return;
      const next = {
        ...cur,
        closed: cur.closed.filter((c) => c !== "flow"),
        mobileOpen: cur.mobileOpen.includes("flow") ? cur.mobileOpen : [...cur.mobileOpen, "flow" as const],
      };
      savePrefs(next);
      setPrefs(next);
    };
    reveal();
    window.addEventListener("hashchange", reveal);
    return () => window.removeEventListener("hashchange", reveal);
  }, []);
  const layout = useLayout();
  const toggled = (list: SectionId[], id: SectionId) => (list.includes(id) ? list.filter((c) => c !== id) : [...list, id]);
  const isOpen = (id: SectionId) => (layout === "phone" ? prefs.mobileOpen.includes(id) : !prefs.closed.includes(id));
  const flip = (id: SectionId) =>
    update(layout === "phone" ? { ...prefs, mobileOpen: toggled(prefs.mobileOpen, id) } : { ...prefs, closed: toggled(prefs.closed, id) });
  // 옮길 수 있는 이웃: 넓은 화면은 같은 열 안에서, 그 밖에는 전체 순서에서
  const peers = (id: SectionId) => (layout === "wide" ? prefs.order.filter((s) => COLUMN[s] === COLUMN[id]) : prefs.order);
  const move = (id: SectionId, by: -1 | 1) => {
    const line = peers(id);
    const other = line[line.indexOf(id) + by];
    if (!other) return;
    const order = [...prefs.order];
    const i = order.indexOf(id);
    const j = order.indexOf(other);
    [order[i], order[j]] = [order[j], order[i]];
    update({ ...prefs, order });
    // 옮긴 구역의 같은 버튼에 초점을 남겨 연달아 옮길 수 있게
    window.setTimeout(() => {
      const same = document.getElementById(`dash-move-${id}-${by}`) as HTMLButtonElement | null;
      (same && !same.disabled ? same : document.getElementById(`dash-move-${id}-${-by}`))?.focus();
    }, 0);
  };

  return (
    <main className="dashboard dashboard-v2">
      <div className="dash-search">
        <NewRunForm />
      </div>
      {/* #464 휴대폰: 구역을 펼치지 않아도 지수 등락 · 공포지수를 한 줄로 */}
      {layout === "phone" && <DashSummary />}
      {/* #317 즐겨찾기 가운데 지금 손댈 종목 */}
      <TodayStrip />
      {layout === "wide" ? (
        <div className="dash-columns">
          {([0, 1, 2] as const).map((col) => (
            <div key={col} className="dash-col">
              {prefs.order.filter((id) => COLUMN[id] === col).map(renderSection)}
            </div>
          ))}
        </div>
      ) : (
        <div className="dash-sections">{prefs.order.map(renderSection)}</div>
      )}
    </main>
  );

  function renderSection(id: SectionId) {
          const s = SECTIONS[id];
          const open = isOpen(id);
          const bodyId = `dash-${id}`;
          const line = peers(id);
          const i = line.indexOf(id);
          return (
            <section key={id} className={`dash-section${s.wide ? " dash-wide" : ""}${open ? "" : " dash-closed"}`} aria-labelledby={`${bodyId}-title`}>
              <header className="dash-head">
                <h2 id={`${bodyId}-title`} className="dash-title">
                  <button type="button" className="dash-toggle" aria-expanded={open} aria-controls={open ? bodyId : undefined}
                    onClick={() => flip(id)}>
                    <span className="dash-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
                    {s.title}
                  </button>
                </h2>
                <span className="dash-order" role="group" aria-label={`${s.title} 순서`}>
                  <button type="button" id={`dash-move-${id}--1`} className="dash-move" disabled={i === 0}
                    aria-label={`${s.title} 위로`} onClick={() => move(id, -1)}>↑</button>
                  <button type="button" id={`dash-move-${id}-1`} className="dash-move" disabled={i === line.length - 1}
                    aria-label={`${s.title} 아래로`} onClick={() => move(id, 1)}>↓</button>
                </span>
              </header>
              {open && <div id={bodyId} className="dash-body">{s.render()}</div>}
            </section>
          );
  }
}
