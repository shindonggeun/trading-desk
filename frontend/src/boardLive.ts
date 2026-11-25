import type { LiveSpec } from "./livePoll";
import type { StreamMessage } from "./marketStream";
import { askedOf } from "./ticker";
import type { BoardItem, ChangeSplit } from "./types";

/**
 * #764 · #1000 시세 묶음(`board:<티커들>`, GET /api/estimates)을 시장 스트림 quote 채널로 이어 받는 법.
 * 시세판 · 즐겨찾기 · 섹터 · 작은 창이 같은 요청 하나를 함께 쓰므로 넷 모두 이 규칙으로 구독한다
 * (poll.ts 는 모든 구독자가 쉬자고 할 때만 REST 를 쉰다).
 * - 실시간 quote(KIS · 토스): 정규장 칸에만 가격을 얹는다. 추정 · 시간외(NXT) 칸은 서버의 계산을 그대로 둔다.
 * - 추정 메시지(#999, source = binance-estimate): collector 가 장외 종목을 10초마다 계산해 민다. payload 는 /api/estimates
 *   추정 한 줄과 같은 모양 + close_at. 추정 · 추정 불가 칸에만 얹는다 — 실시간 칸은 바꾸지 않는다(미국 프리 · 애프터 ·
 *   오버나이트엔 REST 가 장외 체결로 live 인데 추정도 계속 온다). 정규장 칸에 추정이 오면 장이 막 닫힌 것이라 REST 로 다시 맞춘다.
 * - 쉬기: 모든 칸이 정규장이거나 최근에 스트림 추정을 받은 추정 칸일 때만. 추정이 끊긴 칸 · NXT · 장외 체결 칸이 있으면 REST 를
 *   BOARD_POLL_MS 마다 계속 읽는다(로컬 all 모드엔 collector 가 없어 추정 메시지가 없다 — 지금처럼 REST 로).
 */

export type Board = { items: BoardItem[] };

/** #1000 REST 대체 주기 — 추정 메시지와 같은 10초(예전 15초) */
export const BOARD_POLL_MS = 10_000;
/** #764 스트림이 시세를 대신 주는 동안에도 칸의 상태(장중 · 추정 · 장 마감)는 서버가 정한다 — 이만큼마다 한 번 맞춘다 */
export const BOARD_RECONCILE_MS = 5 * 60_000;
/** #999 추정 메시지의 source(BoardItem.quote_source 와 같다) */
export const ESTIMATE_SOURCE = "binance-estimate";
/** 이만큼 추정 메시지가 없으면 그 칸은 스트림이 덮지 않는다고 보고 REST 로 읽는다(10초 주기의 세 배) */
export const ESTIMATE_QUIET_MS = 30_000;

type StreamQuote = { ticker?: string; price?: number | null; source?: string | null; fetched_at?: string | null };
/** #999 추정 메시지 payload */
export type EstimatePayload = Partial<BoardItem> & { price?: number | null; close_at?: string | null };

export const trading = (it: BoardItem) => it.state === "live" && it.session === "regular";
const takesEstimate = (it: BoardItem) => it.state === "estimate" || it.state === "unavailable";
export const isEstimateMessage = (m: Pick<StreamMessage, "source">) => m.source === ESTIMATE_SOURCE;
const mine = (m: StreamMessage, it: BoardItem) => askedOf([m.key], it.ticker) === m.key;
const pct = (price: number, close: number | null | undefined) =>
  close ? Math.round((price / close - 1) * 10_000) / 100 : null;

/** #1002 나눈 하루(정규 · 장외 · 합친)를 새 장외 가격으로 — 기준 종가는 그대로, 장외 · 합친 등락만 다시 잰다.
 *  저녁 추정 칸에서 스트림이 가격만 바꾸면 큰 숫자(합친 하루)가 옛 값에 머물지 않게 */
export function moveSplit(split: ChangeSplit, price: number, at: string): ChangeSplit {
  return {
    ...split,
    extended: { ...split.extended, pct: pct(price, split.extended.base) ?? split.extended.pct, at },
    total: { ...split.total, pct: pct(price, split.total.base) ?? split.total.pct },
  };
}

