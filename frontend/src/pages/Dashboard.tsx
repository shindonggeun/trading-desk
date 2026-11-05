import { useEffect, useState } from "react";
import { api } from "../api";
import MarketFlowPanel from "../components/MarketFlowPanel";
import NewRunForm from "../components/NewRunForm";
import PortfolioPanel from "../components/PortfolioPanel";
import RunsCard from "../components/RunsCard";
import { time } from "../format";
import type { RegimeSnapshot } from "../types";

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
        <MarketFlowPanel />
        <PortfolioPanel />
        <RegimePanel />
      </div>
    </main>
  );
}
