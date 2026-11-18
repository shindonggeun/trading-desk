import { useCallback, useEffect, useRef, useState } from "react";
import { alertKey, hhmm } from "../alertsFeed";
import { api, Unauthorized, type MarketAlert } from "../api";
import MiniTickerEditor from "../components/MiniTickerEditor";
import { krwShort, price } from "../format";
import { loadMiniPrefs, MINI_FUTURES, saveMiniPrefs, useMiniItems, type MiniPrefs } from "../miniWindow";
import { byAsked } from "../ticker";
import type { BoardItem } from "../types";

const QUOTE_MS = 15_000;          // 시세판과 같은 주기
const FLOW_MS = 60_000;
const NEW_ALERT_MS = 20_000;      // 새 알림을 굵게 두는 시간(깜빡이지 않는다)
const STRIP_MAX_H = 140;          // 이 높이 아래면 띠, 위면 세로 카드
const EDIT_SIZE = { width: 420, height: 520 };   // ⚙ 로 종목을 고치는 동안 잠깐 키우는 크기

type Flow = { foreign: number | null; inst: number | null; provisional: boolean } | null;
/** 한 종목의 시세 — 시세판(/api/estimates) 또는 코스피200 선물(/api/market/futures/quote) */
type Quote = { price: number | null; change_pct: number | null; estimate?: boolean; stale?: boolean };

function Mark({ v }: { v: number | null | undefined }) {
  if (v == null || v === 0) return <span aria-hidden="true" className="mini-mark">■</span>;
  return <span aria-hidden="true" className="mini-mark">{v > 0 ? "▲" : "▼"}</span>;
}

const signed = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");
const fromBoard = (it: BoardItem): Quote => ({ price: it.price ?? null, change_pct: it.change_pct ?? null, estimate: it.state === "estimate" });

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

  const boardKey = items.filter((it) => it.ticker !== MINI_FUTURES).map((it) => it.ticker).join(",");
  const wantFutures = items.some((it) => it.ticker === MINI_FUTURES);
  useEffect(() => {
    if (!boardKey) return;
    let alive = true;
    const asked = boardKey.split(",");
    const load = () => api.board(asked).then((r) => {
      if (!alive) return;
      ok();
      // 맨 국내 코드(005930)를 물으면 서버는 005930.KS 로 답한다 — 물은 티커로 다시 읽는다
      const got = byAsked(asked, r.items);
      setQuotes((q) => ({ ...q, ...Object.fromEntries(Object.entries(got).map(([k, it]) => [k, fromBoard(it)])) }));
    }).catch(fail);
    load();
    const id = window.setInterval(load, QUOTE_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [boardKey, fail, ok]);

  useEffect(() => {
    if (!wantFutures) return;
    let alive = true;
    const load = () => api.futuresQuote().then((f) => {
      if (!alive) return;
      ok();
      const q: Quote = f.supported && f.price != null
        ? { price: f.price, change_pct: f.change_pct ?? null, stale: f.stale }
        : { price: null, change_pct: null };
      setQuotes((all) => ({ ...all, [MINI_FUTURES]: q }));
    }).catch(fail);
    load();
    const id = window.setInterval(load, QUOTE_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [wantFutures, fail, ok]);

  useEffect(() => {
    let alive = true;
    const loadFlow = () => api.marketFlow("KOSPI", "1d", 1).then((f) => {
      const r = f.records[0];
      if (alive) ok();
      if (alive) setFlow(r ? { foreign: r.investors.foreigner?.net ?? null, inst: r.investors.institution?.net ?? null, provisional: r.provisional } : null);
    }).catch(fail);
    const loadAlert = () => api.alertsToday().then((a) => {
      if (!alive) return;
      ok();
      const last = a.items[a.items.length - 1] ?? null;
      setAlert(last);
      const k = last ? alertKey(last) : "";
      if (last && lastSeen.current !== null && k !== lastSeen.current) setFresh(true);   // 처음 불러온 것은 새 알림이 아니다
      lastSeen.current = k;
    }).catch(fail);
    loadFlow();
    loadAlert();
    const f = window.setInterval(loadFlow, FLOW_MS);
    const a = window.setInterval(loadAlert, QUOTE_MS);
    return () => { alive = false; window.clearInterval(f); window.clearInterval(a); };
  }, [fail, ok]);

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
      {login ? (
        <p className="mini-login">세션이 끝났어요 — <button type="button" className="link" onClick={openDesk}>데스크에서 다시 로그인</button></p>
      ) : editing ? (
        <section className="mini-editor-panel" aria-label="작은 창 종목 고치기">
          <h2 className="mini-edit-title">작은 창 종목</h2>
          <MiniTickerEditor items={items} onChange={setItems} search={false} />
        </section>
      ) : (
        <ul className="mini-items" aria-label="작은 창 시세">
          {items.length === 0 ? <li className="mini-muted">종목이 없어요 — ⚙ 에서 더해 주세요</li>
            : items.map((it) => {
              const q = quotes[it.ticker];
              return (
                <li key={it.ticker} className="mini-quote" title={it.ticker === MINI_FUTURES ? `${it.name} · 근월물` : `${it.name} · ${it.ticker}`}>
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
              <span className="mini-muted">{hhmm(alert.t)}</span> {fresh && <span className="mini-badge">새 알림</span>} {alert.text}
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
