import { useEffect, useRef } from "react";
import { latencyMode, recordRest, recordStream, type LatencyStamp } from "./marketLatency";
import { lastMessage, streamConnected, streamCovers, useMarketStream, type StreamChannel, type StreamMessage } from "./marketStream";
import { pending, remember, replayOver, type Applied } from "./liveReplay";
import { pushPollData, usePoll, type PollOptions, type PollSnapshot } from "./poll";

/**
 * #764 폴링 + 실시간 스트림. usePoll 과 같게 부르고, live 에 이 요청이 덮는 스트림 채널 · 키와
 * 메시지를 값에 반영하는 법(apply)을 준다.
 * - 그 키들이 이 연결에서 스트림으로 오고 있는 동안은 REST 를 부르지 않는다(reconcileMs 가 있으면 그만큼에 한 번만).
 * - 스트림이 끊기거나 그 채널이 degraded 면 REST 를 바로 한 번 부르고 원래 주기로 돌아간다.
 * - 첫 값은 늘 REST 로 맞춘다(스트림은 받은 값 위에 덧쓴다).
 */
export type LiveSpec<T> = {
  channel: StreamChannel;
  /** 이 요청이 덮는 스트림 키(quote 는 종목). 하나라도 이 연결에서 받은 적이 없으면 폴링을 쉬지 않는다.
   *  null 이면 채널 전체(드물게 오는 알림 — 채널이 살아 있으면 쉰다) */
  keys: string[] | null;
  /** 메시지 하나를 받은 값에 반영한다. 바꿀 것이 없으면 prev 나 undefined */
  apply: (prev: T, m: StreamMessage) => T | undefined;
  /** 받은 값으로 보아 쉬어도 되나(예: 시세판은 모든 칸이 장중일 때만) */
  pauseWhen?: (data: T | undefined) => boolean;
  /** 쉬는 동안에도 이만큼마다 REST 로 한 번 맞춘다(서버가 정하는 상태가 바뀌는 패널 · 놓칠 수 있는 알림) */
  reconcileMs?: number;
  /** 받은 값의 키 · 동일성 · 시각(시세는 가격 · fetched_at, 수급 · 선물은 fetched_at). #690 지연 실측(marketLatency.ts)과
   *  #866 늦은 REST 응답 위에 스트림 값을 다시 얹을 때 더 오래된 메시지를 거르는 데 쓴다. 없으면 시각 비교 없이 얹는다 */
  stamps?: (data: T) => LatencyStamp[];
};

