/**
 * #300 차트 예상도 — 미래 칸 시각과 봉 주기 ↔ 예상도 시간축 짝.
 * 예상도(/api/projection)는 시간축의 주 주기(스윙 일봉 · 단기 스윙 1시간 · 단타 5분)로만 계산된다.
 */
type Key = string | number;

/** 차트 봉 주기 → 예상도를 부를 시간축. 짝이 없으면(주봉 · 15분 등) 그리지 않는다. */
export const PROJECTION_HORIZON: Record<string, { horizon: string; timeframe: string }> = {
  "1d": { horizon: "swing", timeframe: "1d" },
  "60m": { horizon: "short_swing", timeframe: "60m" },
  "5m": { horizon: "intraday", timeframe: "5m" },
};

const STEP_SEC: Record<string, number> = { "5m": 300, "60m": 3600 };

/**
 * 마지막 봉 다음부터 n개의 미래 칸. 일봉은 주말을 건너뛴 날짜(휴장일은 모른다 — 근사),
 * 분봉은 마지막 봉 시각 + 주기(장 마감 · 밤 시간도 이어 센다 — 근사).
 */
export function futureKeys(last: Key, interval: string, n: number): Key[] {
  const out: Key[] = [];
  if (typeof last === "number") {
    const step = STEP_SEC[interval] ?? 60;
    for (let j = 1; j <= n; j++) out.push(last + step * j);
    return out;
  }
  const d = new Date(`${last}T00:00:00Z`);
  while (out.length < n) {
    d.setUTCDate(d.getUTCDate() + 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) out.push(d.toISOString().slice(0, 10));
  }
  return out;
}
