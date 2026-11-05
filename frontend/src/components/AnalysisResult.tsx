import { lazy, Suspense, useMemo } from "react";
import { price } from "../format";
import type { Plan } from "../types";
import type { ExtraLine } from "./PriceChart";
import SectionBoundary from "./SectionBoundary";

const PriceChart = lazy(() => import("./PriceChart"));

// backend desk/related.py (#66) — the brief the agents read, kept in the plan.
interface RelatedRow {
  ticker: string;
  role: "self" | "underlying" | "peer" | "driver";
  price: number;
  as_of: string;
  chg_1d: number | null;
  chg_5d: number | null;
  chg_20d: number | null;
  vs_sma20: number | null;
  vs_sma50: number | null;
  corr_60d: number | null;
}
interface Related {
  ticker: string;
  underlying: string | null;
  leverage: number | null;
  proxy?: boolean;
  note?: string | null;
  self?: RelatedRow | null;
  instruments: RelatedRow[];
  tracking?: { beta_20d: number | null; beta_60d: number | null };
  news?: { title: string; source: string | null; at: string; ticker: string }[];
}
// #64 verdict fields: a hold plan says where it would enter either way.
type Triggers = { long_trigger?: number | null; short_trigger?: number | null };

const ROLE: Record<RelatedRow["role"], string> = { self: "이 종목", underlying: "본주", peer: "관련 종목", driver: "동인" };

/** 관망(또는 어느 계획이든) 롱 · 숏 진입 조건 가격을 점선으로. */
function triggerLines(plan: Plan): ExtraLine[] {
  const t = plan as Plan & Triggers;
  const lines: ExtraLine[] = [];
  if (t.long_trigger != null) lines.push({ price: t.long_trigger, label: "롱 조건", color: "#2ea043", dashed: true });
  if (t.short_trigger != null) lines.push({ price: t.short_trigger, label: "숏 조건", color: "#e5534b", dashed: true });
  return lines;
}

function Chart({ ticker, tradeDate, plan, title }: { ticker: string; tradeDate: string; plan: Plan; title: string }) {
  const extra = useMemo(() => triggerLines(plan), [plan]);
  return (
    <SectionBoundary fallback={<section className="card price-chart"><p className="muted small">{title} 차트를 불러오지 못했습니다.</p></section>}>
      <Suspense fallback={<section className="card price-chart"><p className="muted small">{title} 차트 불러오는 중…</p></section>}>
        <PriceChart ticker={ticker} tradeDate={tradeDate} plan={plan} title={title} extraLines={extra} />
      </Suspense>
    </SectionBoundary>
  );
}

function signed(v: number | null): string {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 본주 · 관련 종목 · 동인(금 선물 · 지수 · 금리)의 추이 — 판단에 쓴 숫자 그대로. */
function RelatedTable({ related }: { related: Related }) {
  const rows = [related.self, ...related.instruments].filter((r): r is RelatedRow => !!r);
  const beta = related.tracking;
  return (
    <section className="card related-card">
      <h3>연관 종목 · 동인 추이</h3>
      {related.underlying && (
        <p className="muted small">
          {related.ticker} 는 {related.underlying} 의 {related.leverage! > 0 ? "+" : ""}
          {related.leverage}배 상품{related.proxy ? "(자체 지수 — 본주는 근사치)" : ""}
          {beta && ` · 실측 배수 20일 ${beta.beta_20d ?? "—"} / 60일 ${beta.beta_60d ?? "—"}`}
        </p>
      )}
      <div className="table-scroll">
        <table className="related-table">
          <thead>
            <tr>
              <th>구분</th>
              <th>종목</th>
              <th className="num">가격</th>
              <th className="num">1일</th>
              <th className="num">5일</th>
              <th className="num">20일</th>
              <th className="num">20일선 대비</th>
              <th className="num">상관(60일)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.role}-${r.ticker}`}>
                <td className="muted small">{ROLE[r.role]}</td>
                <td>{r.ticker}</td>
                <td className="num">{price(r.price)}</td>
                <td className={`num ${(r.chg_1d ?? 0) >= 0 ? "up" : "down"}`}>{signed(r.chg_1d)}</td>
                <td className={`num ${(r.chg_5d ?? 0) >= 0 ? "up" : "down"}`}>{signed(r.chg_5d)}</td>
                <td className={`num ${(r.chg_20d ?? 0) >= 0 ? "up" : "down"}`}>{signed(r.chg_20d)}</td>
                <td className="num">{signed(r.vs_sma20)}</td>
                <td className="num">{r.corr_60d ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!!related.news?.length && (
        <ul className="related-news">
          {related.news.slice(0, 6).map((n) => (
            <li key={n.title}>
              <span className="muted small">{n.at.slice(5, 16).replace("T", " ")} · {n.ticker}</span> {n.title}
              {n.source && <span className="muted small"> ({n.source})</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** 분석이 끝난 뒤의 차트(#68): 레버리지 상품이면 본주 · 상품 두 차트, 아니면 하나. 처음 차트는 그대로 둔다. */
export default function AnalysisResult({ plan, ticker, tradeDate }: { plan: Plan; ticker: string; tradeDate: string }) {
  const u = plan.underlying;
  const related = (plan as Plan & { related?: Related }).related;
  return (
    <div className="analysis-result">
      <h2 className="analysis-result-title">분석 결과 차트</h2>
      {u ? (
        <div className="result-charts result-charts-two">
          <Chart ticker={u.ticker} tradeDate={tradeDate} plan={u} title={`${u.ticker} · 본주 기준`} />
          <Chart
            ticker={ticker}
            tradeDate={tradeDate}
            plan={plan}
            title={`${ticker} · ${u.leverage > 0 ? "+" : ""}${u.leverage}배 상품`}
          />
        </div>
      ) : (
        <div className="result-charts">
          <Chart ticker={ticker} tradeDate={tradeDate} plan={plan} title={`${ticker} · 분석 결과`} />
        </div>
      )}
      {related && related.instruments.length > 0 && <RelatedTable related={related} />}
      {u && (
        <p className="muted small">
          본주 차트의 레벨은 본주 분석 결과, 상품 차트의 레벨은 레버리지 분석가의 타점입니다. 레버리지 상품은 매일 배수를 다시 맞추므로 며칠 보유하면 본주 대비 움직임이 배수와 어긋납니다.
        </p>
      )}
    </div>
  );
}
