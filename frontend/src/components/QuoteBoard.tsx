import { useMemo, useRef, useState } from "react";
import { api } from "../api";
import { askedOf, byAsked } from "../ticker";
import { price, time } from "../format";
import type { BoardItem } from "../types";
import { LongShortBar, LongShortToggle, useLongShort, useLongShortShown } from "./LongShortBar";
import { boardBadge, boardBadgeClass, boardReason, quoteSourceShort } from "../korean";
import { useLivePoll } from "../livePoll";
import { agoText, BOARD_POLL_MS, boardLive } from "../boardLive";
import { useNow } from "../poll";
import LiveBadge from "./LiveBadge";
import { splitView } from "../changeSplit";

export type BoardTicker = { ticker: string; name?: string };

const BUNDLE_KEY = "desk.board.weekend";
const OPEN_KEY = "desk.board.weekend.open";

/** 주말 · 장 마감 중 자동으로 보이는 묶음 — 국내 반도체 · 미장 주요 · 레버리지 · 나스닥100 선물 · S&P · 유가 · 금. */
export const WEEKEND_DEFAULT: BoardTicker[] = [
  { ticker: "005930.KS", name: "삼성전자" },
  { ticker: "000660.KS", name: "SK하이닉스" },
  { ticker: "042700.KS", name: "한미반도체" },
  { ticker: "NVDA", name: "엔비디아" },
  { ticker: "AMD", name: "AMD" },
  { ticker: "AVGO", name: "브로드컴" },
  { ticker: "TSM", name: "TSMC" },
  { ticker: "MU", name: "마이크론" },
  { ticker: "SOXL", name: "SOXL" },
  { ticker: "TQQQ", name: "TQQQ" },
  { ticker: "NQ=F", name: "나스닥100 선물" },
  { ticker: "^GSPC", name: "S&P 500" },
  { ticker: "CL=F", name: "WTI 원유" },
  { ticker: "GC=F", name: "금" },
];

function load<T>(key: string, fallback: T, ok: (v: unknown) => boolean): T {
  try {
    const raw = JSON.parse(localStorage.getItem(key) ?? "null");
    return ok(raw) ? (raw as T) : fallback;
  } catch {
    return fallback;
  }
}
const isList = (v: unknown) => Array.isArray(v) && v.every((x) => x && typeof (x as BoardTicker).ticker === "string");
function save(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 저장 못 해도 화면은 그대로 */
  }
}

/** #238 예전 기본 묶음 이름: 레버리지 ETF 는 티커로, ^NDX(장외에 안 움직임)는 나스닥100 선물로. */
const LEGACY: Record<string, BoardTicker> = {
  "SOXL|반도체 3배": { ticker: "SOXL", name: "SOXL" },
  "TQQQ|나스닥 3배": { ticker: "TQQQ", name: "TQQQ" },
  "^NDX|위켄드 나스닥100": { ticker: "NQ=F", name: "나스닥100 선물" },
};
function renamed(list: BoardTicker[]): BoardTicker[] {
  const out = list.map((t) => LEGACY[`${t.ticker}|${t.name ?? ""}`] ?? t);
  return out.filter((t, i) => out.findIndex((o) => o.ticker === t.ticker) === i);
}

/** 한국 시각 토 · 일 — 국내 · 미장 모두 쉬는 날 */
function isWeekendKst(now = new Date()): boolean {
  const day = new Date(now.getTime() + 9 * 3600_000).getUTCDay();
  return day === 0 || day === 6;
}

function sourceText(it: BoardItem): string {
  if (it.state === "live") return quoteSourceShort(it.quote_source);
  const s = it.source;
  if (!s) return "";
  if (s.kind === "leveraged") return `${s.basis} 추정 × ${s.multiple > 0 ? s.multiple : `(${s.multiple})`}`;
  if (s.kind === "proxy") return `${s.basis} 계약 대용`;
  return `바이낸스 ${s.contract}`;
}

