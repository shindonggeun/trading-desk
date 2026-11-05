import { useEffect, useState } from "react";
import { api } from "../api";
import NewRunForm from "../components/NewRunForm";
import RunsCard from "../components/RunsCard";
import { price, time } from "../format";
import type { Holding, RegimeSnapshot } from "../types";

function HoldingsPanel() {
  const [data, setData] = useState<{ broker: string; error?: string; holdings: Holding[] } | null>(null);
  useEffect(() => {
    api.portfolio().then(setData).catch((e) => setData({ broker: "?", error: String(e), holdings: [] }));
  }, []);
  return (
    <section className="card">
      <h3>보유 종목 <span className="muted small">{data?.broker}</span></h3>
      {data?.error && <p className="error small">{data.error}</p>}
      {data && data.holdings.length === 0 && !data.error && <p className="muted">보유 종목 없음</p>}
      <ul className="holdings">
        {data?.holdings.map((h) => (
          <li key={h.symbol}>
            <div>
              <strong>{h.name}</strong>
              <div className="muted small">{h.yahoo_ticker} · {h.quantity}주 · 평단 {price(h.average_price)}</div>
            </div>
            <div className="num">
              <div>{price(h.last_price)} {h.currency}</div>
              {h.profit_rate !== null && (
                <div className={h.profit_rate >= 0 ? "up small" : "down small"}>
                  {h.profit_rate >= 0 ? "+" : ""}{h.profit_rate}%
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function RegimePanel() {
  const [data, setData] = useState<RegimeSnapshot | null>(null);
  useEffect(() => {
    api.regime().then(setData).catch(() => setData(null));
  }, []);
  if (!data?.enabled) return null;
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

export default function Dashboard() {
  return (
    <main className="dashboard">
      <NewRunForm />
      <RunsCard />
      <div className="side">
        <HoldingsPanel />
        <RegimePanel />
      </div>
    </main>
  );
}
