import { price as fmt } from "./format";
import type { HorizonId, Plan } from "./types";
import type { DirectionScenario, ScenarioSide } from "./directionScenarios";

/**
 * #697 분석 한 번에 네 기간(#696 desk/all_horizons.py, docs/all-horizons.md) — 스캘핑 · 단타 · 짧은 스윙 · 스윙.
 * 모델이 말한 기간(source "model")은 모델의 진입 · 손절 · 목표, 나머지는 데스크 결정적 지도(source "map").
 */

export const HORIZON_ORDER: HorizonId[] = ["scalp", "intraday", "short_swing", "swing"];

export interface InstrumentClass {
  id: "leverage" | "inverse" | "bond" | "defensive" | "general" | string;
  label: string;
  risk?: string;
  basis?: string;
  multiple?: number | null;
  underlying?: string | null;
}

export interface HorizonMap {
  side?: "long" | "short";
  side_ko?: string;
  setup?: string;
  kind?: string;
  kind_ko?: string;
  zone?: [number, number];
  first?: number | null;
  avg_entry?: number | null;
  stop?: number | null;
  stop_pct?: number | null;
  targets?: { role: string; price: number; rr: number | null }[];
  from_now_pct?: number | null;
  expected_r?: number | null;
  ev_r?: number | null;
  p_t1?: number | null;
  n?: number;
  thin?: boolean;
  why?: string[];
  stand_aside?: string | null;
  regime?: string;
  price_now?: number | null;
  atr?: number | null;
}

export interface HorizonModel {
  opinion?: string; rating?: string; bias?: "long" | "short" | "flat" | string;
  why?: string | null;
  entry_price?: number | null; entry_type?: string | null; stop_loss?: number | null; price_target?: number | null;
  long_trigger?: number | null; short_trigger?: number | null; valid_until?: string | null;
}

export interface HorizonSlot {
  horizon: HorizonId;
  label: string;
  holding: string;
  flat_by_close?: boolean;
  source: "model" | "map";
  model: HorizonModel | null;
  map: HorizonMap | null;
  scenarios?: Partial<Record<ScenarioSide, DirectionScenario>> | null;
  levels?: { support: { price: number; strength?: number; basis?: string }[]; resistance: { price: number; strength?: number; basis?: string }[] } | null;
  atr?: number | null;
  chart?: {
    timeframe: string; summary?: string;
    trend?: { state: "up" | "down" | "sideways" | string; ema20?: number | null; ema50?: number | null; basis?: string } | null;
    above?: { price: number; name?: string; refs?: string[]; distance_pct?: number | null }[];
    below?: { price: number; name?: string; refs?: string[]; distance_pct?: number | null }[];
    patterns?: string[];
  } | null;
  errors?: Record<string, string> | null;
}

export type Fit = "good" | "ok" | "poor" | "unknown";
export interface HorizonFit {
  regime: string;
  regime_ko?: string;
  best: HorizonId[];
  fit: Partial<Record<HorizonId, Fit>>;
  sentence: string;
  basis?: string[];
  summary?: string;
  atr_pct?: number | null;
  record?: Partial<Record<HorizonId, { r?: number | null; n?: number } | null>> | null;
  errors?: Record<string, string> | null;
}

/** #696 이 붙이는 plan 필드 — 옛 run 에는 없다 */
export type AllHorizonsPlan = Plan & {
  instrument_class?: InstrumentClass | null;
  horizons?: Partial<Record<HorizonId, HorizonSlot>> | null;
  horizon_fit?: HorizonFit | null;
  horizons_note?: string | null;
  horizons_fake?: boolean;
};

export const FIT_KO: Record<Fit, string> = { good: "잘 맞음", ok: "보통", poor: "안 맞음", unknown: "판단 보류" };
/** #671 손익비가 이보다 낮으면 그 칸을 흐리게 */
export const LOW_RR = 1.5;

export type SlotLines = {
  side: "long" | "short" | null;
  sideKo: string;
  kindKo: string | null;
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  standAside: string | null;
};

/** 손익비 — 진입이 손절과 목표 사이에 있을 때만(#436 levelsContradict 와 같은 뜻). 어긋난 배치는 null */
const rrOf = (entry: number | null, stop: number | null, target: number | null) => {
  if (entry == null || stop == null || target == null) return null;
  const ok = (stop < entry && entry < target) || (target < entry && entry < stop);
  return ok ? Math.abs(target - entry) / Math.abs(entry - stop) : null;
};

