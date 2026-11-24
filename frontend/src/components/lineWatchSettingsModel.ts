import type { LineWatch, LineWatchRule, LineWatchSupport, LineWatchChannels, NotifyChannelId } from "../api";

/** Seed an editable copy. Common settings remain on the server until a save explicitly switches modes. */
export function rulesFromCommon(watch: Pick<LineWatch, "tickers" | "timeframes" | "events">): LineWatchRule[] {
  return watch.tickers.map(ticker => ({
    ticker, enabled: watch.timeframes.length > 0, timeframes: [...watch.timeframes],
    events: { ...watch.events }, side: "both",
  }));
}

export function ruleProblem(rule: LineWatchRule, support: LineWatchSupport | undefined): string | null {
  if (rule.enabled && rule.timeframes.length === 0) return "켜려면 시간봉을 하나 이상 골라 주세요.";
  if (rule.timeframes.length === 0) return null;
  if (!support) return "시간봉 지원 여부를 확인하지 못했어요.";
  return rule.timeframes.some(tf => !support.supported.includes(tf))
    ? "지원하지 않는 시간봉을 빼 주세요." : null;
}

export function channelStatus(channels: LineWatchChannels | null, id: NotifyChannelId): string {
  const channel = channels?.channels.find(item => ("name" in item ? item.name : item.id) === id);
  if (!channel || !channels) return "상태 확인 불가";
  if ("name" in channel) {
    if (!channel.configured) return "승인된 수신처 없음";
    if (!channel.enabled) return "승인된 수신처 · 채널 꺼짐";
    if (channel.kinds.line_signal !== true) return "승인된 수신처 · 차트 선 알림 꺼짐";
    if (channel.kinds.tone_neutral !== true) return "승인된 수신처 · 참고 등급 꺼짐";
    return "승인된 수신처 · 차트 선 알림 켜짐";
  }
  if (!channels.can_edit) return "상태 확인 불가";
  if (!channel.configured) return "서버 설정 필요";
  if (channel.needs_reconnect) return "다시 연결 필요";
  if (!channel.connected) return "연결 안 됨";
  if (!channel.enabled) return "연결됨 · 채널 꺼짐";
  if (!channels.kinds.includes("line_signal")) return "차트 선 알림 지원 안 됨";
  if (channel.kinds.line_signal === false) return "연결됨 · 차트 선 알림 꺼짐";
  if (channel.kinds.tone_neutral === false) return "연결됨 · 참고 등급 꺼짐";
  return "연결됨 · 차트 선 알림 켜짐";
}
