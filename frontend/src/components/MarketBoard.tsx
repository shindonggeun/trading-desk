import { useCallback, useEffect, useState } from "react";
import { api, type Candles } from "../api";
import { price } from "../format";
import {
  type BoardMode, loadMarkets, loadMode, loadOpen, MARKETS_CHANGED, type MarketSymbol, saveMode, saveOpen,
} from "../markets";
import type { Estimate, SupportResistance } from "../types";
import DeepRunConfirm from "./DeepRunConfirm";
import FearIndexStrip from "./FearIndexStrip";
import { TrendStrip } from "./TrendChips";

const REFRESH_MS = 60_000;
const BARS: Record<BoardMode, number> = { "1d": 63, "5m": 78 }; // 일봉 약 3개월 · 5분봉 약 하루

/** 추세선(backend desk/trend.py, #73) 중 패널 요약에 쓰는 부분만. */
interface TrendSummary {
  kind: "support" | "resistance";
  price_now: number;
  slope_pct_per_bar?: number;
  /** intact | testing | broken — 이미 깨진 선은 요약에서 뺀다 */
  state?: string;
}

// 바이낸스 주식 무기한 계약은 기업 종목에만 있다 — 지수 · 선물 · 환율 · 코인은 추정가를 묻지 않는다
const NO_ESTIMATE = /^\^|=F$|=X$|\.NYB$|-(USD|USDT|USDC|BTC|ETH)$/i;

function trendLabel(t: TrendSummary): string {
  const slope = t.slope_pct_per_bar ?? 0;
  const dir = slope > 0 ? "상승" : slope < 0 ? "하락" : "수평";
  return `${t.kind === "support" ? "지지" : "저항"} 추세선(${dir})`;
}

interface Tile {
  bars: Candles["bars"];
  last: number | null;
  change: number | null; // 전일 종가 대비 %
  sr: (SupportResistance & { trendlines?: TrendSummary[] }) | null;
  est: Extract<Estimate, { supported: true }> | null;
  error: boolean;
}

const EMPTY: Tile = { bars: [], last: null, change: null, sr: null, est: null, error: false };

/** 일봉의 마지막 두 종가로 등락률을 낸다 — 분봉 모드에서도 같은 기준. */
function dailyChange(daily: Candles["bars"]): number | null {
  if (daily.length < 2) return null;
  const prev = daily[daily.length - 2].close;
  return prev ? ((daily[daily.length - 1].close - prev) / prev) * 100 : null;
}

function Spark({ bars, up }: { bars: Candles["bars"]; up: boolean }) {
  if (bars.length < 2) return <span className="spark spark-empty" />;
  const closes = bars.map((b) => b.close);
  const hi = Math.max(...closes);
  const lo = Math.min(...closes);
  const span = hi - lo || 1;
  const w = 120;
  const h = 36;
  const d = closes
    .map((c, i) => `${i === 0 ? "M" : "L"}${((i / (closes.length - 1)) * w).toFixed(1)},${(h - ((c - lo) / span) * h).toFixed(1)}`)
    .join(" ");
  return (
    <svg className={`spark ${up ? "spark-up" : "spark-down"}`} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" aria-hidden>
      <path d={d} />
    </svg>
  );
}

/** 지금 가격 바로 아래 지지 · 바로 위 저항, 그리고 추세선 하나씩. */
function SrLine({ sr, last }: { sr: Tile["sr"]; last: number | null }) {
  if (!sr || last === null) return null;
  const below = sr.levels.filter((l) => l.price < last).sort((a, b) => b.price - a.price)[0];
  const above = sr.levels.filter((l) => l.price > last).sort((a, b) => a.price - b.price)[0];
  // 추세선이 여럿이면 깨지지 않은 것 중 지금 가격에 가장 가까운 것
  const nearest = (kind: TrendSummary["kind"]) =>
    (sr.trendlines ?? []).filter((t) => t.kind === kind && t.state !== "broken")
      .sort((a, b) => Math.abs(a.price_now - last) - Math.abs(b.price_now - last))[0];
  const support = nearest("support");
  const resistance = nearest("resistance");
  if (!below && !above && !support && !resistance) return null;
  return (
    <span className="mb-sr small">
      {below && <span title={below.basis}>지지 {price(below.price)}</span>}
      {above && <span title={above.basis}>저항 {price(above.price)}</span>}
      {support && <span className="muted">{trendLabel(support)} {price(support.price_now)}</span>}
      {resistance && <span className="muted">{trendLabel(resistance)} {price(resistance.price_now)}</span>}
    </span>
  );
}

