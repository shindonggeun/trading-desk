/** 선 알림과 차트 탭에서 공유하는 시간봉/관측가 링크 계약. */
const TF_KO: Record<string, string> = {
  "5m": "5분봉", "10m": "10분봉", "15m": "15분봉", "60m": "1시간봉",
  "120m": "2시간봉", "240m": "4시간봉", "1d": "일봉", "1w": "주봉",
};
export const LINE_TF_KO = TF_KO;
const CHART_FALLBACKS = new Map([["10m", "5m"], ["120m", "60m"]]);
export const alertChartFallback = (timeframe: string) => CHART_FALLBACKS.get(timeframe) ?? null;

export function chartFallbackLabel(timeframe: string): string | null {
  const display = alertChartFallback(timeframe);
  return display ? TF_KO[timeframe] + " 차트 · 공급 부족 시 " + TF_KO[display] + "로 보기 가능" : null;
}

export function chartHref(
  s: { ticker: string; timeframe: string }, reference?: string, display = s.timeframe, signalId?: number,
): string {
  const query = new URLSearchParams({ t: s.ticker, iv: display });
  if (Object.hasOwn(TF_KO, s.timeframe)) {
    query.set("signal_tf", s.timeframe);
    if (reference) query.set("signal_ref", reference);
    if (signalId !== undefined) query.set("signal_id", String(signalId));
  }
  return "#/charts?" + query;
}

export function chartLineAlert(query: URLSearchParams) {
  const timeframe = query.get("signal_tf");
  const display = query.get("iv");
  if (!timeframe || !Object.hasOwn(TF_KO, timeframe) || !display) return null;
  const fallback = alertChartFallback(timeframe);
  if (display !== timeframe && display !== fallback) return null;
  const rawId = query.get("signal_id");
  const id = rawId && /^[1-9]\d{0,9}$/.test(rawId) ? Number(rawId) : 0;
  return {
    timeframe, display, fallback, reference: query.get("signal_ref"),
    signalId: fallback && id > 0 && id <= 2_147_483_647 ? id : null,
    exact: fallback !== null && display === timeframe,
  };
}

export function chartFallbackHref(ticker: string, alert: NonNullable<ReturnType<typeof chartLineAlert>>): string | null {
  if (alert.fallback === null) return null;
  return chartHref({ ticker, timeframe: alert.timeframe }, alert.reference ?? undefined, alert.fallback,
    alert.signalId ?? undefined);
}

const TF_MIN: Record<string, number> = { "5m": 5, "10m": 10, "15m": 15, "60m": 60, "120m": 120, "240m": 240, "1d": 1440 };
type SignalTime = { at: string; timeframe: string; recorded_at?: string | null };

/** 새 신호는 실제 저장 시각을 사용한다. 예전 데이터는 표시하던 봉 종료 시각을 유지한다. */
export function signalTime(s: SignalTime): number {
  const recorded = s.recorded_at ? Date.parse(s.recorded_at) : Number.NaN;
  return Number.isFinite(recorded) ? recorded : Date.parse(s.at) + (TF_MIN[s.timeframe] ?? 0) * 60_000;
}

export function signalDateTime(s: SignalTime): string | undefined {
  const at = signalTime(s);
  return Number.isFinite(at) ? new Date(at).toISOString() : undefined;
}
