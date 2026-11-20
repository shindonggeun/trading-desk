import { api, ApiError, type AlertTone, type MarketAlert, type MarketAlertsDay } from "./api";
import { usePoll } from "./poll";

/** #581 오늘 알림(GET /api/alerts/today) — 대시보드 띠 · 타임라인 · 장중 수급 차트 마커가 같이 쓴다.
 *  화면이 보이는 동안 POLL_MS 마다 다시 읽고, 같은 페이지의 여러 구독자는 요청 하나를 나눠 쓴다. */
const POLL_MS = 60_000;

export const KIND_LABEL: Record<MarketAlert["kind"], string> = {
  flow_turn: "수급 전향",
  flow_shift: "수급 흐름",
  flow_warn: "수급 경고",
  nq_alert: "나스닥 선물",
  holding_alert: "보유 종목",
};
/** 종류 이름 — 화면이 아직 모르는 종류(서버가 먼저 낸 새 kind)는 "알림" 으로, undefined 가 보이지 않게 */
export const kindLabel = (kind: string): string => (KIND_LABEL as Record<string, string>)[kind] ?? "알림";
/** #581 보유 종목 알림은 그 종목 차트로 */
export const alertHref = (a: Pick<MarketAlert, "kind" | "market">): string | null =>
  a.kind === "holding_alert" && a.market ? `#/chart/${encodeURIComponent(a.market)}` : null;
export const SEVERITY_LABEL: Record<MarketAlert["severity"], string> = { info: "참고", caution: "주의", strong: "강함" };
/** #642 tone(#641) — 색만으로 가르지 않게 글자와 기호를 같이. 한국식 오름 빨강과 겹치지 않게 호재는 초록 ◎ */
export const TONE_LABEL: Record<AlertTone, string> = { positive: "호재", neutral: "참고", caution: "주의", warning: "경고" };
export const TONE_MARK: Record<AlertTone, string> = { positive: "◎", neutral: "·", caution: "!", warning: "⚠" };
/** 알림의 등급 표 — tone 이 있으면 tone, 없으면(예전 알림) 지금처럼 크기 */
export function badgeOf(a: Pick<MarketAlert, "tone" | "severity">): { cls: string; label: string; mark: string } {
  if (a.tone) return { cls: `tone-${a.tone}`, label: TONE_LABEL[a.tone], mark: TONE_MARK[a.tone] };
  return { cls: `sev-${a.severity}`, label: SEVERITY_LABEL[a.severity], mark: "" };
}
/** 경고인가(tone 이 없으면 크기 "강함") — "경고만" 거르기 */
export const isWarning = (a: Pick<MarketAlert, "tone" | "severity">) => (a.tone ? a.tone === "warning" : a.severity === "strong");
/** 화면에 띄울 만한가 — 참고(tone 이 없으면 크기 info)는 띠 · 토스트에서 뺀다 */
export const isNotable = (a: Pick<MarketAlert, "tone" | "severity">) => (a.tone ? a.tone !== "neutral" : a.severity !== "info");
/** 서버 글(#641 "🟢 호재 · …\n해석", 예전 "🔴 …")의 머리 표시를 떼고 첫 줄 · 해석으로 — 등급은 이름표가 따로 보인다 */
export function alertLines(text: string): { head: string; reading: string } {
  const [first, ...rest] = text.split("\n");
  const head = first.replace(/^[🔴🟠🟡🟢⚪🔵]\s*(?:(?:호재|참고|주의|경고)\s*·\s*)?/u, "");
  return { head, reading: rest.join(" ").trim() };
}

export const alertKey = (a: MarketAlert) => `${a.kind}|${a.rule}|${a.market}|${a.who}|${a.t}`;

export function hhmm(iso: string): string {
  const m = iso.match(/T(\d{2}):(\d{2})/);
  return m ? `${m[1]}:${m[2]}` : iso;
}

/** 수급이 가리키는 쪽 — 매수 확대 · 전향은 up, 매도 확대 · 전향 · 나스닥 하락은 down. "축소" 는 반대 쪽이다
 *  (매수 축소 = 사던 힘이 줄어 아래, 매도 축소 = 팔던 힘이 줄어 위). 색과 함께 ▲▼ 글자로도 쓴다 */
