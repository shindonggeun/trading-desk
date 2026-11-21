import type { AllHorizonsPlan, HorizonSlot } from "./allHorizons";
import type { HorizonId, Plan } from "./types";

export const DIRECTIONS = ["long", "short"] as const;
export type ScenarioSide = typeof DIRECTIONS[number];
export const SIDE_KO: Record<ScenarioSide, string> = { long: "롱", short: "숏" };

export interface ScenarioHistory {
  status: "available" | "insufficient" | "unavailable";
  n: number;
  win_rate: number | null;
  start: string | null;
  end: string | null;
  win_rule: string | null;
}

export interface DirectionScenario {
  status: "consider_entry" | "wait_for_conditions" | "no_setup";
  reason: string | null;
  regime: string | null;
  basis: string[];
  entry_price: number | null;
  entry_zone: [number, number] | null;
  stop_loss: number | null;
  targets: { role: string; price: number; rr: number | null }[];
  confirmation: { trigger_price: number | null; trigger_text: string | null };
  history: ScenarioHistory;
}

export const SCENARIO_STATUS: Record<DirectionScenario["status"], string> = {
  consider_entry: "진입 검토", wait_for_conditions: "조건 대기", no_setup: "적합한 진입 없음",
};

const REGIME: Record<string, string> = { uptrend: "상승 추세", downtrend: "하락 추세", range: "박스권", crash: "급락", unknown: "장세 판단 보류" };

export function scenarioRegime(regime: string | null): string | null {
  return regime ? REGIME[regime] ?? null : null;
}

export function historyText(history: ScenarioHistory): string {
  if (history.status === "available" && history.win_rate != null) {
    return `과거 승률 ${(history.win_rate * 100).toFixed(1)}% · ${history.n}건`;
  }
  return history.status === "insufficient" ? `과거 승률 표본 부족 · ${history.n}건` : "과거 승률 집계 없음";
}

export function scenarioAt(plan: AllHorizonsPlan | null, horizon: HorizonId | null, side: ScenarioSide | null) {
  return horizon && side ? plan?.horizons?.[horizon]?.scenarios?.[side] ?? null : null;
}

export function hasScenarios(slot: HorizonSlot): boolean {
  return DIRECTIONS.some((side) => !!slot.scenarios?.[side]);
}

/** 선택한 시나리오의 가격은 분석 종목 기준이다. 모델 계획·본주 가격·기존 조건선은 가져오지 않는다. */
export function scenarioPlan(plan: Plan, horizon: HorizonId, side: ScenarioSide, scenario: DirectionScenario): Plan {
  const priced = scenario.status !== "no_setup";
  const target = priced ? scenario.targets[0] : undefined;
  return {
    ...plan, horizon, bias: side, levels_side: side, entry_price: priced ? scenario.entry_price : null,
    stop_loss: priced ? scenario.stop_loss : null, price_target: target?.price ?? null, reward_risk: target?.rr ?? null,
    underlying: undefined, long_trigger: null, short_trigger: null, levels_note: null, side_conflict: false,
    levels_source: undefined, trigger_warnings: [], support_resistance: undefined, playbooks: undefined,
  };
}

export function scenarioExtraPrices(scenario: DirectionScenario): { price: number; label: string; kind: "entry" | "target" }[] {
  if (scenario.status === "no_setup") return [];
  const zone = (scenario.entry_zone ?? []).flatMap((value, i) =>
    value === scenario.entry_price || (i === 1 && value === scenario.entry_zone?.[0]) ? [] :
      [{ price: value, label: i === 0 ? "진입 구간 하단" : "진입 구간 상단", kind: "entry" as const }]);
  return [...zone, ...scenario.targets.slice(1).map((target, i) => (
    { price: target.price, label: `목표 ${i + 2}`, kind: "target" as const }
  ))];
}
