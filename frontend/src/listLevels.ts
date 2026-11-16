import type { Plan } from "./types";

/**
 * #430 분석 기록 목록 한 줄의 진입 · 손절 · 목표 · 손익비.
 * - 데스크 계획(plan.summary, #221)이 방향을 정했으면 그 값 — 상세 화면과 같은 숫자다.
 * - 관망이면 조건가(롱 돌파 · 숏 이탈), 그것도 없으면 비운다.
 * - 그 밖엔 TradingAgents 의 등급상 타점을 "참고" 로 보이되, 모순인 모양
 *   (롱인데 손절 ≥ 진입, 손절 = 목표, 등급과 반대 방향)이면 숫자를 숨긴다.
 */
export interface ListLevels {
  entry: number | null;
  stop: number | null;
  target: number | null;
  rr: number | null;
  /** 관망일 때 데스크가 정한 조건가(롱 돌파 · 숏 이탈) */
  triggers: { long: number | null; short: number | null } | null;
  /** desk = 데스크 계획 값, reference = 등급상 타점(참고), watch = 관망 조건가, conflict = 모순이라 숨김, none = 값 없음 */
  kind: "desk" | "reference" | "watch" | "conflict" | "none";
  /** 지금 지시가 아닌 참고 값(등급상 타점, 또는 관찰 · 관망 판정의 데스크 계획) */
  ref: boolean;
}

const EMPTY: ListLevels = { entry: null, stop: null, target: null, rr: null, triggers: null, kind: "none", ref: false };

/**
 * 백엔드 desk/plan.py levels_side 와 같은 규칙: 손절 < 진입 = long, 손절 > 진입 = short, 목표는 있으면 그 방향 너머여야 한다.
 * 진입 · 손절이 없거나 모순이면 null.
 */
export function shapeOf(entry: number | null, stop: number | null, target: number | null): "long" | "short" | null {
  if (entry == null || stop == null || stop === entry) return null;
  const side = stop < entry ? "long" : "short";
  if (target != null && (side === "long" ? target <= entry : target >= entry)) return null;
  return side;
}

/**
 * #436 등급상 타점이 서로 모순(진입 · 손절이 있는데 방향이 안 서거나, 손절 = 목표) — 차트 · 알림 · 목록이 쓰지 않는다.
 * 값이 모자라기만 한 것은 모순이 아니다(있는 값만 쓴다).
 */
export function levelsContradict(plan: Plan | null | undefined): boolean {
  if (!plan) return false;
  const { entry_price: e, stop_loss: s, price_target: t } = plan;
  if (s != null && t != null && s === t) return true;
  return e != null && s != null && shapeOf(e, s, t) == null;
}

export function listLevels(plan: Plan | null | undefined): ListLevels {
  if (!plan) return EMPTY;
  const s = plan.summary;
  if (s?.side && s.stop != null) {
    const t1 = s.targets?.find((t) => t.role === "t1") ?? s.targets?.[0] ?? null;
    // 한 번에 산다면(single_entry)의 손익비는 그 가격 기준, 평균 단가 · 1차면 목표의 손익비(평균 단가 기준)
    const single = s.single_entry ?? null;
    const entry = single?.price ?? s.avg_entry ?? s.ladder?.[0]?.price ?? null;
    const rr = single ? single.rr : t1?.rr ?? null;
    if (entry != null && shapeOf(entry, s.stop, t1?.price ?? null) === s.side) {
      return { entry, stop: s.stop, target: t1?.price ?? null, rr, triggers: null, kind: "desk",
        ref: s.reference ?? (s.verdict === "watch" || s.verdict === "stand_aside") };
    }
  }
  const bias = plan.bias === "long" || plan.bias === "short" ? plan.bias : null;
  const long = plan.long_trigger ?? null;
  const short = plan.short_trigger ?? null;
  // 관망(방향 없음)이면 등급상 타점은 어느 쪽 계획도 아니다 — 조건가가 있으면 그것만
  if (bias == null && (long != null || short != null)) return { ...EMPTY, triggers: { long, short }, kind: "watch" };
  if (bias == null) return EMPTY;   // 조건가도 없는 관망 — 방향 숫자는 계획이 아니다
  const { entry_price: entry, stop_loss: stop, price_target: target } = plan;
  if (entry == null && stop == null && target == null) return EMPTY;
  // 숫자 모양이 모순이거나 등급 방향과 반대면 숨긴다(백엔드 side_conflict 와 같은 판정)
  const shape = shapeOf(entry, stop, target);
  if (levelsContradict(plan) || (shape != null && shape !== bias)) return { ...EMPTY, kind: "conflict" };
  return { entry, stop, target, rr: plan.reward_risk, triggers: null, kind: "reference", ref: true };
}
