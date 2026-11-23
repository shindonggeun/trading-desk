/**
 * #690 시세 · 수급이 화면에 닿기까지의 지연 — 운영 장중 실측용(docs/market-stream-latency.md "장중 운영 실측").
 * 평소에는 꺼져 있다. 탭에서 `localStorage["desk.marketStream"]` 을 정하고 새로고침하면 켜진다.
 * - `"measure"`: 화면은 지금처럼(스트림으로) 그리고 잰다 — "후".
 * - `"poll"`: 화면은 폴링으로만 그리고, 스트림은 비교용으로 받기만 한다 — "전"과 "후"를 한 탭에서 함께.
 *
 * 값마다 "데스크가 그 값을 처음 관측한 시각(스트림 메시지의 `observed_at`, ms) → 그 값이 화면 상태에 닿은 시각".
 * - 스트림(`sse`): 메시지가 바꾼 키만. `ready` 전 스냅숏(재접속)은 전송 지연이 아니라 세지 않는다.
 * - 폴링(`rest`, poll 모드만): REST 로 바뀐 키의 값을, 그 요청 전에 스트림이 마지막으로 받아 둔 값이 같을 때 짝지어
 *   그 값의 observed_at 부터 잰다 — 폴링 대기가 그대로 들어간다. 짝이 없으면 `unmatched`, 그 요청을 보낸 뒤에야 스트림에 나타난 값(폴링이 직접 읽어
 *   발행한 값)은 `self` 로 세고 표본에서 뺀다.
 * - 값의 동일성: 시세는 가격, 수급 · 선물은 `freshness.fetched_at`(패널의 LiveSpec.stamps).
 * - 화면 출력(페인트)이 아니라 데이터가 패널 상태에 닿은 시각이다. #822 측정에서 수신 → DOM 은 수 ms 였다.
 * - 브라우저와 서버 시계의 차이는 `skew()` 로 재서 보정한다(보정 전은 `*_raw`).
 * 콘솔: `await __deskMarketLatency.skew()` · `__deskMarketLatency.reset()` · `__deskMarketLatency.report()`.
 */

export type LatencyStamp = { key: string; id: string | number | null | undefined; at?: string | null };
type Mode = "off" | "measure" | "poll";
type Arrival = { at: number; observed: number; source: number | null };

const KEEP = 1_000;          // 채널 · 경로마다 최근 표본
const ARRIVALS_PER_KEY = 64; // 키마다 스트림이 가져온 값(동일성 → 처음 도착)

function readMode(): Mode {
  try {
    const v = typeof localStorage !== "undefined" ? localStorage.getItem("desk.marketStream") : null;
    return v === "poll" || v === "measure" ? v : "off";
  } catch {
    return "off";
  }
}

const mode: Mode = readMode();   // 새로고침해야 바뀐다 — 연결 · 폴링이 도중에 갈리지 않게
/** 이 탭의 계측 모드. "poll" 이면 marketStream 이 화면에 스트림을 쓰지 않는다 */
export function latencyMode(): Mode {
  return mode;
}

let since = Date.now();
let skewMs = 0;              // 서버 시계 - 브라우저 시계(ms). skew() 가 정한다
let skewRtt: number | null = null;
const sse = new Map<string, Arrival[]>();     // 채널 → 스트림 도착 표본
const rest = new Map<string, Arrival[]>();    // 채널 → 폴링 화면 도착 표본(짝지은 스트림 값의 시각)
const counts = new Map<string, { unmatched: number; self: number }>();
const arrivals = new Map<string, Map<string, Arrival>>();   // 채널|키 → 값 → 스트림으로 처음 온 때
const lastRest = new Map<string, string>();                  // 채널|키 → 폴링이 마지막으로 가져온 값

function push(into: Map<string, Arrival[]>, channel: string, a: Arrival): void {
  const list = into.get(channel) ?? [];
  list.push(a);
  if (list.length > KEEP) list.shift();
  into.set(channel, list);
}

const parse = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** 스트림 메시지 하나가 바꾼 값들(stamps 의 앞 · 뒤 차이). ready 가 아니면 동일성만 기억하고 표본은 남기지 않는다 */
export function recordStream(channel: string, before: LatencyStamp[], after: LatencyStamp[],
                             observedAt: string, sourceAt: string | null | undefined, ready: boolean, at = Date.now()): void {
  if (mode === "off") return;
  const observed = parse(observedAt);
  if (observed == null) return;
  const prev = new Map(before.map((s) => [s.key, s.id == null ? null : String(s.id)]));
  for (const s of after) {
    if (s.id == null) continue;
    const id = String(s.id);
    if (prev.get(s.key) === id) continue;
    const a: Arrival = { at, observed, source: parse(sourceAt) };
    const slot = `${channel}|${s.key}`;
    const seen = arrivals.get(slot) ?? new Map<string, Arrival>();
    seen.delete(id);                 // 같은 값으로 다시 바뀌면 가장 최근의 바뀜을 쓴다
    seen.set(id, a);
    if (seen.size > ARRIVALS_PER_KEY) seen.delete(seen.keys().next().value as string);
    arrivals.set(slot, seen);
    if (ready) push(sse, channel, a);
  }
}

