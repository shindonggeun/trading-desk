import { useCallback, useEffect, useRef, useState } from "react";

/**
 * #626 주기적으로 다시 읽기는 모두 여기서 — 화면 코드는 setInterval 을 직접 쓰지 않는다.
 * - 같은 key 는 요청 하나를 함께 쓴다(먼저 온 구독자의 fetch, 주기는 구독자 중 가장 짧은 것).
 * - 탭이 가려지면 쉬고(pauseHidden, 기본 켬 — 모든 구독자가 원할 때만), 다시 보이면 쉬는 동안 건너뛴 차례를 바로 채운다.
 * - 실패하면 다음 차례를 2 · 4 · 8배로 늦추되 첫 실패 뒤엔 RETRY_CAP_MS(2분)를 넘기지 않고(이어지면 4 · 8 · 16분)(backoffOnError, 기본 켬) 성공하면 원래 주기로.
 *   주기가 2분보다 긴 카드(10분 · 30분 · 몇 시간)는 실패하면 오히려 2분 뒤 다시 본다(#678 — 502 한 번에 몇 시간 밀리지 않게).
 * - 요청이 끝난 뒤 다음 차례를 잡아 겹쳐 부르지 않는다. 화면을 떠난 구독자에게는 늦게 온 응답을 주지 않는다.
 */

export type PollSnapshot<T> = {
  data: T | undefined;
  error: unknown;
  /** 마지막으로 성공한 시각(ms) */
  at: number | null;
  /** 마지막 요청이 실패했다(앞서 받은 data 는 그대로) */
  failing: boolean;
};

export type PollOptions<T> = {
  /** 탭이 가려지면 쉰다(기본 true). 다른 창(PiP 작은 창)에서 보는 것은 false */
  pauseHidden?: boolean;
  /** 실패 뒤 다음 차례를 늦춘다(기본 true) */
  backoffOnError?: boolean;
  /** 처음 요청을 이만큼 미룬다(여러 카드가 한꺼번에 부르지 않게) */
  delay?: number;
  /** meta 는 REST 응답으로 막 받았을 때만 — 그 요청을 보낸 시각(ms, #690 지연 실측) */
  onData?: (d: T, meta?: { startedAt: number }) => void;
  onError?: (e: unknown) => void;
  /** #764 true 면 이번 차례를 건너뛴다(값이 이미 있을 때만 — 실시간 스트림이 대신 채우는 동안). livePoll.ts.
   *  restAt: 이 key 를 마지막으로 REST 로 받은 시각(ms, 스트림이 밀어 넣은 값은 세지 않는다) */
  paused?: (data: T | undefined, restAt: number) => boolean;
};

const MAX_BACKOFF = 8;
/** #678 실패 뒤 다시 묻기까지 가장 오래 기다리는 시간 */
export const RETRY_CAP_MS = 120_000;

type Sub<T> = {
  fetch: { current: () => Promise<T> };
  opts: { current: PollOptions<T> };
  ms: number;
  set: (s: PollSnapshot<T>) => void;
};

type Entry<T> = {
  subs: Set<Sub<T>>;
  snap: PollSnapshot<T>;
  timer?: number;
  inflight: boolean;
  fails: number;
  missed: boolean;
  /** #764 마지막으로 REST 로 받은 시각 */
  restAt: number;
};

const entries = new Map<string, Entry<unknown>>();

const hidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";
const pauses = (e: Entry<unknown>) => [...e.subs].every((s) => s.opts.current.pauseHidden !== false);
const period = (e: Entry<unknown>) => Math.min(...[...e.subs].map((s) => s.ms));

function schedule(e: Entry<unknown>, wait?: number) {
  window.clearTimeout(e.timer);
  e.timer = undefined;
  if (!e.subs.size) return;
  const base = period(e);
  const backoff = e.fails > 0 && [...e.subs].some((s) => s.opts.current.backoffOnError !== false);
  // 실패가 이어지면 상한도 2 · 4 · 8 · 16분으로 늘린다(하루 종일 막힌 원천을 2분마다 두드리지 않게, 주기 × 8 을 넘지 않게)
  const cap = RETRY_CAP_MS * 2 ** Math.max(0, e.fails - 1);
  const delay = wait ?? (backoff ? Math.min(base * Math.min(2 ** e.fails, MAX_BACKOFF), cap) : base);
  e.timer = window.setTimeout(() => run(e), delay);
}

function run(e: Entry<unknown>, force = false) {
  if (!e.subs.size || e.inflight) return;
  if (hidden() && pauses(e)) {
    e.missed = true;   // 보이게 되면 바로 채운다
    schedule(e);
    return;
  }
  e.missed = false;
  if (!force && e.snap.at != null && [...e.subs].every((s) => s.opts.current.paused?.(e.snap.data, e.restAt))) {
    schedule(e);   // #764 스트림이 채우는 중 — 끊기면 livePoll 이 바로 다시 부른다
    return;
  }
  const first = e.subs.values().next().value as Sub<unknown>;
  e.inflight = true;
  const startedAt = Date.now();
  let p: Promise<unknown>;
  try {
    p = first.fetch.current();
  } catch (err) {
    p = Promise.reject(err);
  }
  p.then(
    (data) => {
      e.fails = 0;
      e.restAt = Date.now();
      e.snap = { data, error: null, at: Date.now(), failing: false };
      for (const s of [...e.subs]) { s.opts.current.onData?.(data, { startedAt }); s.set(e.snap); }
    },
    (error) => {
      e.fails += 1;
      e.snap = { ...e.snap, error, failing: true };
      for (const s of [...e.subs]) { s.opts.current.onError?.(error); s.set(e.snap); }
    },
  ).finally(() => {
    e.inflight = false;
    schedule(e);
  });
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (hidden()) return;
    for (const e of entries.values()) if (e.missed && !e.inflight) run(e);
  });
}

