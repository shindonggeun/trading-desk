import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Layout as GridLayoutItems } from "react-grid-layout";
import { api } from "../api";
import FavoritesPanel from "../components/FavoritesPanel";
import FuturesQuoteCard from "../components/FuturesQuoteCard";
import CreditPanel from "../components/CreditPanel";
import MacroBrief from "../components/MacroBrief";
import MarketBoard from "../components/MarketBoard";
import WeekAgenda from "../components/WeekAgenda";
import SectorNews from "../components/SectorNews";
import NewRunForm from "../components/NewRunForm";
import PlanStatsCard from "../components/PlanStatsCard";
import PaperCard from "../components/PaperCard";
import MorningBriefCard from "../components/MorningBriefCard";
import DayCallCard from "../components/DayCallCard";
import PortfolioPanel from "../components/PortfolioPanel";
import RecentRuns from "../components/RecentRuns";
import WeeklyMapBrief from "../components/WeeklyMapBrief";
import BottomZoneBrief from "../components/BottomZoneBrief";
import HalvingBrief from "../components/HalvingBrief";
import TodayStrip from "../components/TodayStrip";
import WhenVisible from "../components/WhenVisible";
import SectionBoundary from "../components/SectionBoundary";
import ChunkFailed from "../components/ChunkFailed";
import DashSummary from "../components/DashSummary";
import { time } from "../format";
import { DEFAULT_H, GRID_COLS, HEAD_ROWS, KEY_H_STEP, MIN_W, loadBand, measureLayout, resetBand, saveAutoHeights, saveBand, unhideSection, type Band, type BandLayout } from "../dashLayout";
import { useAutoHeights } from "../dashAutoHeight";

// #487 격자(react-grid-layout)는 배치를 편집하거나 저장한 배치가 있을 때만 불러온다
const DashGrid = lazy(() => import("../components/DashGrid"));
// #537 차트 라이브러리를 쓰는 구역은 그 구역이 보일 때 받는다(첫 화면 JS 에서 뺀다)
const MarketFlowPanel = lazy(() => import("../components/MarketFlowPanel"));
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

type SectionId = "morning" | "daycall" | "macro" | "sectornews" | "favorites" | "market" | "kfutures" | "agenda" | "weekly" | "bottom" | "halving" | "portfolio" | "recent" | "stats" | "paper" | "flow" | "credit" | "regime";
/** 구역 — 순서는 이슈 #100 의 기본값. wide 는 넓은 화면에서 한 줄을 다 쓴다. */
const SECTIONS: Record<SectionId, { title: string; wide?: boolean; render: () => ReactNode }> = {
  // #573 아침 시황(#571) · 오늘 방향(#572) — 맨 위, 휴대폰 첫 화면에서도 펼친 채로
  morning: { title: "아침 시황", wide: true, render: () => <MorningBriefCard /> },
  daycall: { title: "오늘 방향", render: () => <DayCallCard /> },
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
  // #515 비트코인 반감기 사이클 — 지금 위치 한 장, 누르면 #/halving
  halving: { title: "비트코인 반감기 사이클", render: () => <HalvingBrief /> },
  portfolio: { title: "내 투자", render: () => <PortfolioPanel /> },
  recent: { title: "최근 분석", render: () => <RecentRuns /> },
  stats: { title: "적중률", render: () => <PlanStatsCard /> },
  // #528 모의 투자(#527) — 분석 타점대로 샀다면
  paper: { title: "모의 투자", render: () => <PaperCard /> },
  flow: { title: "시장 수급", render: () => <MarketFlowPanel /> },
  credit: { title: "빚투 · 예탁금", render: () => <CreditPanel /> },
  regime: { title: "레짐", render: () => <RegimePanel /> },
};
const BASE_ORDER = Object.keys(SECTIONS) as SectionId[];

