import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { ProfileStep, StockProfile } from "../types";
import StockSocialPanel from "./StockSocialPanel";

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

function Steps({ title, steps, ticker }: { title: string; steps: ProfileStep[]; ticker: string }) {
  if (!steps.length) return null;
  return (
    <div className="sp-steps">
      <h4 className="small">{title}</h4>
      {steps.map((s) => (
        // 누르면 시세판 봉 차트로 그 가격을 넘겨 강조선을 긋는다(#296)
        <a key={`${s.step}-${s.price}`} className="sp-row sp-step"
          href={`#/live?t=${encodeURIComponent(ticker)}&p=${s.price}&l=${encodeURIComponent(`${title.replace("선", "")} ${s.step}단계`)}`}
          title={s.source === "volume" ? "매물대(거래량 두꺼운 가격대)" : "차트 지지 · 저항선"}>
          <span className="muted small">{s.step}단계{s.source === "volume" ? " · 매물대" : ""}</span>
          <span className="num">{price(s.price)}</span>
          <span className={`num small ${toneOf(s.distance_pct)}`}>{pct(s.distance_pct)}</span>
        </a>
      ))}
    </div>
  );
}

/** #296 종목 정보 카드: 기본 정보 · 최근 10거래일 · 지표(지금가 대비 %) · 지지/저항 단계 · 공매도. */
export default function StockProfileCard({ ticker, onClose }: { ticker: string; onClose?: () => void }) {
  const [card, setCard] = useState<StockProfile | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setCard(null);
    setFailed(null);
    api.stockProfile(ticker).then(
      (c) => alive && setCard(c),
      (e: unknown) => alive && setFailed(e instanceof Error ? e.message : "불러오지 못했습니다"),
    );
    return () => {
      alive = false;
    };
  }, [ticker]);

  const head = (
    <header className="sp-head">
      <strong>{card?.facts?.name ?? ticker}</strong>
      <span className="muted small"> {ticker}{card?.facts?.exchange ? ` · ${card.facts.exchange}` : ""}</span>
      {onClose && <button type="button" className="link small sp-close" onClick={onClose} aria-label="종목 정보 닫기">닫기</button>}
    </header>
  );
  const social = <StockSocialPanel key={ticker} ticker={ticker} load={api.stockSocial} />;
  if (failed) return <section className="card sp-card">{head}<p className="muted small">종목 정보를 불러오지 못했습니다: {failed}</p>{social}</section>;
  if (!card) return <section className="card sp-card">{head}<p className="muted small">불러오는 중…</p>{social}</section>;

  const ind = card.indicators;
  const f = card.facts;
  return (
    <section className="card sp-card" aria-label={`${f?.name ?? ticker} 종목 정보`}>
      {head}
      <p className="sp-price num">
        {price(card.price)} <span className="muted small">{card.price_source === "live" ? "현재가" : "최근 종가"}</span>
      </p>
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
      {card.levels && (
        <div className="sp-grid">
          <Steps title="저항선" steps={card.levels.resistances} ticker={ticker} />
          <Steps title="지지선" steps={card.levels.supports} ticker={ticker} />
        </div>
      )}
      <p className="muted small">지지/저항은 90거래일 거래량 기준, 참고용{card.errors && Object.keys(card.errors).length ? ` · 일부 항목 없음(${Object.keys(card.errors).join(", ")})` : ""}</p>
      {social}
    </section>
  );
}
