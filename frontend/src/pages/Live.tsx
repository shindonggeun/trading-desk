import { useEffect, useMemo, useState } from "react";
import { notificationsAllowed, usePriceAlerts } from "../alerts";
import { api } from "../api";
import InstrumentSearch, { type Picked } from "../components/InstrumentSearch";
import BoardChart from "../components/BoardChart";
import LiveChart from "../components/LiveChart";
import QuoteBoard, { type BoardTicker } from "../components/QuoteBoard";
import FavoriteStar from "../components/FavoriteStar";
import { addFavorite, removeFavorite, useFavorites } from "../favorites";
import { price, time } from "../format";
import type { Run } from "../types";
import StockProfileCard from "../components/StockProfileCard";

/** #/live?t=NVDA — 대시보드 즐겨찾기 타일에서 넘어온 종목 */
function linkedTicker(): string | null {
  const q = window.location.hash.split("?")[1];
  const t = q ? new URLSearchParams(q).get("t") : null;
  return t ? t.trim().toUpperCase() : null;
}

const VIEW_KEY = "desk.board.view";
function weekendKst(): boolean {
  const day = new Date(Date.now() + 9 * 3600_000).getUTCDay();
  return day === 0 || day === 6;
}

/**
 * 시세판(#124 · 예전 실시간 차트 #26): 관심 종목 = 즐겨찾기(#135, 서버 저장). 위에 즐겨찾기 · 주말 추정 타일(장중 실시간 · 장외 추정가),
 * 아래에 고른 종목 차트 — "1분 실시간"(타점 겹쳐 보기 · 도달 알림 #42) 또는 "봉 · 추정"(5분 ~ 주봉 + 장외 추정 봉).
 */
