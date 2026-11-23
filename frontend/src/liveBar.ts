import { useEffect, useState, type RefObject } from "react";
import type { CandleLive } from "./api";

/**
 * #654 분석 차트의 마지막 봉 · 현재가 실시간 갱신(GET /api/candles/live, #653).
 * 진행 중 봉은 보기용이다 — 채널 · 추세선 · 갭 · 매매 지도는 끝난 봉 기준 그대로(#611).
 */

/** 분봉은 3초, 일봉 이상은 5초(서버가 2초 캐시하고 원천은 키마다 한 번만 묻는다) */
export const liveMs = (intraday: boolean) => (intraday ? 3_000 : 5_000);

export const LIVE_SOURCE_KO: Record<string, string> = {
  kis: "실시간 · 한투",
  toss: "토스",
  yahoo: "야후",
  nxt: "NXT",
  "krx-overtime": "시간외 단일가",
  "binance-estimate": "추정(바이낸스)",
};

const SESSION_KO: Record<string, string> = { pre: "프리장", post: "애프터장", overnight: "야간", closed: "장 마감" };

const CLOCK = new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

/** 지연 이름표 — 1분 넘게 늦으면 "약 N분 지연", 아니면 없음 */
export function delayText(sec: number | null | undefined): string | null {
  if (sec == null || !Number.isFinite(sec) || sec < 60) return null;
  return `약 ${Math.round(sec / 60)}분 지연`;
}

/** "실시간 · 한투 · 14:03:21" · "야후 · 약 20분 지연" · "추정(바이낸스) · 프리장 · 06:41:10" */
export function liveLabel(l: CandleLive): string {
  const src = LIVE_SOURCE_KO[l.source] ?? l.source;
  const session = l.session && l.session !== "regular" ? SESSION_KO[l.session] ?? null : null;
  const late = l.source === "yahoo" && l.live === false && l.price_at == null
    ? "지연 · 시각 미확인"
    : delayText(l.delay_sec);
  const at = l.fetched_at ? CLOCK.format(new Date(l.fetched_at)) : null;
  return [src, session, late ?? at].filter(Boolean).join(" · ");
}

export const LIVE_NOTE = "진행 중인 봉(반투명)은 보기용입니다 — 채널 · 추세선 · 갭 판단은 끝난 봉 기준입니다.";

/** 요소가 지금 화면(+ margin) 안에 있는지 — 나가면 false 로 돌아간다(WhenVisible 과 달리 한 번 보였다고 두지 않는다). */
export function useInView(ref: RefObject<Element | null>, margin = "100px"): boolean {
  const [inView, setInView] = useState(() => typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => setInView(entries.some((e) => e.isIntersecting)), { rootMargin: `${margin} 0px` });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, margin]);
  return inView;
}