export function sideOf(a: MarketAlert): "up" | "down" | null {
  // 장초 매수 뒤 매도 반전(open_buy_reversal) 도 이제 파는 쪽이라 반대
  const shrink = /shrink|reversal/.test(a.rule);
  if (/buy/.test(a.rule)) return shrink ? "down" : "up";
  if (/sell/.test(a.rule)) return shrink ? "up" : "down";
  if (/drop|from_high/.test(a.rule)) return "down";
  // #694 보유 종목: 익절 고려는 위, 손절 · 손절 근접 · 하락 추세는 아래
  if (a.kind === "holding_alert") return a.rule === "hold_take_profit" ? "up" : "down";
  return null;
}

type Feed = { data: MarketAlertsDay | null; error: string | null };

/** #626 같은 요청(ALERTS_KEY)을 대시보드 띠 · 타임라인 · 토스트 · 작은 창이 함께 쓴다. DEV 에서 API 가 없으면(404) 예시 */
export const ALERTS_KEY = "alerts-today";
export const loadAlerts = (): Promise<MarketAlertsDay> => api.alertsToday().catch((e) => {
  if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) return fakeAlerts();
  throw e;
});

/** 오늘 알림 — 1분마다(가려진 탭은 쉰다), 실패하면 앞서 받은 값을 두고 오류만 */
export function useTodayAlerts(): Feed {
  const s = usePoll(ALERTS_KEY, loadAlerts, POLL_MS);
  return { data: s.data ?? null, error: s.failing ? (s.error instanceof Error ? s.error.message : "불러오지 못했습니다") : null };
}

/** DEV 에서 백엔드에 /api/alerts/today 가 없을 때(404)만 쓰는 예시 */
export function fakeAlerts(): MarketAlertsDay {
  const kst = new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
  const at = (hm: string) => `${kst}T${hm}:00+09:00`;
  const a = (x: Partial<MarketAlert> & Pick<MarketAlert, "kind" | "rule" | "market" | "t" | "text" | "severity">): MarketAlert =>
    ({ trade_date: kst, who: "foreign", value: null, prev: null, pushed: x.severity !== "info", ...x });
  return {
    date: kst,
    items: [
      // #642 #641 형식(머리 등급 + 둘째 줄 해석) — 네 가지 tone 이 다 보이게
      a({ kind: "nq_alert", rule: "nq_drop", market: "nq", who: "all", severity: "caution", tone: "warning", t: at("08:20"),
          text: "🔴 경고 · 나스닥 선물 5분 -0.42% — 평소 하위 1% 보다 큰 하락, 거래량 1.8배 (예시)\n나스닥 급락 — 장 초반 지수에 부담" }),
      a({ kind: "flow_shift", rule: "sell_grow", market: "futures", severity: "caution", tone: "caution", t: at("09:35"),
          text: "🟠 주의 · 외인 선물 매도 확대 — 30분 -1,240계약 (예시)\n외인 선물 매도 늘어남 — 지수에 부담" }),
      a({ kind: "flow_turn", rule: "buy_turn", market: "kospi", severity: "caution", tone: "positive", t: at("10:41"),
          text: "🟢 호재 · 외국인 코스피 순매수 전향 +312억 (예시)\n외인 매수 유입 — 지수에 우호적" }),
      a({ kind: "flow_shift", rule: "buy_grow", market: "kospi", who: "individual", severity: "info", tone: "neutral", t: at("11:10"),
          text: "⚪ 참고 · 개인 코스피 현물 매수 확대 — 30분 +420억 (예시)\n개인은 대개 반대편 — 참고만" }),
      a({ kind: "flow_shift", rule: "sell_grow", market: "kospi", severity: "strong", t: at("13:25"),
          text: "🔴 외인 코스피 현물 매도 확대 — 30분 -2,850억 (예시 · tone 없는 예전 알림)" }),
    ],
  };
}