export default function Live() {
  const { list, error: favError } = useFavorites();
  const tabs: Picked[] = useMemo(() => (list ?? []).map((f) => ({ ticker: f.ticker, name: f.name ?? undefined })), [list]);
  // 고른 탭은 순서가 아니라 종목으로 기억한다 — 다른 기기 · 되돌리기로 목록이 바뀌어도 같은 종목을 본다
  const [active, setActive] = useState<string | null>(null);
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [runs, setRuns] = useState<Run[]>([]);
  // the price with the ticker it belongs to: a tab switch must not hand one ticker's price to another's alerts
  const [tick, setTick] = useState<{ ticker: string; price: number } | null>(null);
  const [permission, setPermission] = useState(notificationsAllowed());
  const tab = tabs.find((t) => t.ticker === active) ?? tabs[0];
  // 주말 묶음에서 고른 종목(관심 종목 탭이 아닌 것) — 차트만 보여 준다
  const [focus, setFocus] = useState<BoardTicker | null>(null);
  const [view, setView] = useState<"live" | "bars">(() => {
    try {
      const v = localStorage.getItem(VIEW_KEY);
      if (v === "live" || v === "bars") return v;
    } catch {
      /* 기본값 */
    }
    return weekendKst() ? "bars" : "live";
  });
  const [infoOn, setInfoOn] = useState(false);   // #296 선택 종목의 종목 정보 카드
  const pickView = (v: "live" | "bars") => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* 그대로 */
    }
  };
  const pickTile = (t: BoardTicker) => {
    if (tabs.some((x) => x.ticker === t.ticker)) {
      setActive(t.ticker);
      setFocus(null);
    } else setFocus(t);
  };
  const shown = focus ?? tab;

  // 대시보드 타일에서 넘어오면(#/live?t=…) 그 종목을 고른다 — 목록을 읽은 뒤 한 번
  const [linked, setLinked] = useState<string | null>(linkedTicker);
  useEffect(() => {
    const on = () => setLinked(linkedTicker());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!linked || list === null) return;
    if (tabs.some((x) => x.ticker === linked)) {
      setActive(linked);
      setFocus(null);
    } else setFocus({ ticker: linked });
    setLinked(null);
  }, [linked, list, tabs]);

  // re-read on every tab switch: an analysis that finished meanwhile brings its levels
  useEffect(() => {
    api.runs(1, 100).then((p) => setRuns(p.items)).catch(() => setRuns([]));
  }, [tab?.ticker]);
  const livePrice = tick && tick.ticker === tab?.ticker ? tick.price : null;

  // 이 종목의 가장 최근 완료 분석
  const run = useMemo(
    () => runs.find((r) => r.ticker === tab?.ticker && r.status === "done" && r.plan) ?? null,
    [runs, tab?.ticker],
  );
  const plan = run?.plan ?? null;
  // 보기(1분 실시간 / 봉 · 추정)마다 따로 센다 — 추정가와 실제가를 섞어 거짓 "도달" 이 나지 않게
  const alerts = usePriceAlerts(`${tab?.ticker}:${run?.id ?? ""}:${view}`,
    `${tab?.name ?? tab?.ticker ?? ""}${view === "bars" ? " (시세판 · 장외면 추정가)" : ""}`, plan, livePrice);

  const add = () => {
    const t = pick.ticker.trim().toUpperCase();
    if (!t) return;
    setFocus(null);
    if (!tabs.some((x) => x.ticker === t)) addFavorite(t, pick.name);
    setActive(t);
    setPick({ ticker: "" });
  };
  const remove = (ticker: string) => {
    removeFavorite(ticker);
    if (ticker === tab?.ticker) setActive(null);   // 보던 탭을 빼면 첫 탭으로, 다른 탭이면 그대로
  };
  const askPermission = () => {
    if (typeof Notification === "undefined") return;
    Notification.requestPermission().then((p) => setPermission(p === "granted"));
  };

  return (
    <main className="live">
      <div className="live-head">
        <h1>시세판</h1>
        <form className="live-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <InstrumentSearch value={pick} onChange={setPick} />
          <button type="submit" disabled={!pick.ticker.trim()}>추가</button>
        </form>
      </div>
      <QuoteBoard watch={tabs} selected={shown?.ticker} onPick={pickTile}
        // 봉 · 추정 보기에선 1분 흐름이 없으니 시세판 값(장외면 추정가)으로 도달 알림을 이어 간다
        onPrice={(t, p) => { if (view === "bars" && t === tab?.ticker) setTick({ ticker: t, price: p }); }} />
      {favError && <p className="error small">{favError}</p>}
      {list !== null && tabs.length === 0 && !focus && (
        <p className="empty-state">즐겨찾기가 없습니다. 위에서 종목을 찾아 추가하거나, 타일을 고른 뒤 ☆ 를 누르세요.</p>
      )}
      <div className="live-tabs" role="tablist" aria-label="관심 종목(즐겨찾기)">
        {tabs.map((t) => (
          <span key={t.ticker} className={`live-tab ${t === tab ? "live-tab-on" : ""}`}>
            <button type="button" role="tab" aria-selected={!focus && t === tab} onClick={() => { setActive(t.ticker); setFocus(null); }}>
              {t.name ? `${t.name} ` : ""}<span className="muted small">{t.ticker}</span>
            </button>
            <button type="button" className="link live-tab-x" aria-label={`${t.name ?? t.ticker} 즐겨찾기에서 빼기`} title="즐겨찾기에서 빼기" onClick={() => remove(t.ticker)}>×</button>
          </span>
        ))}
      </div>
      {shown && (
        <div className="live-view chips" role="group" aria-label="차트 종류">
          {focus && (
            <span className="small">
              <FavoriteStar ticker={focus.ticker} name={focus.name} /> <strong>{focus.name ?? focus.ticker}</strong> <span className="muted">{focus.ticker}</span>
            </span>
          )}
          <button type="button" className={`chip${view === "live" ? " chip-on" : ""}`} aria-pressed={view === "live"} onClick={() => pickView("live")}>1분 실시간</button>
          <button type="button" className={`chip${view === "bars" ? " chip-on" : ""}`} aria-pressed={view === "bars"} onClick={() => pickView("bars")}>봉 · 장외 추정</button>
          <button type="button" className={`chip${infoOn ? " chip-on" : ""}`} aria-pressed={infoOn} onClick={() => setInfoOn((v) => !v)}>정보</button>
        </div>
      )}
      {shown && view === "bars" && <BoardChart key={shown.ticker} ticker={shown.ticker} name={shown.name} />}
      {focus && view === "live" && <LiveChart ticker={focus.ticker} plan={null} />}
      {shown && infoOn && <StockProfileCard key={shown.ticker} ticker={shown.ticker} onClose={() => setInfoOn(false)} />}
      {!focus && tab && (
        <>
          <div className="live-plan muted small">
            {run && plan ? (
              <>
                마지막 분석 <a className="link" href={`#/runs/${run.id}`}>{run.trade_date}</a> · {plan.rating} · 진입 {price(plan.entry_price)} · 손절{" "}
                {price(plan.stop_loss)} · 목표 {price(plan.price_target)}
              </>
            ) : (
              "이 종목의 완료된 분석이 없어 타점선 없이 보여 줍니다."
            )}
            {livePrice != null && <> · {view === "bars" ? "시세판 값" : "현재가"} <strong>{price(livePrice)}</strong></>}
          </div>
          {view === "live" && <LiveChart ticker={tab.ticker} plan={plan} onPrice={(price) => setTick({ ticker: tab.ticker, price })} />}
          {plan && (
            <section className="card live-alerts">
              <div className="live-alerts-head">
                <h3>도달 알림</h3>
                {!permission && typeof Notification !== "undefined" && (
                  <button type="button" className="secondary" onClick={askPermission}>브라우저 알림 켜기</button>
                )}
              </div>
              <p className="muted small">
                현재가가 진입 · 손절 · 목표(와 롱 · 숏 조건)를 지나가면 여기에 남기고{permission ? " 브라우저 알림도 띄웁니다" : ""}.
                이 화면을 열어 둔 동안만 동작합니다.
              </p>
              {alerts.length > 0 ? (
                <ul className="live-alert-list">
                  {alerts.map((a) => (
                    <li key={`${a.key}-${a.at}`}>
                      <strong>{a.label} {price(a.level)}</strong> 도달 · 현재가 {price(a.price)} <span className="muted small">{time(a.at)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">아직 도달한 레벨이 없습니다.</p>
              )}
            </section>
          )}
        </>
      )}
    </main>
  );
}
