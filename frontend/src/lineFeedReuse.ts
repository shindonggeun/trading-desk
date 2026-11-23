/** #705 선 알림을 다시 읽지 않아도 되는 때 — 알림 피드가 같은 내용으로 곧바로 다시 읽혔을 때(첫 화면: 스트림이 붙으며 한 번 더) */
export const SAME_FEED_MS = 5_000;

export type LastRead<T> = { date: string; feed: string; at: number; items: T };

/** 방금(SAME_FEED_MS 안) 같은 날짜 · 같은 피드 내용으로 읽은 결과가 있으면 그것, 아니면 null(다시 읽는다) */
export function reusable<T>(last: LastRead<T> | null, date: string, feed: string, now: number): T | null {
  return last && last.date === date && last.feed === feed && now - last.at < SAME_FEED_MS ? last.items : null;
}
