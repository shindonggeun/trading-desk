import type { DeskEvent, Run } from "./types";

export type QueueReason = { text: string; title: string; tone: "wait" | "warn" };

function waitsForAnalyst(events: readonly DeskEvent[]): boolean {
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (event.type === "status" && event.content === "queued") return event.data?.waiting_for === "analyst";
  }
  return false;
}

export function queueReason(run: Pick<Run, "status" | "queue">, events: readonly DeskEvent[]): QueueReason | null {
  if (run.status !== "queued") return null;

  const queue = run.queue;
  const counts = queue ? `앞에 ${queue.ahead}건 · 실행 중 ${queue.running}건` : null;
  if (queue?.analyst === "down") {
    return {
      tone: "warn", text: `분석 서비스 응답 없음 · ${counts}`,
      title: "분석 서비스가 대기열을 읽기 시작하면 이어서 진행해요.",
    };
  }

  const waitingForAnalyst = queue?.analyst === "up" || waitsForAnalyst(events);
  return waitingForAnalyst
    ? { tone: "wait", text: counts ?? "대기열에 등록됨 · 분석 서비스가 가져가길 기다리는 중",
        title: "분석 서비스가 대기열에서 차례대로 가져가요." }
    : { tone: "wait", text: counts ?? "대기열에 등록됨 · 곧 시작해요",
        title: "앞의 분석이나 같은 종목 분석이 끝나면 시작해요." };
}
