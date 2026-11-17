import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import InstrumentSearch, { type Picked } from "../components/InstrumentSearch";
import type { ExtraLine, Interval } from "../components/PriceChart";
import { openChat } from "../chat";
import { useFavorites } from "../favorites";
import { price } from "../format";
import type { Run, WeeklyMap, WeeklyMapError } from "../types";
import { sameTicker } from "../ticker";
import { nextLines, WEEKLY_WORDS, weeklyLink } from "../weekly";

const PriceChart = lazy(() => import("../components/PriceChart"));

/**
 * #220 차트 탭 — 분석을 돌리지 않고 종목 차트만 크게. `#/charts?t=SOXL&iv=1d` 로 종목 · 봉 주기가 주소에 남아
 * 새로고침 · 공유해도 같은 화면이다. 레벨은 "지금 기준"(/api/levels), RSI · 스토캐스틱 · 과열/침체 표시는
 * 차트의 기본 표시 그대로(#206). 레버리지 상품은 기초자산 차트로 바로 전환.
 */

const INTERVALS: Interval[] = ["5m", "15m", "30m", "60m", "240m", "1d", "1w", "1M", "1y"];
const RECENT_KEY = "desk.charts.recent";
const RECENT_MAX = 8;
const WEEKLY_KEY = "desk.charts.weekly";

const isMap = (m: WeeklyMap | WeeklyMapError): m is WeeklyMap => !("error" in m);

function css(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** #235 주간 선(검정선 · 빨간선)을 차트에 겹칠지 — 이 브라우저에 기억, 기본 켬 */
function loadWeeklyOn(): boolean {
  try {
    return localStorage.getItem(WEEKLY_KEY) !== "off";
  } catch {
    return true;
  }
}
function saveWeeklyOn(on: boolean) {
  try {
    localStorage.setItem(WEEKLY_KEY, on ? "on" : "off");
  } catch {
    /* storage blocked: the choice is not kept */
  }
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 이 브라우저에서 최근 본 종목(편의용 — 비공개 창이면 비어 있다) */
function loadRecent(): Picked[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((r) => r && typeof r.ticker === "string").slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}
function saveRecent(list: Picked[]) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
  } catch {
    /* storage blocked: the list just is not kept */
  }
}

/** 주소를 바꾸되 뒤로 가기 기록은 종목이 바뀔 때만 남긴다(봉 주기만 바뀌면 덮어쓴다) */
function go(ticker: string, iv: Interval | null, push: boolean) {
  const q = new URLSearchParams();
  if (ticker) q.set("t", ticker);
  if (iv) q.set("iv", iv);
  const hash = `#/charts${q.toString() ? `?${q}` : ""}`;
  if (window.location.hash === hash) return;
  if (push) window.location.hash = hash;
  else window.history.replaceState(null, "", hash);
}

