import { useEffect, useState } from "react";
import { api } from "../api";
import { byAsked } from "../ticker";
import { boardBadge, boardBadgeClass, boardReason, quoteSourceShort } from "../korean";
import { clearFavoriteError, moveFavorite, removeFavorite, useFavorites } from "../favorites";
import { price, time } from "../format";
import type { BoardItem, EstimatedCandles, LongShortItem } from "../types";
import { LongShortBar, LongShortToggle, useLongShort, useLongShortShown } from "./LongShortBar";
import EarningsPreviewCard from "./EarningsPreviewCard";
import { addMonths, todayKst } from "../calendar";

const EARN_SOON_DAYS = 3;     // #294 실적 발표가 이 날 수 안이면 타일에 D-n 배지

/** #294 즐겨찾기 종목의 가까운 실적 발표일(이번 달 · 다음 달 캘린더에서). 실패하면 배지만 없다. */
function useEarningsSoon(key: string): Record<string, number> {
  const [soon, setSoon] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const today = todayKst();
    const month = today.slice(0, 7);
    Promise.allSettled([api.marketMonth(month), api.marketMonth(addMonths(month, 1))]).then((rs) => {
      if (!alive) return;
      const want = new Set(key.split(","));
      const out: Record<string, number> = {};
      const t0 = Date.parse(today);
      for (const r of rs) {
        if (r.status !== "fulfilled") continue;
        for (const d of r.value.days) {
          const days = Math.round((Date.parse(d.date) - t0) / 86_400_000);
          if (days < 0 || days > EARN_SOON_DAYS) continue;
          for (const e of d.earnings) if (want.has(e.ticker) && !(e.ticker in out)) out[e.ticker] = days;
        }
      }
      setSoon(out);
    });
    return () => { alive = false; };
  }, [key]);
  return soon;
}
import StockProfileCard from "./StockProfileCard";
import { fundBadgeText, fundHref, useFundChanges } from "../fundBadges";
import type { FundChangeItem } from "../types";

const POLL_MS = 15_000;      // 시세판과 같은 주기
const SPARK_MS = 120_000;    // 작은 차트는 2분마다
const MAX_QUOTES = 30;       // /api/estimates 한 번에 30종목
const SPARK_STAGGER_MS = 250; // 타일 i 의 첫 차트는 i × 0.25초 뒤

type Line = { real: number[]; est: number[] };

/** 최근 5분봉 종가 + 장외면 그 뒤의 추정 봉 종가. 타일마다 시작을 조금씩 늦춰 한꺼번에 몰리지 않게 한다. */
function useSpark(ticker: string, order: number): Line | null | "none" {
  const [line, setLine] = useState<Line | null | "none">(null);
  useEffect(() => {
    let alive = true;
    let missed = false;
    const load = async () => {
      if (document.hidden) {
        missed = true;   // 가려진 동안 건너뛴 차례는 다시 보일 때 바로 채운다
        return;
      }
      missed = false;
      const [real, est] = await Promise.allSettled([api.candles(ticker, "5m", 78), api.estimateCandles(ticker, "5m")]);
      if (!alive) return;
      const bars = real.status === "fulfilled" ? real.value.bars : [];
      const lastAt = bars.length ? new Date(bars[bars.length - 1].at).getTime() : 0;
      const e = est.status === "fulfilled" && "bars" in est.value ? (est.value as EstimatedCandles) : null;
      const estBars = e && e.market_state === "closed" ? e.bars.filter((b) => new Date(b.at).getTime() > lastAt).slice(-78) : [];
      const next = { real: bars.map((b) => b.close), est: estBars.map((b) => b.close) };
      setLine(next.real.length + next.est.length >= 2 ? next : "none");
    };
    const first = window.setTimeout(load, order * SPARK_STAGGER_MS);
    const t = window.setInterval(load, SPARK_MS);
    const onShow = () => {
      if (!document.hidden && missed) load();
    };
    document.addEventListener("visibilitychange", onShow);
    return () => {
      alive = false;
      window.clearTimeout(first);
      window.clearInterval(t);
      document.removeEventListener("visibilitychange", onShow);
    };
  }, [ticker, order]);
  return line;
}

