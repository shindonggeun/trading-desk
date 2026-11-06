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
  flat: "당일 청산", unfilled: "미체결", invalid: "채점 불가", unscorable: "채점 불가", error: "채점 오류",
};

/**
 * 국면 근거(#129) — backend desk/playbook.py detect_regime 의 evidence key 를 읽는 문구로.
 * 모르는 key 는 그대로 보인다.
 */
const num = (v: number, d = 2) => String(Number(v.toFixed(d)));
const pct = (v: number) => `${v > 0 ? "+" : ""}${num(v * 100, 1)}%`;
const EVIDENCE: Record<string, { text: (v: number) => string; limit: (t: number) => string }> = {
  dd20: { text: (v) => `20일 고점 대비 ${pct(v)}`, limit: (t) => `${pct(t)} 이하` },
  dd20_atr: { text: (v) => `낙폭 ${num(v)} ATR`, limit: (t) => `${num(t)} ATR 이하` },
  down_streak: { text: (v) => `연속 하락 ${num(v, 0)}일`, limit: (t) => `${num(t, 0)}일 이상` },
  rsi_d: { text: (v) => `일봉 RSI ${num(v, 1)}`, limit: (t) => `${num(t, 0)} 이하` },
  ma20_atr: { text: (v) => `20일선 ${v < 0 ? "아래" : "위"} ${num(Math.abs(v))} ATR`, limit: (t) => `${num(Math.abs(t))} ATR 이상 아래` },
  vol_x: { text: (v) => `거래량 20일 평균의 ${num(v)}배`, limit: (t) => `${num(t)}배 이상` },
  gap_atr: { text: (v) => `시가 갭 ${num(v)} ATR`, limit: (t) => `${num(t)} ATR 이하` },
  vix: { text: (v) => `VIX ${num(v, 1)}`, limit: (t) => `${num(t, 0)} 이상` },
};
type Evidence = Playbooks["regime"]["evidence"][number];
export function evidenceText(e: Evidence): { text: string; limit: string | null } {
  const spec = EVIDENCE[e.key];
  if (!spec || typeof e.value !== "number") return { text: e.key, limit: null };
  return { text: spec.text(e.value), limit: typeof e.threshold === "number" ? spec.limit(e.threshold) : null };
}

export type PlaybookState = "none" | "waiting" | "near" | "in_zone" | "beyond" | "confirmed" | "invalid" | "closed";
const STATE: Record<PlaybookState, string> = {
  none: "타점 없음", waiting: "대기", near: "구역 근접", in_zone: "구역 안", beyond: "구역 지나침", confirmed: "확인됨", invalid: "무효", closed: "끝남",
};
/** 채점이 끝난 방향은 가격과 상관없이 — 손절 · 시간 손절은 무효, 목표 · 본전 · 당일 청산 · 미체결은 끝남 */
const LOST = new Set(["stop", "time_stop"]);
const ENDED = new Set(["t2", "t1_be", "flat", "unfilled"]);

const has = (side: PlaybookSide) => side.setup !== "none" && (!!side.zone || side.entries.length > 0 || !!side.trigger);

/**
 * 지금 가격으로 본 상태: 대기 → 구역 근접(1 ATR) → 구역 안 → 구역 지나침(손절 전) → 무효(손절 통과 · 채점 종료).
 * 확인됨(롱 reclaim · 숏 rejection_bar)은 분석 당시 값이라 가격이 구역 ± 1 ATR 안에 있을 때만 인정한다.
 * 숏은 구역이 저항(위)이고 손절이 위 — 방향만 뒤집는다.
 */
