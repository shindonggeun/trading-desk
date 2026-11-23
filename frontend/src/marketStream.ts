import { useEffect, useRef, useSyncExternalStore } from "react";
import { latencyMode } from "./marketLatency";
import { sameTicker } from "./ticker";

/**
 * #764 시장 실시간 스트림(GET /api/stream/market, #690 · docs/market-stream.md)을 탭 하나에 연결 하나로 나눠 쓴다.
 * - 구독자(채널 · 종목)를 모아 쿼리 하나로 연다. 모음이 바뀌면 잠깐 뒤 다시 연다(종목이 바뀌면 다시 연다는 계약).
 * - 데이터 이벤트(quote · flow · futures · alert, 메시지 v1)는 event_id 로 한 번만 반영하고, 키마다 마지막 값을 둔다
 *   (늦게 붙은 구독자에게 바로 준다).
 * - 채널이 "살아 있다" = 연결됨 · ready 받음 · degraded 에 없음 · 45초 안에 무엇이든 받음. 키는 이 연결에서
 *   한 번이라도 받은 것만 살아 있다고 본다(스트림이 보내지 않는 키는 폴링이 맡는다).
 * - 끊기거나 reset · 45초 무소식이면 닫고, 지터를 둔 백오프로 다시 연다. 가려진 탭에서는 닫고 보이면 다시 연다.
 * 폴링과의 연결은 livePoll.ts.
 */

export type StreamChannel = "quote" | "flow" | "futures" | "alert";
export type StreamMessage<P = unknown> = {
  v: 1;
  kind: StreamChannel;
  key: string;
  event_id: string;
  source: string;
  source_at: string | null;
  observed_at: string;
  payload: P;
};

export const STREAM_PATH = "/api/stream/market";
const CHANNELS: StreamChannel[] = ["quote", "flow", "futures", "alert"];
/** 하트비트 15초의 세 배 — 그보다 오래 아무것도 오지 않으면 죽은 연결 */
export const DEAD_MS = 45_000;
const REOPEN_DEBOUNCE_MS = 300;
const RETRY_BASE_MS = 3_000;
const RETRY_CAP_MS = 120_000;
const SEEN_MAX = 2_000;
const MAX_TICKERS = 50;

type Listener = (m: StreamMessage) => void;
type Sub = { channel: StreamChannel; tickers: string[]; onMessage: Listener };

type State = {
  es: EventSource | null;
  url: string | null;
  ready: boolean;
  degraded: Set<string>;
  lastAt: number;
  /** 이 연결에서 받은 키(채널|키) */
  seenKeys: Set<string>;
  /** 마지막 값(채널|키) — 늦게 붙은 구독자에게 */
  last: Map<string, StreamMessage>;
  seenIds: Set<string>;
  fails: number;
  retryTimer?: number;
  reopenTimer?: number;
  watchdog?: number;
};

const subs = new Set<Sub>();
const watchers = new Set<() => void>();
const st: State = { es: null, url: null, ready: false, degraded: new Set(), lastAt: 0, seenKeys: new Set(), last: new Map(), seenIds: new Set(), fails: 0 };

/** 테스트 · 개발용: EventSource 를 바꿔 끼운다(가짜 스트림) */
let makeSource: (url: string) => EventSource = (url) => new EventSource(url, { withCredentials: true });
export function setEventSourceFactory(f: ((url: string) => EventSource) | null): void {
  makeSource = f ?? ((url) => new EventSource(url, { withCredentials: true }));
  close();
  wantOpen();
}

const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
const notify = () => { for (const w of [...watchers]) w(); };
const slot = (channel: string, key: string) => `${channel}|${key}`;

function wanted(): string | null {
  if (!subs.size || typeof window === "undefined" || typeof EventSource === "undefined") return null;
  const channels = new Set<StreamChannel>();
  const tickers = new Set<string>();
  for (const s of subs) {
    if (s.channel === "quote" && !s.tickers.length) continue;
    channels.add(s.channel);
    if (s.channel === "quote") for (const t of s.tickers) tickers.add(t.toUpperCase());
  }
  if (!channels.size) return null;
  const ch = CHANNELS.filter((c) => channels.has(c));
  const tk = [...tickers].sort().slice(0, MAX_TICKERS);   // 맨 코드도 그대로 — 서버가 표기를 맞춘다
  const q = new URLSearchParams({ channels: ch.join(",") });
  if (ch.includes("quote")) q.set("tickers", tk.join(","));
  return `${STREAM_PATH}?${q.toString()}`;
}