export function Spark({ ticker, tone, order }: { ticker: string; tone: "up" | "down" | ""; order: number }) {
  const line = useSpark(ticker, order);
  if (line === null) return <span className="spark spark-empty" />;
  if (line === "none") return <span className="spark spark-empty fav-spark-none muted small">차트 없음</span>;
  const all = [...line.real, ...line.est];
  const lo = Math.min(...all);
  const hi = Math.max(...all);
  const w = 100;
  const h = 36;
  const x = (i: number) => (all.length > 1 ? (i / (all.length - 1)) * w : 0);
  const y = (v: number) => (hi === lo ? h / 2 : h - 2 - ((v - lo) / (hi - lo)) * (h - 4));
  const path = (vals: number[], from: number) => vals.map((v, i) => `${i ? "L" : "M"}${x(from + i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  // 추정선은 마지막 실제 점에서 이어 그린다
  const estFrom = Math.max(line.real.length - 1, 0);
  const estVals = line.real.length ? [line.real[line.real.length - 1], ...line.est] : line.est;
  return (
    <svg className={`spark ${tone === "down" ? "spark-down" : "spark-up"}${tone ? "" : " fav-spark-flat"}`} viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none" aria-hidden>
      {line.real.length > 1 && <path d={path(line.real, 0)} />}
      {line.est.length > 0 && <path className="fav-spark-est" d={path(estVals, estFrom)} />}
    </svg>
  );
}

function Tile({ ticker, name, it, ls, first, last, editing, order, earnIn, onEarn, onInfo, funds }: {
  ticker: string; name: string; it: BoardItem | undefined; ls: LongShortItem | undefined; first: boolean; last: boolean;
  editing: boolean; order: number; earnIn?: number; onEarn: () => void; onInfo: (ticker: string) => void; funds?: FundChangeItem[];
}) {
  const ch = it?.change_pct;
  const tone = ch == null ? "" : ch > 0 ? "up" : ch < 0 ? "down" : "";
  const badge = it ? boardBadge(it) : "…";
  return (
    <div className="qb-cell fav-cell">
      <a className={`qb-tile fav-tile${it?.state === "estimate" ? " qb-est" : ""}`} href={`#/live?t=${encodeURIComponent(ticker)}`}
        aria-label={`${name} 시세판에서 보기`}>
        <span className="qb-top">
          <span className="qb-name">{name}</span>
          <span className={boardBadgeClass(it)} title={(it && boardReason(it)) ?? undefined}>{badge}</span>
        </span>
        <span className="qb-price num">
          {it?.price != null ? price(it.price) : "—"}
          <span className={`small ${tone}`}>{ch != null ? ` ${ch > 0 ? "+" : ""}${ch.toFixed(2)}%` : ""}</span>
        </span>
        <Spark ticker={ticker} tone={tone} order={order} />
        {/* #316 기준 종가는 툴팁으로 — 좁은 카드에서 아래 줄이 잘리지 않게 */}
        <span className="qb-sub muted small"
          title={it?.close != null ? `등락률 기준 종가 ${price(it.close)}${it.close_date ? `(${it.close_date.slice(5)})` : ""}` : undefined}>
          {ticker}
          {it?.quote_source && ` · ${quoteSourceShort(it.quote_source)}`}
        </span>
      </a>
      <LongShortBar it={ls} />
      {/* #433 실적 칩과 종목 정보 링크를 한 줄에 — "정보" 가 홀로 떠 무엇의 정보인지 모호했다 */}
      <div className="fav-foot">
        {earnIn != null && (
          <button type="button" className="fav-earn small" onClick={onEarn} aria-label={`${name} 실적 프리뷰 열기`}>
            실적 {earnIn === 0 ? "오늘" : `D-${earnIn}`}
          </button>
        )}
        {/* #484 유명 기관 최신 13F 에서 이 종목을 사고팔았으면 */}
        {(funds ?? []).map((c) => {
          const text = fundBadgeText(c);
          return text ? <a key={c.fund} className={`fund-badge small fund-chg-${c.change}`} href={fundHref(c)} title={`${text} — 13F 는 최대 4~5개월 늦은 보유`}>{text}</a> : null;
        })}
        <button type="button" className="link small fav-info" aria-label={`${name} 종목 정보`} onClick={() => onInfo(ticker)}>종목 정보 ›</button>
      </div>
      {editing && (
        <span className="fav-tools" role="group" aria-label={`${name} 편집`}>
          <button type="button" className="dash-move" disabled={first} aria-label={`${name} 앞으로`} onClick={() => moveFavorite(ticker, -1)}>←</button>
          <button type="button" className="dash-move" disabled={last} aria-label={`${name} 뒤로`} onClick={() => moveFavorite(ticker, 1)}>→</button>
          <button type="button" className="dash-move" aria-label={`${name} 즐겨찾기 빼기`} onClick={() => removeFavorite(ticker)}>×</button>
        </span>
      )}
    </div>
  );
}

