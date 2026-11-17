import type { Trendline, TrendScenario } from "./types";

/**
 * #531 여러 하락 저항선 · 상승 지지선(#530) — 이름 · 굵기 · 거르기 · 키. 옛 run · 옛 서버의 선(새 필드 없음)도 그대로 다룬다.
 */
export type TrendScale = NonNullable<Trendline["scale"]>;
export const SCALE_KO: Record<TrendScale, string> = { short: "단기", mid: "중기", long: "장기" };
export const STATUS_KO: Record<NonNullable<Trendline["status"]>, string> = { broken: "이탈", testing: "시험 중", near: "가까움", far: "멀리" };
export const KIND_KO: Record<Trendline["kind"], string> = { support: "지지", resistance: "저항" };
export const SCENARIO_KO: Record<TrendScenario["kind"], string> = { breakout: "돌파하면", reject: "막히면", bounce: "받치면", breakdown: "깨지면" };

/** 선 하나의 키 — 서버 id 가 없으면(옛 응답) 같은 규칙으로 만든다 */
export const trendKey = (t: Trendline) => t.id ?? `${t.timeframe}-${t.kind}-${t.anchors[0]?.at ?? ""}-${t.anchors[1]?.at ?? ""}`;

/** 스윙 크기별 굵기(짧게 얇게 · 길게 굵게). 옛 선은 예전 굵기 2 */
export const trendWidth = (t: Trendline): 1 | 2 | 3 => (t.scale === "short" ? 1 : t.scale === "long" ? 3 : 2);