/**
 * key 의 요청을 ms 마다 다시 부른다. key 가 null 이면 쉰다(조건이 안 맞을 때).
 * 받은 값은 돌려주는 data 로 쓰거나 onData 로 그 화면의 상태에 옮긴다.
 */
export function usePoll<T>(key: string | null, fetch: () => Promise<T>, ms: number, options: PollOptions<T> = {}): PollSnapshot<T> & { refresh: (force?: boolean) => void } {
  const fetchRef = useRef(fetch);
  fetchRef.current = fetch;
  const optsRef = useRef(options);
  optsRef.current = options;
  const [snap, setSnap] = useState<PollSnapshot<T>>(() =>
    (key && (entries.get(key)?.snap as PollSnapshot<T> | undefined)) || { data: undefined, error: null, at: null, failing: false });
  const subRef = useRef<Sub<T> | null>(null);

  useEffect(() => {
    if (!key) {
      setSnap({ data: undefined, error: null, at: null, failing: false });
      return;
    }
    let e = entries.get(key) as Entry<T> | undefined;
    const fresh = !e;
    if (!e) {
      e = { subs: new Set(), snap: { data: undefined, error: null, at: null, failing: false }, inflight: false, fails: 0, missed: false, restAt: 0 };
      entries.set(key, e as Entry<unknown>);
    }
    const sub: Sub<T> = { fetch: fetchRef, opts: optsRef, ms, set: setSnap };
    subRef.current = sub;
    e.subs.add(sub);
    const entry = e as Entry<unknown>;
    // #678 이 key 의 지금 값으로 바꾼다(새 entry 면 빈 값) — key 를 바꾼 뒤 예전 key 의 값 · 실패 표시가 남지 않게
    setSnap(e.snap);
    if (fresh) {
      schedule(entry, optsRef.current.delay ?? 0);
    } else {
      // 이미 받은 값이 있으면 이 구독자에게도 바로 — 주기보다 오래됐으면 지금 다시 부른다(실패 중이면 늦춘 차례 그대로)
      if (e.snap.at != null && e.snap.data !== undefined) optsRef.current.onData?.(e.snap.data);
      if (e.inflight || e.fails > 0) { /* 끝나면 · 늦춘 차례에 */ }
      else if (e.snap.at == null || Date.now() - e.snap.at >= ms) schedule(entry, 0);
      else schedule(entry, Math.max(0, e.snap.at + period(entry) - Date.now()));
    }
    return () => {
      e!.subs.delete(sub);
      subRef.current = null;
      if (!e!.subs.size) {
        window.clearTimeout(e!.timer);
        entries.delete(key);
      } else if (!e!.inflight && e!.fails === 0 && e!.snap.at != null) {
        // #678 남은 구독자의 주기로, 마지막으로 받은 때부터 — 구독자가 붙었다 떨어질 때마다 차례가 밀리지 않게
        schedule(entry, Math.max(0, e!.snap.at + period(entry) - Date.now()));
      }
    };
    // ms 는 아래 effect 가 따로 맞춘다(바뀌어도 다시 부르지 않게)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // 주기만 바뀌면(장중 ↔ 장외) 다시 부르지 않고 다음 차례만 다시 잡는다
  useEffect(() => {
    const sub = subRef.current;
    if (!key || !sub || sub.ms === ms) return;
    sub.ms = ms;
    const e = entries.get(key);
    if (e && !e.inflight) schedule(e);
  }, [key, ms]);

  const refresh = useCallback((force = false) => {
    const e = key ? entries.get(key) : undefined;
    if (e && !e.inflight) { window.clearTimeout(e.timer); run(e, force); }
  }, [key]);
  return { ...snap, refresh };
}

/**
 * #764 key 의 값을 밖에서(실시간 스트림) 바꾼다 — 구독자 모두에게 onData · 새 값. update 가 undefined 나 같은 값을 주면 그대로.
 * 아직 받은 값이 없으면 바꾸지 않는다(첫 값은 REST 가 맞춘다).
 */
export function pushPollData<T>(key: string, update: (prev: T) => T | undefined): void {
  const e = entries.get(key) as Entry<T> | undefined;
  if (!e || e.snap.data === undefined) return;
  const data = update(e.snap.data);
  if (data === undefined || data === e.snap.data) return;
  e.snap = { data, error: null, at: Date.now(), failing: false };
  for (const s of [...e.subs]) { s.opts.current.onData?.(data); s.set(e.snap); }
}

/** ms 마다 바뀌는 지금 시각(경과 · "n분 전" 표시용). enabled 가 false 면 멈춘다 */
export function useNow(ms: number, enabled = true): number {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    return every(() => setNow(Date.now()), ms);
  }, [ms, enabled]);
  return now;
}

/** 화면 밖 코드(차트 좌표 맞추기 · 안내 투어)의 고정 주기 — 멈추는 함수를 돌려준다 */
export function every(fn: () => void, ms: number): () => void {
  const id = window.setInterval(fn, ms);
  return () => window.clearInterval(id);
}

/** 마지막 갱신이 실패했을 때 카드에 작게: "14:05 갱신 · 다시 시도 중" */
export function staleText(s: Pick<PollSnapshot<unknown>, "at" | "failing">): string | null {
  if (!s.failing) return null;
  if (s.at == null) return "불러오지 못했어요 · 다시 시도 중";
  const d = new Date(s.at);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())} 갱신 · 다시 시도 중`;
}
