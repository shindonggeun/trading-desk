import { lazy, Suspense, useMemo, useState } from "react";
import { price, time } from "../format";
import type { Plan, RunOutlook } from "../types";
import type { ExtraLine, Interval } from "./PriceChart";
import LevelLadder from "./LevelLadder";
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
export interface Related {
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

const ROLE: Record<RelatedRow["role"], string> = { self: "이 종목", underlying: "본주", peer: "관련 종목", driver: "동인" };

/** 관망(또는 어느 계획이든) 롱 · 숏 진입 조건 가격을 점선으로. */
function triggerLines(plan: Plan | null): ExtraLine[] {
  if (!plan) return [];
  const lines: ExtraLine[] = [];
  const css = (name: string, fallback: string) =>
    getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  if (plan.long_trigger != null) lines.push({ price: plan.long_trigger, label: "롱 조건", color: css("--long", "#f2464b"), dashed: true });
  if (plan.short_trigger != null) lines.push({ price: plan.short_trigger, label: "숏 조건", color: css("--short", "#3a86f8"), dashed: true });
  return lines;
}

// 계획의 시간축(#63)에 맞는 봉: 스캘핑 · 단타는 5분봉, 2~3일은 1시간봉, 스윙은 일봉.
const HORIZON_INTERVAL: Record<string, Interval> = { scalp: "5m", intraday: "5m", short_swing: "60m", swing: "1d" };

function Chart({ ticker, tradeDate, plan, title, outlook = null }: { ticker: string; tradeDate: string; plan: Plan | null; title: string; outlook?: RunOutlook | null }) {
  const extra = useMemo(() => triggerLines(plan), [plan]);
  const [picked, setPicked] = useState<number | null>(null);   // #440 사다리에서 고른 가격
  return (
    <div className="chart-with-ladder">
      <SectionBoundary fallback={<section className="card price-chart"><p className="muted small">{title} 차트를 불러오지 못했습니다.</p></section>}>
        <Suspense fallback={<section className="card price-chart"><p className="muted small">{title} 차트 불러오는 중…</p></section>}>
          <PriceChart ticker={ticker} tradeDate={tradeDate} plan={plan} title={title} extraLines={extra} lists={false}
            initialInterval={plan ? HORIZON_INTERVAL[plan.horizon ?? "swing"] ?? "1d" : undefined} highlight={picked} outlook={outlook} />
        </Suspense>
      </SectionBoundary>
      {plan && <LevelLadder ticker={ticker} plan={plan} onPick={setPicked} />}
    </div>
  );
}

/**
 * 분석 화면 차트 탭(#99): 차트 하나. 레버리지 상품이면 본주 · 상품을 전환한다(처음은 입력한 상품).
 * 분석 중(계획 없음)에는 레벨 없이 시세만.
 */
export function ResultChart({ plan, ticker, tradeDate, outlook = null }: { plan: Plan | null; ticker: string; tradeDate: string; outlook?: RunOutlook | null }) {
  const u = plan?.underlying;
  const [side, setSide] = useState<"product" | "underlying">("product");
  if (!u) return <Chart ticker={ticker} tradeDate={tradeDate} plan={plan} title={plan ? `${ticker} · 분석 결과` : `${ticker} · 가격`} outlook={outlook} />;
  const lev = `${u.leverage > 0 ? "+" : ""}${u.leverage}배`;
  return (
    <div className="result-chart">
      <div className="chips result-chart-switch" role="group" aria-label="차트 종목">
        <button type="button" className={`chip ${side === "product" ? "chip-on" : ""}`} aria-pressed={side === "product"}
          onClick={() => setSide("product")}>{ticker} · {lev} 상품</button>
        <button type="button" className={`chip ${side === "underlying" ? "chip-on" : ""}`} aria-pressed={side === "underlying"}
          onClick={() => setSide("underlying")}>{u.ticker} · 본주</button>
      </div>
      {side === "product" ? (
        // #438 전망은 분석한 종목(상품) 가격 기준 — 본주 차트에는 그리지 않는다
        <Chart key="product" ticker={ticker} tradeDate={tradeDate} plan={plan} title={`${ticker} · ${lev} 상품`} outlook={outlook} />
      ) : (
        <Chart key="underlying" ticker={u.ticker} tradeDate={tradeDate} plan={u} title={`${u.ticker} · 본주 기준`} />
      )}
      <p className="muted small">
        상품 차트의 레벨은 레버리지 분석가의 타점, 본주 차트의 레벨은 본주 분석 결과입니다. 레버리지 상품은 매일 배수를 다시 맞추므로 며칠 보유하면 본주 대비 움직임이 배수와 어긋납니다.
      </p>
    </div>
  );
}

function signed(v: number | null): string {
  return v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 본주 · 관련 종목 · 동인(금 선물 · 지수 · 금리)의 추이 — 판단에 쓴 숫자 그대로. */
export function RelatedTable({ related }: { related: Related }) {
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
              <th scope="col">구분</th>
              <th scope="col">종목</th>
              <th scope="col" className="num">가격</th>
              <th scope="col" className="num">1일</th>
              <th scope="col" className="num">5일</th>
              <th scope="col" className="num">20일</th>
              <th scope="col" className="num">20일선 대비</th>
              <th scope="col" className="num">상관(60일)</th>
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
              <span className="muted small">{time(n.at)} · {n.ticker}</span> {n.title}
              {n.source && <span className="muted small"> ({n.source})</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
