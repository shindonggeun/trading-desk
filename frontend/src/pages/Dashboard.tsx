import { useEffect, useState } from "react";
import { api } from "../api";
import NewRunForm from "../components/NewRunForm";
import { BiasBadge, RatingText } from "../components/PlanCard";
import { price, STATUS_LABEL, time } from "../format";
import type { Holding, RegimeSnapshot, Run } from "../types";

function RunsTable({ runs }: { runs: Run[] }) {
  if (runs.length === 0) return <p className="muted">아직 분석이 없습니다. 왼쪽에서 첫 분석을 시작하세요.</p>;
  return (
    <div className="table-wrap">
      <table className="runs">
        <thead>
          <tr>
            <th>종목</th>
            <th>기준일</th>
            <th>판단</th>
            <th className="num">진입</th>
            <th className="num">손절</th>
            <th className="num">목표</th>
            <th className="num">손익비</th>
            <th>상태</th>
            <th>요청</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} onClick={() => (window.location.hash = `#/runs/${r.id}`)}>
              <td className="ticker">
                {r.options.name ? <>{r.options.name} <span className="muted small">{r.ticker}</span></> : r.ticker}
              </td>
              <td>{r.trade_date}</td>
              <td>{r.plan ? <><RatingText rating={r.plan.rating} /> <BiasBadge plan={r.plan} /></> : "—"}</td>
              <td className="num">{price(r.plan?.entry_price)}</td>
              <td className="num">{price(r.plan?.stop_loss)}</td>
              <td className="num">{price(r.plan?.price_target)}</td>
              <td className="num">{r.plan?.reward_risk ?? "—"}</td>
              <td><span className={`status status-${r.status}`}>{STATUS_LABEL[r.status]}</span></td>
              <td className="muted">{time(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

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
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    const load = () => api.runs().then(setRuns).catch(() => {});
    load();
    const timer = setInterval(load, 5000); // running rows fill in on their own
    return () => clearInterval(timer);
  }, []);

  return (
    <main className="dashboard">
      <NewRunForm />
      <section className="card runs-card">
        <h2>분석 기록</h2>
        <RunsTable runs={runs} />
      </section>
      <div className="side">
        <HoldingsPanel />
        <RegimePanel />
      </div>
    </main>
  );
}