export function useLivePoll<T>(
  key: string | null, fetch: () => Promise<T>, ms: number, options: PollOptions<T> = {}, live: LiveSpec<T> | null = null,
): PollSnapshot<T> & { refresh: () => void; live: boolean } {
  const liveRef = useRef(live);
  liveRef.current = live;
  const covered = (data: T | undefined, restAt: number): boolean => {
    const l = liveRef.current;
    return !!l && streamCovers(l.channel, l.keys) && (l.pauseWhen?.(data) ?? true) &&
      !(l.reconcileMs && Date.now() - restAt >= l.reconcileMs);
  };
  const optionsRef = useRef(options);
  optionsRef.current = options;
  // #866 적용한 스트림 메시지(liveReplay.remember) — REST 응답 위에 다시 얹는다
  const applied = useRef<Applied<StreamMessage>[]>([]);
  const poll = usePoll<T>(key, fetch, ms, {
    ...options,
    // #690 지연 실측(poll 모드): REST 가 막 가져온 값 — 같은 요청을 함께 쓰는 다른 화면이 먼저 구독했어도 여기로 온다
    onData: (d, meta) => {
      const l = liveRef.current;
      if (meta && l?.stamps && latencyMode() === "poll") {
        try { recordRest(l.channel, l.stamps(d), meta.startedAt); } catch { /* 계측은 화면을 막지 않는다 */ }
      }
      optionsRef.current.onData?.(d, meta);
    },
    // #866 요청 시점의 값을 담은 응답이 그 뒤 온 스트림 새 값을 덮지 않게(더 새 값이 이긴다 — liveReplay.ts)
    reconcile: (d, { startedAt }) => {
      const l = liveRef.current;
      if (!l || latencyMode() === "poll") return d;
      const since = key ? pending(applied.current, key, startedAt, !!l.stamps) : [];
      return since.length ? replayOver(d, since, l.apply, l.stamps) : d;
    },
    paused: (data, restAt) => covered(data, restAt) || !!options.paused?.(data, restAt),
  });
  const dataRef = useRef(poll.data);
  dataRef.current = poll.data;
  // #690 지연 실측: 스트림만으로 이어 간 값(화면 값과 따로 — 폴링 · 맞춤 REST 가 바꾼 것과 섞지 않는다)
  const shadow = useRef<{ key: string | null; data: T | undefined }>({ key: null, data: undefined });
  const { live: coveredKeys } = useMarketStream(live?.channel ?? "alert", live?.keys ?? null, (m) => {
    const l = liveRef.current;
    if (!key || !l || l.channel !== m.kind || (l.keys && !l.keys.includes(m.key))) return;
    if (l.stamps && latencyMode() !== "off") {
      try {
        const base = shadow.current.key === key ? shadow.current.data : dataRef.current;
        const next = base === undefined ? undefined : l.apply(base, m);
        if (base !== undefined && next !== undefined && next !== base) {
          shadow.current = { key, data: next };
          recordStream(l.channel, l.stamps(base), l.stamps(next), m.observed_at, m.source_at, streamConnected());
        }
      } catch { /* 계측은 화면을 막지 않는다 */ }
    }
    if (latencyMode() === "poll") return;   // 이 탭은 폴링으로만 그린다
    applied.current = remember(applied.current, key, m.key, m, !!l.stamps, Date.now());
    pushPollData<T>(key, (prev) => l.apply(prev, m));
  }, !!key && !!live && (live.keys === null || live.keys.length > 0));
  // 스트림 상태가 바뀔 때마다 REST 를 바로 한 번 — 끊기면(degraded) 그 패널만 폴링으로, 다시 살아나면(ready · 회복)
  // 그 사이 놓친 것(스트림은 알림을 다시 보내지 않는다)을 맞춘 뒤 쉰다. 처음 붙을 때는 usePoll 이 이미 부른다
  // #866 · #705 키가 있는 채널(시세 · 수급 · 선물)은 처음 live 가 될 때 다시 읽지 않는다: live 는 그 키의 메시지(새 연결의
  // 스냅숏 = 최신값)를 받은 뒤에야 켜지고, 첫 REST 가 그보다 늦게 와도 reconcile 이 그 메시지를 다시 얹는다. 알림(스냅숏
  // 없음)과 끊겼다 살아날 때(그 사이 버스가 놓친 것)는 그대로 읽는다 — 대시보드 첫 방문 요청이 하나 준다
  const was = useRef<{ key: string | null; live: boolean; once: boolean } | null>(null);
  const refresh = poll.refresh;
  const isLive = coveredKeys && (live?.pauseWhen?.(poll.data) ?? true);
  useEffect(() => {
    const same = !!key && was.current?.key === key;
    const once = same && !!was.current?.once;
    if (same && was.current!.live !== isLive) {
      const firstLive = isLive && !once && liveRef.current?.keys != null;
      if (!firstLive) refresh(true);
    }
    was.current = { key, live: isLive, once: once || isLive };
  }, [isLive, key, refresh]);
  return { ...poll, live: isLive };
}

/** "실시간 · 폴링" 표시와 지연 툴팁에 쓰는 글 — 마지막 메시지의 observed_at 기준 */
export function liveLabel(channel: StreamChannel, keys: string[] | null, on: boolean): { text: string; title: string } {
  if (!on) return { text: "폴링", title: "실시간 스트림이 없어 주기적으로 다시 읽어요" };
  const latest = (keys ?? []).map((k) => lastMessage(channel, k)).filter((m): m is StreamMessage => !!m)
    .map((m) => Date.parse(m.observed_at)).filter((t) => !Number.isNaN(t)).sort((a, b) => b - a)[0];
  if (latest == null) return { text: "실시간", title: "실시간 스트림으로 받는 중" };
  const sec = Math.max(0, Math.round((Date.now() - latest) / 1000));
  return { text: "실시간", title: `실시간 스트림 — 마지막 값 ${sec}초 전 확인(observed_at)` };
}
