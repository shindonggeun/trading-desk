import { price } from "../format";
import { ACTION, BIAS, RATING, term } from "../korean";
import type { Plan } from "../types";

export function BiasBadge({ plan }: { plan: Plan }) {
  const b = BIAS[plan.bias];
  return (
    <span className={`bias bias-${plan.bias}`} title={b.desc}>
      {b.ko}
    </span>
  );
}

/** 표에 쓰는 짧은 등급: "비중 축소 Underweight". */
export function RatingText({ rating }: { rating: string }) {
  const t = term(RATING, rating);
  return (
    <span className="rating" title={t?.desc}>
      {t ? t.ko : rating}
      {t && rating !== "REVIEW" && <span className="muted small"> {rating}</span>}
    </span>
  );
}

type LevelKey = "entry_price" | "stop_loss" | "price_target";

/** 가격 사다리: 손절 · 진입 · 목표를 한 축에 놓는다. */
function Ladder({ plan }: { plan: Plan }) {
  const levels = [
    { key: "target", label: "목표", v: plan.price_target },
    { key: "entry", label: "진입", v: plan.entry_price },
    { key: "stop", label: "손절", v: plan.stop_loss },
  ].filter((l): l is { key: string; label: string; v: number } => l.v !== null);
  if (levels.length < 2) return null;
  const vs = levels.map((l) => l.v);
  const hi = Math.max(...vs);
  const lo = Math.min(...vs);
  const span = hi - lo || 1;
  return (
    <div className="ladder" aria-label="가격 레벨">
      <div className="ladder-axis" />
      {levels.map((l) => (
        <div key={l.key} className={`ladder-level ladder-${l.key}`} style={{ top: `${((hi - l.v) / span) * 100}%` }}>
          <span className="ladder-tick" />
          <span className="ladder-label">{l.label}</span>
          <span className="ladder-value">{price(l.v)}</span>
        </div>
      ))}
    </div>
  );
}

function Level({ plan, k }: { plan: Plan; k: LevelKey }) {
  const source = plan.levels_source?.[k];
  return (
    <dd>
      {price(plan[k])}
      {source === "atr" && (
        <span className="src src-atr" title="모델이 비운 값을 최근 가격 변동폭(ATR 14일)으로 계산했습니다">
          계산값
        </span>
      )}
      {source === "model" && <span className="src">모델 제시</span>}
    </dd>
  );
}

function pct(from: number | null, to: number | null): string {
  if (from === null || to === null || from === 0) return "";
  const p = ((to - from) / from) * 100;
  return ` (${p > 0 ? "+" : ""}${p.toFixed(1)}%)`;
}

export default function PlanCard({ plan, ticker, profile }: { plan: Plan; ticker: string; profile?: string }) {
  const rating = term(RATING, plan.rating);
  const action = term(ACTION, plan.trader_action);
  const anyAtr = Object.values(plan.levels_source ?? {}).includes("atr");
  return (
    <section className="card plan-card">
      <div className="plan-head">
        <div>
          <div className="muted small">
            최종 판단 · {ticker}
            {profile && ` · ${profile}`}
          </div>
          <div className="plan-rating">
            {rating ? rating.ko : plan.rating}
            {rating && plan.rating !== "REVIEW" && <span className="plan-rating-en"> {plan.rating}</span>}
          </div>
        </div>
        <BiasBadge plan={plan} />
      </div>
      {rating && <p className="plan-explain">{rating.desc}</p>}
      {action && (
        <p className="muted small">
          트레이더 행동: <strong>{action.ko}</strong> ({plan.trader_action}) — {action.desc}
        </p>
      )}
      <div className="plan-body">
        <dl className="plan-grid">
          <dt>진입가</dt>
          <Level plan={plan} k="entry_price" />
          <dt>손절가</dt>
          <Level plan={plan} k="stop_loss" />
          <dt>목표가</dt>
          <Level plan={plan} k="price_target" />
          <dt>손익비</dt>
          <dd>
            {plan.reward_risk !== null ? `${plan.reward_risk} : 1` : "—"}
            <span className="muted small">
              {plan.stop_loss !== null && ` 손절${pct(plan.entry_price, plan.stop_loss)}`}
              {plan.price_target !== null && ` 목표${pct(plan.entry_price, plan.price_target)}`}
            </span>
          </dd>
          <dt>보유 기간</dt>
          <dd>{plan.time_horizon ?? "—"}</dd>
          <dt>포지션 크기</dt>
          <dd>{plan.position_sizing ?? "—"}</dd>
        </dl>
        <Ladder plan={plan} />
      </div>
      {plan.executive_summary && <p className="plan-summary">{plan.executive_summary}</p>}
      <p className="muted small">
        LLM 이 낸 계획이며 주문은 연결되어 있지 않습니다.
        {anyAtr &&
          ` "계산값" 은 모델이 비운 자리를 분석일 종가와 ATR(14일 평균 변동폭 ${price(plan.atr ?? null)})로 채운 것입니다 — 손절 1.5 ATR, 목표 3 ATR.`}
        {plan.levels_error && ` 가격 데이터를 못 가져와 비운 값을 채우지 못했습니다(${plan.levels_error}).`}
        {" "}레버리지 상품은 기초자산 대비 배수만큼 손익이 커집니다.
      </p>
    </section>
  );
}
