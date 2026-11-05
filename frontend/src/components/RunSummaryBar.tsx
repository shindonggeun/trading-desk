import { useState } from "react";
import { agentInfo, TEAM_LABEL } from "../agents";
import { duration, price, STATUS_LABEL, time } from "../format";
import { OPINION } from "../korean";
import type { DeskEvent, Plan, Run, RunStatus } from "../types";

/** 분석 중 지금 일하는 에이전트: 마지막 발언 뒤에 "thinking" 을 알린 에이전트, 아니면 마지막 발언자. */
function current(events: DeskEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.agent === "system") continue;
    if (e.type === "message" || (e.type === "status" && e.content === "thinking") || e.type === "tool") return e.agent;
  }
  return null;
}

function opinionOf(plan: Plan): string {
  if (plan.opinion) return plan.opinion;
  return plan.bias === "long" ? "buy" : plan.bias === "short" ? "sell" : plan.bias === "flat" ? "hold" : "review";
}

/**
 * 분석 화면 맨 위 결론 줄(#99): 의견 · 승률 · 진입 / 손절 / 목표 · 유효 시간 · 진행 상태.
 * 분석 중에는 지금 일하는 에이전트와 단계. 좁은 화면에서는 한 줄로 접히고 누르면 펼친다.
 */
export default function RunSummaryBar({ run, plan, status, events }: { run: Run; plan: Plan | null; status: RunStatus; events: DeskEvent[] }) {
  const [open, setOpen] = useState(false);
  const name = run.options.name ? `${run.options.name} ${run.ticker}` : run.ticker;
  const working = status === "running" || status === "queued";
  const who = working ? current(events) : null;
  const op = plan ? opinionOf(plan) : null;
  const opText = op ? OPINION[op]?.ko ?? op : null;
  const directional = plan && (op === "buy" || op === "sell");
  const failed = status === "failed";

  return (
    <div className={`run-bar ${open ? "run-bar-open" : ""}`}>
      <button type="button" className="run-bar-main" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="run-bar-name">{name}</span>
        {opText && !working ? (
          <span className={`run-bar-op opinion-${op}`}>{opText}</span>
        ) : (
          <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>
        )}
        {plan && !working && plan.win_probability != null && (
          <span className="run-bar-prob">승률 {Math.round(plan.win_probability * 100)}%</span>
        )}
        {plan && !working && (
          <span className="run-bar-levels num">
            {directional || (plan.long_trigger == null && plan.short_trigger == null) ? (
              <>
                진입 <strong>{price(plan.entry_price)}</strong> · 손절 <strong className="lv-stop">{price(plan.stop_loss)}</strong> · 목표{" "}
                <strong className="lv-target">{price(plan.price_target)}</strong>
              </>
            ) : (
              <>
                롱 {price(plan.long_trigger ?? null)} 돌파 · 숏 {price(plan.short_trigger ?? null)} 이탈
              </>
            )}
          </span>
        )}
        {working && (
          <span className="run-bar-now small">
            {who ? `${TEAM_LABEL[agentInfo(who).team]} · ${agentInfo(who).name}` : "준비 중"} · {duration(run.started_at, null)}
          </span>
        )}
        {failed && <span className="run-bar-now small">{run.error ?? "분석이 실패했습니다"}</span>}
      </button>
      <div className="run-bar-more small muted">
        {plan && !working && !directional && plan.entry_price != null && (plan.long_trigger != null || plan.short_trigger != null) && (
          <span>등급상 타점(참고) 진입 {price(plan.entry_price)} · 손절 {price(plan.stop_loss)} · 목표 {price(plan.price_target)}</span>
        )}
        {plan?.valid_until && <span>진입 유효 {time(plan.valid_until)}까지</span>}
        {!working && <span>{STATUS_LABEL[status]} · {duration(run.started_at, run.finished_at)}</span>}
        <span>{run.trade_date}</span>
      </div>
    </div>
  );
}