/** 실시간 quote — 정규장 칸의 가격만 */
export function applyQuote(prev: Board, m: StreamMessage): Board | undefined {
  const q = m.payload as StreamQuote | null;
  if (!q || q.price == null || !Number.isFinite(q.price)) return undefined;
  let hit = false;
  const items = prev.items.map((it) => {
    if (!trading(it) || !mine(m, it) || it.price === q.price) return it;
    hit = true;
    return { ...it, price: q.price!, change_pct: pct(q.price!, it.close) ?? it.change_pct,
             quote_source: q.source ?? it.quote_source, fetched_at: q.fetched_at ?? m.observed_at };
  });
  return hit ? { ...prev, items } : undefined;
}

/** #999 추정 메시지 — 추정 · 추정 불가 칸을 그 줄로 갈아 끼운다. 이미 가진 것보다 옛 계산은 얹지 않는다 */
export function applyEstimate(prev: Board, m: StreamMessage): Board | undefined {
  const e = m.payload as EstimatePayload | null;
  if (!e || e.price == null || !Number.isFinite(e.price)) return undefined;
  const at = e.fetched_at ?? m.observed_at;
  let hit = false;
  const items = prev.items.map((it) => {
    if (!takesEstimate(it) || !mine(m, it)) return it;
    if (it.state === "estimate") {
      if (it.price === e.price && it.fetched_at === at) return it;
      const was = it.fetched_at ? Date.parse(it.fetched_at) : NaN;
      if (!Number.isNaN(was) && Date.parse(at) < was) return it;
    }
    hit = true;
    const close = e.close !== undefined ? e.close : it.close;
    return {
      ...it,
      state: "estimate" as const,
      price: e.price!,
      change_pct: e.change_pct ?? pct(e.price!, close) ?? it.change_pct,
      close,
      close_date: e.close_date !== undefined ? e.close_date : it.close_date,
      source: e.source !== undefined ? e.source : it.source,
      session: e.session ?? it.session,
      quote_source: ESTIMATE_SOURCE,
      fetched_at: at,
      // #1001 메시지에 나눈 하루가 실려 오면 그것을, 아니면 칸이 가진 것을 새 가격으로 옮긴다
      change_split: e.change_split ?? (it.change_split ? moveSplit(it.change_split, e.price!, at) : undefined),
    };
  });
  return hit ? { ...prev, items } : undefined;
}

/** 시세 묶음의 스트림 규칙. 구독자마다 하나(useMemo) — 칸별로 추정 메시지를 마지막으로 받은 시각을 들고 있다 */
export function boardLive(tickers: string[], now: () => number = Date.now): LiveSpec<Board> {
  const heard = new Map<string, number>();   // 물은 티커 → 추정 메시지를 받은 시각(ms, 이 탭의 시계)
  const fresh = (it: BoardItem) => {
    const t = askedOf(tickers, it.ticker);
    const at = heard.get(t);
    return at !== undefined && now() - at < ESTIMATE_QUIET_MS;
  };
  return {
    channel: "quote",
    keys: tickers,
    apply: (prev, m) => (isEstimateMessage(m) ? applyEstimate(prev, m) : applyQuote(prev, m)),
    pauseWhen: (d) => !!d && d.items.length > 0 && d.items.every((it) => trading(it) || (it.state === "estimate" && fresh(it))),
    received: (d, m) => {
      if (!isEstimateMessage(m)) return false;
      heard.set(m.key, now());
      // 정규장 칸에 추정이 왔다 = 그 시장이 막 닫혔다 — 칸의 상태(live → estimate)는 서버가 정하니 REST 로 맞춘다
      return !!d && d.items.some((it) => trading(it) && mine(m, it));
    },
    reconcileMs: BOARD_RECONCILE_MS,
    // #690 지연 실측 — 정규장 칸만(추정 칸은 서버가 계산한 값이라 원천 지연이 아니다)
    stamps: (d) => d.items.filter(trading).map((it) => ({ key: it.ticker, id: it.price, at: it.fetched_at })),
  };
}

/** 추정 칸의 "n초 전" — 그 추정을 계산한 시각(fetched_at)부터. 시계가 어긋나 미래면 "방금" */
export function agoText(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const sec = Math.round((now - t) / 1000);
  if (sec < 5) return "방금";
  if (sec < 60) return `${sec}초 전`;
  const min = Math.floor(sec / 60);
  return min < 60 ? `${min}분 전` : null;
}
