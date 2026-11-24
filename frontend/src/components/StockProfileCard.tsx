import { useEffect, useState } from "react";
import { api, type Candles } from "../api";
import { price } from "../format";
import type { StockProfile } from "../types";
import StockLevelSummary from "./StockLevelSummary";
import StockSocialPanel from "./StockSocialPanel";
import "./StockProfileAnalysis.css";

const RSI_KO = { overbought: "과매수", neutral: "중립", oversold: "과매도" } as const;

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function toneOf(v: number | null | undefined): string {
  return v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "";
}

function Row({ label, value, vs }: { label: string; value: string; vs?: number | null }) {
  return (
    <div className="sp-row">
      <span className="muted small">{label}</span>
      <span className="num">{value}</span>
      <span className={`num small ${toneOf(vs)}`}>{vs === undefined ? "" : pct(vs)}</span>
    </div>
  );
}

function RecentTrend({ ticker }: { ticker: string }) {
  const [state, setState] = useState<Candles | "failed" | null>(null);
  useEffect(() => {
    let active = true;
    api.candles(ticker, "1d", 30).then(
      (value) => { if (active) setState(value); },
      () => { if (active) setState("failed"); },
    );
    return () => { active = false; };
  }, [ticker]);
  if (state == null) return <p className="muted small" role="status">일봉 추이를 불러오는 중…</p>;
  if (state === "failed") return <p className="muted small">일봉 추이를 불러오지 못했습니다.</p>;
  const bars = state.bars.filter((bar) => Number.isFinite(bar.close) && bar.close > 0).slice(-30);
  if (bars.length < 2) return <p className="muted small">일봉 추이 미제공 · 자료 부족</p>;
  const values = bars.map((bar) => bar.close);
  const low = Math.min(...values);
  const range = Math.max(...values) - low;
  const change = (values[values.length - 1] / values[0] - 1) * 100;
  return (
    <figure className="stock-profile-trend">
      <figcaption className="small">
        최근 {bars.length}개 일봉 추이 · {price(values[0])} → {price(values[values.length - 1])} ({pct(change)})
        {state.session_elapsed != null && " · 진행 일봉 포함"}
      </figcaption>
      <div className="stock-profile-trend-bars" aria-hidden="true">
        {bars.map((bar, index) => (
          <span key={bar.at} style={{ height: `${range ? 10 + 90 * (values[index] - low) / range : 50}%` }} />
        ))}
      </div>
    </figure>
  );
}

/** #296 종목 정보 카드: 기본 정보 · 최근 10거래일 · 지표(지금가 대비 %) · 지지/저항 단계 · 공매도. */
type ProfileProps = { ticker: string; name?: string | null; onClose?: () => void };

function StockProfileForTicker({ ticker, name, onClose }: ProfileProps) {
  const [card, setCard] = useState<StockProfile | null>(null);
  const [failed, setFailed] = useState(false);
  const [viewedAnalysis, setViewedAnalysis] = useState(false);
  useEffect(() => {
    let alive = true;
    setCard(null);
    setFailed(false);
    api.stockProfile(ticker).then(
      (c) => alive && setCard(c),
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, [ticker]);

  const ind = card?.indicators;
  const f = card?.facts;
  const label = f?.name ?? name ?? ticker;
  return (
    <section className="card sp-card stock-profile-summary" aria-label={`${label} 종목 정보`}>
      <header className="sp-head">
        <strong>{label}</strong>
        {(label !== ticker || f?.exchange) && <span className="muted small">{ticker}{f?.exchange ? ` · ${f.exchange}` : ""}</span>}
        {onClose && <button type="button" className="link small sp-close" onClick={onClose} aria-label="종목 정보 닫기">닫기</button>}
      </header>
      <StockLevelSummary ticker={ticker} />
      <a className="link small stock-profile-analysis-link" href={`#/?ticker=${encodeURIComponent(ticker)}`}>종목 분석 열기</a>
      <details className="stock-profile-analysis" onToggle={(event) => {
        if (event.currentTarget.open) setViewedAnalysis(true);
      }}>
        <summary>최근 거래일·지표·공매도</summary>
        {viewedAnalysis && <RecentTrend ticker={ticker} />}
        {failed && <p className="muted small">종목 상세 정보를 불러오지 못했습니다.</p>}
        {!failed && !card && <p className="muted small" role="status">종목 상세 정보 불러오는 중…</p>}
        {card && <>
          <div className="sp-grid">
            <div>
              <h4 className="small">기본 정보</h4>
              {f?.market_cap != null && <Row label="시가총액" value={`${(f.market_cap / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}억${f.currency ? ` ${f.currency}` : ""}`} />}
              {f?.first_trade_date && <Row label="상장일" value={f.first_trade_date} />}
              <h4 className="small">최근 10거래일</h4>
              <table className="sp-days small">
                <tbody>
                  {(card.recent?.days ?? []).map((d) => (
                    <tr key={d.date} className={d.highest_close ? "sp-hi" : d.lowest_close ? "sp-lo" : ""}>
                      <td className="muted">{d.date.slice(5)}</td>
                      <td className="num">{price(d.close)}{d.highest_close ? " ▲고점" : d.lowest_close ? " ▼저점" : ""}</td>
                      <td className={`num ${toneOf(d.change_pct)}`}>{pct(d.change_pct)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h4 className="small">종목 분석 <span className="muted">(지금가 대비)</span></h4>
              {ind?.week52 && <Row label={`52주 최고${ind.week52.full === false ? "(기록 짧음)" : ""}`} value={price(ind.week52.high)} vs={ind.week52.from_high_pct} />}
              {ind?.week52 && <Row label="52주 최저" value={price(ind.week52.low)} vs={ind.week52.from_low_pct} />}
              {ind?.bollinger && <Row label="볼린저 상단" value={price(ind.bollinger.upper)} vs={ind.bollinger.to_upper_pct} />}
              {ind?.bollinger && <Row label="볼린저 하단" value={price(ind.bollinger.lower)} vs={ind.bollinger.to_lower_pct} />}
              {(ind?.moving_averages ?? []).filter((m) => m.value != null).map((m) => (
                <Row key={m.days} label={`${m.days}일 이동평균`} value={price(m.value)} vs={m.vs_pct} />
              ))}
              {ind?.rsi && <Row label="RSI(14)" value={`${ind.rsi.value.toFixed(1)} · ${RSI_KO[ind.rsi.zone]}`} />}
              {card.short?.available ? (
                <Row label={`공매도 잔고${card.short.as_of ? `(${card.short.as_of})` : ""}`}
                  value={card.short.percent_of_float != null ? `유통 주식의 ${card.short.percent_of_float}%` : "—"} />
              ) : card.short?.reason ? <p className="muted small">공매도: {card.short.reason}</p> : null}
            </div>
          </div>
          {card.errors && Object.keys(card.errors).length > 0 && <p className="muted small">상세 정보 일부 미제공</p>}
        </>}
      </details>
      <StockSocialPanel ticker={ticker} load={api.stockSocial} />
    </section>
  );
}

export default function StockProfileCard(props: ProfileProps) {
  return <StockProfileForTicker key={props.ticker} {...props} />;
}
