import { useEffect, useState } from "react";
import { api, ApiError, type DeskStatus, type StatusJob } from "./api";

/** #624 데이터 원천 상태(GET /api/status, #623) — 설정 칸과 상단바 점이 요청 하나를 나눠 쓴다.
 *  화면이 보이는 동안 POLL_MS 마다 다시 읽는다. DEV 에서 404(백엔드에 #623 이 없을 때)면 예시. */
const POLL_MS = 120_000;

export const STATE_TEXT: Record<StatusJob["state"], { mark: string; text: string; cls: string }> = {
  fresh: { mark: "●", text: "정상", cls: "st-ok" },
  running: { mark: "▶", text: "도는 중", cls: "st-ok" },
  starting: { mark: "…", text: "시작 중", cls: "st-muted" },
  stale: { mark: "◐", text: "늦음", cls: "st-warn" },
  error: { mark: "✕", text: "오류", cls: "st-bad" },
  stopped: { mark: "■", text: "멈춤", cls: "st-bad" },
  off: { mark: "–", text: "꺼짐", cls: "st-muted" },
};
/** 상단바에 점을 띄울 상태 */
export const TROUBLE: StatusJob["state"][] = ["stale", "error", "stopped"];

type Feed = { data: DeskStatus | null; error: string | null };
const shared: { feed: Feed; listeners: Set<(f: Feed) => void>; timer?: number; inflight?: Promise<void> } = {
  feed: { data: null, error: null }, listeners: new Set(),
};

function publish(feed: Feed) {
  shared.feed = feed;
  shared.listeners.forEach((l) => l(feed));
}

function load(): Promise<void> {
  if (shared.inflight) return shared.inflight;
  shared.inflight = api.status()
    .then((d) => publish({ data: d, error: null }))
    .catch((e) => {
      if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) return publish({ data: fakeStatus(), error: null });
      publish({ data: shared.feed.data, error: e instanceof ApiError && e.status === 404 ? "준비 중" : "불러오지 못했습니다" });
    })
    .finally(() => { shared.inflight = undefined; });
  return shared.inflight;
}

function schedule() {
  window.clearTimeout(shared.timer);
  if (!shared.listeners.size) return;
  shared.timer = window.setTimeout(async () => {
    if (document.visibilityState === "visible") await load();
    schedule();
  }, POLL_MS);
}

export function useDeskStatus(): Feed & { reload: () => void } {
  const [feed, setFeed] = useState<Feed>(shared.feed);
  useEffect(() => {
    shared.listeners.add(setFeed);
    if (shared.listeners.size === 1) { void load(); schedule(); }
    const onVisible = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      shared.listeners.delete(setFeed);
      document.removeEventListener("visibilitychange", onVisible);
      if (!shared.listeners.size) window.clearTimeout(shared.timer);
    };
  }, []);
  return { ...feed, reload: () => { void load(); } };
}

/** "방금" · "12분 전" · "3시간 전" · "2일 전" (미래면 "12분 뒤") */
export function relTime(iso: string | null, now = Date.now()): string {
  if (!iso) return "—";
  const sec = (new Date(iso).getTime() - now) / 1000;
  const a = Math.abs(sec);
  const tail = sec > 0 ? "뒤" : "전";
  if (a < 60) return sec > 0 ? "곧" : "방금";
  if (a < 3600) return `${Math.round(a / 60)}분 ${tail}`;
  if (a < 48 * 3600) return `${Math.round(a / 3600)}시간 ${tail}`;
  return `${Math.round(a / 86400)}일 ${tail}`;
}

/** DEV 예시 — 여러 상태가 다 보이게 */
export function fakeStatus(): DeskStatus {
  const at = (min: number) => new Date(Date.now() + min * 60_000).toISOString();
  const job = (x: Partial<StatusJob> & Pick<StatusJob, "name" | "label" | "source" | "state">): StatusJob => ({
    parent: null, runs: 12, errors: 0, last_run: at(-3), last_ok: at(-3), last_error: null, last_error_at: null,
    next_due: at(12), interval_sec: 900, ...x,
  });
  return {
    now: new Date().toISOString(),
    jobs: [
      job({ name: "listing", label: "종목 목록", source: "토스", state: "fresh", interval_sec: 3600 }),
      job({ name: "news", label: "뉴스 흐름", source: "GDELT · 구글 뉴스", state: "fresh" }),
      job({ name: "macro_news", label: "거시 · 정책 뉴스", source: "GDELT · 구글 뉴스", parent: "news", state: "error",
            errors: 3, last_ok: at(-95), last_error: "HTTPStatusError", last_error_at: at(-4) }),
      job({ name: "sector_news", label: "섹터 · 인물 이슈", source: "GDELT · 구글 뉴스", parent: "news", state: "fresh" }),
      job({ name: "krx", label: "KRX 일별 이력", source: "KRX OPEN API", state: "stale", last_ok: at(-300), next_due: at(-200) }),
      job({ name: "funds", label: "13F 기관 보유", source: "SEC EDGAR", state: "fresh", interval_sec: 86400, next_due: at(600) }),
      job({ name: "prewarm", label: "대시보드 미리 읽기", source: "야후 · 토스 · 한투", state: "running", last_ok: null, next_due: null }),
      job({ name: "kis_stream", label: "실시간 시세 스트림", source: "한투(KIS)", state: "stopped", last_ok: null,
            last_error: "ConnectionClosed", last_error_at: at(-30), next_due: null }),
      job({ name: "reports", label: "정기 리포트", source: "ChatGPT · 야후", state: "off", runs: 0, last_run: null, last_ok: null, next_due: null }),
    ],
    cooldowns: [
      { name: "chatgpt", label: "ChatGPT 플랜(분석 · 채팅 · 아침 시황)", until: at(18), left_sec: 18 * 60 },
      { name: "gdelt", label: "GDELT(뉴스 — 그동안 구글 뉴스)", until: null, left_sec: 0 },
    ],
  };
}