// #464 넓은 화면(≥1280px) 3열: 왼쪽 시장 · 지수 · 공포 · 선물, 가운데 즐겨찾기 · 오늘 볼 것, 오른쪽 수급 · 내 투자.
// 열 안의 순서는 저장된 순서를 따르고, ↑↓ 는 같은 열 안의 이웃과 바꾼다.
const COLUMN: Record<SectionId, 0 | 1 | 2> = {
  market: 0, kfutures: 0, weekly: 0, regime: 0, credit: 0,
  morning: 1, daycall: 1, favorites: 1, macro: 1, sectornews: 1, agenda: 1, recent: 1, bottom: 1,
  flow: 2, portfolio: 2, stats: 2, halving: 2, paper: 2,
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

// #487 배치 편집을 쓰는 폭 구간: ≥1280 넓은 화면, 1000~1279 중간. 그보다 좁으면(휴대폰 포함) 예전 흐름 그대로.
const MID_Q = "(min-width: 1000px)";
function useBand(layout: Layout): Band | null {
  const [mid, setMid] = useState(() => window.matchMedia?.(MID_Q).matches === true);
  useEffect(() => {
    const m = window.matchMedia?.(MID_Q);
    if (!m) return;
    const on = () => setMid(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return layout === "wide" ? "wide" : mid && layout !== "phone" ? "mid" : null;
}

// 구역 순서 · 접힘은 이 브라우저에 기억한다(손상값 · 새로 생긴 구역은 기본값으로).
// #464 휴대폰은 접힌 머리글로 시작하고, 펼친 구역(mobileOpen)을 데스크톱 접힘(closed)과 따로 기억한다.
const PREFS_KEY = "desk.dashboard.v1";
// rev 464: 넓은 화면 오른쪽 열에서 시장 수급을 내 투자 · 적중률 위로(한 번만 — 그 뒤 사용자가 옮긴 순서는 그대로)
// rev 573: 아침 시황 · 오늘 방향을 휴대폰에서 펼친 채로 시작(한 번만 — 그 뒤 접으면 그대로)
const FLOW_REV = 464;
const PREFS_REV = 573;
const TOP_SECTIONS: SectionId[] = ["morning", "daycall"];
type Prefs = { order: SectionId[]; closed: SectionId[]; mobileOpen: SectionId[]; rev?: number };
function flowFirstInColumn(order: SectionId[]): SectionId[] {
  const rest: SectionId[] = order.filter((s) => s !== "flow");
  const at = rest.findIndex((s) => COLUMN[s] === COLUMN.flow);
  // 이미 같은 열 첫 구역보다 앞(사용자가 위로 올려 둔 경우 포함)이면 그대로 — 위로만 옮기고 내리지 않는다
  if (at < 0 || order.indexOf("flow") <= order.indexOf(rest[at])) return order;
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
    const rev = typeof raw.rev === "number" ? raw.rev : 0;
    const saved_open: SectionId[] = Array.isArray(raw.mobileOpen) ? raw.mobileOpen.filter(known) : [];
    const mobileOpen = rev >= PREFS_REV ? saved_open : [...new Set([...TOP_SECTIONS, ...saved_open])];
    return { order: rev >= FLOW_REV ? order : flowFirstInColumn(order), closed, mobileOpen, rev: PREFS_REV };
  } catch {
    return { order: DEFAULT_ORDER, closed: [], mobileOpen: [...TOP_SECTIONS], rev: PREFS_REV };
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
    // #487 저장 배치에서 시장 수급을 숨겨 두었으면 다시 보이게(맨 아래) — 알림 클릭이 아무 일도 안 하지 않게
    const revealInGrid = () => {
      if (!/[?&]flow=intraday(?:&|$)/.test(window.location.hash)) return;
      for (const b of ["wide", "mid"] as const) if (unhideSection(b, "flow")) setSaved((cur) => (cur && bandSeen.current === b ? loadBand(b, DEFAULT_ORDER, TOP_SECTIONS) : cur));
    };
    revealInGrid();
    window.addEventListener("hashchange", revealInGrid);
    reveal();
    window.addEventListener("hashchange", reveal);
    return () => { window.removeEventListener("hashchange", reveal); window.removeEventListener("hashchange", revealInGrid); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const layout = useLayout();
  const toggled = (list: SectionId[], id: SectionId) => (list.includes(id) ? list.filter((c) => c !== id) : [...list, id]);
  const isOpen = (id: SectionId) => (layout === "phone" ? prefs.mobileOpen.includes(id) : !prefs.closed.includes(id));
  const flip = (id: SectionId) =>
    update(layout === "phone" ? { ...prefs, mobileOpen: toggled(prefs.mobileOpen, id) } : { ...prefs, closed: toggled(prefs.closed, id) });
  // 옮길 수 있는 이웃: 넓은 화면은 같은 열 안에서, 그 밖에는 전체 순서에서
  // 넓은 화면: 맨 위 한 줄(아침 시황 · 오늘 방향)은 그 둘끼리, 나머지는 같은 열 안에서
  const peers = (id: SectionId) => (layout !== "wide" ? prefs.order
    : TOP_SECTIONS.includes(id) ? prefs.order.filter((s) => TOP_SECTIONS.includes(s))
    : prefs.order.filter((s) => !TOP_SECTIONS.includes(s) && COLUMN[s] === COLUMN[id]));
  const move = (id: SectionId, by: -1 | 1) => {
    const line = peers(id);
    const other = line[line.indexOf(id) + by];
    if (!other) return;
    // 맞바꾸지 않고 옮길 구역만 이웃 앞(↑) · 뒤(↓)로 끼운다 — 다른 구역의 상대 순서는 그대로(다른 레이아웃에서 이웃이 튀지 않게)
    const order = prefs.order.filter((s) => s !== id);
    order.splice(order.indexOf(other) + (by === 1 ? 1 : 0), 0, id);
    update({ ...prefs, order });
    // 옮긴 구역의 같은 버튼에 초점을 남겨 연달아 옮길 수 있게
    window.setTimeout(() => {
      const same = document.getElementById(`dash-move-${id}-${by}`) as HTMLButtonElement | null;
      (same && !same.disabled ? same : document.getElementById(`dash-move-${id}-${-by}`))?.focus();
    }, 0);
  };

  // #487 배치 편집 — 저장한 격자 배치가 있으면 보기 모드도 격자로, 없으면 예전 흐름(#464) 그대로
  const band = useBand(layout);
  // 저장 배치는 첫 렌더부터 읽는다 — 예전 흐름으로 한 번 그렸다가 격자로 바꾸면 구역이 두 번 마운트돼 요청이 두 번 나가고 깜빡인다
  const [saved, setSaved] = useState<BandLayout | null>(() => (band ? loadBand(band, DEFAULT_ORDER, TOP_SECTIONS) : null));
  const [draft, setDraft] = useState<BandLayout | null>(null);
  const [said, setSaid] = useState("");
  const flowRef = useRef<HTMLDivElement>(null);
  // 격자 폭 — 감싸개를 늘 그려 두고 직접 잰다(라이브러리 훅을 첫 화면 번들에 넣지 않으려고)
  const areaRef = useRef<HTMLDivElement>(null);
  const [areaWidth, setAreaWidth] = useState(0);
  useLayoutEffect(() => {
    const el = areaRef.current;
    if (!el) return;
    setAreaWidth(Math.round(el.getBoundingClientRect().width)); // 그리기 전에 한 번 — 첫 화면에 빈칸이 남지 않게
    const ro = new ResizeObserver(([e]) => setAreaWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // 폭 구간이 바뀔 때만 다시 읽는다(첫 렌더는 위에서 읽었다). 편집 중이었으면 그 편집은 버리고 알린다.
  const bandSeen = useRef(band);
  useEffect(() => {
    if (bandSeen.current === band) return;
    bandSeen.current = band;
    setDraft((d) => { if (d) setSaid("화면 폭이 바뀌어 배치 편집을 취소했습니다"); return null; });
    setSaved(band ? loadBand(band, DEFAULT_ORDER, TOP_SECTIONS) : null);
  }, [band]);
  const grid = band ? draft ?? saved : null;
  const startEdit = () => {
    if (!band) return;
    if (saved) { setDraft({ items: saved.items.map((it) => ({ ...it })), hidden: [...saved.hidden], fixed: [...saved.fixed] }); return; }
    // 처음: 지금 화면을 재서 격자로 — 넓은 화면은 지금 3열(4칸씩) 그대로, 중간 폭은 잰 위치 · 폭.
    // 높이는 모두 자동(#519)이라 잰 높이는 내용을 다시 잴 때까지의 첫 짐작 — 접힌 구역 · 아직 불러오는 중인 구역은 보통 높이로
    const measured = (flowRef.current ? measureLayout(flowRef.current, prefs.order) : [])
      .map((it) => ({ ...it, h: prefs.closed.includes(it.i as SectionId) || it.h <= HEAD_ROWS ? DEFAULT_H : it.h }));
    // #573 맨 위 한 줄은 넓게(8칸) · 좁게(4칸) 그대로, 나머지는 3열(4칸씩)
    const topOrder = prefs.order.filter((s) => TOP_SECTIONS.includes(s));
    const items = band === "wide" ? measured.map((it) => {
      const t = topOrder.indexOf(it.i as SectionId);
      if (t >= 0) return { ...it, y: 0, x: t === 0 ? 0 : GRID_COLS - MIN_W, w: t === 0 && topOrder.length > 1 ? GRID_COLS - MIN_W : t === 0 ? GRID_COLS : MIN_W };
      return { ...it, x: COLUMN[it.i as SectionId] * 4, w: 4 };
    }) : measured;
    setDraft({ items, hidden: [], fixed: [] });
  };
  const finishEdit = () => {
    if (band && draft) { saveBand(band, draft); setSaved(draft); }
    setDraft(null);
  };
  const resetLayout = () => {
    if (!band) return;
    resetBand(band);
    setSaved(null);
    setDraft(null);
  };
  const hide = (id: string) => {
    if (!draft || draft.items.length <= 1) return; // 마지막 구역은 숨기지 않는다(빈 배치는 저장되지 않아 초기화처럼 된다)
    setDraft({ ...draft, items: draft.items.filter((it) => it.i !== id), hidden: [...draft.hidden, id], fixed: draft.fixed.filter((f) => f !== id) });
    window.setTimeout(() => document.querySelector<HTMLElement>(".dash-edit-bar button")?.focus(), 0); // 초점이 사라지지 않게
  };
  const unhide = (id: string) => draft && setDraft({
    ...draft,
    items: [...draft.items, { i: id, x: 0, y: draft.items.reduce((m, it) => Math.max(m, it.y + it.h), 0), w: 4, h: DEFAULT_H }],
    hidden: draft.hidden.filter((h) => h !== id),
  });
  // #519 높이: 자동 구역은 잰 내용 높이(아직 못 쟀으면 저장값), 고정 구역은 저장값 — 단 내용보다 작게는 안 된다
  // (내용이 길어지면 고정 높이도 내용까지 늘고, 모서리 · Shift+↑ 로도 내용보다 작게 못 줄인다 → 어떤 배치에서도 안쪽 스크롤이 없다).
  // 보기 모드에서 접힌 구역은 머리글 높이. 높이가 바뀐 배치를 넘기면 격자가 verticalCompactor 로 다시 위로 붙인다
  const auto = useAutoHeights();
  const gridItems: GridLayoutItems = !grid ? [] : grid.items.map((it) => {
    if (!draft && !isOpen(it.i as SectionId)) return { ...it, h: HEAD_ROWS };
    const need = auto.rows[it.i];
    const h = grid.fixed.includes(it.i) ? Math.max(it.h, need ?? 0) : need ?? it.h;
    return { ...it, h, ...(need ? { minH: need } : {}) };
  });
  // 잰 높이를 저장 배치에도 적어 둔다(다음 첫 그림용, 보기 모드에서만 · 1초 모아서)
  useEffect(() => {
    if (!band || draft || !saved) return;
    const t = window.setTimeout(() => saveAutoHeights(band, auto.rows), 1000);
    return () => window.clearTimeout(t);
  }, [auto.rows, band, draft, saved]);
  const setFixed = (id: string, on: boolean, h?: number) => setDraft((d) => {
    if (!d) return d;
    // 모서리로 정한 높이는 그 값 그대로 저장한다(화면에는 내용보다 작지 않게만 보인다)
    if (on) return { ...d, fixed: d.fixed.includes(id) ? d.fixed : [...d.fixed, id], items: h == null ? d.items : d.items.map((it) => (it.i === id ? { ...it, h } : it)) };
    if (!d.fixed.includes(id)) return d;
    // 자동으로 되돌리면 잰 내용 높이로 바로 맞춘다
    return { ...d, fixed: d.fixed.filter((f) => f !== id), items: d.items.map((it) => (it.i === id && auto.rows[id] ? { ...it, h: auto.rows[id] } : it)) };
  });
  // "빈칸 없애기": 모든 구역을 자동 높이로 되돌린다(열 · 순서 · 폭은 그대로, 위로 붙이기는 격자가)
  const fitAll = () => setDraft((d) => d && {
    ...d, fixed: [],
    items: d.items.map((it) => (auto.rows[it.i] ? { ...it, h: auto.rows[it.i] } : it)),
  });
  // 키보드: 화살표 = 옮기기(↑↓ 는 위 · 아래 구역을 건너), Shift+화살표 = 폭 · 높이
  const onGridKey = (id: string) => (e: KeyboardEvent<HTMLDivElement>) => {
    if (!draft || e.target !== e.currentTarget || !e.key.startsWith("Arrow")) return;
    e.preventDefault();
    const items = draft.items.map((it) => ({ ...it }));
    const it = items.find((x) => x.i === id);
    if (!it) return;
    const overlapX = (o: { x: number; w: number }) => o.x < it.x + it.w && it.x < o.x + o.w;
    if (e.shiftKey) {
      if (e.key === "ArrowLeft") it.w = Math.max(MIN_W, it.w - 1);
      if (e.key === "ArrowRight") it.w = Math.min(GRID_COLS - it.x, it.w + 1);
      const cur = draft.fixed.includes(id) ? it.h : auto.rows[id] ?? it.h;   // 자동이던 구역은 보이는 높이에서 시작
      if (e.key === "ArrowUp") it.h = Math.max(auto.rows[id] ?? HEAD_ROWS, cur - KEY_H_STEP);   // 내용보다 작게는 못 줄인다
      if (e.key === "ArrowDown") it.h = cur + KEY_H_STEP;
    } else if (e.key === "ArrowLeft") it.x = Math.max(0, it.x - 1);
    else if (e.key === "ArrowRight") it.x = Math.min(GRID_COLS - it.w, it.x + 1);
    else if (e.key === "ArrowUp") {
      // 바로 위 구역과 자리를 맞바꾼다(같은 y 로 두면 정렬 순서 때문에 안 움직일 수 있다)
      const above = items.filter((o) => o !== it && overlapX(o) && o.y < it.y).sort((a, b) => b.y - a.y)[0];
      if (above) { it.y = above.y; above.y = above.y + it.h; }
    } else {
      const below = items.filter((o) => o !== it && overlapX(o) && o.y > it.y).sort((a, b) => a.y - b.y)[0];
      if (below) { below.y = it.y; it.y = it.y + below.h; }
    }
    const fixed = e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && !draft.fixed.includes(id) ? [...draft.fixed, id] : draft.fixed;
    setDraft({ ...draft, items, fixed });
    setSaid(`${SECTIONS[id as SectionId].title}: ${it.x + 1}번째 칸부터 폭 ${it.w}칸, 높이 ${it.h}`);
  };

  return (
    <main className="dashboard dashboard-v2">
      <div className="dash-search" data-tour="dash-search">
        <NewRunForm />
      </div>
      {/* #464 휴대폰: 구역을 펼치지 않아도 지수 등락 · 공포지수를 한 줄로 */}
      {layout === "phone" && <DashSummary />}
      {/* #317 즐겨찾기 가운데 지금 손댈 종목 */}
      <TodayStrip />
      {band && (
        <div className="dash-edit-bar" role="toolbar" aria-label="대시보드 배치" data-tour="dash-layout">
          {!draft ? (
            <>
              <button type="button" className="link small" onClick={startEdit}>배치 편집</button>
              {saved && <button type="button" className="link small" onClick={resetLayout}>기본 배치로</button>}
            </>
          ) : (
            <>
              <strong className="small">배치 편집 중</strong>
              <span className="muted small">손잡이(⠿)로 끌기 · 모서리로 크기 · 구역을 고른 뒤 화살표로 옮기기, Shift+화살표로 크기</span>
              <button type="button" onClick={finishEdit}>완료</button>
              <button type="button" className="secondary" onClick={() => setDraft(null)}>취소</button>
              <button type="button" className="link small" onClick={fitAll} title="모든 구역 높이를 내용에 맞추고 위로 붙입니다(자리 · 폭은 그대로)">빈칸 없애기</button>
              <button type="button" className="link small" onClick={resetLayout}>기본 배치로</button>
              {draft.hidden.length > 0 && (
                <span className="small dash-hidden">숨긴 구역:
                  {draft.hidden.map((h) => (
                    <button key={h} type="button" className="link small" onClick={() => unhide(h)}>{SECTIONS[h as SectionId].title} 보이기</button>
                  ))}
                </span>
              )}
            </>
          )}
          <span className="sr-only" aria-live="polite">{said}</span>
        </div>
      )}
      {/* 폭 재기용 감싸개는 늘 그려 둔다 — 격자로 바뀌는 순간에도 크기 감시가 붙어 있게 */}
      <div ref={areaRef} className="dash-area">
      {grid ? (
        <div className={`dash-grid${band === "wide" ? " dash-grid-wide" : ""}${draft ? " dash-grid-edit" : ""}`}>
          {areaWidth > 0 && (
            <Suspense fallback={<p className="muted small">배치 불러오는 중…</p>}>
              <DashGrid width={areaWidth} items={gridItems} fixed={grid.fixed} editing={!!draft}
                // 고정 구역의 h 는 사용자가 정한 값으로 둔다 — 화면용 max(정한 값, 내용) 이 저장되어 높이가 쌓이지 않게
                onLayoutChange={(l) => setDraft((d) => (d ? { ...d, items: l.map((it) => (d.fixed.includes(it.i) ? { ...it, h: d.items.find((o) => o.i === it.i)?.h ?? it.h } : it)) } : d))}
                onHeightSet={(id, h) => setFixed(id, true, h)}
                renderItem={(id) => renderSection(id as SectionId)} onKey={onGridKey}
                label={(id) => `${SECTIONS[id as SectionId].title} — 화살표로 옮기기, Shift+화살표로 크기`} />
            </Suspense>
          )}
        </div>
      ) : layout === "wide" ? (
        <div className="dash-wide-flow" ref={flowRef}>
          {/* #573 아침 시황 · 오늘 방향은 3열 위 한 줄 — 넓은 화면 첫눈에 */}
          <div className="dash-top">{prefs.order.filter((id) => TOP_SECTIONS.includes(id)).map(renderSection)}</div>
          <div className="dash-columns">
            {([0, 1, 2] as const).map((col) => (
              <div key={col} className="dash-col">
                {prefs.order.filter((id) => !TOP_SECTIONS.includes(id) && COLUMN[id] === col).map(renderSection)}
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="dash-sections" ref={flowRef}>{prefs.order.map(renderSection)}</div>
      )}
      </div>
    </main>
  );

  function renderSection(id: SectionId) {
    const s = SECTIONS[id];
    const open = isOpen(id);
    const bodyId = `dash-${id}`;
    const line = peers(id);
    const i = line.indexOf(id);
    // 격자(#487)에서는 자리를 격자가 정하므로 ↑↓ 대신 편집 중 끌기 손잡이 · 숨기기, 내용은 편집 중에도 펼쳐 둔다
    const inGrid = !!grid;
    const shownOpen = draft ? true : open;
    return (
      <section key={id} data-section={id} data-tour={`dash-${id}`} className={`dash-section${s.wide ? " dash-wide" : ""}${shownOpen ? "" : " dash-closed"}`} aria-labelledby={`${bodyId}-title`}>
        <header className="dash-head">
          {draft && <span className="dash-drag" aria-hidden="true" title="끌어서 옮기기">⠿</span>}
          <h2 id={`${bodyId}-title`} className="dash-title">
            <button type="button" className="dash-toggle" aria-expanded={shownOpen} aria-controls={shownOpen ? bodyId : undefined}
              onClick={() => flip(id)} disabled={!!draft}>
              <span className="dash-caret" aria-hidden="true">{shownOpen ? "▾" : "▸"}</span>
              {s.title}
            </button>
          </h2>
          {draft ? (
            <span className="dash-item-actions">
              {draft.fixed.includes(id) && (
                <button type="button" className="link small" onClick={() => setFixed(id, false)} title="높이를 직접 정한 구역 — 내용 높이로 되돌립니다">내용에 맞추기</button>
              )}
              <button type="button" className="link small dash-hide" onClick={() => hide(id)}>숨기기</button>
            </span>
          ) : !inGrid && (
            <span className="dash-order" role="group" aria-label={`${s.title} 순서`}>
              <button type="button" id={`dash-move-${id}--1`} className="dash-move" disabled={i === 0}
                aria-label={`${s.title} 위로`} onClick={() => move(id, -1)}>↑</button>
              <button type="button" id={`dash-move-${id}-1`} className="dash-move" disabled={i === line.length - 1}
                aria-label={`${s.title} 아래로`} onClick={() => move(id, 1)}>↓</button>
            </span>
          )}
        </header>
        {/* 편집 중엔 내용 클릭 · 초점을 막는다(inert — React 18 타입에 없어 속성으로) */}
        {shownOpen && (
          <div id={bodyId} className="dash-body" ref={(el) => { if (el) { if (draft) el.setAttribute("inert", ""); else el.removeAttribute("inert"); } }}>
            {/* #519 내용 높이를 재는 감싸개(격자에서만 잰다) */}
            <div className="dash-body-inner" ref={inGrid ? auto.register(id) : undefined}>
              {/* #537 화면 밖 구역은 보일 때 그리고(그 전엔 API 도 안 부른다), 코드가 따로인 구역은 받는 동안 안내 */}
              {/* #362 알림(#/?flow=intraday)이 연 수급 구역은 화면 밖이어도 바로 그려 "오늘 장중" 탭 · 스크롤이 일어나게 */}
              <WhenVisible eager={id === "flow" && /[?&]flow=intraday(?:&|$)/.test(window.location.hash)}>
                <SectionBoundary fallback={<ChunkFailed />}>
                  <Suspense fallback={<p className="muted small">불러오는 중…</p>}>{s.render()}</Suspense>
                </SectionBoundary>
              </WhenVisible>
            </div>
          </div>
        )}
      </section>
    );
  }
}