/**
 * 대시보드 "즐겨찾기 시세"(#135): 즐겨찾기마다 가격 · 기준 종가 대비 % · 실시간/추정 배지와 작은 차트(최근 5분봉,
 * 장외면 추정 봉을 점선으로 이어서). 15초마다 갱신, 누르면 시세판에서 그 종목 큰 차트.
 */
export default function FavoritesPanel() {
  const { list, error } = useFavorites();
  const [items, setItems] = useState<Record<string, BoardItem>>({});
  const [failedAt, setFailedAt] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [info, setInfo] = useState<string | null>(null);
  const tickers = (list ?? []).slice(0, MAX_QUOTES).map((f) => f.ticker);
  const key = tickers.join(",");
  const [lsShown, flipLs] = useLongShortShown();
  const ls = useLongShort(tickers, lsShown);
  const earnSoon = useEarningsSoon(key);
  const fundCh = useFundChanges(tickers);   // #484 기관 배지
  const [preview, setPreview] = useState<{ ticker: string; name: string } | null>(null);

  useEffect(() => {
    if (!key) return;
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.board(key.split(","))
        .then((r) => {
          if (!alive) return;
          setItems((cur) => ({ ...cur, ...byAsked(key.split(","), r.items) }));
          setFailedAt(null);
        })
        .catch(() => alive && setFailedAt(new Date().toISOString()));
    };
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [key]);

  if (list === null) return <p className="empty-state">불러오는 중…</p>;
  const updated = Object.values(items).map((i) => i.fetched_at).filter(Boolean).sort().pop();
  const shown = list.slice(0, MAX_QUOTES);
  return (
    <section className="card fav-panel" aria-label="즐겨찾기 시세">
      {error && (
        <p className="error small">
          {error} <button type="button" className="link small" onClick={clearFavoriteError}>닫기</button>
        </p>
      )}
      {list.length === 0 ? (
        <p className="empty-state">
          즐겨찾기가 없습니다. 분석 화면 · 시세판 · 시장 패널에서 종목 옆 ☆ 를 누르면 여기에 실시간 가격 · 장외 추정가와 차트가 나옵니다.
        </p>
      ) : (
        <>
          <div className="qb-head">
            <span className="muted small">
              {updated ? `${time(updated)} 갱신` : "불러오는 중"}
              {failedAt && ` · ${time(failedAt)} 갱신 실패 — 마지막 값`}
              {list.length > MAX_QUOTES && ` · 앞 ${MAX_QUOTES}개만 보입니다(나머지는 시세판 탭에서 보고 뺄 수 있습니다)`}
            </span>
            <span className="qb-head-tools">
              <LongShortToggle shown={lsShown} onFlip={flipLs} />
              <button type="button" className="link small" aria-pressed={editing} onClick={() => setEditing((v) => !v)}>
                {editing ? "편집 끝" : "순서 · 빼기"}
              </button>
            </span>
          </div>
          <div className="qb-grid fav-grid">
            {shown.map((f, i) => (
              <Tile key={f.ticker} ticker={f.ticker} name={f.name ?? f.ticker} it={items[f.ticker]} ls={lsShown ? ls[f.ticker] : undefined}
                first={i === 0} last={i === list.length - 1} editing={editing} order={i}
                earnIn={earnSoon[f.ticker]} onEarn={() => setPreview({ ticker: f.ticker, name: f.name ?? f.ticker })}
                onInfo={(t) => setInfo((cur) => (cur === t ? null : t))} funds={fundCh[f.ticker]} />
            ))}
          </div>
          {info && <StockProfileCard ticker={info} onClose={() => setInfo(null)} />}
          <p className="muted small qb-note">추정가 · 점선은 바이낸스 무기한 선물로 낸 장외 추정이며 실제 체결가가 아닙니다.</p>
        </>
      )}
      {preview && <EarningsPreviewCard ticker={preview.ticker} name={preview.name} onClose={() => setPreview(null)} />}
    </section>
  );
}