function Tile({ it, name, on, onPick, now }: { it: BoardItem | undefined; name: string; on: boolean; onPick: () => void; now: number }) {
  // #1002 국장 정규장 뒤 NXT · 시간외 · 저녁 추정: 큰 숫자는 합친 하루, 작은 줄에 정규 · 장외(기준 종가는 툴팁)
  const sv = splitView(it);
  const ch = sv ? sv.pct : it?.change_pct;
  const tone = ch == null ? "" : ch > 0 ? "up" : ch < 0 ? "down" : "";
  const badge = it ? boardBadge(it) : "…";
  return (
    <button type="button" className={`qb-tile${on ? " qb-on" : ""}${it?.state === "estimate" ? " qb-est" : ""}`} aria-pressed={on} onClick={onPick}>
      <span className="qb-top">
        <span className="qb-name">{name}</span>
        <span className={boardBadgeClass(it)} title={(it && boardReason(it)) ?? undefined}>{badge}</span>
      </span>
      <span className="qb-price num">
        {it?.price != null ? price(it.price) : "—"}
        <span className={`small ${tone}`}>{ch != null ? ` ${ch > 0 ? "+" : ""}${ch.toFixed(2)}%` : ""}</span>
      </span>
      {sv && <span className="qb-split muted small" title={sv.title}>{sv.line}</span>}
      {on && it && boardReason(it) && <span className="qb-reason small">{boardReason(it)}</span>}
      <span className="qb-sub muted small">
        {/* #1002 나눈 줄이 있으면 "기준 …" 은 툴팁으로, #1000 추정 칸은 계산한 지 "n초 전" */}
        {[it?.close != null && !sv ? `기준 ${price(it.close)}${it.close_date ? `(${it.close_date.slice(5)})` : ""}` : null,
          it ? sourceText(it) || null : null,
          it?.state === "estimate" ? agoText(it.fetched_at, now) : null].filter(Boolean).join(" · ")}
      </span>
    </button>
  );
}

/**
 * 시세판(#124): 관심 종목 타일 — 장중엔 실시간, 장 마감 · 주말엔 바이낸스 무기한으로 낸 추정가.
 * 주말 · 모든 관심 종목이 장 마감이면 "주말 추정" 묶음이 위에 뜬다(접기 · 빼기 기억). 실시간 · 추정 모두 시장 스트림으로 받고, 스트림이 없으면 10초마다 다시 읽는다(#1000).
 */