export function playbookState(side: PlaybookSide, dir: "long" | "short", p: number | null, atr: number,
  result?: PlaybookOutcome): PlaybookState {
  if (!has(side)) return "none";
  if (result && LOST.has(result.status)) return "invalid";
  if (result && ENDED.has(result.status)) return "closed";
  if (p == null) return "waiting";
  if (side.stop != null && (dir === "long" ? p <= side.stop : p >= side.stop)) return "invalid";
  const lo = side.zone?.low ?? side.trigger?.price ?? side.entries[0]?.price;
  const hi = side.zone?.high ?? side.trigger?.price ?? side.entries[0]?.price;
  if (lo == null || hi == null) return "waiting";
  const dist = p < lo ? lo - p : p > hi ? p - hi : 0;
  const confirmId = dir === "long" ? "reclaim" : "rejection_bar";
  if (dist <= atr && side.conditions.some((c) => c.id === confirmId && c.met === true)) return "confirmed";
  if (dist === 0) return "in_zone";
  // 롱은 구역 아래, 숏은 구역 위로 지나쳤지만 아직 손절 전 — 들어갔다면 가장 깊은 자리
  if (dir === "long" ? p < lo : p > hi) return "beyond";
  return dist <= atr ? "near" : "waiting";
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
                <span key={e.step}>{e.step}차 <span className="num">{price(e.price)}</span> <span className="muted small">({Math.round(e.share * 100)}%{e.kind === "stop" ? (dir === "long" ? " · 돌파" : " · 이탈") : ""})</span></span>
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
            <li key={c.id} className={c.how === "watch" || c.met == null ? "pb-watch" : c.met ? "pb-met" : "pb-unmet"}>
              <span aria-hidden>{c.how === "watch" ? "◌" : c.met == null ? "–" : c.met ? "●" : "○"}</span>{" "}
              {CONDITION[c.id] ?? c.id}
              <span className="sr-only">{c.how === "watch" ? " — 직접 지켜볼 것" : c.met == null ? " — 판단 불가(데이터 없음)" : c.met ? " — 충족" : " — 미충족"}</span>
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
  // 지난 거래일 run(symbol 없음)은 useLiveQuote 가 분석 당시 값을 돌려주므로 기준가로 "분석 당시" 라 적는다
  const p = symbol ? est?.estimate ?? live?.price ?? pb.frame.reference_price ?? null : pb.frame.reference_price ?? null;
  const basis = !symbol ? "분석 당시" : est ? "추정가" : live ? "현재가" : "기준가";
  const atr = pb.frame.atr;
  const res = outcome?.playbooks;
  const states = {
    long: playbookState(pb.long, "long", p, atr, res?.long),
    short: playbookState(pb.short, "short", p, atr, res?.short),
  };
  const hits = pb.regime.evidence.filter((e) => e.hit);
  const misses = pb.regime.evidence.filter((e) => !e.hit && e.value != null);
  return (
    <section className="card playbook" aria-labelledby="pb-title">
      <div className="pb-head">
        <h3 id="pb-title">플레이북</h3>
        <span className={`pb-regime pb-regime-${pb.regime.id}`}>{REGIME[pb.regime.id] ?? pb.regime.id}</span>
        {p != null && <span className="muted small">{basis} {price(p)} · {pb.frame.primary} 봉 기준</span>}
      </div>
      {hits.length > 0 && <p className="muted small pb-evidence">국면 근거: {hits.map((e) => evidenceText(e).text).join(" · ")}</p>}
      {misses.length > 0 && (
        <details className="pb-misses small">
          <summary className="muted">기준에 못 미친 것 {misses.length}개</summary>
          <ul>
            {misses.map((e) => {
              const { text, limit } = evidenceText(e);
              return <li key={e.key}>{text}{limit && <span className="muted"> (기준 {limit})</span>}</li>;
            })}
          </ul>
        </details>
      )}
      {pb.bias_note?.text && <p className="small pb-bias">거시 · 정책: {pb.bias_note.text}</p>}
      <div className="pb-sides">
        <Side side={pb.long} dir="long" state={states.long} result={outcome?.playbooks?.long} />
        <Side side={pb.short} dir="short" state={states.short} result={outcome?.playbooks?.short} />
      </div>
      <p className="muted small pb-note">가격은 계산이지 예측이 아닙니다. ◌ 는 직접 지켜볼 신호, – 는 데이터가 없어 판단하지 못한 신호입니다.</p>
    </section>
  );
}