/** REST 응답이 화면에 가져온 값들(poll 모드만). startedAt: 그 요청을 보낸 시각 */
export function recordRest(channel: string, stamps: LatencyStamp[], startedAt: number, at = Date.now()): void {
  if (mode !== "poll") return;
  const c = counts.get(channel) ?? { unmatched: 0, self: 0 };
  counts.set(channel, c);
  for (const s of stamps) {
    if (s.id == null) continue;
    const id = String(s.id);
    const slot = `${channel}|${s.key}`;
    const was = lastRest.get(slot);
    lastRest.set(slot, id);
    if (was === undefined || was === id) continue;   // 키의 첫 값은 기준선, 같은 값은 바뀜이 아니다
    // 짝: 요청을 보내기 전에 스트림이 마지막으로 가져온 값이 바로 이 값일 때만(같은 가격이 다시 나온 경우 예전 도착과
    // 짝짓지 않게). Map 은 도착 순서다(같은 값이 다시 오면 지우고 넣는다)
    const seen = arrivals.get(slot);
    let before: [string, Arrival] | null = null;
    for (const entry of seen ?? []) if (entry[1].at < startedAt) before = entry;
    if (before && before[0] === id) push(rest, channel, { at, observed: before[1].observed, source: before[1].source });
    else if (seen?.get(id) && seen.get(id)!.at >= startedAt) c.self += 1;
    else c.unmatched += 1;
  }
}

function pct(values: number[]) {
  if (!values.length) return null;
  const v = [...values].sort((x, y) => x - y);
  const rank = (q: number) => v[Math.min(v.length - 1, Math.ceil(q * v.length) - 1)];
  return { n: v.length, p50: Math.round(rank(0.5)), p95: Math.round(rank(0.95)), max: Math.round(v[v.length - 1]) };
}

function summary(list: Arrival[]) {
  return {
    observed: pct(list.map((s) => s.at + skewMs - s.observed)),
    observed_raw: pct(list.map((s) => s.at - s.observed)),
    source: pct(list.filter((s) => s.source != null).map((s) => s.at + skewMs - s.source!)),
  };
}

/** 채널별 지연(ms) — sse: 스트림 "후", rest: 폴링 "전"(poll 모드). observed: 관측 → 화면 상태, source: 원천 시각 → 화면 상태 */
export function latencyReport() {
  const channels: Record<string, Record<string, unknown>> = {};
  for (const ch of new Set([...sse.keys(), ...rest.keys(), ...counts.keys()])) {
    channels[ch] = { sse: summary(sse.get(ch) ?? []) };
    if (mode === "poll") channels[ch].rest = { ...summary(rest.get(ch) ?? []), ...(counts.get(ch) ?? { unmatched: 0, self: 0 }) };
  }
  return { mode, since: new Date(since).toISOString(), skew_ms: skewMs, skew_rtt_ms: skewRtt, channels };
}

export function resetLatency(): void {
  since = Date.now();
  sse.clear();
  rest.clear();
  counts.clear();
  // 스트림 도착 · 마지막 폴링 값은 둔다 — 짝짓기 · 기준선은 이어 간다
}

/** 서버 시계와의 차이를 잰다 — /api/status 의 now(ms) 를 n 번 읽어 왕복이 가장 짧은 표본으로. 오차는 ±왕복/2 */
export async function measureSkew(n = 7): Promise<{ skew_ms: number; rtt_ms: number } | null> {
  let best: { skew: number; rtt: number } | null = null;
  for (let i = 0; i < n; i++) {
    const t0 = Date.now();
    let now: number | null;
    try {
      const r = await fetch("/api/status", { credentials: "same-origin", cache: "no-store" });
      now = parse(((await r.json()) as { now?: string }).now);
    } catch {
      continue;
    }
    const t1 = Date.now();
    if (now == null) continue;
    if (!best || t1 - t0 < best.rtt) best = { skew: now - (t0 + t1) / 2, rtt: t1 - t0 };
  }
  if (!best) return null;
  skewMs = Math.round(best.skew);
  skewRtt = best.rtt;
  return { skew_ms: skewMs, rtt_ms: best.rtt };
}

if (typeof window !== "undefined" && mode !== "off") {
  (window as unknown as Record<string, unknown>).__deskMarketLatency = {
    report: latencyReport, reset: resetLatency, skew: measureSkew,
  };
}
