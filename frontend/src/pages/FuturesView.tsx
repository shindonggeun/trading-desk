import {
  CandlestickSeries, ColorType, createChart, HistogramSeries, LineStyle,
  type IChartApi, type IPriceLine, type ISeriesApi, type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { crosshairTime, ticksClearOfLabels } from "../components/chartFormat";
import { percent, price, tone } from "../format";
import { chartSec, contracts, hidden, isLive, kstStamp, LIVE_REFRESH_MS, nextRefresh, SESSION_HOURS, SESSION_KO, sessionBadge, standIn } from "../futures";
import type { FuturesCandles, FuturesInterval, FuturesQuote, FuturesSession } from "../types";
import { css, useColorScheme } from "../chartColors";

/**
 * #444 코스피200 선물 시세 전용 화면(#/futures?session=day|night&iv=1m|5m|15m).
 * 주간(08:45~15:45) / 야간(18:00~다음날 06:00) 중 한 세션씩 — 가로축은 그 세션 시각 그대로라 주간 · 야간 사이 공백이 없다.
 * 가격 캔들 + 거래량 막대, 기준가(주간 = 전일 정산가, 야간 = 주간 종가) 점선. 그 세션이 열려 있으면 15초마다 다시 읽는다.
 */
const INTERVALS: { iv: FuturesInterval; label: string }[] = [
  { iv: "1m", label: "1분" }, { iv: "5m", label: "5분" }, { iv: "15m", label: "15분" },
];
const IV_KEY = "desk.futures.iv";


function savedIv(): FuturesInterval {
  try {
    const v = localStorage.getItem(IV_KEY);
    if (v === "1m" || v === "5m" || v === "15m") return v;
  } catch { /* 기본값 */ }
  return "5m";
}

function FuturesViewInner({ query }: { query: URLSearchParams }) {
  const qs = query.get("session");
  const asked: FuturesSession | undefined = qs === "day" || qs === "night" ? qs : undefined;
  const qi = query.get("iv");
  const [iv, setIv] = useState<FuturesInterval>(qi === "1m" || qi === "5m" || qi === "15m" ? qi : savedIv);
  const [quote, setQuote] = useState<FuturesQuote | null>(null);
  const [candles, setCandles] = useState<FuturesCandles | null>(null);
  const [failed, setFailed] = useState(false);
  // 세션을 고르지 않았으면 서버가 정한 것(지금 열린 세션, 닫혔으면 마지막 세션)
  const session: FuturesSession | null = asked ?? quote?.session ?? null;

  const go = (s: FuturesSession | undefined, i: FuturesInterval) => {
    const p = new URLSearchParams();
    if (s) p.set("session", s);
    p.set("iv", i);
    window.location.replace(`#/futures?${p}`);
  };
  const pickIv = (i: FuturesInterval) => {
    setIv(i);
    try { localStorage.setItem(IV_KEY, i); } catch { /* 기억 못 해도 동작은 같다 */ }
    go(asked, i);
  };

  // 세션을 바꾸면 이전 세션 값이 새 버튼 아래 남지 않게 비운다(간격만 바꾸면 시세는 그대로)
  useEffect(() => {
    setQuote(null);
    setCandles(null);
  }, [asked]);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const later = (ms: number | null) => {
      if (alive && ms != null) timer = window.setTimeout(load, ms);
    };
    const load = async () => {
      if (hidden()) return later(LIVE_REFRESH_MS);
      try {
        const q = await api.futuresQuote(asked);
        if (!alive) return;
        setQuote(q);
        if (q.supported) {
          const c = await api.futuresCandles(q.session, iv);
          if (alive) setCandles(c);
        }
        if (alive) setFailed(false);
        // 닫혀 있어도 길게 다시 묻는다 — 켜 둔 화면이 다음 세션 시작을 놓치지 않게
        later(nextRefresh(q));
      } catch (e) {
        if (!alive) return;
        setFailed(true);
        later(nextRefresh(null, e));
      }
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [asked, iv]);

  return (
    <main className="futures-view">
      <section className="card fv-card">
        <header className="fv-head">
          <h2 className="fv-title">코스피200 선물
            {quote?.contract && <span className="muted small"> {quote.contract.name ?? quote.contract.code} · 만기 {quote.contract.expiry} (D-{quote.contract.days_left})</span>}
          </h2>
          <div className="chips" role="group" aria-label="세션">
            {(["day", "night"] as const).map((s) => (
              <button key={s} type="button" className={`chip ${session === s ? "chip-on" : ""}`} aria-pressed={session === s}
                onClick={() => go(s, iv)}>
                {SESSION_KO[s]} <span className="muted small">{SESSION_HOURS[s]}</span>
              </button>
            ))}
          </div>
          <div className="chips" role="group" aria-label="봉 간격">
            {INTERVALS.map((x) => (
              <button key={x.iv} type="button" className={`chip ${iv === x.iv ? "chip-on" : ""}`} aria-pressed={iv === x.iv}
                onClick={() => pickIv(x.iv)}>{x.label}</button>
            ))}
          </div>
        </header>
        {failed && !quote ? (
          <p className="empty-state">선물 시세를 불러오지 못했습니다. 잠시 뒤 새로 고쳐 주세요.</p>
        ) : !quote ? (
          <p className="empty-state">불러오는 중…</p>
        ) : !quote.supported ? (
          <p className="empty-state">선물 시세를 받을 수 없습니다{quote.error ? ` — ${quote.error}` : ""}.</p>
        ) : (
          <>
            <QuoteLine q={quote} />
            {(quote.stale || candles?.stale) && (
              <p className="small error">마지막으로 받은 값입니다{(quote.error ?? candles?.error) ? ` — ${quote.error ?? candles?.error}` : ""}</p>
            )}
            {failed && <p className="small error">새로 읽기에 실패해 이전 값을 보이고 있습니다.</p>}
            {/* 시세와 봉의 세션 · 간격이 맞을 때만 — 바꾸는 사이 다른 세션 기준선이 엇갈려 그려지지 않게 */}
            <FuturesChart candles={candles && candles.session === quote.session ? candles : null} iv={iv}
              base={quote.prev_close ?? null} baseLabel={quote.prev_close_basis ?? "기준가"} />
            <p className="muted small">
              {SESSION_KO[quote.session]} 세션 {quote.session_date ?? ""} · {kstStamp(quote.as_of)} 기준
              {isLive(quote) ? " · 15초마다 갱신" : ""} · 점선은 {quote.prev_close_basis ?? "기준가"}
            </p>
          </>
        )}
      </section>
    </main>
  );
}

function QuoteLine({ q }: { q: FuturesQuote }) {
  return (
    <div className="fv-quote">
      <span className={`fq-badge ${isLive(q) ? "fq-live" : ""}`}>{sessionBadge(q)}</span>
      <strong className={`fv-price num ${tone(q.change)}`}>{price(q.price)}</strong>
      <span className={`num ${tone(q.change)}`}>
        {q.change == null ? "—" : `${q.change > 0 ? "▲" : q.change < 0 ? "▼" : ""}${price(Math.abs(q.change))}`} {percent(q.change_pct)}
      </span>
      <span className="fv-stats small muted">
        <span>시 <b className="num">{price(q.open)}</b></span>
        <span>고 <b className="num up">{price(q.high)}</b></span>
        <span>저 <b className="num down">{price(q.low)}</b></span>
        <span>{q.prev_close_basis ?? "기준가"} <b className="num">{price(q.prev_close)}</b></span>
        <span>거래량 <b className="num">{contracts(q.volume)}</b></span>
        <span>미결제 <b className="num">{contracts(q.open_interest)}</b>
          {q.open_interest_change != null && <span className={`num ${tone(q.open_interest_change)}`}> ({contracts(q.open_interest_change, true)})</span>}</span>
      </span>
      {standIn(q) && <span className="small muted">{standIn(q)}{q.from_bars ? " — 시 · 고 · 저 · 거래량도 분봉으로 셈" : ""}</span>}
    </div>
  );
}

function FuturesChart({ candles, iv, base, baseLabel }: { candles: FuturesCandles | null; iv: FuturesInterval; base: number | null; baseLabel: string }) {
  const box = useRef<HTMLDivElement>(null);
  const ref = useRef<{ chart: IChartApi; series: ISeriesApi<"Candlestick">; volume: ISeriesApi<"Histogram"> } | null>(null);
  const baseLine = useRef<IPriceLine | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const grid = css("--border");
    const up = css("--up");
    const down = css("--down");
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: css("--muted") },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: grid },
      timeScale: { borderColor: grid, timeVisible: true, secondsVisible: false },
      localization: { locale: "ko-KR", priceFormatter: (v: number) => price(v), timeFormatter: crosshairTime },
    });
    const labelled: ISeriesApi<"Candlestick">[] = [];
    const series = chart.addSeries(CandlestickSeries, {
      upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
      // #434 축 라벨(마지막 값 · 기준가) 가까운 눈금은 비운다
      priceFormat: { type: "custom", minMove: 0.05, formatter: (v: number) => price(v), tickmarksFormatter: ticksClearOfLabels(() => labelled, (v) => price(v)) },
    });
    labelled.push(series);
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "", lastValueVisible: false, priceLineVisible: false });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    ref.current = { chart, series, volume };
    return () => {
      ref.current = null;
      baseLine.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const up = css("--up");
    const down = css("--down");
    const bars = candles?.bars ?? [];
    c.series.setData(bars.map((b) => ({ time: chartSec(b.t) as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    c.volume.setData(bars.map((b) => ({ time: chartSec(b.t) as UTCTimestamp, value: b.v, color: (b.c >= b.o ? up : down) + "55" })));
    if (baseLine.current) c.series.removePriceLine(baseLine.current);
    baseLine.current = base == null ? null : c.series.createPriceLine({
      price: base, title: baseLabel, color: css("--muted"), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true,
    });
  }, [candles, base, baseLabel]);

  // 세션 · 간격이 바뀌면 처음부터 끝까지 보이게(같은 세션의 15초 갱신은 보는 자리를 그대로)
  const key = `${candles?.session}-${candles?.session_date}-${iv}-${candles?.bars[0]?.t}`;
  useEffect(() => {
    if (candles?.bars.length) ref.current?.chart.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const empty = candles != null && candles.bars.length === 0;
  return (
    <div className="fv-chart-wrap">
      <div ref={box} className="fv-chart" />
      {empty && <p className="fv-chart-empty muted small">이 세션의 봉이 아직 없습니다.</p>}
    </div>
  );
}

/** #627 테마(다크 · 라이트)가 바뀌면 새 색으로 차트를 다시 만든다 — 차트 라이브러리는 CSS 변수를 직접 못 받는다 */
export default function FuturesView(props: Parameters<typeof FuturesViewInner>[0]) {
  const scheme = useColorScheme();
  return <FuturesViewInner key={scheme} {...props} />;
}