/** 모델 칸이 관망일 때 한 줄 — 롱 · 숏 조건 가격과 함께(영어 opinion 을 그대로 보이지 않게) */
function modelAside(m: HorizonModel, filtered: boolean): string {
  const t = [
    m.long_trigger != null ? `${fmt(m.long_trigger)} 위로 올라서면 롱 검토` : null,
    m.short_trigger != null ? `${fmt(m.short_trigger)} 아래로 내려가면 숏 검토` : null,
  ].filter(Boolean).join(" · ");
  const head = m.opinion === "review" ? "다시 검토가 필요한 계획이에요"
    : filtered ? "방향은 있지만 기준(확률 · 손익비)을 넘지 못해 관망" : "지금은 관망";
  return t ? `${head} — ${t}` : `${head} — 새 신호를 기다려요`;
}

/** 그 기간 칸의 진입 · 손절 · 목표 — 모델이 말한 기간은 모델 값, 아니면 데스크 지도 */
export function slotLines(s: HorizonSlot): SlotLines {
  if (s.source === "model" && s.model) {
    const m = s.model;
    const biased = m.bias === "long" || m.bias === "short" ? m.bias : null;
    // verdict 필터에 걸리면 opinion 은 hold 인데 bias 는 남는다(desk/verdict.py) — 칸은 opinion 을 따른다
    const side = m.opinion ? (m.opinion === "buy" ? "long" : m.opinion === "sell" ? "short" : null) : biased;
    const entry = m.entry_price ?? null, stop = m.stop_loss ?? null, target = m.price_target ?? null;
    return { side, sideKo: side === "long" ? "롱" : side === "short" ? "숏" : "관망", kindKo: null, entry, stop, target,
      rr: rrOf(entry, stop, target), standAside: side ? null : modelAside(m, !!biased) };
  }
  const mp = s.map;
  if (!mp || !mp.side) {
    return { side: null, sideKo: "관망", kindKo: null, entry: null, stop: null, target: null, rr: null, standAside: mp?.stand_aside ?? "지금 고를 쪽이 없어요" };
  }
  const entry = mp.first ?? mp.avg_entry ?? null;
  const t1 = mp.targets?.[0];
  return { side: mp.side, sideKo: mp.side_ko ?? (mp.side === "long" ? "롱" : "숏"), kindKo: mp.kind_ko ?? null, entry,
    stop: mp.stop ?? null, target: t1?.price ?? null, rr: rrOf(entry, mp.stop ?? null, t1?.price ?? null) == null ? null : t1?.rr ?? rrOf(entry, mp.stop ?? null, t1?.price ?? null), standAside: null };
}

/** 처음 고른 기간 — 지금 장세에 맞는 기간 중 첫 칸, 없으면 분석한 기간, 없으면 첫 칸 */
export function defaultHorizon(plan: AllHorizonsPlan | null): HorizonId | null {
  const hs = plan?.horizons;
  if (!hs) return null;
  const have = HORIZON_ORDER.filter((h) => hs[h]);
  return plan?.horizon_fit?.best?.find((h) => have.includes(h)) ?? (plan?.horizon && have.includes(plan.horizon) ? plan.horizon : have[0] ?? null);
}

/** 고른 기간이 분석한 기간과 다른가 — 그러면 차트는 그 칸의 계획을 그리고, 분석 기간의 근거 해설은 숨긴다 */
export function isOtherHorizon(plan: AllHorizonsPlan | null, h: HorizonId | null): boolean {
  return !!(plan && h && plan.horizons?.[h] && h !== (plan.horizon ?? "swing"));
}

/**
 * 차트에 그릴 계획 — 고른 기간이 분석한 기간과 다르면 방향 · 진입 · 손절 · 목표 · 손익비를 그 칸 것으로 바꾼다.
 * 분석 기간에만 맞는 것(조건선 · 플레이북 · 저장된 일봉 지지 · 저항 · 선 출처)은 뺀다 — 5분봉에 스윙 상자가 그려지지 않게.
 */
export function planFor(plan: AllHorizonsPlan | null, h: HorizonId | null): Plan | null {
  if (!plan || !isOtherHorizon(plan, h)) return plan;
  const l = slotLines(plan.horizons![h!]!);
  return {
    ...plan, horizon: h!, bias: l.side ?? "flat", entry_price: l.entry, stop_loss: l.stop, price_target: l.target,
    reward_risk: l.rr != null ? Math.round(l.rr * 100) / 100 : null, long_trigger: null, short_trigger: null,
    levels_side: l.side, levels_note: null, side_conflict: false, trigger_warnings: [], levels_source: undefined,
    support_resistance: undefined, playbooks: undefined,
  };
}

/** 그 칸이 어디서 왔는지 — 차트 제목 꼬리 "단타 · 데스크 지도" */
export function slotTitle(plan: AllHorizonsPlan | null, h: HorizonId | null): string | null {
  const s = isOtherHorizon(plan, h) ? plan!.horizons![h!] : null;
  return s ? `${s.label} · ${s.source === "model" ? "에이전트 분석" : "데스크 지도"}` : null;
}
