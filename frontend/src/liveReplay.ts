/**
 * #866 REST 응답 위에 그 요청을 보낸 뒤 받은 스트림 메시지를 다시 얹는다 — 도착 순서가 아니라 더 새 값이 이기게.
 *
 * 패널이 live 로 바뀌거나 맞출 때 부른 REST 는 요청 시점의 값을 담는다. 그 응답이 사이에 온 SSE 새 값 뒤에 도착하면
 * 새 값을 덮어, 다음 이벤트까지(수급은 최대 1분) 옛 값이 보였다. 그래서 응답을 받으면 그 사이의 메시지를 순서대로
 * 다시 적용한다. 값의 시각(stamps 의 at — 시세 fetched_at, 수급 · 선물 freshness.fetched_at)이 있으면, REST 가 가져온
 * 값보다 오래된 메시지는 건너뛴다(REST 가 더 새 값을 읽어 온 경우). 시각이 같으면 메시지가 이긴다.
 * 알림처럼 시각이 없는 채널은 apply 가 이미 중복을 거르므로 그대로 다시 얹는다.
 */

export type ReplayStamp = { key: string; at?: string | null };

export function replayOver<T, M>(data: T, messages: M[], apply: (prev: T, m: M) => T | undefined,
                                 stamps?: (data: T) => ReplayStamp[]): T {
  let out = data;
  for (const m of messages) {
    const next = apply(out, m);
    if (next === undefined || next === out) continue;
    if (stamps && goesBack(stamps(out), stamps(next))) continue;
    out = next;
  }
  return out;
}

/** 바뀐 키 중 하나라도 시각이 앞보다 이르면(더 오래된 값으로 돌아가면) true */
function goesBack(before: ReplayStamp[], after: ReplayStamp[]): boolean {
  const was = new Map(before.map((s) => [s.key, s.at]));
  for (const s of after) {
    const prev = was.get(s.key);
    if (!prev || !s.at || prev === s.at) continue;
    const a = Date.parse(s.at);
    const b = Date.parse(prev);
    if (!Number.isNaN(a) && !Number.isNaN(b) && a < b) return true;
  }
  return false;
}

/** 다시 얹을 메시지 기억 — key 는 이 패널의 요청(poll key), skey 는 스트림 키, at 은 받은 시각(ms) */
export type Applied<M> = { key: string; skey: string; at: number; m: M };

export const REPLAY_KEEP_MS = 60_000;   // 이보다 오래 받은 메시지는 잊는다(새 메시지가 올 때 정리)
export const REPLAY_MAX = 200;

/** 받은 메시지 하나를 기억에 더한다. 시각이 있는 채널(stamped: 최신값만 의미 — 시세 · 수급 · 선물)은 스트림 키마다
 *  마지막 하나만 둔다(30종목 시세판의 초당 메시지가 상한을 채워 다른 종목을 밀어내지 않게). 알림은 모두 둔다 */
export function remember<M>(list: Applied<M>[], key: string, skey: string, m: M, stamped: boolean,
                            now: number): Applied<M>[] {
  const kept = list.filter((a) => now - a.at < REPLAY_KEEP_MS && !(stamped && a.key === key && a.skey === skey));
  return [...kept, { key, skey, at: now, m }].slice(-REPLAY_MAX);
}

/** 그 요청(startedAt 에 보냄)의 응답 위에 다시 얹을 메시지, 받은 순서대로. 시각이 있으면 기억 전부 — 요청 전에 받은
 *  것이라도 서버가 더 옛 캐시를 줄 수 있고, 더 오래된 메시지는 replayOver 의 시각 비교가 거른다. 알림은 요청 뒤의 것만 */
export function pending<M>(list: Applied<M>[], key: string, startedAt: number, stamped: boolean): M[] {
  return list.filter((a) => a.key === key && (stamped || a.at >= startedAt)).map((a) => a.m);
}