export default function Charts({ query }: { query: URLSearchParams }) {
  const ticker = (query.get("t") ?? "").trim().toUpperCase();
  const ivRaw = query.get("iv");
  const iv = INTERVALS.includes(ivRaw as Interval) ? (ivRaw as Interval) : undefined;
  const [search, setSearch] = useState<Picked>({ ticker: "" });
  const [recent, setRecent] = useState<Picked[]>(loadRecent);
  const favorites = useFavorites().list ?? [];
  const [underlying, setUnderlying] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Run | null>(null);
  const [weekly, setWeekly] = useState<WeeklyMap | WeeklyMapError | null>(null);
  const [weeklyOn, setWeeklyOn] = useState(loadWeeklyOn);
  const name = recent.find((r) => sameTicker(r.ticker, ticker))?.name ?? favorites.find((f) => sameTicker(f.ticker, ticker))?.name ?? null;

  const open = (p: Picked) => {
    const t = p.ticker.trim().toUpperCase();
    if (!t) return;
    const next = [{ ticker: t, name: p.name }, ...recent.filter((r) => !sameTicker(r.ticker, t))];
    setRecent(next);
    saveRecent(next);
    setSearch({ ticker: "" });
    go(t, iv ?? null, true);
  };

  // 주소로 바로 연 종목도 최근 목록에
  useEffect(() => {
    if (!ticker) return;
    setRecent((old) => {
      if (sameTicker(old[0]?.ticker, ticker)) return old;
      const next = [{ ticker, name: old.find((r) => sameTicker(r.ticker, ticker))?.name }, ...old.filter((r) => !sameTicker(r.ticker, ticker))];
      saveRecent(next);
      return next;
    });
  }, [ticker]);

  // 레버리지 · 인버스 상품이면 기초자산(#203 판정이 함께 준다), 이 종목의 최근 분석
  useEffect(() => {
    setUnderlying(null);
    setLastRun(null);
    if (!ticker) return;
    let alive = true;
    api.oscillator(ticker, "1d").then((v) => alive && setUnderlying(v.underlying?.ticker ?? null), () => {});
    api.runs(1, 50).then((p) => alive && setLastRun(p.items.find((r) => sameTicker(r.ticker, ticker)) ?? null), () => {});
    return () => { alive = false; };
  }, [ticker]);

  // #235 이 종목의 주간 지도(#202 · #229) — 실패는 위치 줄에서 한 줄로
  useEffect(() => {
    setWeekly(null);
    if (!ticker) return;
    let alive = true;
    api.weeklyMaps(ticker).then(
      (r) => alive && setWeekly(r.maps[0] ?? { symbol: ticker, name: ticker, error: "empty" }),
      () => alive && setWeekly({ symbol: ticker, name: ticker, error: "failed" }),
    );
    return () => { alive = false; };
  }, [ticker]);

  // 다른 심볼로 계산한 지도(코스피200 → 069500.KS 등)는 가격 단위가 달라 선을 겹치지 않는다
  const map = weekly && isMap(weekly) && (!weekly.source_symbol || sameTicker(weekly.source_symbol, ticker)) ? weekly : null;
  const weeklyLines = useMemo<ExtraLine[] | undefined>(() => {
    if (!map || !weeklyOn) return undefined;
    return [
      { price: map.lines.black.price, label: `검정선 ${map.lines.black.weeks}주`, color: css("--text", "#e6e9ee") },
      { price: map.lines.red.price, label: `빨간선 ${map.lines.red.weeks}주`, color: css("--up", "#e5484d") },
    ];
  }, [map, weeklyOn]);
  const words = map ? WEEKLY_WORDS[map.position] : null;
  const next = map ? nextLines(map) : null;
  const toggleWeekly = (on: boolean) => {
    setWeeklyOn(on);
    saveWeeklyOn(on);
  };

  const onInterval = useCallback((next: Interval) => go(ticker, next, false), [ticker]);
  const analyze = `#/?ticker=${encodeURIComponent(ticker)}${name ? `&name=${encodeURIComponent(name)}` : ""}`;

  return (
    <main className="page charts-page">
      <section className="card charts-bar">
        <div className="charts-search">
          <label className="sr-only" htmlFor="charts-search">종목 검색</label>
          <InstrumentSearch inputId="charts-search" value={search} onChange={setSearch} onPick={open}
            placeholder="종목 검색 (예: SOXL, 삼성전자)" />
          <button type="button" onClick={() => open(search)} disabled={!search.ticker.trim()}>열기</button>
        </div>
        {(recent.length > 0 || favorites.length > 0) && (
          <div className="charts-chips">
            {recent.length > 0 && <span className="muted small">최근</span>}
            {recent.map((r) => (
              <button key={`r-${r.ticker}`} type="button" className={`chip ${sameTicker(r.ticker, ticker) ? "chip-on" : ""}`}
                aria-pressed={sameTicker(r.ticker, ticker)} onClick={() => open(r)} title={r.name ?? r.ticker}>{r.ticker}</button>
            ))}
            {favorites.length > 0 && <span className="muted small charts-chips-gap">즐겨찾기</span>}
            {favorites.map((f) => (
              <button key={`f-${f.ticker}`} type="button" className={`chip ${sameTicker(f.ticker, ticker) ? "chip-on" : ""}`}
                aria-pressed={sameTicker(f.ticker, ticker)} onClick={() => open({ ticker: f.ticker, name: f.name ?? undefined })}
                title={f.name ?? f.ticker}>{f.name ? `${f.name}` : f.ticker}</button>
            ))}
          </div>
        )}
        {ticker && (
          <div className="charts-actions small">
            <strong>{name && name !== ticker ? `${name} · ${ticker}` : ticker}</strong>
            {underlying && !sameTicker(underlying, ticker) && (
              <button type="button" className="secondary" onClick={() => open({ ticker: underlying })}>
                기초자산 {underlying} 차트로
              </button>
            )}
            <a href={analyze}>이 종목으로 분석 시작 →</a>
            <button type="button" className="secondary" onClick={() => openChat({ ticker, run: lastRun?.id ?? null })}>AI 에게 묻기</button>
            {lastRun && <a href={`#/runs/${lastRun.id}`}>최근 분석({lastRun.trade_date}) 열기</a>}
          </div>
        )}
        {ticker && weekly && (
          <div className="charts-weekly small">
            {map && words && next ? (
              <>
                <span className="muted">주간</span>
                <span className={`wm-pos wm-pos-${map.position}`} title={`${words.where} — ${words.meaning}`}>
                  {words.name}
                </span>
                <span className="num">검정선(20주 평균) {price(map.lines.black.price)} · 빨간선(60주 평균) {price(map.lines.red.price)}</span>
                <span className="num">
                  <span className="down" title={`아래 받침 — ${next.down.what} ${price(next.down.price)}`}>↓ {next.down.text}</span>
                  {" · "}<span className="up" title={`위 저항 — ${next.up.what} ${price(next.up.price)}`}>↑ {next.up.text}</span>
                </span>
                <span className="charts-weekly-todo">{words.todo}</span>
                {map.cross && <strong>{map.cross === "broke_black" ? "이번 주 검정선 이탈" : "이번 주 검정선 되찾음"}</strong>}
                <label className="charts-weekly-toggle">
                  <input type="checkbox" checked={weeklyOn} onChange={(e) => toggleWeekly(e.target.checked)} />
                  주간 선 차트에 표시
                </label>
                <a href={weeklyLink(ticker)}>주간 지도 보기 →</a>
              </>
            ) : isMap(weekly) ? (
              <>
                <span className="muted">주간 지도는 {weekly.source_symbol} 로 계산해 이 차트에 선을 겹치지 않습니다.</span>
                <a href={weeklyLink(ticker)}>주간 지도 보기 →</a>
              </>
            ) : (
              <span className="muted">주봉이 모자라거나 받지 못해 주간 선 없이 차트만 보여 줍니다.</span>
            )}
          </div>
        )}
      </section>

      {ticker ? (
        <Suspense fallback={<p className="muted">차트를 여는 중…</p>}>
          <PriceChart key={ticker} ticker={ticker} tradeDate={today()} plan={null} title={name ?? ticker}
            initialInterval={iv} onIntervalChange={onInterval} extraLines={weeklyLines} />
        </Suspense>
      ) : (
        <section className="card charts-empty">
          <p className="muted">종목을 검색하거나 위 칩을 누르면 분석 없이 차트가 열립니다. 지지 · 저항 · 추세선 · 매물대 ·
            RSI · 스토캐스틱 · 과열/침체 표시가 함께 나옵니다.</p>
        </section>
      )}
    </main>
  );
}