export const pctText = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v) >= 10 ? Math.round(Math.abs(v)) : Math.abs(v).toFixed(1)}%`;

/** 깨져서 지금 역할이 바뀐 선인가 — 깨진 지지선은 위에서 막고, 깨진 저항선은 아래에서 받친다(시나리오로 판단, #530 보완) */
export function roleFlipped(t: Trendline): boolean {
  if (t.state !== "broken" || !t.scenarios?.length) return false;
  const nowRes = t.scenarios.some((s) => s.kind === "breakout" || s.kind === "reject");
  return nowRes !== (t.kind === "resistance");
}

/** 선 끝 이름표 — "중기 저항 · +18%" (옛 선: "저항선"), 역할이 바뀐 선은 "중기 깨진 지지 · +5.3%"(#560 카드와 같은 말) */
export function trendLabel(t: Trendline): string {
  const head = `${t.scale ? `${SCALE_KO[t.scale]} ` : ""}${roleFlipped(t) ? "깨진 " : ""}${KIND_KO[t.kind]}${t.scale ? "" : "선"}`;
  return t.distance_pct != null ? `${head} · ${pctText(t.distance_pct)}` : head;
}

// ── 범례 켜고 끄기(이 브라우저에 기억) ──
export type TrendFilter = Record<TrendScale | Trendline["kind"], boolean>;
const FILTER_KEY = "desk.chart.trendFilter";
export const ALL_ON: TrendFilter = { short: true, mid: true, long: true, support: true, resistance: true };
export function loadTrendFilter(): TrendFilter {
  try {
    const raw = JSON.parse(localStorage.getItem(FILTER_KEY) ?? "{}") as Partial<TrendFilter>;
    return Object.fromEntries(Object.entries(ALL_ON).map(([k, v]) => [k, typeof raw[k as keyof TrendFilter] === "boolean" ? raw[k as keyof TrendFilter] : v])) as TrendFilter;
  } catch {
    return ALL_ON;
  }
}
export function saveTrendFilter(f: TrendFilter) {
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(f)); } catch { /* 이번 화면에서만 */ }
}
/** 스윙 크기가 없는 옛 선은 크기 거르기와 상관없이 보인다 */
export const passes = (t: Trendline, f: TrendFilter) => f[t.kind] && (!t.scale || f[t.scale]);

/**
 * 개발 서버 전용 예시 — #530 이 아직 없어 새 필드가 하나도 없을 때만, 있는 선에 모양을 보이려는 값을 붙이고
 * 먼 저항선 하나를 더한다. 숫자는 지금 선 값에서 단순 계산한 예시라 실제 시나리오가 아니다(카드에 "예시").
 */
export function devExample(lines: Trendline[], ref: number | null | undefined): Trendline[] {
  if (!import.meta.env.DEV || !ref || lines.length === 0 || lines.some((t) => t.scenarios || t.id)) return lines;
  const scales: TrendScale[] = ["short", "mid", "long"];
  const enrich = (t: Trendline, i: number, far = false): Trendline => {
    const now = t.price_now;
    const dist = ((now - ref) / ref) * 100;
    const step = 1 + t.slope_pct_per_bar / 100;
    const res = t.state === "broken" ? t.kind === "support" : t.kind === "resistance";   // 깨진 선은 지금 역할로(#530 보완)
    const sc: TrendScenario[] = res ? [
      { kind: "breakout", trigger: now, volume_min: null, target: +(now * 1.06).toFixed(2), target_basis: "앞 고점(예시)", invalid: +(now * 0.98).toFixed(2), text: `종가로 ${now.toFixed(2)} 위에 올라서면 하락 흐름이 멈춘 신호예요(예시).` },
      { kind: "reject", trigger: now, volume_min: null, target: +(now * 0.93).toFixed(2), target_basis: "일봉 지지 구역(예시)", invalid: +(now * 1.01).toFixed(2), text: `${now.toFixed(2)} 근처에서 막히면 아래 받침까지 되돌릴 수 있어요(예시).` },
    ] : [
      { kind: "bounce", trigger: now, volume_min: null, target: +(now * 1.07).toFixed(2), target_basis: "일봉 저항 구역(예시)", invalid: +(now * 0.98).toFixed(2), text: `${now.toFixed(2)} 근처에서 받치면 위 저항까지 반등을 기대해요(예시).` },
      { kind: "breakdown", trigger: now, volume_min: null, target: +(now * 0.92).toFixed(2), target_basis: "앞 저점(예시)", invalid: +(now * 1.02).toFixed(2), text: `종가로 ${now.toFixed(2)} 아래로 내려가면 상승 흐름이 꺾인 신호예요(예시).` },
    ];
    return {
      ...t, id: `${trendKey(t)}${far ? "-far" : ""}`, scale: far ? "long" : scales[i % 3], swing_k: [5, 10, 20][i % 3],
      status: t.state === "broken" ? "broken" : t.state === "testing" ? "testing" : far || Math.abs(t.distance_atr) > 3 ? "far" : "near",   // #530 보완: ATR 3배 밖이면 far
      distance_pct: +dist.toFixed(1),
      ahead: [5, 10, 20].map((n) => { const p = now * step ** n; return { bars: n, price: +p.toFixed(2), distance_pct: +(((p - ref) / ref) * 100).toFixed(1) }; }),
      scenarios: sc,
      summary: [
        `${res ? "막힌" : "받친"} 기록이 ${t.touches.length}번인 ${far ? "장기" : SCALE_KO[scales[i % 3]]} ${res ? "하락 저항선" : "상승 지지선"}이에요. 지금 선 값은 ${now.toFixed(2)}(${pctText(dist)}).`,
        "예시 데이터 — 개발 서버에서 #530 없이 화면을 보기 위한 값이에요.",
      ],
    };
  };
  const out = lines.map((t, i) => enrich(t, i));
  const base = lines.find((t) => t.kind === "resistance") ?? lines[0];
  const k = 1.18 * (ref / base.price_now);
  const far: Trendline = {
    ...base, kind: "resistance", state: "intact", distance_atr: -12,
    anchors: base.anchors.map((a) => ({ ...a, price: a.price * k })), end: { ...base.end, price: base.end.price * k }, price_now: base.price_now * k,
  };
  return [...out, enrich(far, 2, true)];
}
