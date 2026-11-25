import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { alertKey, alertLines, ALERTS_KEY, badgeOf, hhmm, loadAlerts } from "../alertsFeed";
import { api, Unauthorized, type MarketAlert } from "../api";
import MiniTickerEditor from "../components/MiniTickerEditor";
import { KIND_KO, MARKET_KO, verdictText } from "../dayCall";
import { krwShort, price } from "../format";
import { loadMiniPrefs, MINI_FUTURES, saveMiniPrefs, useMiniItems, type MiniPrefs } from "../miniWindow";
import { byAsked } from "../ticker";
import type { BoardItem, DayCallMarket, DayCallToday } from "../types";
import { usePoll } from "../poll";
import { splitView } from "../changeSplit";
import { useLivePoll } from "../livePoll";
import { BOARD_POLL_MS, boardLive } from "../boardLive";

const QUOTE_MS = 15_000;          // 시세판과 같은 주기
const FLOW_MS = 60_000;
const NEW_ALERT_MS = 20_000;      // 새 알림을 굵게 두는 시간(깜빡이지 않는다)
const STRIP_MAX_H = 140;          // 이 높이 아래면 띠, 위면 세로 카드
const EDIT_SIZE = { width: 420, height: 520 };   // ⚙ 로 종목을 고치는 동안 잠깐 키우는 크기

type Flow = { foreign: number | null; inst: number | null; provisional: boolean } | null;
/** 한 종목의 시세 — 시세판(/api/estimates) 또는 코스피200 선물(/api/market/futures/quote) */
type Quote = { price: number | null; change_pct: number | null; estimate?: boolean; stale?: boolean; split?: string };

function Mark({ v }: { v: number | null | undefined }) {
  if (v == null || v === 0) return <span aria-hidden="true" className="mini-mark">■</span>;
  return <span aria-hidden="true" className="mini-mark">{v > 0 ? "▲" : "▼"}</span>;
}

const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");
function MiniDirection({ market, data, failing }: { market: DayCallMarket; data?: DayCallToday; failing: boolean }) {
  const kind = data?.calls.prev_close ? "prev_close" : "open";
  const call = data?.calls[kind];
  const verdict = call ? verdictText(call) : null;
  const title = call && data ? `${data.date} · ${KIND_KO[kind]} · ${call.round_label}` : undefined;
  return (
    <li className="mini-direction" title={title}>
      <span className="mini-name">{MARKET_KO[market]}</span>{" "}
      {verdict ? <span className={verdict.cls}><span aria-hidden="true">{verdict.mark}</span> {verdict.text}</span>
        : <span className="mini-muted">{failing ? "불러오기 실패" : data ? "판정 없음" : "불러오는 중"}</span>}
      {call && data?.today === false && <span className="mini-muted"> 이전 장</span>}
      {call && failing && <span className="mini-muted"> 지연</span>}
    </li>
  );
}

// #1002 국장 정규장 뒤 장외: 등락은 합친 하루, 정규 · 장외는 툴팁(작은 창은 한 줄)
const fromBoard = (it: BoardItem): Quote => {
  const sv = splitView(it);
  return { price: it.price ?? null, change_pct: sv ? sv.pct : it.change_pct ?? null, estimate: it.state === "estimate",
           split: sv ? `${sv.line}
${sv.title}` : undefined };
};

/**
 * #582 회사에서 조용히 띄워 두는 작은 창(#/mini · Document PiP) — 고른 종목 시세 · 외인/기관 수급 한 줄 · 오늘 마지막 알림.
 * #616 종목은 작은 창 전용 목록(기본 10종)을 한글 이름으로 보이고, 설정 또는 창 안 ⚙ 에서 고친다.
 * 창이 낮으면 띠(넘치면 두 줄까지), 높으면 세로 카드. 조용한 모드는 회색조 · ▲▼ 기호, 글자 크기 3단계(이 브라우저에 기억).
 */
