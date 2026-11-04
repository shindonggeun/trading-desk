import { BIAS_LABEL, price } from "../format";
import type { Plan } from "../types";

export function BiasBadge({ plan }: { plan: Plan }) {
  return <span className={`bias bias-${plan.bias}`}>{BIAS_LABEL[plan.bias]}</span>;
}

/** Price ladder: stop, entry and target placed on one vertical scale. */
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

export default function PlanCard({ plan, ticker }: { plan: Plan; ticker: string }) {
  return (
    <section className="card plan-card">
      <div className="plan-head">
        <div>
          <div className="muted small">최종 판단 · {ticker}</div>
          <div className="plan-rating">{plan.rating}</div>
        </div>
        <BiasBadge plan={plan} />
      </div>
      <div className="plan-body">
        <dl className="plan-grid">
          <dt>진입가</dt>
          <dd>{price(plan.entry_price)}</dd>
          <dt>손절가</dt>
          <dd>{price(plan.stop_loss)}</dd>
          <dt>목표가</dt>
          <dd>{price(plan.price_target)}</dd>
          <dt>손익비</dt>
          <dd>{plan.reward_risk !== null ? `${plan.reward_risk} : 1` : "—"}</dd>
          <dt>기간</dt>
          <dd>{plan.time_horizon ?? "—"}</dd>
          <dt>비중</dt>
          <dd>{plan.position_sizing ?? "—"}</dd>
        </dl>
        <Ladder plan={plan} />
      </div>
      {plan.executive_summary && <p className="plan-summary">{plan.executive_summary}</p>}
      <p className="muted small">
        LLM이 낸 계획입니다. 비어 있는 값은 모델이 확신하지 못해 생략한 것입니다. 주문은 연결되어 있지 않습니다.
      </p>
    </section>
  );
}