function close(): void {
  window.clearTimeout(st.reopenTimer);
  window.clearTimeout(st.retryTimer);
  window.clearInterval(st.watchdog);
  st.reopenTimer = st.retryTimer = st.watchdog = undefined;
  if (st.es) st.es.close();
  const was = st.ready;
  st.es = null;
  st.url = null;
  st.ready = false;
  st.degraded = new Set();
  st.seenKeys = new Set();
  if (was) notify();
}

/** 끊김 — 닫고 폴링으로 돌린 뒤(구독자에게 알림) 지터를 둔 백오프로 다시 연다 */
function drop(): void {
  close();
  st.fails += 1;
  const base = Math.min(RETRY_BASE_MS * 2 ** Math.min(st.fails - 1, 6), RETRY_CAP_MS);
  const wait = base / 2 + Math.random() * base / 2;
  st.retryTimer = window.setTimeout(() => { st.retryTimer = undefined; wantOpen(); }, wait);
  notify();
}

function parse(ev: MessageEvent): Record<string, unknown> | null {
  try {
    const d = JSON.parse(ev.data);
    return d && typeof d === "object" && d.v === 1 ? d : null;   // 모르는 버전은 무시(폴링 유지)
  } catch {
    return null;
  }
}

/** 시세 키를 구독자가 쓰는 표기로 — 서버는 맨 코드(005930)를 005930.KS 로 바꿔 보낸다(#477 전 즐겨찾기) */
function quoteKeys(key: string): string[] {
  const out = new Set<string>([key]);
  for (const s of subs) if (s.channel === "quote") for (const t of s.tickers) if (sameTicker(t, key)) out.add(t);
  return [...out];
}

/** watchdog 이 닫기 전에 프레임이 다시 오면 같은 연결에서 실시간 상태를 복구한다. */
function markActivity(): boolean {
  const now = Date.now();
  const recovered = st.ready && now - st.lastAt > DEAD_MS;
  st.lastAt = now;
  return recovered;
}

function onData(ev: MessageEvent): void {
  const raw = parse(ev) as StreamMessage | null;
  if (!raw || !CHANNELS.includes(raw.kind) || typeof raw.key !== "string") return;
  const recovered = markActivity();
  const keys = raw.kind === "quote" ? quoteKeys(raw.key) : [raw.key];
  let first = false;
  for (const key of keys) {
    const k = slot(raw.kind, key);
    first = first || !st.seenKeys.has(k);
    st.seenKeys.add(k);   // 재접속 스냅숏이 이미 본 id 여도 이 연결에서 오는 키다
  }
  if (st.ready && (first || recovered)) notify();
  if (raw.event_id) {
    if (st.seenIds.has(raw.event_id)) return;          // 재접속 스냅숏과 겹친 이벤트 — 값은 이미 반영했다
    st.seenIds.add(raw.event_id);
    if (st.seenIds.size > SEEN_MAX) st.seenIds = new Set([...st.seenIds].slice(-SEEN_MAX / 2));
  }
  for (const key of keys) {
    const m = key === raw.key ? raw : { ...raw, key };
    st.last.set(slot(m.kind, key), m);
    for (const s of [...subs]) {
      if (s.channel !== m.kind) continue;
      if (m.kind === "quote" && !s.tickers.includes(key)) continue;
      s.onMessage(m);
    }
  }
}

function onHealth(ev: MessageEvent): void {
  const d = parse(ev);
  if (!d || !Array.isArray(d.degraded)) return;
  const recovered = markActivity();
  const next = new Set(d.degraded.map(String));
  const same = next.size === st.degraded.size && [...next].every((c) => st.degraded.has(c));
  st.degraded = next;
  if (!same || recovered) notify();
}

function open(url: string): void {
  const es = makeSource(url);
  st.es = es;
  st.url = url;
  st.ready = false;
  st.lastAt = Date.now();
  for (const c of CHANNELS) es.addEventListener(c, onData as EventListener);
  es.addEventListener("ready", ((ev: MessageEvent) => {
    const d = parse(ev);
    if (!d) return;
    st.lastAt = Date.now();
    st.ready = true;
    st.fails = 0;
    st.degraded = new Set(Array.isArray(d.degraded) ? (d.degraded as unknown[]).map(String) : []);
    notify();
  }) as EventListener);
  es.addEventListener("degraded", onHealth as EventListener);
  es.addEventListener("heartbeat", onHealth as EventListener);
  es.addEventListener("reset", () => drop());
  // 401 · 503 · 네트워크 — 브라우저의 자동 재연결 대신 백오프(그동안은 폴링)
  es.onerror = () => { if (st.es === es) drop(); };
  st.watchdog = window.setInterval(() => {
    if (st.es === es && Date.now() - st.lastAt > DEAD_MS) drop();
  }, 5_000);
}

