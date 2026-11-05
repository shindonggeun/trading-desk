import { useEffect, useMemo, useState } from "react";
import { notificationsAllowed, usePriceAlerts } from "../alerts";
import { api } from "../api";
import InstrumentSearch, { type Picked } from "../components/InstrumentSearch";
import LiveChart from "../components/LiveChart";
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

/** 실시간 차트(#26): 관심 종목 탭, 1분봉 · 현재가 자동 갱신, 마지막 분석의 타점 겹쳐 보기, 도달 알림(#42). */
export default function Live() {
  const [tabs, setTabs] = useState<Picked[]>(loadTabs);
  const [active, setActive] = useState(0);
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [runs, setRuns] = useState<Run[]>([]);
  const [livePrice, setLivePrice] = useState<number | null>(null);
  const [permission, setPermission] = useState(notificationsAllowed());
  const tab = tabs[Math.min(active, tabs.length - 1)];

  useEffect(() => {
    try {
      localStorage.setItem(STORE, JSON.stringify(tabs));
    } catch {
      /* 저장 못 해도 화면은 그대로 */
    }
  }, [tabs]);

  useEffect(() => {
    api.runs(1, 100).then((p) => setRuns(p.items)).catch(() => setRuns([]));
  }, []);
  useEffect(() => setLivePrice(null), [tab?.ticker]);

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
        <h1>실시간 차트</h1>
        <form className="live-add" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <InstrumentSearch value={pick} onChange={setPick} />
          <button type="submit" disabled={!pick.ticker.trim()}>추가</button>
        </form>
      </div>
      <div className="live-tabs" role="tablist" aria-label="관심 종목">
        {tabs.map((t, i) => (
          <span key={t.ticker} className={`live-tab ${t === tab ? "live-tab-on" : ""}`}>
            <button type="button" role="tab" aria-selected={t === tab} onClick={() => setActive(i)}>
              {t.name ? `${t.name} ` : ""}<span className="muted small">{t.ticker}</span>
            </button>
            <button type="button" className="link live-tab-x" aria-label={`${t.ticker} 탭 닫기`} onClick={() => remove(i)}>×</button>
          </span>
        ))}
      </div>
      {tab && (
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
            {livePrice != null && <> · 현재가 <strong>{price(livePrice)}</strong></>}
          </div>
          <LiveChart ticker={tab.ticker} plan={plan} onPrice={setLivePrice} />
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
