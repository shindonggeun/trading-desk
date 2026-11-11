import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type {
  EconEvent, HorizonId, LivePlaybooks, LivePlaybookSide, LowerTrigger, Outcome, Plan, PlanTrigger, PlaybookOutcome, PlaybookSide,
  PlaybookCalibration, Playbooks, RegimeId, RiskPhase, RiskRegime,
} from "../types";
import { useEstimate, useLiveQuote } from "./PlanCard";

/** 서버 캐시 TTL(desk/horizons.py sr_ttl_sec)에 맞춘 폴링 간격 — 더 자주 불러도 같은 값이다 */
const POLL_MS: Record<HorizonId, number> = { scalp: 30_000, intraday: 60_000, short_swing: 120_000, swing: 300_000 };

/**
 * #132: 아직 열린 거래일 run 의 플레이북을 지금 값으로(#128). 탭이 숨으면 쉬고 돌아오면 바로 부른다.
 * 실패하면 마지막으로 받은 값을 그대로 둔다(없으면 null — 카드는 분석 당시 값으로).
 */
export function useLivePlaybooks(runId: string | undefined, horizon: HorizonId | undefined, enabled: boolean): LivePlaybooks | null {
  const [data, setData] = useState<LivePlaybooks | null>(null);
  useEffect(() => {
    setData(null);
    if (!runId || !enabled) return;
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      // 거의 동시에 부른 두 응답이 거꾸로 와도 더 오래된 계산(as_of)으로 되돌아가지 않게
      api.livePlaybooks(runId).then((d) => alive && setData((old) => (old && old.as_of > d.as_of ? old : d)), () => {});
    };
    load();
    const timer = setInterval(load, POLL_MS[horizon ?? "swing"] ?? POLL_MS.swing);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
    };
  }, [runId, horizon, enabled]);
  return data;
}