function useTiles(markets: MarketSymbol[], mode: BoardMode, enabled: boolean): Record<string, Tile> {
  const [tiles, setTiles] = useState<Record<string, Tile>>({});
  const key = markets.map((m) => m.ticker).join(",");
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const set = (ticker: string, patch: Partial<Tile>) =>
      alive &&
      setTiles((cur) => ({
        ...cur,
        [ticker]: { ...EMPTY, ...cur[ticker], ...patch },
      }));
    const loadPrices = () => {
      if (document.hidden) return;
      for (const { ticker } of markets) {
        const daily = api.candles(ticker, "1d", BARS["1d"]);
        const chart = mode === "1d" ? daily : api.candles(ticker, "5m", BARS["5m"]);
        Promise.all([daily, chart]).then(
          ([d, c]) => set(ticker, { bars: c.bars, last: c.bars.length ? c.bars[c.bars.length - 1].close : null, change: dailyChange(d.bars), error: false }),
          () => set(ticker, { error: true }),
        );
      }
    };
    loadPrices();
    const timer = setInterval(loadPrices, REFRESH_MS);
    document.addEventListener("visibilitychange", loadPrices);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", loadPrices);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, mode, enabled]);

  // 지지 · 저항 · 추정가는 차트 주기와 상관없이 목록이 바뀔 때 한 번 — 서버가 캐시한다
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const set = (ticker: string, patch: Partial<Tile>) =>
      alive && setTiles((cur) => ({ ...cur, [ticker]: { ...EMPTY, ...cur[ticker], ...patch } }));
    for (const { ticker } of markets) {
      api.levels(ticker).then((sr) => set(ticker, { sr }), () => {});
      if (!NO_ESTIMATE.test(ticker))
        api.estimate(ticker).then(
          (e) => set(ticker, { est: e.supported && (e.market_state === "closed" || e.market_state === "nxt") ? e : null }),
          () => {},
        );
    }
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
  return tiles;
}

