import type { Run, RunRead, RunStatusOnly } from "./types";

export function isFullRun(run: RunRead): run is Run {
  return "ticker" in run && typeof run.ticker === "string";
}

const STATUS_NOTICE: Record<RunStatusOnly["status"], string> = {
  progress: "분석이 진행 중입니다.",
  completed: "분석이 완료됐습니다. 이 화면에서는 결과를 볼 수 없습니다.",
  failed: "분석을 완료하지 못했습니다.",
};

export function statusOnlyNotice(run: RunStatusOnly): string {
  return STATUS_NOTICE[run.status] ?? "이 화면에서는 분석을 볼 수 없습니다.";
}