/** 구독 모음에 맞춰 연결을 연다 · 다시 연다 · 닫는다(바로 하지 않고 잠깐 모아서) */
function wantOpen(): void {
  if (typeof window === "undefined") return;
  window.clearTimeout(st.reopenTimer);
  st.reopenTimer = window.setTimeout(() => {
    st.reopenTimer = undefined;
    const url = hidden() ? null : wanted();
    if (url === st.url) return;
    if (st.retryTimer !== undefined && url) return;   // 백오프 중 — 그 차례에 새 모음으로 연다
    close();
    if (url) open(url);
    notify();
  }, REOPEN_DEBOUNCE_MS);
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => wantOpen());
}

/** 그 채널(과 키)이 지금 스트림으로 오고 있나 — 폴링을 쉬어도 되나 */
export function streamLive(channel: StreamChannel, key?: string): boolean {
  if (latencyMode() === "poll") return false;   // #690 이 탭은 폴링으로 그리고 스트림은 비교용으로만 받는다
  return streamReady(channel, key);
}

/** #690 지연 실측: 연결 · ready(재접속 스냅숏이 끝남) · 45초 안에 받음 — degraded 와 별개 */
export function streamConnected(): boolean {
  return !!st.es && st.ready && Date.now() - st.lastAt <= DEAD_MS;
}

/** 연결 · ready · degraded 아님 · 45초 안에 받음(· 그 키를 받은 적 있음) — 화면이 스트림을 쓰는지와 별개 */
function streamReady(channel: StreamChannel, key?: string): boolean {
  if (!st.es || !st.ready || st.degraded.has(channel)) return false;
  if (Date.now() - st.lastAt > DEAD_MS) return false;
  return key == null || st.seenKeys.has(slot(channel, key));
}

export function streamCovers(channel: StreamChannel, keys: string[] | null): boolean {
  if (keys === null) return streamLive(channel);
  return keys.length > 0 && keys.every((key) => streamLive(channel, key));
}

/** 그 채널 · 키의 마지막 메시지(지연 표시용) */
export function lastMessage(channel: StreamChannel, key: string): StreamMessage | undefined {
  return st.last.get(slot(channel, key));
}

/** 스트림 상태가 바뀌면(연결 · ready · degraded · 새 키) 부른다. 멈추는 함수를 돌려준다 */
export function watchStream(fn: () => void): () => void {
  watchers.add(fn);
  return () => { watchers.delete(fn); };
}

/** 구독 — 이 채널(quote 는 종목들)의 메시지를 onMessage 로. 멈추는 함수를 돌려준다 */
export function subscribe(channel: StreamChannel, tickers: string[], onMessage: Listener): () => void {
  const sub: Sub = { channel, tickers, onMessage };
  subs.add(sub);
  // 이미 받은 마지막 값을 바로
  for (const m of st.last.values()) {
    if (m.kind !== channel || !st.seenKeys.has(slot(m.kind, m.key))) continue;
    if (channel === "quote" && !tickers.includes(m.key)) continue;
    onMessage(m);
  }
  wantOpen();
  return () => {
    subs.delete(sub);
    wantOpen();
  };
}

/**
 * 화면용: channel(quote 는 tickers)을 구독해 메시지를 onMessage 로 넘기고, 그 채널이 살아 있는지 돌려준다.
 * enabled 가 false 면 구독하지 않는다.
 */
export function useMarketStream(channel: StreamChannel, keys: string[] | null, onMessage: Listener, enabled = true): { live: boolean } {
  const cb = useRef(onMessage);
  cb.current = onMessage;
  const live = useSyncExternalStore(watchStream, () => enabled && streamCovers(channel, keys));
  const tk = channel === "quote" ? keys?.join(",") ?? "" : "";
  useEffect(() => {
    if (!enabled) return;
    return subscribe(channel, tk ? tk.split(",") : [], (m) => cb.current(m));
  }, [channel, tk, enabled]);
  return { live };
}