/** 대시보드 위 "시장" 패널(#75): 지수 · 선물 · 환율 미니 차트, 누르면 확인 뒤 분석을 시작한다. */
/** ``embedded``: 대시보드 구역(#100)이 제목 · 접기를 맡는다 — 자기 제목 · 접기를 빼고 늘 펼친다. */
export default function MarketBoard({ embedded = false }: { embedded?: boolean } = {}) {
  const [savedOpen, setOpen] = useState(loadOpen);
  const open = embedded || savedOpen;
  const [mode, setMode] = useState<BoardMode>(loadMode);
  const [markets, setMarkets] = useState<MarketSymbol[]>(loadMarkets);
  const [picked, setPicked] = useState<MarketSymbol | null>(null);
  const tiles = useTiles(markets, mode, open);

  useEffect(() => {
    const reload = () => setMarkets(loadMarkets());
    window.addEventListener(MARKETS_CHANGED, reload);
    window.addEventListener("storage", reload); // 다른 탭에서 설정을 바꿨을 때
    return () => {
      window.removeEventListener(MARKETS_CHANGED, reload);
      window.removeEventListener("storage", reload);
    };
  }, []);

  const cancel = useCallback(() => setPicked(null), []);

  const toggle = () => {
    saveOpen(!open);
    setOpen(!open);
  };
  const switchMode = (m: BoardMode) => {
    saveMode(m);
    setMode(m);
  };

  // #539 칸을 누르면 차트 분석(쿼터 없음, 지금 고른 주기 그대로). 에이전트 토론 상세 분석은 칸 아래 버튼으로만
  const chartHref = (m: MarketSymbol) =>
    `#/chart/${encodeURIComponent(m.ticker)}?interval=${mode}&name=${encodeURIComponent(m.name)}`;

  return (
    <section className={`card market-board${open ? "" : " market-board-closed"}`}>
      <div className="mb-head">
        {embedded ? (
          <span className="muted small">{markets.length}종목 · 누르면 차트</span>
        ) : (
          <h2>
            시장 <span className="muted small">{markets.length}종목 · 누르면 차트</span>
          </h2>
        )}
        {open && (
          <div className="chips" role="group" aria-label="차트 주기">
            {(["1d", "5m"] as const).map((m) => (
              <button key={m} type="button" className={`chip ${mode === m ? "chip-on" : ""}`} aria-pressed={mode === m}
                onClick={() => switchMode(m)}>
                {m === "1d" ? "3개월 일봉" : "최근 5분봉"}
              </button>
            ))}
          </div>
        )}
        {!embedded && (
          <button type="button" className="link" aria-expanded={open} onClick={toggle}>
            {open ? "접기" : "펼치기"}
          </button>
        )}
      </div>
      {/* #461 지수별 지금 추세(코스피 · 나스닥100 · S&P500 · 반도체) */}
      {open && <TrendStrip />}
      {open && <FearIndexStrip />}
      {open && markets.length === 0 && (
        <p className="muted small">표시할 종목이 없습니다. 설정에서 추가하세요.</p>
      )}
      {open && markets.length > 0 && (
        <div className="mb-grid">
          {markets.map((m) => {
            const t = tiles[m.ticker];
            const up = (t?.change ?? 0) >= 0;
            return (
              <div key={m.ticker} className="mb-cell">
              <a className={`mb-tile${picked?.ticker === m.ticker ? " mb-tile-on" : ""}`} href={chartHref(m)}
                title={`${m.name} 차트 분석 열기(지지 · 저항 · 추세선 · 보조지표)`}>
                <span className="mb-name">
                  <strong>{m.name}</strong> <span className="muted small">{m.ticker}</span>
                </span>
                <span className="mb-price">
                  <span>{t?.last != null ? price(t.last) : t?.error ? "—" : "…"}</span>
                  {t?.change != null && (
                    <span className={`small ${up ? "up" : "down"}`}>
                      {up ? "+" : ""}
                      {t.change.toFixed(2)}%
                    </span>
                  )}
                </span>
                <Spark bars={t?.bars ?? []} up={up} />
                {t?.est && (
                  <span className="small muted" title={t.est.market_state === "nxt" ? "NXT(넥스트레이드) 프리 · 애프터마켓 마지막 체결가 — 20:00 이 지나면 추정가로 바뀝니다" :
                    `바이낸스 ${t.est.contract} 무기한 선물로 낸 추정가 — 실제 체결가가 아닙니다` +
                    (t.est.anchor_source === "nxt" && t.est.anchor_price != null
                      ? ` · NXT 애프터마켓 마지막 체결 ${price(t.est.anchor_price)} 에서 계약 움직임만 얹음` : "")}>
                    {t.est.market_state === "nxt" ? "NXT" : "추정가"} {price(t.est.estimate)} ({t.est.change_pct > 0 ? "+" : ""}
                    {t.est.change_pct.toFixed(2)}%)
                  </span>
                )}
                {/* #464 타일은 이름 · 값 · 등락 · 미니 차트만 — 지지 · 저항은 눌렀을 때 아래 확인 칸에 */}
                {t?.error && <span className="small error">시세를 못 읽었습니다</span>}
              </a>
              <button type="button" className="link small mb-deep" onClick={() => setPicked(m)}
                aria-controls="mb-confirm" aria-expanded={picked?.ticker === m.ticker}
                title="에이전트들이 토론해 타점을 내는 상세 분석(확인 한 번 · 구독 사용량 차감)">
                <span aria-hidden="true">🤖 </span>상세 분석<span className="sr-only"> — {m.name}</span>
              </button>
              </div>
            );
          })}
        </div>
      )}
      {open && picked && (
        <DeepRunConfirm ticker={picked.ticker} name={picked.name} onCancel={cancel}
          extra={<SrLine sr={tiles[picked.ticker]?.sr ?? null} last={tiles[picked.ticker]?.last ?? null} />} />
      )}
    </section>
  );
}
