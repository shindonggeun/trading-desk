import { liveLabel } from "../livePoll";
import type { StreamChannel } from "../marketStream";
import { useNow } from "../poll";

/** #764 패널 머리의 작은 표시 — "실시간"(스트림) · "폴링"(주기 읽기), 툴팁에 마지막 값의 지연(observed_at 기준) */
export default function LiveBadge({ channel, keys, on }: { channel: StreamChannel; keys: string[] | null; on: boolean }) {
  useNow(5_000, on);   // 툴팁의 "n초 전" 을 맞춘다
  const { text, title } = liveLabel(channel, keys, on);
  return <span className={`live-badge${on ? " on" : ""}`} title={title} aria-label={`${text} — ${title}`}>{on ? "● " : ""}{text}</span>;
}
