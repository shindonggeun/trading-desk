import { price } from "../format";
import type { Outcome, Plan, PlaybookOutcome, PlaybookSide, Playbooks, RegimeId } from "../types";
import { useEstimate, useLiveQuote } from "./PlanCard";

/** 롱(하따) · 숏(단타) 플레이북(#116 · #117) — 국면, 두 방향의 구역 · 사다리 · 확인 신호 · 손절 · 목표, 지금 가격 기준 상태. */

const REGIME: Record<RegimeId, string> = {
  crash: "급락", downtrend: "하락 추세", range: "박스권", uptrend: "상승 추세", unknown: "판단 보류",
};
const SETUP: Record<string, string> = {
  dip_catch: "하따(급락 받기)", range_low: "박스 하단", pullback: "눌림목", fade: "반등 매도", breakdown: "이탈 매도", none: "없음",
};
const CONDITION: Record<string, string> = {
  in_zone: "구역 안", rejection_wick: "아래꼬리", rsi_divergence: "RSI 다이버전스", higher_low: "저점 높임", reclaim: "구역 되찾기",
  volume_dry: "거래량 마름", climax_recent: "투매 직후", tape_hh_hl: "체결 고점 · 저점 높임",
  at_resistance: "저항 도달", rejection_bar: "윗꼬리 · 음봉", lower_high: "고점 낮춤", below_vwap: "VWAP 아래",
  below_ma20: "20선 아래", volume_on_break: "이탈 거래량", no_new_entry_late: "장 막판 신규 금지",
};
const SOURCE: Record<string, string> = { sr: "지지 · 저항", vp: "매물대", fib: "피보나치", trend: "추세선", ma: "이평선", vwap: "VWAP", atr: "ATR" };
const RESULT: Record<string, string> = {
  waiting: "대기", open: "보유 중", t1: "T1 도달", t2: "T2 도달", t1_be: "T1 뒤 본전", stop: "손절", time_stop: "시간 손절",
  flat: "당일 청산", expired: "만료", unfilled: "미체결", invalid: "채점 불가", unscorable: "채점 불가",
};

export type PlaybookState = "none" | "waiting" | "near" | "in_zone" | "confirmed" | "invalid";
const STATE: Record<PlaybookState, string> = {
  none: "타점 없음", waiting: "대기", near: "구역 근접", in_zone: "구역 안", confirmed: "확인됨", invalid: "무효",
};

const has = (side: PlaybookSide) => side.setup !== "none" && (!!side.zone || side.entries.length > 0 || !!side.trigger);

/**
 * 지금 가격으로 본 상태: 대기 → 구역 근접(1 ATR) → 구역 안 → 확인됨(롱 reclaim · 숏 rejection_bar) → 무효(손절 통과).
 * 숏은 구역이 저항(위)이고 손절이 위 — 방향만 뒤집는다.
 */
export function playbookState(side: PlaybookSide, dir: "long" | "short", p: number | null, atr: number): PlaybookState {
  if (!has(side)) return "none";
  if (p == null) return "waiting";
  if (side.stop != null && (dir === "long" ? p <= side.stop : p >= side.stop)) return "invalid";
  const confirmId = dir === "long" ? "reclaim" : "rejection_bar";
  if (side.conditions.some((c) => c.id === confirmId && c.met)) return "confirmed";
  const lo = side.zone?.low ?? side.trigger?.price ?? side.entries[0]?.price;
  const hi = side.zone?.high ?? side.trigger?.price ?? side.entries[0]?.price;
  if (lo == null || hi == null) return "waiting";
  if (p >= lo && p <= hi) return "in_zone";
  const gap = dir === "long" ? p - hi : lo - p;   // 롱 구역은 아래, 숏 구역은 위에서 다가온다
  return gap >= 0 && gap <= atr ? "near" : "waiting";
}