export default function MiniWindow({ pip = false }: { pip?: boolean }) {
  const [prefs, setPrefs] = useState<MiniPrefs>(loadMiniPrefs);
  const [items, setItems] = useMiniItems();
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [flow, setFlow] = useState<Flow>(null);
  const [alert, setAlert] = useState<MarketAlert | null>(null);
  const [fresh, setFresh] = useState(false);
  const [login, setLogin] = useState(false);
  const [strip, setStrip] = useState(true);
  const [editing, setEditing] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const lastSeen = useRef<string | null>(null);
  const before = useRef<{ width: number; height: number } | null>(null);

  const update = (next: MiniPrefs) => { setPrefs(next); saveMiniPrefs(next); };
  const fail = useCallback((e: unknown) => { if (e instanceof Unauthorized) setLogin(true); }, []);
  const ok = useCallback(() => setLogin(false), []);   // 데스크 탭에서 다시 로그인하면 다음 요청이 성공해 안내를 내린다
  const view = () => root.current?.ownerDocument.defaultView ?? window;   // PiP 안에서 전역 window 는 원래 탭이다

  // 배치: 이 창(PiP 면 그 창)의 높이로
  useEffect(() => {
    const v = view();
    const apply = () => setStrip(v.innerHeight < STRIP_MAX_H);
    apply();
    v.addEventListener("resize", apply);
    return () => v.removeEventListener("resize", apply);
  }, []);

  // 방향도 PiP의 원래 탭이 가려진 동안 시세와 같은 주기로 갱신한다.
  const kr = usePoll("day-call:kr", () => api.dayCall("kr"), QUOTE_MS, { pauseHidden: false });
  const us = usePoll("day-call:us", () => api.dayCall("us"), QUOTE_MS, { pauseHidden: false });
  const needsLogin = login || kr.error instanceof Unauthorized || us.error instanceof Unauthorized;

  const boardKey = items.filter((it) => it.ticker !== MINI_FUTURES).map((it) => it.ticker).join(",");
  const wantFutures = items.some((it) => it.ticker === MINI_FUTURES);
  // #626 작은 창은 연 탭이 가려져도(다른 앱을 보는 중) 계속 읽는다
  // #1000 시세판과 같은 스트림 규칙(실시간 · #999 추정) — 탭이 가려지면 스트림은 닫히고 REST 로 10초마다 읽는다
  const liveSpec = useMemo(() => boardLive(boardKey ? boardKey.split(",") : []), [boardKey]);
  useLivePoll(boardKey ? `board:${boardKey}` : null, () => api.board(boardKey.split(",")), BOARD_POLL_MS, {
    pauseHidden: false,
    onData: (r) => {
      ok();
      // 맨 국내 코드(005930)를 물으면 서버는 005930.KS 로 답한다 — 물은 티커로 다시 읽는다
      const got = byAsked(boardKey.split(","), r.items);
      setQuotes((q) => ({ ...q, ...Object.fromEntries(Object.entries(got).map(([k, it]) => [k, fromBoard(it)])) }));
    },
    onError: fail,
  }, liveSpec);

  usePoll(wantFutures ? "futures-quote" : null, () => api.futuresQuote(), QUOTE_MS, {
    pauseHidden: false,
    onData: (f) => {
      ok();
      const q: Quote = f.supported && f.price != null
        ? { price: f.price, change_pct: f.change_pct ?? null, stale: f.stale }
        : { price: null, change_pct: null };
      setQuotes((all) => ({ ...all, [MINI_FUTURES]: q }));
    },
    onError: fail,
  });

  usePoll("market-flow:KOSPI:1d:1", () => api.marketFlow("KOSPI", "1d", 1), FLOW_MS, {
    pauseHidden: false,
    onData: (f) => {
      const r = f.records[0];
      ok();
      setFlow(r ? { foreign: r.investors.foreigner?.net ?? null, inst: r.investors.institution?.net ?? null, provisional: r.provisional } : null);
    },
    onError: fail,
  });
  // 대시보드 알림 띠와 같은 요청을 함께 쓴다(작은 창이 떠 있는 동안은 15초마다)
  usePoll(ALERTS_KEY, loadAlerts, QUOTE_MS, {
    pauseHidden: false,
    onData: (a) => {
      ok();
      const last = a.items[a.items.length - 1] ?? null;
      setAlert(last);
      const k = last ? alertKey(last) : "";
      if (last && lastSeen.current !== null && k !== lastSeen.current) setFresh(true);   // 처음 불러온 것은 새 알림이 아니다
      lastSeen.current = k;
    },
    onError: fail,
  });

  useEffect(() => {
    if (!fresh) return;
    const id = window.setTimeout(() => setFresh(false), NEW_ALERT_MS);
    return () => window.clearTimeout(id);
  }, [fresh]);

  const openDesk = () => {
    view().open(`${location.origin}${location.pathname}#/`, "_blank", "noopener");
  };
  // ⚙: 띠 크기에서는 고칠 칸이 안 보여 창을 잠깐 키우고, 다 고치면 원래 크기로(PiP · 팝업 모두 사용자 동작 안에서만 된다)
  const toggleEdit = () => {
    const v = view();
    if (!editing) {
      before.current = { width: v.outerWidth, height: v.outerHeight };
      if (v.innerHeight < EDIT_SIZE.height) {
        try { v.resizeTo(Math.max(v.outerWidth, EDIT_SIZE.width), EDIT_SIZE.height); } catch { /* 크기를 못 바꾸면 그 안에서 스크롤 */ }
      }
    } else if (before.current) {
      try { v.resizeTo(before.current.width, before.current.height); } catch { /* 그대로 둔다 */ }
      before.current = null;
    }
    setEditing(!editing);
  };
  const sizes: MiniPrefs["size"][] = ["s", "m", "l"];
  const nextSize = sizes[(sizes.indexOf(prefs.size) + 1) % sizes.length];
  const layout = editing ? "edit" : strip ? "strip" : "card";

  return (
    <div ref={root} className={`mini mini-${layout} mini-size-${prefs.size}${prefs.quiet ? " mini-quiet" : ""}${pip ? " mini-pip" : ""}`}>
      {needsLogin ? (
        <p className="mini-login">세션이 끝났어요 — <button type="button" className="link" onClick={openDesk}>데스크에서 다시 로그인</button></p>
      ) : editing ? (
        <section className="mini-editor-panel" aria-label="작은 창 종목 고치기">
          <h2 className="mini-edit-title">작은 창 종목</h2>
          <MiniTickerEditor items={items} onChange={setItems} search={false} />
        </section>
      ) : (
        <ul className="mini-items" aria-label="작은 창 시세와 오늘 방향">
          <MiniDirection market="kr" data={kr.data} failing={kr.failing} />
          <MiniDirection market="us" data={us.data} failing={us.failing} />
          {items.length === 0 ? <li className="mini-muted">종목이 없어요 — ⚙ 에서 더해 주세요</li>
            : items.map((it) => {
              const q = quotes[it.ticker];
              return (
                <li key={it.ticker} className="mini-quote"
                  title={it.ticker === MINI_FUTURES ? `${it.name} · 근월물` : `${it.name} · ${it.ticker}${q?.split ? `
${q.split}` : ""}`}>
                  <span className="mini-name">{it.name}</span>{" "}
                  <span className="mini-price">{q?.price != null ? price(q.price) : "—"}</span>{" "}
                  <span className={`mini-chg ${tone(q?.change_pct)}`}><Mark v={q?.change_pct} />{signed(q?.change_pct)}</span>
                  {q?.estimate && <span className="mini-muted"> 추정</span>}
                  {q?.stale && <span className="mini-muted"> 지연</span>}
                </li>
              );
            })}
          {flow && (flow.foreign != null || flow.inst != null) && (
            <li className="mini-flow">
              <span className="mini-muted">코스피</span> 외인 <span className={tone(flow.foreign)}><Mark v={flow.foreign} />{krwShort(flow.foreign)}</span>
              {" · "}기관 <span className={tone(flow.inst)}><Mark v={flow.inst} />{krwShort(flow.inst)}</span>
              {flow.provisional && <span className="mini-muted"> 잠정</span>}
            </li>
          )}
          {alert && (
            <li className={`mini-alert${fresh ? " mini-new" : ""}`} role="status">
              <span className="mini-muted">{hhmm(alert.t)}</span> {fresh && <span className="mini-badge">새 알림</span>}{" "}
              <span className={`mini-tone ${badgeOf(alert).cls}`}>{badgeOf(alert).mark} {badgeOf(alert).label}</span> {alertLines(alert.text).head}
            </li>
          )}
        </ul>
      )}
      <div className="mini-tools">
        <button type="button" aria-pressed={editing} title={editing ? "다 고쳤어요" : "종목 고치기"} onClick={toggleEdit}>
          {editing ? "✓" : "⚙"}<span className="sr-only">{editing ? " 종목 고치기 끝" : " 종목 고치기"}</span>
        </button>
        <button type="button" aria-pressed={prefs.quiet} title="조용한 모드 — 회색조 · 기호만" onClick={() => update({ ...prefs, quiet: !prefs.quiet })}>
          {prefs.quiet ? "◐" : "●"}<span className="sr-only"> 조용한 모드</span>
        </button>
        <button type="button" title={`글자 크기(지금 ${prefs.size === "s" ? "작게" : prefs.size === "m" ? "보통" : "크게"})`} onClick={() => update({ ...prefs, size: nextSize })}>
          가<span className="sr-only"> 글자 크기 바꾸기</span>
        </button>
        <button type="button" title="데스크 열기" onClick={openDesk}>↗<span className="sr-only"> 데스크 열기</span></button>
      </div>
    </div>
  );
}
