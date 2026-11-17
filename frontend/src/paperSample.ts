import type { PaperEquity, PaperSettings, PaperSummary, PaperTrade, PaperTrades } from "./types";

/**
 * #528 개발 모드에서만 — 모의 투자 API(#527)가 아직 없을 때(404) #527 계약 모양의 예시로 화면을 그린다.
 * 운영 빌드에서는 쓰지 않는다(Paper.tsx 가 import.meta.env.DEV 일 때만 부른다).
 */
const START = 10_000_000;

function trades(): PaperTrade[] {
  const names: [string, string | null, "KR" | "US"][] = [
    ["SOXL", "Direxion Semiconductor Bull 3X", "US"], ["NVDA", "NVIDIA", "US"], ["005930.KS", "삼성전자", "KR"],
    ["TSLA", "Tesla", "US"], ["000660.KS", "SK하이닉스", "KR"], ["QQQ", "Invesco QQQ", "US"],
  ];
  const out: PaperTrade[] = [];
  const statuses: PaperTrade["status"][] = ["closed", "closed", "closed", "open", "closed", "skipped", "closed", "waiting"];
  for (let i = 0; i < 24; i++) {
    const [ticker, name, market] = names[i % names.length];
    const status = statuses[i % statuses.length];
    const direction = i % 5 === 3 ? "short" : "long";
    const day = new Date(Date.UTC(2026, 7, 3 + i * 4, 13, 40));
    const entry = market === "KR" ? 70_000 + i * 900 : 30 + i * 2.5;
    const win = i % 3 !== 1;
    const r = status === "closed" ? (win ? 1.6 + (i % 4) * 0.3 : -1) : status === "open" ? 0.4 : null;
    const qty = status === "skipped" || status === "waiting" ? null : market === "KR" ? 12 : 40;
    const fx = market === "US" && qty ? { entry: 1380 + i, exit: status === "closed" ? 1385 + i : null } : null;
    const amount = qty ? Math.round(qty * entry * (fx?.entry ?? 1)) : null;
    const pnl = amount && r !== null ? Math.round(amount * 0.04 * r) : null;
    out.push({
      run_id: `sample${i}`, ticker, name, market, currency: market === "KR" ? "KRW" : "USD", direction,
      rating: win ? "Buy" : "Overweight", horizon: "swing", plan_at: day.toISOString(), status,
      exit_reason: status === "closed" ? (win ? "target" : i % 2 ? "stop" : "expired") : null,
      skip: status === "skipped" ? { code: "max_positions", text: "동시 보유 한도(5개)라 건너뜀" } : null,
      plan: { entry, stop: entry * 0.95, target: entry * 1.08 },
      entry: qty ? { at: new Date(day.getTime() + 3600e3).toISOString(), price: entry, fill: entry * 1.0005 } : null,
      exit: status === "closed" ? { at: new Date(day.getTime() + 6 * 86400e3).toISOString(), price: entry * (win ? 1.08 : 0.95),
        fill: entry * (win ? 1.0795 : 0.9495) } : null,
      qty, fx, amount_krw: amount, costs_krw: amount ? Math.round(amount * 0.002) : null,
      pnl_krw: pnl, pnl_pct: pnl !== null && amount ? (pnl / amount) * 100 : null, r, marked: status === "open",
    });
  }
  return out.reverse();
}

export function sampleSettings(): PaperSettings {
  return { start_capital: START, start_date: null, sizing: "risk", risk_pct: 1, weight_pct: 10, max_positions: 5,
    fee_kr_pct: 0.015, fee_us_pct: 0.1, tax_kr_pct: 0.18, slippage_pct: 0.05, allow_short: true, version: 1, updated_at: null };
}

export function sampleTrades(status = ""): PaperTrades {
  const all = trades();
  const count = (s: PaperTrade["status"]) => all.filter((t) => t.status === s).length;
  return { settings_version: 1, counts: { closed: count("closed"), open: count("open"), waiting: count("waiting"), skipped: count("skipped") },
    items: status ? all.filter((t) => t.status === status) : all };
}

export function sampleEquity(): PaperEquity {
  const points: PaperEquity["points"] = [];
  let bal = START;
  for (let d = 0; d < 105; d++) {
    bal += Math.round(Math.sin(d / 6) * 60_000 + 9_000);
    points.push({ date: new Date(Date.UTC(2026, 7, 3 + d)).toISOString().slice(0, 10), balance: bal });
  }
  const weekly = Array.from({ length: 15 }, (_, i) => ({
    start: new Date(Date.UTC(2026, 7, 3 + i * 7)).toISOString().slice(0, 10), pnl: Math.round(Math.sin(i) * 180_000 + 40_000) }));
  const monthly = ["2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01"].map((start, i) => ({ start, pnl: [210_000, -95_000, 320_000, 120_000][i] }));
  return { settings_version: 1, start_capital: START, points, weekly, monthly };
}

export function sampleSummary(): PaperSummary {
  const eq = sampleEquity();
  const balance = eq.points[eq.points.length - 1].balance;
  const t = sampleTrades();
  return {
    as_of: new Date().toISOString(), scored_at: new Date().toISOString(), settings_version: 1, start_capital: START,
    balance, cash: balance - 2_300_000, invested: 2_300_000, realized_pnl: balance - START - 113_000, unrealized_pnl: 113_000,
    total_pnl: balance - START, total_pnl_pct: ((balance - START) / START) * 100, trades: t.counts, wins: 8, losses: 4,
    win_rate: 8 / 12, avg_r: 0.62, payoff: 1.6,
    max_drawdown: { krw: -380_000, pct: -3.6, peak_at: "2026-09-02", trough_at: "2026-09-19" },
    this_month: { start: "2026-11-01", pnl: 120_000, pct: 1.15 },
    by_ticker: [{ key: "SOXL", trades: 4, wins: 3, pnl: 210_000, avg_r: 0.9 }, { key: "삼성전자", trades: 4, wins: 2, pnl: -40_000, avg_r: 0.1 }],
    by_direction: [{ key: "long", trades: 10, wins: 7, pnl: 400_000, avg_r: 0.8 }, { key: "short", trades: 2, wins: 1, pnl: -20_000, avg_r: 0.3 }],
    by_rating: [{ key: "Buy", trades: 8, wins: 6, pnl: 330_000, avg_r: 0.9 }],
  };
}