function Side({ side, dir, state, result }: {
  side: PlaybookSide; dir: "long" | "short"; state: PlaybookState; result?: PlaybookOutcome;
}) {
  const name = dir === "long" ? "롱" : "숏";
  if (!has(side)) {
    return (
      <div className={`pb-side pb-${dir} pb-empty`}>
        <div className="pb-side-head"><strong>{name}</strong><span className="pb-state pb-state-none">{STATE.none}</span></div>
        <p className="muted small">{side.reason ?? "이번 국면에는 이 방향 타점을 두지 않습니다."}</p>
      </div>
    );
  }
  const t1 = side.targets.find((t) => t.role === "t1");
  const t2 = side.targets.find((t) => t.role === "t2");
  return (
    <div className={`pb-side pb-${dir}`}>
      <div className="pb-side-head">
        <strong>{name} · {SETUP[side.setup] ?? side.setup}</strong>
        <span className={`pb-state pb-state-${state}`}>{STATE[state]}</span>
      </div>
      {side.reason && <p className="small pb-reason">{side.reason}</p>}
      <dl className="pb-grid">
        {side.zone && (
          <div className="pb-row">
            <dt>{dir === "long" ? "매수 구역" : "매도 구역"}</dt>
            <dd>
              <span className="num">{price(side.zone.low)} ~ {price(side.zone.high)}</span>
              <span className="muted small"> · 근거 {side.zone.members.slice(0, 3).map((m) => SOURCE[m.source] ?? m.source).join(" · ")}</span>
            </dd>
          </div>
        )}
        {side.trigger && (
          <div className="pb-row"><dt>트리거</dt><dd><span className="num">{price(side.trigger.price)}</span> <span className="muted small">{side.trigger.text}</span></dd></div>
        )}
        {side.entries.length > 0 && (
          <div className="pb-row">
            <dt>진입 사다리</dt>
            <dd className="pb-ladder">
              {side.entries.map((e) => (
                <span key={e.step}>{e.step}차 <span className="num">{price(e.price)}</span> <span className="muted small">({Math.round(e.share * 100)}%{e.kind === "stop" ? " · 돌파" : ""})</span></span>
              ))}
            </dd>
          </div>
        )}
        {side.stop != null && (
          <div className="pb-row"><dt>손절</dt><dd><span className="num">{price(side.stop)}</span>{side.avg_entry != null && <span className="muted small"> · 평균 진입 {price(side.avg_entry)}</span>}</dd></div>
        )}
        {(t1 || t2) && (
          <div className="pb-row">
            <dt>목표</dt>
            <dd className="pb-ladder">
              {[t1, t2].filter(Boolean).map((t) => (
                <span key={t!.role}>{t!.role.toUpperCase()} <span className="num">{price(t!.price)}</span>{t!.rr != null && <span className="muted small"> ({t!.rr}R{t!.action === "take_half" ? " · 절반" : ""})</span>}</span>
              ))}
            </dd>
          </div>
        )}
        {side.time_stop && (
          <div className="pb-row"><dt>시간 손절</dt><dd className="small">{side.time_stop.rule}</dd></div>
        )}
        <div className="pb-row">
          <dt>비중</dt>
          <dd className="small">{side.size_hint === "half" ? "평소의 절반" : "평소대로"}{side.flat_by_close ? " · 그날 안에 청산" : ""} · 점수 {side.score}</dd>
        </div>
      </dl>
      {side.conditions.length > 0 && (
        <ul className="pb-conditions" aria-label={`${name} 확인 신호`}>
          {side.conditions.map((c) => (
            <li key={c.id} className={c.how === "watch" ? "pb-watch" : c.met ? "pb-met" : "pb-unmet"}>
              <span aria-hidden>{c.how === "watch" ? "◌" : c.met ? "●" : "○"}</span>{" "}
              {CONDITION[c.id] ?? c.id}
              <span className="sr-only">{c.how === "watch" ? " — 직접 지켜볼 것" : c.met ? " — 충족" : " — 미충족"}</span>
            </li>
          ))}
        </ul>
      )}
      {result && result.status !== "waiting" && (
        <p className="small pb-result">채점: <strong>{RESULT[result.status] ?? result.status}</strong>{result.r != null && ` · ${result.r > 0 ? "+" : ""}${result.r}R`}</p>
      )}
    </div>
  );
}

export default function PlaybookCard({ plan, symbol, outcome }: { plan: Plan; symbol?: string; outcome?: Outcome | null }) {
  const pb: Playbooks | undefined = plan.playbooks;
  // 15초 현재가, 장 마감 중이면 추정가(#42) — 훅은 카드가 숨을 때도 같은 순서로
  const live = useLiveQuote(symbol, plan.live_price, !!pb);
  const est = useEstimate(symbol, !!pb);
  if (!pb) return null;
  const p = est?.estimate ?? live?.price ?? pb.frame.reference_price ?? null;
  const basis = est ? "추정가" : live ? "현재가" : "기준가";
  const atr = pb.frame.atr;
  const states = { long: playbookState(pb.long, "long", p, atr), short: playbookState(pb.short, "short", p, atr) };
  const hits = pb.regime.evidence.filter((e) => e.hit);
  return (
    <section className="card playbook" aria-labelledby="pb-title">
      <div className="pb-head">
        <h3 id="pb-title">플레이북</h3>
        <span className={`pb-regime pb-regime-${pb.regime.id}`}>{REGIME[pb.regime.id] ?? pb.regime.id}</span>
        {p != null && <span className="muted small">{basis} {price(p)} · {pb.frame.primary} 봉 기준</span>}
      </div>
      {hits.length > 0 && <p className="muted small pb-evidence">국면 근거: {hits.map((e) => e.key).join(" · ")}</p>}
      {pb.bias_note?.text && <p className="small pb-bias">거시 · 정책: {pb.bias_note.text}</p>}
      <div className="pb-sides">
        <Side side={pb.long} dir="long" state={states.long} result={outcome?.playbooks?.long} />
        <Side side={pb.short} dir="short" state={states.short} result={outcome?.playbooks?.short} />
      </div>
      <p className="muted small pb-note">가격은 계산이지 예측이 아닙니다. ◌ 는 화면이 판단하지 못해 직접 지켜볼 신호입니다.</p>
    </section>
  );
}