/** "3분째" · "2시간째" — 상태가 이어진 시간 */
function since(iso: string | null, now: number): string | null {
  if (!iso) return null;
  const min = Math.floor((now - Date.parse(iso)) / 60_000);
  if (!Number.isFinite(min) || min < 1) return "방금";
  if (min < 60) return `${min}분째`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}시간째` : `${Math.floor(h / 24)}일째`;
}

const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });

/** 롱(하따) · 숏(단타) 플레이북(#116 · #117) — 국면, 두 방향의 구역 · 사다리 · 확인 신호 · 손절 · 목표, 지금 가격 기준 상태. */

export const REGIME: Record<RegimeId, string> = {
  crash: "급락", downtrend: "하락 추세", range: "박스권", uptrend: "상승 추세", unknown: "판단 보류",
};
export const SETUP: Record<string, string> = {
  dip_catch: "하따(급락 받기)", range_low: "박스 하단", pullback: "눌림목", fade: "반등 매도", breakdown: "이탈 매도",
  value_swing: "과매도 스윙", none: "없음",
};
const CONDITION: Record<string, string> = {
  in_zone: "구역 안", rejection_wick: "아래꼬리", rsi_divergence: "RSI 다이버전스", higher_low: "저점 높임", reclaim: "구역 되찾기",
  volume_dry: "거래량 마름", climax_recent: "투매 직후", tape_hh_hl: "체결 고점 · 저점 높임",
  at_resistance: "저항 도달", rejection_bar: "윗꼬리 · 음봉", lower_high: "고점 낮춤", below_vwap: "VWAP 아래",
  below_ma20: "20선 아래", volume_on_break: "이탈 거래량", no_new_entry_late: "장 막판 신규 금지",
  event_clear: "경제 일정 통과",
  vs_oversold: "과매도", vs_depth: "낙폭", vs_base: "바닥 구조", vs_fear: "시장 공포", vs_flows: "수급 · 뉴스",
};
const SOURCE: Record<string, string> = { sr: "지지 · 저항", vp: "매물대", fib: "피보나치", trend: "추세선", ma: "이평선", vwap: "VWAP", avwap: "앵커드 VWAP", gap: "갭 메움", atr: "ATR" };
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

/**
 * `trigger` 는 플레이북이 정한 진입가(이탈 매도 등), `lower_trigger` 는 실시간 플레이북이 하위 봉으로 읽은
 * 트리거(#149)다(#193 에서 키를 나눴다). 나누기 전 서버가 `trigger` 에 하위 봉 트리거를 실어 보내도
 * 계획 트리거로 잘못 읽지 않게 text 가 있을 때만 계획 트리거로 본다.
 */
const planTrigger = (side?: PlaybookSide | null): PlanTrigger | null =>
  side?.trigger && "text" in side.trigger ? side.trigger : null;
const lowerTrigger = (side?: LivePlaybookSide | null): LowerTrigger | null => side?.lower_trigger ?? null;

const has = (side: PlaybookSide) => side.setup !== "none" && (!!side.zone || side.entries.length > 0 || !!planTrigger(side));

/** #149 하위 봉 트리거 — 상태 · 신호(롱 / 숏 문구가 거울) */
const TRIGGER_STATE: Record<LowerTrigger["state"], string> = { armed: "신호 대기", triggered: "발동", invalid: "무효" };
const SIGNAL: Record<NonNullable<LowerTrigger["signal"]>, { long: string; short: string }> = {
  break: { long: "직전 봉 고점 돌파", short: "직전 봉 저점 이탈" },
  vwap_reclaim: { long: "VWAP 되찾기", short: "VWAP 아래로" },
  absorption: { long: "투매 봉 저점 지킴", short: "급등 봉 고점 막힘" },
  higher_low: { long: "저점 높임", short: "고점 낮춤" },
};

/** #146 투매 국면별로 플레이북에 무엇이 달라지는지 */
const RISK_RULE: Record<RiskPhase, string> = {
  selling: "하따 사다리는 1차만, 2 · 3차는 끝물 신호까지 보류",
  exhausting: "사다리는 계획대로, 바닥 반등에 숏은 쉼",
  calm: "시장 전체 투매 없음 — 종목 플레이북 그대로",
};

const whenLocal = (iso: string) =>
  new Date(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit" });

/** #148 다음 주요(시장 전체) 경제 발표 — 열린 거래일 카드에서만 한 번 부른다. 실패하면 null */
function useNextEvent(enabled: boolean): EconEvent | null {
  const [next, setNext] = useState<EconEvent | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    api.econCalendar().then((c) => {
      const now = Date.now();
      if (alive) setNext(c.events.find((e) => e.importance >= 2 && Date.parse(e.at) > now) ?? null);
    }, () => {});
    return () => { alive = false; };
  }, [enabled]);
  return next;
}

function RiskGauge({ risk }: { risk: RiskRegime }) {
  return (
    <details className={`pb-risk pb-risk-${risk.phase}`}>
      <summary>
        <span className="pb-risk-name small">시장 투매 점수</span>
        <span className="pb-risk-bar" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={risk.score}
          aria-label={`시장 투매 점수 ${risk.score} / 100, ${risk.label}`}>
          <span style={{ width: `${Math.max(0, Math.min(100, risk.score))}%` }} />
        </span>
        <strong className="num">{risk.score}</strong>
        <span className="pb-risk-label">{risk.label}</span>
      </summary>
      <div className="pb-risk-body small">
        <p className="pb-risk-rule">{RISK_RULE[risk.phase]}</p>
        <ul>
          {risk.parts.map((p) => <li key={p.id}><span className="num">{p.score}</span> {p.text}</li>)}
        </ul>
        {risk.signals.length > 0 && <p>끝물 신호: {risk.signals.map((s) => s.text).join(" · ")}</p>}
        <p className="muted">
          {risk.market === "KR" ? "한국" : "미국"} 지수 기준{risk.as_of ? ` · ${risk.as_of} 종가` : ""} · 최근 10거래일 최고 {risk.peak}
          {risk.missing.length > 0 && ` · 못 받은 지표 ${risk.missing.join(", ")}`}
        </p>
      </div>
    </details>
  );
}

function LowerTriggerRow({ t, dir, interval }: { t: LowerTrigger; dir: "long" | "short"; interval?: string }) {
  const signal = t.signal ? SIGNAL[t.signal]?.[dir] ?? t.signal : null;
  return (
    <div className="pb-row">
      <dt>진입 신호{interval ? <span className="muted"> ({interval})</span> : null}</dt>
      <dd>
        <span className={`pb-trig pb-trig-${t.state}`}>{TRIGGER_STATE[t.state] ?? t.state}</span>{" "}
        {t.price != null ? (
          <>
            <span className="num">{price(t.price)}</span>
            <span className="muted small">
              {signal && ` · ${signal}`}
              {t.invalidation != null && ` · 무효화 ${price(t.invalidation)}`}
              {t.rr_t1 != null && ` · T1까지 ${t.rr_t1}R`}
            </span>
          </>
        ) : (
          <span className="muted small">{t.state === "armed" ? "구역 터치 — 하위 봉 반전 신호를 기다리는 중" : "손절이 먼저 닿음"}</span>
        )}
        {t.preferred && t.state === "armed" && (
          <span className="muted small pb-trig-hint"> 구역 지정가보다 이 신호를 기다리는 쪽이 백테스트에서 나았습니다</span>
        )}
      </dd>
    </div>
  );
}

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
  const lo = side.zone?.low ?? planTrigger(side)?.price ?? side.entries[0]?.price;
  const hi = side.zone?.high ?? planTrigger(side)?.price ?? side.entries[0]?.price;
  if (lo == null || hi == null) return "waiting";
  const dist = p < lo ? lo - p : p > hi ? p - hi : 0;
  const confirmId = dir === "long" ? "reclaim" : "rejection_bar";
  if (dist <= atr && side.conditions.some((c) => c.id === confirmId && c.met === true)) return "confirmed";
  if (dist === 0) return "in_zone";
  // 롱은 구역 아래, 숏은 구역 위로 지나쳤지만 아직 손절 전 — 들어갔다면 가장 깊은 자리
  if (dir === "long" ? p < lo : p > hi) return "beyond";
  return dist <= atr ? "near" : "waiting";
}

const CAL_LEVEL: Record<NonNullable<PlaybookCalibration["level"]>, string> = {
  band: "같은 셋업 · 국면 · 점수대", regime: "같은 셋업 · 국면", setup: "같은 셋업 전체",
};

/** #147 데스크 실적 — 이 셋업이 지금까지 T1 에 먼저 닿은 비율과 비용 차감 기대 R. 표본이 모자라면 숫자를 내지 않는다 */
function CalibrationRow({ cal }: { cal: PlaybookCalibration }) {
  if (cal.thin || cal.p_t1 == null || cal.exp_r == null) {
    return (
      <div className="pb-row">
        <dt>데스크 실적</dt>
        <dd className="small"><span className="pb-cal-thin">표본 부족</span> <span className="muted">({cal.n}건 — 확률을 내지 않습니다)</span></dd>
      </div>
    );
  }
  const r = `${cal.exp_r > 0 ? "+" : ""}${cal.exp_r.toFixed(2)}R`;
  return (
    <div className="pb-row">
      <dt>데스크 실적</dt>
      <dd className="small">
        T1 먼저 <strong className="num">{Math.round(cal.p_t1 * 100)}%</strong>
        {cal.p_range && <span className="muted"> ({Math.round(cal.p_range[0] * 100)}~{Math.round(cal.p_range[1] * 100)}%)</span>}
        {" · "}기대 <strong className={`num ${cal.exp_r > 0 ? "up" : cal.exp_r < 0 ? "down" : ""}`}>{r}</strong>
        <span className="muted"> · {cal.n.toLocaleString("ko-KR")}건{cal.level ? `, ${CAL_LEVEL[cal.level]}` : ""}{cal.live_n ? ` · 운영 ${cal.live_n}건 포함` : ""}</span>
        {cal.negative && <span className="pb-cal-neg"> 실적상 기대 R 이 음수 — 근거를 더 갖추고 비중을 줄이세요</span>}
      </dd>
    </div>
  );
}

/** #167 과매도 스윙 근거 묶음 · 세부 지표 이름 */
const VS_GROUP: Record<string, string> = {
  oversold: "과매도", depth: "낙폭", base: "바닥 구조", fear: "시장 공포", flows: "수급 · 뉴스", record: "데스크 실적 양수",
};
const VS_CHECK: Record<string, string> = {
  rsi_daily: "일봉 RSI", rsi_weekly: "주봉 RSI", rsi_divergence: "RSI 상승 다이버전스", bb_reentry: "볼린저 하단 복귀",
  below_ma20: "20일선 아래 이격", down_streak: "연속 하락", drawdown_pctl: "52주 낙폭 분위", below_ma200: "200일선 아래 이격",
  weekly_support: "주봉 지지 근처", old_crash_low: "과거 폭락 저점 근처", capitulation_then_dry: "투매 거래량 뒤 마름",
  new_low_taken_back: "신저가 뒤 되찾기", selling_score: "시장 투매 점수", vix: "VIX 공포", flows_news: "수급 · 뉴스(아직 계산 안 함)",
};
const mark = (met: boolean | null | undefined) => (met == null ? "–" : met ? "●" : "○");
const cls = (met: boolean | null | undefined) => (met == null ? "pb-watch" : met ? "pb-met" : "pb-unmet");
const shown = (v: unknown) => (typeof v === "number" ? String(Number(v.toFixed(2))) : typeof v === "string" ? v : null);

/** 과매도 스윙(#167): 승부인지, 왜인지(근거 묶음 · 세부 지표), 한 번에 잃을 수 있는 계좌 비율과 맞춘 비중, 승부 해제 조건 */
function ValueSwingBlock({ side }: { side: PlaybookSide }) {
  const conv = side.conviction;
  const budget = side.risk_budget;
  return (
    <div className="pb-vs">
      {conv && (
        <>
          <p className="small pb-vs-head">
            <span className={`pb-vs-badge ${conv.active ? "pb-vs-on" : ""}`}>{conv.active ? "승부 타점" : "일반 과매도 스윙"}</span>{" "}
            확신 <strong className="num">{conv.score}</strong><span className="muted">/100</span>
            {!conv.active && <span className="muted"> — 승부는 묶음 4개 이상 + 데스크 실적 양수일 때</span>}
          </p>
          <ul className="pb-conditions" aria-label="과매도 스윙 근거 묶음">
            {conv.checklist.map((c) => (
              <li key={c.id} className={cls(c.met)}>
                <span aria-hidden>{mark(c.met)}</span> {VS_GROUP[c.id] ?? c.id}
                <span className="sr-only">{c.met == null ? " — 판단 불가" : c.met ? " — 충족" : " — 미충족"}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {budget && (
        <p className="small pb-vs-budget">
          위험 예산: 한 번에 계좌의 <strong className="num">{budget.account_pct}%</strong>까지 잃게 —{" "}
          권장 비중 계좌의 <strong className="num">{budget.position_pct}%</strong>
          <span className="muted"> (평균 진입 → 손절 거리로 계산{budget.kind === "conviction" ? ", 승부라 예산 2배" : ""})</span>
        </p>
      )}
      {side.replaced && <p className="muted small">원래 셋업 {SETUP[side.replaced] ?? side.replaced} 대신 과매도 스윙으로 냈습니다.</p>}
      {(side.evidence || conv?.release.length) && (
        <details className="pb-misses small">
          <summary className="muted">근거 세부 · 승부 해제 조건</summary>
          {side.evidence && (
            <ul>
              {Object.entries(side.evidence).map(([g, ev]) => (
                <li key={g}>
                  <span className={cls(ev.met)}>{mark(ev.met)} {VS_GROUP[g] ?? g}</span>
                  <span className="muted">
                    {" — "}
                    {ev.checks.map((c) => `${mark(c.met)} ${VS_CHECK[c.id] ?? c.id}${shown(c.value) ? ` ${shown(c.value)}` : ""}`).join(" · ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {conv && conv.release.length > 0 && <p className="pb-vs-release">해제: {conv.release.join(" · ")}</p>}
        </details>
      )}
    </div>
  );
}

function Side({ side, dir, state, result, live, lower, interval, cal }: {
  side: PlaybookSide; dir: "long" | "short"; state: PlaybookState; result?: PlaybookOutcome;
  live?: { since: string | null; stale: boolean; note: string | null };
  /** #149 실시간 하위 봉 트리거와 그 봉 간격 */
  lower?: LowerTrigger | null; interval?: string;
  /** #147 보정 — 실시간 응답에 없으면 분석 당시 값 */
  cal?: PlaybookCalibration | null;
}) {
  const name = dir === "long" ? "롱" : "숏";
  if (!has(side)) {
    return (
      <div className={`pb-side pb-${dir} pb-empty`}>
        <div className="pb-side-head">
          <strong>{name}</strong>
          <span className="pb-state pb-state-none">{side.gate ? "시장 위험으로 쉼" : STATE.none}</span>
        </div>
        <p className="muted small">{side.reason ?? "이번 국면에는 이 방향 타점을 두지 않습니다."}</p>
        {side.gate?.setup && <p className="muted small">원래 셋업: {SETUP[side.gate.setup] ?? side.gate.setup}</p>}
      </div>
    );
  }
  const trig = planTrigger(side);
  const gateHeld = side.gate?.held ?? [];
  const t1 = side.targets.find((t) => t.role === "t1");
  const t2 = side.targets.find((t) => t.role === "t2");
  const t3 = side.targets.find((t) => t.role === "t3");
  // 과매도 스윙의 근거 묶음(vs_*)은 위 블록의 체크리스트가 보여 준다 — 같은 것을 두 번 그리지 않는다
  const conditions = side.conviction ? side.conditions.filter((c) => !c.id.startsWith("vs_")) : side.conditions;
  return (
    <div className={`pb-side pb-${dir}`}>
      <div className="pb-side-head">
        <strong>{name} · {SETUP[side.setup] ?? side.setup}{side.conviction?.active && <span className="pb-vs-badge pb-vs-on pb-vs-inline">승부</span>}</strong>
        <span className={`pb-state pb-state-${state}`}>{STATE[state]}</span>
      </div>
      {live && (live.since || live.stale || live.note) && (
        <p className="muted small pb-live">
          {live.since && <>이 상태 {live.since}</>}
          {live.stale && <span className="pb-stale">{live.since ? " · " : ""}시세 실패 — 마지막 종가 기준</span>}
          {live.note && <span className="pb-stale">{live.since || live.stale ? " · " : ""}{live.note}</span>}
        </p>
      )}
      {side.reason && <p className="small pb-reason">{side.reason}</p>}
      {side.gate && <p className="small pb-gate">{side.gate.text}</p>}
      {side.setup === "value_swing" && <ValueSwingBlock side={side} />}
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
        {trig && (
          <div className="pb-row"><dt>트리거</dt><dd><span className="num">{price(trig.price)}</span> <span className="muted small">{trig.text}</span></dd></div>
        )}
        {lower && <LowerTriggerRow t={lower} dir={dir} interval={interval} />}
        {side.entries.length + gateHeld.length > 0 && (
          <div className="pb-row">
            <dt>진입 사다리</dt>
            <dd className="pb-ladder">
              {side.entries.map((e) => (
                <span key={e.step} className={e.held ? "pb-held" : undefined}>
                  {e.step}차 <span className="num">{price(e.price)}</span>{" "}
                  <span className="muted small">({Math.round(e.share * 100)}%{e.kind === "stop" ? (dir === "long" ? " · 돌파" : " · 이탈") : ""}{e.held === "event" ? " · 일정 보류" : ""})</span>
                </span>
              ))}
              {gateHeld.map((e) => (
                <span key={`held-${e.step}`} className="pb-held">
                  {e.step}차 <span className="num">{price(e.price)}</span> <span className="muted small">({Math.round(e.share * 100)}% · 투매 보류)</span>
                </span>
              ))}
            </dd>
          </div>
        )}
        {side.stop != null && (
          <div className="pb-row"><dt>손절</dt><dd><span className="num">{price(side.stop)}</span>{side.avg_entry != null && <span className="muted small"> · 평균 진입 {price(side.avg_entry)}</span>}</dd></div>
        )}
        {(t1 || t2 || t3) && (
          <div className="pb-row">
            <dt>목표</dt>
            <dd className="pb-ladder">
              {[t1, t2, t3].filter(Boolean).map((t) => (
                <span key={t!.role}>{t!.role.toUpperCase()} <span className="num">{price(t!.price)}</span>{(t!.rr != null || t!.action === "trail") && <span className="muted small"> ({t!.rr != null ? `${t!.rr}R` : ""}{t!.action === "take_half" ? " · 절반" : ""}{t!.action === "trail" ? `${t!.rr != null ? " · " : ""}${t!.trail_atr ?? 2} ATR 추적 손절` : ""})</span>}</span>
              ))}
            </dd>
          </div>
        )}
        {side.time_stop && (
          <div className="pb-row"><dt>시간 손절</dt><dd className="small">{side.time_stop.rule}</dd></div>
        )}
        {side.exit && (
          <div className="pb-row"><dt>청산</dt><dd className="small">{side.exit.text}</dd></div>
        )}
        <div className="pb-row">
          <dt>비중</dt>
          <dd className="small">{side.size_hint === "half" ? "평소의 절반" : "평소대로"}{side.flat_by_close ? " · 그날 안에 청산" : ""} · 점수 {side.score}</dd>
        </div>
        {cal && <CalibrationRow cal={cal} />}
      </dl>
      {conditions.length > 0 && (
        <ul className="pb-conditions" aria-label={`${name} 확인 신호`}>
          {conditions.map((c) => (
            <li key={c.id} className={c.how === "watch" || c.met == null ? "pb-watch" : c.met ? "pb-met" : "pb-unmet"}>
              <span aria-hidden>{c.how === "watch" ? "◌" : c.met == null ? "–" : c.met ? "●" : "○"}</span>{" "}
              {CONDITION[c.id] ?? c.id}
              <span className="sr-only">{c.how === "watch" ? " — 직접 지켜볼 것" : c.met == null ? " — 판단 불가(데이터 없음)" : c.met ? " — 충족" : " — 미충족"}</span>
            </li>
          ))}
        </ul>
      )}
      {result && result.status !== "waiting" && (
        <p className="small pb-result">채점: <strong>{result.trailed ? "T1 뒤 추적 청산" : RESULT[result.status] ?? result.status}</strong>{result.r != null && ` · ${result.r > 0 ? "+" : ""}${result.r}R`}</p>
      )}
    </div>
  );
}

export default function PlaybookCard({ plan, symbol, outcome, runId, horizon }: {
  plan: Plan; symbol?: string; outcome?: Outcome | null; runId?: string;
  /** 폴링 간격용 — 서버처럼 plan.horizon, 없으면 run 옵션 */
  horizon?: HorizonId;
}) {
  const saved: Playbooks | undefined = plan.playbooks;
  // 15초 현재가, 장 마감 중이면 추정가(#42) — 훅은 카드가 숨을 때도 같은 순서로
  const live = useLiveQuote(symbol, plan.live_price, !!saved);
  const est = useEstimate(symbol, !!saved);
  // #132: 열린 거래일(symbol 있음)에만 지금 값으로 — 지난 run 은 부르지 않는다
  const fresh = useLivePlaybooks(runId, horizon ?? plan.horizon, !!saved && !!symbol);
  const nextEvent = useNextEvent(!!saved && !!symbol);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!fresh) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(t);
  }, [fresh]);
  if (!saved) return null;
  // levels_from "fresh" 는 분석의 레벨이 아니라 새로 계산한 것 — 이 카드(분석의 플레이북)에는 쓰지 않는다
  const cur: LivePlaybooks | null = fresh && fresh.levels_from === "run" ? fresh : null;
  const pb: Playbooks = cur ?? saved;
  const lower = { long: lowerTrigger(cur?.long), short: lowerTrigger(cur?.short) };
  const interval = cur?.frame.trigger_interval;
  // 투매 점수는 분석 때 값(분석의 플레이북을 거른 것) — 실시간 응답은 분석이 있으면 다시 매기지 않는다
  const risk = cur?.risk_regime ?? saved.risk_regime ?? null;
  const hold = cur ? cur.event_hold ?? null : saved.event_hold ?? null;
  // 지난 거래일 run(symbol 없음)은 useLiveQuote 가 분석 당시 값을 돌려주므로 기준가로 "분석 당시" 라 적는다
  const p = symbol ? est?.estimate ?? live?.price ?? pb.frame.reference_price ?? null : pb.frame.reference_price ?? null;
  const basis = !symbol ? "분석 당시" : est ? "추정가" : live ? "현재가" : "기준가";
  const atr = pb.frame.atr;
  const res = outcome?.playbooks;
  // 채점이 끝난 방향은 채점 결과, 아니면 서버의 지금 상태, 없으면(실패 · null) 화면 계산
  // 상태 우선순위: 채점 결과 → 실제 현재가로 본 손절 통과(서버는 최대 TTL 늦다) → 서버 state
  // (시세 실패로 마지막 종가 기준이면 화면 가격이 더 새롭다) → 화면 계산
  const local = { long: playbookState(pb.long, "long", p, atr, res?.long), short: playbookState(pb.short, "short", p, atr, res?.short) };
  const scored = (dir: "long" | "short") => { const s = res?.[dir]?.status; return !!s && (LOST.has(s) || ENDED.has(s)); };
  const stateOf = (dir: "long" | "short"): PlaybookState => {
    const srv = cur?.[dir];
    if (local[dir] === "none" || scored(dir) || !srv || srv.state == null) return local[dir];
    if (local[dir] === "invalid" && !est && live) return local[dir];
    if (srv.stale && (est || live)) return local[dir];
    return srv.state;
  };
  const states = { long: stateOf("long"), short: stateOf("short") };
  const liveOf = (s: LivePlaybookSide, dir: "long" | "short") => ({
    // 경과 시간은 보이는 상태가 서버 상태와 같을 때만(채점 · 화면 판정이 이기면 그 시간이 아니다)
    since: !s.stale && states[dir] === s.state ? since(s.state_since, now) : null,
    stale: s.stale,
    // 장외 추정가로는 손절을 넘었지만 서버(실제 시세)는 아직 — 두 기준이 다르다는 것을 밝힌다
    note: est && local[dir] === "invalid" && states[dir] !== "invalid" ? "추정가로는 손절 너머" : null,
  });
  const hits = pb.regime.evidence.filter((e) => e.hit);
  const misses = pb.regime.evidence.filter((e) => !e.hit && e.value != null);
  return (
    <section className="card playbook" aria-labelledby="pb-title">
      <div className="pb-head">
        <h3 id="pb-title">플레이북</h3>
        <span className={`pb-regime pb-regime-${pb.regime.id}`}>{REGIME[pb.regime.id] ?? pb.regime.id}</span>
        {p != null && <span className="muted small">{basis} {price(p)} · {pb.frame.primary} 봉 기준</span>}
        {cur && <span className="muted small pb-asof">조건 · 상태 {hhmm(cur.as_of)} 시세 기준</span>}
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
      {risk && <RiskGauge risk={risk} />}
      {hold ? (
        <p className="small pb-event pb-event-hold" role="status">
          <strong>진입 보류</strong> · {hold.label} {whenLocal(hold.at)} 앞뒤 — 사다리 1차만, 나머지는 발표 반응을 본 뒤
        </p>
      ) : nextEvent && (
        <p className="muted small pb-event">다음 주요 일정: {nextEvent.label} · {whenLocal(nextEvent.at)}</p>
      )}
      <div className="pb-sides">
        <Side side={pb.long} dir="long" state={states.long} result={outcome?.playbooks?.long} live={cur ? liveOf(cur.long, "long") : undefined}
          lower={lower.long} interval={interval} cal={pb.long.calibration ?? saved.long.calibration} />
        <Side side={pb.short} dir="short" state={states.short} result={outcome?.playbooks?.short} live={cur ? liveOf(cur.short, "short") : undefined}
          lower={lower.short} interval={interval} cal={pb.short.calibration ?? saved.short.calibration} />
      </div>
      <p className="muted small pb-note">가격은 계산이지 예측이 아닙니다. ◌ 는 직접 지켜볼 신호, – 는 데이터가 없어 판단하지 못한 신호입니다.</p>
    </section>
  );
}
