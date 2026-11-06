import { useEffect, useMemo, useState } from "react";
import { notificationsAllowed, usePriceAlerts } from "../alerts";
import { api } from "../api";
import InstrumentSearch, { type Picked } from "../components/InstrumentSearch";
import BoardChart from "../components/BoardChart";
import LiveChart from "../components/LiveChart";
import QuoteBoard, { type BoardTicker } from "../components/QuoteBoard";
import { price, time } from "../format";
import type { Run } from "../types";

const STORE = "desk.live.tickers";
const DEFAULT: Picked[] = [{ ticker: "NVDA", name: "엔비디아" }];

function loadTabs(): Picked[] {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) ?? "null");
    return Array.isArray(raw) && raw.length ? raw : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

const VIEW_KEY = "desk.board.view";
function weekendKst(): boolean {
  const day = new Date(Date.now() + 9 * 3600_000).getUTCDay();
  return day === 0 || day === 6;
}

/**
 * 시세판(#124 · 예전 실시간 차트 #26): 위에 관심 종목 · 주말 추정 타일(장중 실시간 · 장외 추정가),
 * 아래에 고른 종목 차트 — "1분 실시간"(타점 겹쳐 보기 · 도달 알림 #42) 또는 "봉 · 추정"(5분 ~ 주봉 + 장외 추정 봉).
 */
export default function Live() {
  const [tabs, setTabs] = useState<Picked[]>(loadTabs);
  const [active, setActive] = useState(0);
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [runs, setRuns] = useState<Run[]>([]);
  // the price with the ticker it belongs to: a tab switch must not hand one ticker's price to another's alerts
  const [tick, setTick] = useState<{ ticker: string; price: number } | null>(null);
  const [permission, setPermission] = useState(notificationsAllowed());
  const tab = tabs[Math.min(active, tabs.length - 1)];
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
  const pickView = (v: "live" | "bars") => {
    setView(v);
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* 그대로 */
    }
  };
  const pickTile = (t: BoardTicker) => {
    const i = tabs.findIndex((x) => x.ticker === t.ticker);
    if (i >= 0) {
      setActive(i);
      setFocus(null);
    } else setFocus(t);
  };
  const shown = focus ?? tab;

  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify(tabs));
    } catch {
      /* 저장 못 해도 화면은 그대로 */
    }
  }, [tabs]);

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
  const alerts = usePriceAlerts(`${tab?.ticker}:${run?.id ?? ""}`, tab?.name ?? tab?.ticker ?? "", plan, livePrice);

  const add = () => {
    const t = pick.ticker.trim().toUpperCase();
    if (!t) return;
    const at = tabs.findIndex((x) => x.ticker === t);
    setFocus(null);
    if (at >= 0) setActive(at);
    else {
      setTabs([...tabs, { ticker: t, name: pick.name }]);
      setActive(tabs.length);
    }
    setPick({ ticker: "" });
  };
  const remove = (i: number) => {
    const next = tabs.filter((_, j) => j !== i);
    setTabs(next.length ? next : DEFAULT);
    setActive(0);
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
      <div className="live-tabs" role="tablist" aria-label="관심 종목">
        {tabs.map((t, i) => (
          <span key={t.ticker} className={`live-tab ${t === tab ? "live-tab-on" : ""}`}>
            <button type="button" role="tab" aria-selected={!focus && t === tab} onClick={() => { setActive(i); setFocus(null); }}>
              {t.name ? `${t.name} ` : ""}<span className="muted small">{t.ticker}</span>
            </button>
            <button type="button" className="link live-tab-x" aria-label={`${t.ticker} 탭 닫기`} onClick={() => remove(i)}>×</button>
          </span>
        ))}
      </div>
      {shown && (
        <div className="live-view chips" role="group" aria-label="차트 종류">
          {focus && <span className="small"><strong>{focus.name ?? focus.ticker}</strong> <span className="muted">{focus.ticker}</span></span>}
          <button type="button" className={`chip${view === "live" ? " chip-on" : ""}`} aria-pressed={view === "live"} onClick={() => pickView("live")}>1분 실시간</button>
          <button type="button" className={`chip${view === "bars" ? " chip-on" : ""}`} aria-pressed={view === "bars"} onClick={() => pickView("bars")}>봉 · 장외 추정</button>
          {focus && (
            <button type="button" className="link small" onClick={() => { setTabs([...tabs, focus]); setActive(tabs.length); setFocus(null); }}>
              관심 종목에 추가
            </button>
          )}
        </div>
      )}
      {shown && view === "bars" && <BoardChart key={shown.ticker} ticker={shown.ticker} name={shown.name} />}
      {focus && view === "live" && <LiveChart ticker={focus.ticker} plan={null} />}
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
