import { agentInfo } from "../agents";
import { price } from "../format";
import type { Plan, Stance } from "../types";

const STANCE_KO: Record<Stance, string> = { long: "롱", short: "숏", hold: "관망" };
const ENTRY_KO: Record<string, string> = { now: "지금 진입", limit: "눌림 지정가", stop: "돌파 시 진입" };

/** 의견(#64)과 등급으로 정한 행동: 롱 · 숏 · 관망 · 확인 필요. */
function action(plan: Plan): { key: string; label: string } {
  const op = plan.opinion ?? (plan.bias === "long" ? "buy" : plan.bias === "short" ? "sell" : plan.bias === "flat" ? "hold" : "review");
  if (op === "buy") return { key: "long", label: "롱" };
  if (op === "sell") return { key: "short", label: "숏" };
  if (op === "hold") return { key: "hold", label: "관망" };
  return { key: "review", label: "확인 필요" };
}

function Levels({ plan }: { plan: Plan }) {
  const rows: [string, number | null][] = [
    ["진입", plan.entry_price],
    ["손절", plan.stop_loss],
    ["목표", plan.price_target],
  ];
  return (
    <div className="cs-levels">
      {rows.map(([label, v]) => (
        <div key={label} className={`cs-level cs-level-${label}`}>
          <span className="muted small">{label}</span>
          <strong>{price(v)}</strong>
        </div>
      ))}
      <div className="cs-level">
        <span className="muted small">손익비</span>
        <strong>{plan.reward_risk != null ? `${plan.reward_risk} : 1` : "—"}</strong>
      </div>
    </div>
  );
}

function Triggers({ plan }: { plan: Plan }) {
  if (plan.long_trigger == null && plan.short_trigger == null) return null;
  return (
    <>
      <ul className="cs-triggers small">
        {plan.long_trigger != null && <li><span className="cs-long">롱</span> {price(plan.long_trigger)} 회복 · 돌파 시</li>}
        {plan.short_trigger != null && <li><span className="cs-short">숏</span> {price(plan.short_trigger)} 이탈 시</li>}
      </ul>
      {/* #311 이미 충족됐거나 롱 · 숏 기준이 엇갈린 트리거 */}
      {plan.trigger_warnings && plan.trigger_warnings.length > 0 && (
        <ul className="plan-trigger-warn small">
          {plan.trigger_warnings.map((w) => <li key={w}><span aria-hidden>⚠</span> {w}</li>)}
        </ul>
      )}
    </>
  );
}

const HOLDER_KO: Record<string, { label: string; desc: string }> = {
  hold: { label: "버티기", desc: "근거가 아직 살아 있습니다. 손절선 위에 있는 동안 들고 갑니다." },
  trim: { label: "약손절 · 비중 축소", desc: "일부를 덜어 위험을 줄이고, 나머지는 손절선으로 관리합니다." },
  exit: { label: "손절", desc: "판단이 보유 방향과 반대이거나 지켜야 할 레벨을 이미 깼습니다. 전량 정리를 권합니다." },
};

function Holder({ plan }: { plan: Plan }) {
  const h = plan.consensus?.holder;
  if (!h) return null;
  const t = HOLDER_KO[h.action];
  if (!t) return null;
  const pos = h.position;
  return (
    <div className={`cs-holder cs-holder-${h.action}`}>
      <div className="cs-holder-head">
        <span className="muted small">보유 중이라면</span>
        <strong className="cs-holder-label" title={t.desc}>{t.label}</strong>
      </div>
      {pos ? (
        <p className="small cs-holder-pos">
          내 보유 {pos.quantity.toLocaleString("ko-KR")}주{pos.average_price != null && ` · 평단 ${price(pos.average_price)}`}
          {pos.pnl_pct != null && (
            <> · 지금 <strong className={pos.pnl_pct >= 0 ? "cs-long" : "cs-short"}>{pos.pnl_pct >= 0 ? "+" : ""}{pos.pnl_pct}%</strong></>
          )}
        </p>
      ) : (
        <p className="muted small cs-holder-pos">보유 정보 없이 판단했습니다 — "내 보유 종목을 반영해서 판단" 을 켜고 분석하면 평단 기준으로 봅니다.</p>
      )}
      {(h.cut_level != null || h.trim_level != null) && (
        <ul className="cs-triggers small">
          {h.cut_level != null && <li><span className="cs-short">손절</span> {price(h.cut_level)} 이탈 시 전량 정리</li>}
          {h.trim_level != null && <li><span className="cs-hold-text">축소</span> {price(h.trim_level)} 에서 일부 덜기</li>}
        </ul>
      )}
      {h.reason && <p className="small cs-holder-reason">{h.reason}</p>}
    </div>
  );
}

