import { useEffect, useRef, useState } from "react";
import { followPortfolio, type HoldingLine, type StreamState } from "../api";
import { HoldingBadge, useHoldingLines } from "../holdingWatch";
import { type Currency, money, percent, price, time, tone } from "../format";
import type { Money, Portfolio, PortfolioRow } from "../types";

// "내 투자" like the Toss app: total value and P/L in KRW or USD (US holdings converted at the
// live USD/KRW rate), refreshed from the SSE stream. Numbers stay put when the stream or a
// quote fails - the server keeps the last values and says why in ``error``.

const GROUPS: Record<string, string> = { US: "해외주식", KR: "국내주식" };

function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key);
    return allowed.includes(v as T) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: the choice just isn't kept */
  }
}

function pick(m: Money, currency: Currency): number | null {
  return currency === "KRW" ? m.krw : m.usd;
}

/** Blinks briefly when ``value`` changes (not on first render). */
function Flash({ value, className = "", children }: { value: unknown; className?: string; children: React.ReactNode }) {
  // Compare with the last value seen (not "skip the first effect"): StrictMode runs effects twice.
  const last = useRef(value);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (Object.is(last.current, value)) return;
    last.current = value;
    setTick((t) => t + 1);
  }, [value]);
  return (
    <span key={tick} className={`${className} ${tick ? "flash" : ""}`.trim()}>
      {children}
    </span>
  );
}

function Row({ row, currency, view, line }: { row: PortfolioRow; currency: Currency; view: "price" | "value"; line?: HoldingLine }) {
  const pnl = pick(row.pnl, currency);
  const native = row.currency === "USD" ? "USD" : "KRW";
  return (
    <li>
      <div className="pf-name">
        <strong>{row.name}</strong>
        <div className="muted small">
          {row.yahoo_ticker} · {row.quantity.toLocaleString()}주 · 평단 {money(row.average_price, native)}
        </div>
        {line && <HoldingBadge line={line} />}
      </div>
      <div className="num">
        {/* key: a toggle remounts the cell, so only data changes blink */}
        {view === "price" ? (
          <Flash key="price" value={row.last_price}>{money(row.last_price, native)}</Flash>
        ) : (
          <Flash key={`value-${currency}`} value={pick(row.value, currency)}>{money(pick(row.value, currency), currency)}</Flash>
        )}
        <div className={`small ${tone(pnl ?? row.pnl_rate)}`}>
          <Flash key={currency} value={pnl}>
            {money(pnl, currency, true)} ({percent(row.pnl_rate)})
          </Flash>
        </div>
      </div>
    </li>
  );
}

export default function PortfolioPanel() {
  const [data, setData] = useState<Portfolio | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // #775 계좌는 주인 계정 것 — 다른 계정은 스트림이 403 으로 끝나고, 접근 확인의 403 이 패널을 비운 뒤 다시 잇지 않는다
  // (followPortfolio 의 403 갈래가 retry 를 부르지 않는 것이 재연결 고리를 막는다)
  const [notOwner, setNotOwner] = useState(false);
  const [stream, setStream] = useState<StreamState | "connecting">("connecting");
  const [currency, setCurrency] = useState<Currency>(() => remembered("pf.currency", ["KRW", "USD"] as const, "KRW"));
  const [view, setView] = useState<"price" | "value">(() => remembered("pf.view", ["price", "value"] as const, "value"));

  useEffect(() => {
    // #705 the stream's first message is the snapshot — no REST read before it (the server built it twice).
    // #775 not the owner: the stream answers 403, the access check clears the panel and nothing reconnects.
    const stop = followPortfolio(
      (p) => {
        setData(p);
        setFailure(null);
      },
      setStream,
      () => {
        setData(null);
        setFailure(null);
        setNotOwner(true);
      },
      (e) => setFailure(String(e)),
    );
    return stop;
  }, []);

  const choose = (c: Currency) => {
    setCurrency(c);
    remember("pf.currency", c);
  };
  const chooseView = (v: "price" | "value") => {
    setView(v);
    remember("pf.view", v);
  };

  const total = data?.total;
  const totalValue = total ? (currency === "KRW" ? total.krw : total.usd) : null;
  const totalPnl = total ? (currency === "KRW" ? total.pnl_krw : total.pnl_usd) : null;
  const groups = data ? Object.entries(data.groups) : [];
  const lines = useHoldingLines();       // #714 보유 종목 알림(#694)이 켜져 있으면 장세 · 손절선까지 %

  return (
    <section className="card pf">
      <div className="pf-head">
        <h3>
          내 투자 <span className="muted small">{data?.broker}</span>
        </h3>
        <div className="seg" role="group" aria-label="표시 통화">
          <button className={currency === "USD" ? "on" : ""} onClick={() => choose("USD")}>$</button>
          <button className={currency === "KRW" ? "on" : ""} onClick={() => choose("KRW")}>원</button>
        </div>
      </div>

      {data && (
        <div className="pf-total">
          <Flash key={`total-${currency}`} value={totalValue} className="pf-amount">{money(totalValue, currency)}</Flash>
          <div className={tone(totalPnl ?? total?.pnl_rate)}>
            <Flash key={`pnl-${currency}`} value={totalPnl}>
              {money(totalPnl, currency, true)} ({percent(total?.pnl_rate)})
            </Flash>
          </div>
          <div className="muted small">
            {data.fx ? `환율 ${price(data.fx.usd_krw)}원 (${data.fx.source})` : "환율 없음"} · {time(data.updated_at)} 갱신
            {stream === "lost" && " · 재접속 중"}
          </div>
        </div>
      )}

      {notOwner && <p className="muted">계좌(보유 · 체결)는 주인 계정에서만 보여요.</p>}
      {failure && <p className="error small">{failure}</p>}
      {data?.error && <p className="error small">{data.error}</p>}
      {data && data.holdings.length === 0 && !data.error && <p className="muted">보유 종목 없음</p>}

      {data && data.holdings.length > 0 && (
        <>
          <div className="pf-view">
            <div className="seg small" role="group" aria-label="오른쪽 숫자">
              <button className={view === "price" ? "on" : ""} onClick={() => chooseView("price")}>현재가</button>
              <button className={view === "value" ? "on" : ""} onClick={() => chooseView("value")}>평가금</button>
            </div>
          </div>
          {groups.map(([market, rows]) => (
            <div key={market} className="pf-group">
              <div className="muted small pf-group-title">{GROUPS[market] ?? market}</div>
              <ul className="holdings">
                {rows.map((row) => (
                  <Row key={row.symbol} row={row} currency={currency} view={view} line={lines.get(row.yahoo_ticker)} />
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </section>
  );
}
