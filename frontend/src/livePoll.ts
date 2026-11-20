import { useEffect, useRef } from "react";
import { lastMessage, streamLive, useMarketStream, type StreamChannel, type StreamMessage } from "./marketStream";
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
  /** 쉬는 동안에도 이만큼마다 REST 로 한 번 맞춘다(서버가 정하는 상태가 바뀌는 패널) */
  reconcileMs?: number;
};

export function useLivePoll<T>(
  key: string | null, fetch: () => Promise<T>, ms: number, options: PollOptions<T> = {}, live: LiveSpec<T> | null = null,
): PollSnapshot<T> & { refresh: () => void; live: boolean } {
  const liveRef = useRef(live);
  liveRef.current = live;
  const restAt = useRef(0);
  const covered = (data: T | undefined): boolean => {
    const l = liveRef.current;
    if (!l || !keysLive(l)) return false;
    if (l.pauseWhen && !l.pauseWhen(data)) return false;
    return !(l.reconcileMs && Date.now() - restAt.current >= l.reconcileMs);
  };
  const poll = usePoll<T>(key, () => { restAt.current = Date.now(); return fetch(); }, ms, {
    ...options,
    paused: (data) => covered(data) || !!options.paused?.(data),
  });
  const tickers = live?.channel === "quote" ? live.keys ?? [] : [];
  const { live: channelLive } = useMarketStream(live?.channel ?? "alert", tickers, (m) => {
    const l = liveRef.current;
    if (!key || !l || (l.keys && !l.keys.includes(m.key))) return;
    pushPollData<T>(key, (prev) => l.apply(prev, m));
  }, !!key && !!live && (live.keys === null || live.keys.length > 0));
  // 살아 있다가 끊기면(또는 degraded) REST 를 바로 한 번 — 그 패널만 폴링으로
  const was = useRef(false);
  const refresh = poll.refresh;
  useEffect(() => {
    if (was.current && !channelLive) refresh();
    was.current = channelLive;
  }, [channelLive, refresh]);
  return { ...poll, live: !!live && channelLive && keysLive(live) };
}

function keysLive(l: Pick<LiveSpec<unknown>, "channel" | "keys">): boolean {
  if (l.keys === null) return streamLive(l.channel);
  return l.keys.length > 0 && l.keys.every((k) => streamLive(l.channel, k));
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