/**
 * 분석 끝의 한 장 요약(#95): 결론과 타점, 롱 · 숏 · 관망 투표수, 양쪽 관점, 트레이더 · 리스크 팀 토론 요지.
 * 숫자는 모두 계획(plan)의 값이다 — 결론 정리는 레벨을 새로 쓰지 않는다.
 */
export default function ConsensusCard({ plan, ticker }: { plan: Plan; ticker: string }) {
  const c = plan.consensus;
  const act = action(plan);
  const directional = plan.bias === "long" || plan.bias === "short";
  const total = c ? c.tally.long + c.tally.short + c.tally.hold : 0;

  return (
    <section className="card consensus" aria-label="결론 요약">
      <h3>결론 요약</h3>
      <div className={`cs-action cs-${act.key}`}>
        <span className="cs-action-label">{act.label}</span>
        {(act.key === "long" || act.key === "short") && plan.entry_type && (
          <span className="muted small">{ENTRY_KO[plan.entry_type] ?? plan.entry_type}</span>
        )}
        {plan.time_horizon && <span className="muted small">보유 {plan.time_horizon}</span>}
      </div>
      {act.key === "long" || act.key === "short" ? (
        <Levels plan={plan} />
      ) : (
        <>
          <Triggers plan={plan} />
          {directional && plan.entry_price != null && (
            <div className="cs-ref">
              <span className="muted small">
                등급상 {plan.bias === "long" ? "롱" : "숏"} 타점(고확률 기준 미달 — 참고만)
              </span>
              <Levels plan={plan} />
            </div>
          )}
        </>
      )}
      {c?.conclusion && <p className="cs-conclusion">{c.conclusion}</p>}
      <Holder plan={plan} />

      {c && total > 0 ? (
        <>
          <div className="cs-votes-head">
            <span className="small">
              투표 <strong className="cs-long">롱 {c.tally.long}</strong> · <strong className="cs-short">숏 {c.tally.short}</strong> ·{" "}
              <strong className="cs-hold">관망 {c.tally.hold}</strong>
              {c.majority ? ` — ${STANCE_KO[c.majority]} 우세` : " — 동률"}
            </span>
            {c.subject !== ticker && <span className="muted small">{c.subject} 토론 기준</span>}
          </div>
          <div className="cs-bar" role="img" aria-label={`롱 ${c.tally.long}, 숏 ${c.tally.short}, 관망 ${c.tally.hold}`}>
            {(["long", "hold", "short"] as Stance[]).map((s) =>
              c.tally[s] ? (
                <span key={s} className={`cs-seg cs-seg-${s} ${c.majority === s ? "cs-seg-top" : ""}`} style={{ flexGrow: c.tally[s] }}>
                  {STANCE_KO[s]} {c.tally[s]}
                </span>
              ) : null,
            )}
          </div>
          <div className="cs-cases">
            <div className="cs-case cs-case-long">
              <h4>롱 관점</h4>
              {c.long_case.length ? <ul className="small">{c.long_case.map((p, i) => <li key={i}>{p}</li>)}</ul> : <p className="muted small">없음</p>}
            </div>
            <div className="cs-case cs-case-short">
              <h4>숏 관점</h4>
              {c.short_case.length ? <ul className="small">{c.short_case.map((p, i) => <li key={i}>{p}</li>)}</ul> : <p className="muted small">없음</p>}
            </div>
          </div>
          {c.debate && (
            <p className="small cs-debate">
              <span className="muted">토론 요지 </span>
              {c.debate}
            </p>
          )}
          <details className="cs-detail">
            <summary className="small">에이전트별 투표 {total}표</summary>
            <ul className="cs-vote-list small">
              {c.votes.map((v) => (
                <li key={v.agent}>
                  <span className={`cs-chip cs-chip-${v.stance}`}>{STANCE_KO[v.stance]}</span>
                  <strong>{agentInfo(v.agent).name}</strong>
                  {v.fixed && <span className="muted" title="등급 라벨에서 읽은 표"> · 등급</span>}
                  {v.reason && <span className="muted"> — {v.reason}</span>}
                </li>
              ))}
            </ul>
          </details>
        </>
      ) : (
        <p className="muted small">
          {plan.consensus_error ? "결론 정리가 실패해 투표 · 관점 요약이 없습니다." : "이 분석에는 투표 · 관점 요약이 없습니다(이전 버전 분석)."}
        </p>
      )}
    </section>
  );
}