export default function QuoteBoard({ watch, selected, onPick, onPrice }: {
  watch: BoardTicker[];
  selected: string | undefined;
  onPick: (t: BoardTicker) => void;
  /** 읽을 때마다 종목별 가격(실시간 또는 추정) */
  onPrice?: (ticker: string, price: number) => void;
}) {
  const onPriceRef = useRef(onPrice);
  onPriceRef.current = onPrice;
  const lastPrice = useRef<Record<string, { p: number; at: number }>>({});
  const [bundle, setBundle] = useState<BoardTicker[]>(() => renamed(load(BUNDLE_KEY, WEEKEND_DEFAULT, isList)));
  const [bundleOpen, setBundleOpen] = useState<boolean>(() => load(OPEN_KEY, true, (v) => typeof v === "boolean"));
  const [items, setItems] = useState<Record<string, BoardItem>>({});
  const [failedAt, setFailedAt] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  // #1000 추정 칸의 "n초 전" — 추정 칸이 있을 때만 1초마다
  const now = useNow(1_000, Object.values(items).some((it) => it.state === "estimate"));
  const weekend = isWeekendKst();
  const watchLive = watch.some((w) => items[w.ticker]?.state === "live");
  // 주말이거나, 관심 종목이 하나도 장중이 아니면(장 마감) 주말 추정 묶음을 보인다
  // 관심 종목이 비었으면 묶음을 늘 보인다
  const closedMode = weekend || watch.length === 0 || (watch.every((w) => items[w.ticker]) && !watchLive);
  const tickers = useMemo(() => {
    // 접힌 묶음은 읽지 않는다
    const all = [...watch, ...(closedMode && bundleOpen ? bundle : [])].map((t) => t.ticker);
    return [...new Set(all)].slice(0, 30);
  }, [watch, bundle, closedMode, bundleOpen]);
  const key = tickers.join(",");
  const [lsShown, flipLs] = useLongShortShown();
  const ls = useLongShort(tickers, lsShown);

  // #626 · #1000 10초마다(가려진 탭은 쉰다) — 같은 종목 묶음이면 즐겨찾기 · 섹터 · 작은 창과 요청 하나를 함께 쓴다.
  // 스트림(실시간 quote · #999 추정)이 모든 칸을 덮는 동안은 쉰다(boardLive.ts)
  const liveSpec = useMemo(() => boardLive(tickers), [tickers]);
  const boardPoll = useLivePoll(tickers.length ? `board:${key}` : null, () => api.board(tickers), BOARD_POLL_MS, {
    onData: (r) => {
      setItems((cur) => ({ ...cur, ...byAsked(tickers, r.items) }));
      for (const it of r.items) {
        const t = askedOf(tickers, it.ticker);
        // #764 스트림이 칸 하나를 바꿀 때마다 모든 종목을 다시 알리지 않게 — 바뀐 가격만(같은 값은 주기에 한 번)
        const before = lastPrice.current[t];
        if (it.price == null || (before && before.p === it.price && Date.now() - before.at < BOARD_POLL_MS)) continue;
        lastPrice.current[t] = { p: it.price, at: Date.now() };
        onPriceRef.current?.(t, it.price);
      }
      setFailedAt(null);
    },
    onError: () => setFailedAt(new Date().toISOString()),
  }, liveSpec);

  const flipBundle = () => {
    save(OPEN_KEY, !bundleOpen);
    setBundleOpen(!bundleOpen);
  };
  const removeFromBundle = (t: string) => {
    const next = bundle.filter((b) => b.ticker !== t);
    save(BUNDLE_KEY, next);
    setBundle(next);
  };
  const resetBundle = () => {
    save(BUNDLE_KEY, WEEKEND_DEFAULT);
    setBundle(WEEKEND_DEFAULT);
  };
  const updated = Object.values(items).map((i) => i.fetched_at).filter(Boolean).sort().pop();

  return (
    <section className="card quote-board" aria-label="시세판">
      <div className="qb-head">
        <h2>시세판</h2>
        {tickers.length > 0 && <LiveBadge channel="quote" keys={tickers} on={boardPoll.live} />}
        <span className="muted small">
          {updated ? `${time(updated)} 갱신` : "불러오는 중"}
          {failedAt && ` · ${time(failedAt)} 갱신 실패 — 마지막 값`}
        </span>
        <LongShortToggle shown={lsShown} onFlip={flipLs} />
      </div>
      {closedMode && (
        <div className="qb-group">
          <div className="qb-group-head">
            <button type="button" className="dash-toggle" aria-expanded={bundleOpen} onClick={flipBundle}>
              <span className="dash-caret" aria-hidden="true">{bundleOpen ? "▾" : "▸"}</span>
              주말 · 장 마감 추정 <span className="muted small">{bundle.length}종목</span>
            </button>
            {bundleOpen && (
              <span className="qb-group-tools">
                <button type="button" className="link small" onClick={() => setEditing((v) => !v)}>{editing ? "편집 끝" : "편집"}</button>
                {editing && <button type="button" className="link small" onClick={resetBundle}>기본값</button>}
              </span>
            )}
          </div>
          {bundleOpen && (
            <div className="qb-grid">
              {bundle.map((b) => (
                <div key={b.ticker} className="qb-cell">
                  <Tile it={items[b.ticker]} name={b.name ?? b.ticker} on={selected === b.ticker} onPick={() => onPick(b)} now={now} />
                  {lsShown && <LongShortBar it={ls[b.ticker]} />}
                  {editing && (
                    <button type="button" className="qb-x" aria-label={`${b.name ?? b.ticker} 빼기`} onClick={() => removeFromBundle(b.ticker)}>×</button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="qb-group">
        <div className="qb-group-head"><span className="small">관심 종목</span></div>
        <div className="qb-grid">
          {watch.map((w) => (
            <div key={w.ticker} className="qb-cell">
              <Tile it={items[w.ticker]} name={w.name ?? w.ticker} on={selected === w.ticker} onPick={() => onPick(w)} now={now} />
              {lsShown && <LongShortBar it={ls[w.ticker]} />}
            </div>
          ))}
        </div>
      </div>
      <p className="muted small qb-note">
        추정가는 바이낸스 주식 · 원자재 무기한 선물이 기준 종가 이후 움직인 비율로 낸 값이며 실제 체결가가 아닙니다.
        지수는 ETF 계약, 바이낸스에 없는 레버리지는 기초 자산 × 배수로 환산한 근사치입니다.
      </p>
    </section>
  );
}
