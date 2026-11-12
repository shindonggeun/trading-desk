import { type ReactNode, useEffect, useRef, useState } from "react";
import { ANALYST_CHOICES } from "../agents";
import { api } from "../api";
import { price } from "../format";
import { ACTION, BIAS, FACTOR, HORIZON, OPINION, OUTCOME, quoteSourceText, RATING, term } from "../korean";
import type { Estimate, Outcome, OutcomeStatus, Plan } from "../types";

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

/** 가격 사다리: 손절 · 진입 · 목표를 한 축에 놓는다. #311 위에 관점을 적는다. */
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
  const side = levelsSide(plan);
  return (
    <div className="ladder" aria-label={`가격 레벨${side ? ` — ${LEVEL_SIDE_KO[side]}` : ""}`}>
      {side && <span className={`ladder-side plan-side-${side}`}>{LEVEL_SIDE_KO[side]}</span>}
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

function Level({ plan, k, after }: { plan: Plan; k: LevelKey; after?: ReactNode }) {
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
      {source === "converted" && (
        <span className="src src-atr" title="모델이 본주(기초자산) 기준으로 낸 레벨을 일일 배수로 환산했습니다. 며칠 보유하면 실제와 어긋날 수 있습니다">
          환산값
        </span>
      )}
      {after}
    </dd>
  );
}

const ENTRY_KIND: Record<string, Record<Gap["kind"], string>> = {
  long: { now: "지금 진입", limit: "눌림 대기 (지정가 매수)", stop: "돌파 확인 후 (스톱 매수)" },
  short: { now: "지금 진입", limit: "반등 대기 (지정가 숏)", stop: "이탈 확인 후 (스톱 숏)" },
};

// backend desk/levels.py entry_vs_live 와 같은 기준
const NOW_ATR = 0.5;
const NOW_PCT = 0.01;
const FAR_ATR = 2;
const FAR_PCT = 0.05;
const LIVE_MAX_GAP = 0.25;
const QUOTE_EVERY_MS = 15_000;
const ESTIMATE_EVERY_MS = 60_000;
const ESTIMATE_STALE_MS = 3 * 60_000;

type Gap = NonNullable<Plan["entry_vs_live"]>;

/** 진입가 · 손절 · 목표가 지금 가격에 대해 어디 있는지. 롱 · 숏 계획이 아니면 null. */
export function entryGap(plan: Plan, live: number | null): Gap | null {
  const dir = plan.bias === "long" ? 1 : plan.bias === "short" ? -1 : 0;
  const entry = plan.entry_price;
  if (!dir || entry === null || !live || plan.levels_warning) return null;
  // 분할 · 다른 통화로 단위가 어긋난 시세와는 비교하지 않는다(백엔드 LIVE_MAX_GAP)
  const anchor = plan.last_close || entry;
  if (Math.abs(live - anchor) > LIVE_MAX_GAP * anchor) return null;
  const atr = plan.support_resistance?.atr || plan.atr || null;
  const gap = entry - live;
  const gapAtr = atr ? Math.abs(gap) / atr : null;
  const near = gapAtr !== null ? gapAtr <= NOW_ATR : Math.abs(gap) / live <= NOW_PCT;
  const far = gapAtr !== null ? gapAtr > FAR_ATR : Math.abs(gap) / live > FAR_PCT;
  return {
    live,
    gap,
    gap_pct: (gap / live) * 100,
    gap_atr: gapAtr !== null ? Math.round(gapAtr * 100) / 100 : null,
    kind: near ? "now" : gap * dir < 0 ? "limit" : "stop",
    far,
    stop_passed: plan.stop_loss !== null && (live - plan.stop_loss) * dir <= 0,
    target_passed: plan.price_target !== null && (plan.price_target - live) * dir <= 0,
  };
}

interface Live {
  price: number;
  source: string;
  fetched_at: string;
}

/** 카드가 열려 있는 동안 현재가를 주기적으로 다시 읽는다. 실패하면 계획에 저장된 값을 쓴다. */
export function useLiveQuote(symbol: string | undefined, fallback: Live | undefined, enabled: boolean): Live | null {
  const [quote, setQuote] = useState<Live | null>(null);
  useEffect(() => {
    if (!symbol || !enabled) return;
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.quote(symbol).then((q) => alive && setQuote(q), () => {});
    };
    load();
    const timer = setInterval(load, QUOTE_EVERY_MS);
    document.addEventListener("visibilitychange", load); // 탭으로 돌아오면 바로
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
    };
  }, [symbol, enabled]);
  return quote ?? fallback ?? null;
}

type ClosedEstimate = Extract<Estimate, { supported: true }>;

/** 장이 닫혀 있는 동안의 추정가(바이낸스 주식 무기한 선물). 계약이 없거나 장중 · 실패면 null. */
export function useEstimate(symbol: string | undefined, enabled: boolean): ClosedEstimate | null {
  const [est, setEst] = useState<Estimate | null>(null);
  useEffect(() => {
    setEst(null);
    if (!symbol || !enabled) return;
    let alive = true;
    let stopped = false;
    const load = () => {
      if (document.hidden || stopped) return;
      api.estimate(symbol).then(
        (e) => {
          if (!alive) return;
          setEst(e);
          if (!e.supported) stopped = true; // 계약이 없는 종목은 다시 묻지 않는다
        },
        // 한 번 실패로 기준이 추정가 ↔ 현재가로 흔들리지 않게, 오래되기 전까지는 마지막 값을 둔다
        () => alive && setEst((prev) => (prev?.supported && Date.now() - Date.parse(prev.fetched_at) < ESTIMATE_STALE_MS ? prev : null)),
      );
    };
    load();
    const timer = setInterval(load, ESTIMATE_EVERY_MS);
    document.addEventListener("visibilitychange", load);
    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
    };
  }, [symbol, enabled]);
  // #209: NXT trading (until 20:00 KST) shows its real trade; after it, the estimate
  return est && est.supported && (est.market_state === "closed" || est.market_state === "nxt") ? est : null;
}

function hhmm(iso: string): string {
  return new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

/** 진입가가 지금 가격에서 얼마나 떨어져 있고, 어떤 주문이 되는지. */
function EntryVsLive({ plan, g, basis }: { plan: Plan; g: Gap | null; basis: string }) {
  if (!g) return null;
  const kind = ENTRY_KIND[plan.bias]?.[g.kind];
  return (
    <span className={`muted small${g.far ? " entry-far" : ""}`}>
      {" "}{basis} 대비 {g.gap_pct > 0 ? "+" : ""}
      {g.gap_pct.toFixed(1)}%{g.gap_atr !== null && ` (${g.gap_atr} ATR)`}
      {kind && ` · ${kind}`}
    </span>
  );
}

function pct(from: number | null, to: number | null): string {
  if (from === null || to === null || from === 0) return "";
  const p = ((to - from) / from) * 100;
  return ` (${p > 0 ? "+" : ""}${p.toFixed(1)}%)`;
}

/** #311 레벨이 어느 관점인지 — 서버 levels_side, 옛 응답이면 bias */
function levelsSide(plan: Plan): "long" | "short" | null {
  if (plan.levels_side !== undefined) return plan.levels_side;
  return plan.bias === "long" || plan.bias === "short" ? plan.bias : null;
}

/** #311 손절 · 목표를 손실 · 이익으로: 롱이면 손절 아래 = 손실, 숏이면 손절 위 = 손실 */
function outcomePct(plan: Plan, side: "long" | "short", k: "stop_loss" | "price_target"): string {
  const e = plan.entry_price;
  const v = plan[k];
  if (e === null || v === null || e === 0) return "";
  const move = ((v - e) / e) * 100 * (side === "long" ? 1 : -1);   // 그 방향의 손익
  const word = move >= 0 ? "이익" : "손실";
  return `${word} ${move >= 0 ? "+" : "−"}${Math.abs(move).toFixed(1)}%`;
}

const LEVEL_SIDE_KO = { long: "롱 관점", short: "숏 관점" } as const;

/** #311 레벨 표 머리: 관점 배지(색 + 글자) · 관망이면 "들어간다면 …" · 등급과 어긋남 · 레벨 모순 */
function SideHead({ plan }: { plan: Plan }) {
  const side = levelsSide(plan);
  if (plan.entry_price === null && plan.stop_loss === null && plan.price_target === null) return null;
  return (
    <div className="plan-side-head">
      {side ? (
        <span className={`plan-side plan-side-${side}`}>{side === "long" ? "▲" : "▼"} {LEVEL_SIDE_KO[side]}</span>
      ) : plan.levels_side === null ? (
        <span className="plan-side plan-side-none">? 방향 불명</span>
      ) : null}
      {plan.bias === "flat" && side && (
        <span className="small"><strong>관망</strong> · 들어간다면 {side === "long" ? "롱" : "숏"} 기준</span>
      )}
      {plan.side_conflict && plan.bias !== "flat" && side && (
        <span className="small plan-side-warn">등급({plan.bias === "long" ? "롱" : "숏"})과 레벨 방향({side === "long" ? "롱" : "숏"})이 다릅니다</span>
      )}
      {plan.levels_note && <span className="muted small">{plan.levels_note}</span>}
    </div>
  );
}

/** 계획이 지금 가격과 어긋날 때: 진입이 멀거나, 손절 · 목표를 이미 지났을 때. */
function LiveWarning({ g, basis }: { g: Gap | null; basis: string }) {
  if (!g || !(g.far || g.stop_passed || g.target_passed)) return null;
  const notes: string[] = [];
  if (g.stop_passed) notes.push(`${basis}가 이미 손절가를 넘어섰습니다 — 이 계획은 지금 가격에서 유효하지 않습니다.`);
  else if (g.target_passed)
    notes.push(`${basis}가 이미 목표가를 지났습니다 — 진입가까지 되돌아오지 않으면 이 계획은 성립하지 않습니다.`);
  if (g.far && !g.stop_passed)
    notes.push(
      `진입가가 ${basis}에서 ${Math.abs(g.gap_pct).toFixed(1)}% 떨어져 있습니다 — 그 가격이 올 때만 거는 조건부 주문이며, 오지 않으면 거래하지 않습니다.`,
    );
  return <p className="plan-warning">{notes.join(" ")}</p>;
}

/** 30초마다 바뀌는 지금 시각 — 진입 유효 시간이 지났는지 볼 때. */
function useNow(enabled: boolean): number {
  const [t, setT] = useState(Date.now());
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(() => setT(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, [enabled]);
  return t;
}

function stamp(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay
    ? hhmm(iso)
    : d.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const pctText = (p: number) => `${Math.round(p * 100)}%`;

/** 필터가 관망으로 돌린 이유 — 서버 문구 대신 계획 값과 기준으로 한국어로 다시 쓴다. */
function filterReasons(plan: Plan): string[] {
  const f = plan.filter;
  if (!f || f.passed !== false) return [];
  const th = f.thresholds;
  const out: string[] = [];
  const p = plan.win_probability;
  if (p == null || p < th.win_probability) out.push(`승률 ${p != null ? pctText(p) : "—"} < 기준 ${pctText(th.win_probability)}`);
  const rr = plan.reward_risk;
  if (rr == null || rr < th.reward_risk) out.push(`손익비 ${rr ?? "—"} < 기준 ${th.reward_risk}`);
  const dir = plan.bias === "long" || plan.bias === "short" ? plan.bias : null;
  const agree = dir ? plan.score?.[dir] ?? 0 : 0;
  if (agree < th.agreeing) out.push(`같은 방향 근거 ${agree}개 < 기준 ${th.agreeing}개`);
  if (plan.entry_vs_live?.far) out.push("진입가가 지금 가격에서 너무 멀어 제때 체결되기 어렵습니다");
  // 백엔드가 새 기준을 더하면 여기서 못 맞출 수 있다 — 그때는 서버 문구를 그대로
  return out.length ? out : f.reasons;
}

/**
 * #110: 상품과 반대로 걸 때는 그 상품을 공매도하지 않고 짝(SOXL ↔ SOXS)을 산다 — 짝 가격의 진입 · 손절 · 목표.
 * 분석 시작은 확인을 한 번 거친다(구독 플랜 사용량).
 */
function PairBlock({ plan, code, profileId }: { plan: Plan; code: string; profileId?: string }) {
  const pair = plan.pair;
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  // "따로 분석하기" 가 사라지면 초점을 확인 줄의 "시작" 으로(키보드 · 화면낭독기가 위치를 잃지 않게)
  useEffect(() => {
    if (confirm) startRef.current?.focus();
  }, [confirm]);
  if (!pair) return null;
  // 배수를 모르면 방향을 짐작하지 않는다
  const title = pair.leverage == null ? `${pair.ticker} 매수` : `${pair.leverage < 0 ? "하락" : "상승"}에 걸 때 — ${pair.ticker} 매수`;
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        ticker: pair.ticker, horizon: plan.horizon ?? undefined, output_language: "Korean",
        profile: profileId || undefined,   // 원래 분석과 같은 투자 성향 — 두 계획을 나란히 비교하게
        analysts: ANALYST_CHOICES.map((c) => c.key), use_portfolio: true,
      });
      window.location.hash = `#/runs/${run.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };
  return (
    <section className="pair-block" aria-label={title}>
      <div className="pair-head">
        <span className="pair-title"><strong>{title}</strong></span>
        {pair.leverage != null && <span className="muted small">{pair.leverage > 0 ? "+" : ""}{pair.leverage}배</span>}
      </div>
      <p className="muted small pair-note">
        {code} 를 공매도하지 않고 {pair.ticker} 를 삽니다. 아래 숫자는 모두 {pair.ticker} 가격입니다
        {pair.price != null ? ` (분석 때 ${pair.price_source === "close" ? "종가" : "현재가"} ${price(pair.price)})` : ""}.
      </p>
      <dl className="pair-levels">
        <div><dt>진입</dt><dd className="num">{price(pair.entry_price)}</dd></div>
        <div><dt>손절</dt><dd className="num">{price(pair.stop_loss)}</dd></div>
        <div><dt>목표</dt><dd className="num">{price(pair.price_target)}</dd></div>
        <div><dt>손익비</dt><dd className="num">{pair.reward_risk != null ? `${pair.reward_risk} : 1` : "—"}</dd></div>
      </dl>
      {confirm ? (
        <div className="pair-confirm">
          <span className="small">{pair.ticker} 분석을 시작할까요? 수 분이 걸리고 사용량이 차감됩니다.</span>
          <button ref={startRef} type="button" onClick={start} disabled={busy}>{busy ? "등록 중…" : "시작"}</button>
          <button type="button" className="secondary" onClick={() => setConfirm(false)} disabled={busy}>취소</button>
        </div>
      ) : (
        <button type="button" className="secondary pair-start" onClick={() => setConfirm(true)}>{pair.ticker} 따로 분석하기</button>
      )}
      {error && <p className="error small" role="alert">{error}</p>}
    </section>
  );
}

/** 카드 맨 위: 매수 · 매도 · 관망 · 확인 필요. 진입 조건(롱 · 숏)을 나란히. */
function OpinionBanner({ plan, ticker }: { plan: Plan; ticker: string }) {
  const op = plan.opinion;
  if (!op) return null;
  const t = OPINION[op];
  if (!t) return null; // 모르는 의견 값은 카드를 깨지 않고 건너뛴다
  const reasons = filterReasons(plan);
  const demoted = plan.filter?.passed === false; // 등급은 방향인데 필터가 관망으로
  const triggers = plan.long_trigger != null || plan.short_trigger != null;
  return (
    <div className={`opinion opinion-${op}`}>
      <div className="opinion-head">
        <span className="opinion-label" title={t.desc}>{t.ko}</span>
        {plan.win_probability != null && (
          <span className="opinion-prob" title="손절보다 목표에 먼저 닿을 확률 — 모델 추정">
            승률 추정 <strong>{pctText(plan.win_probability)}</strong>
          </span>
        )}
      </div>
      <p className="small opinion-desc">
        {demoted
          ? `등급은 ${plan.bias === "long" ? "롱" : "숏"} 쪽이지만 고확률 기준에 못 미쳐 관망으로 둡니다.`
          : t.desc}
      </p>
      {reasons.length > 0 && (
        <ul className="opinion-reasons small">
          {reasons.map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
      {triggers && (
        <div className="triggers-row">
          <div className="trigger trigger-long">
            <span className="muted small">롱 진입 조건 · {ticker}</span>
            <strong>{plan.long_trigger != null ? `${price(plan.long_trigger)} 회복 · 돌파` : "없음"}</strong>
          </div>
          <div className="trigger trigger-short">
            <span className="muted small">{plan.pair ? "보유분 축소 · 손절 조건" : "숏 · 축소 조건"} · {ticker}</span>
            <strong>{plan.short_trigger != null ? `${price(plan.short_trigger)} 이탈` : "없음"}</strong>
          </div>
        </div>
      )}
    </div>
  );
}

const SIDE_KO: Record<string, string> = { long: "롱", short: "숏", neutral: "중립" };

/** 여섯 근거의 롱 · 숏 · 중립. */
function Checklist({ plan }: { plan: Plan }) {
  const c = plan.checklist;
  if (!c) return null;
  return (
    <div className="checklist" aria-label="판단 근거">
      {Object.entries(FACTOR).map(([k, ko]) => {
        const side = c[k as keyof typeof c];
        return (
          <span key={k} className={`factor factor-${side ?? "none"}`}>
            {ko} <strong>{side ? SIDE_KO[side] : "—"}</strong>
          </span>
        );
      })}
    </div>
  );
}

/** 서버 채점 결과(1시간마다). */
function OutcomeLine({ outcome, status }: { outcome?: Outcome | null; status?: OutcomeStatus | null }) {
  const st = status ?? outcome?.status;
  if (!st) return null;
  const t = OUTCOME[st];
  const r = outcome?.r;
  return (
    <p className={`outcome outcome-${st} small`}>
      채점: <strong title={t?.desc}>{t?.ko ?? st}</strong>
      {r != null && ` · ${r > 0 ? "+" : ""}${r}R`}
      {outcome?.late && " · 분석이 진입 유효 시간보다 늦게 끝났습니다"}
      {outcome?.exited_at && <span className="muted"> · {stamp(outcome.exited_at)}</span>}
    </p>
  );
}

export default function PlanCard({ plan, ticker, code, symbol, profile, profileId, outcome, outcomeStatus }: {
  plan: Plan;
  ticker: string;
  /** 가격 옆에 붙이는 티커만(SOXL) — ticker 는 "이름 (SOXL)" 표시용이라 길다. 없으면 ticker */
  code?: string;
  /** 투자 성향 id — 짝 분석을 같은 성향으로 돌린다(profile 은 표시 라벨) */
  profileId?: string;
  /** 시세를 읽을 티커(NVDA, 005930.KS). 없으면 계획에 저장된 현재가만 쓴다 — 오래된 · 과거 날짜 분석. */
  symbol?: string;
  profile?: string;
  outcome?: Outcome | null;
  outcomeStatus?: OutcomeStatus | null;
}) {
  const directional = plan.bias === "long" || plan.bias === "short";
  const live = useLiveQuote(symbol, plan.live_price, directional);
  const est = useEstimate(symbol, directional);
  // 장이 닫혀 있으면 시세는 마지막 종가라, 진입가 거리는 추정가로 잰다.
  const basis = est ? "추정가" : "현재가";
  const g = entryGap(plan, est?.estimate ?? live?.price ?? null);
  const rating = term(RATING, plan.rating);
  const action = term(ACTION, plan.trader_action);
  const anyAtr = Object.values(plan.levels_source ?? {}).includes("atr");
  const horizon = plan.horizon ? HORIZON[plan.horizon] : null; // 예전 run 은 시간축이 없다 = 스윙
  const now = useNow(!!plan.valid_until);
  const expired = !!plan.valid_until && Date.parse(plan.valid_until) <= now;
  const dir = plan.bias === "long" || plan.bias === "short" ? plan.bias : null;
  return (
    <section className={`card plan-card${expired ? " plan-expired" : ""}`}>
      <div className="plan-head">
        <div>
          <div className="muted small">
            최종 판단 · {ticker}
            {profile && ` · ${profile}`}
            {horizon && ` · ${horizon.ko}(${horizon.holding})`}
          </div>
          <div className="plan-rating">
            {rating ? rating.ko : plan.rating}
            {rating && plan.rating !== "REVIEW" && <span className="plan-rating-en"> {plan.rating}</span>}
          </div>
        </div>
        <BiasBadge plan={plan} />
      </div>
      {plan.analysis_subject && plan.analysis_subject !== (code ?? ticker) && (
        <p className="muted small plan-basis">
          분석은 {plan.analysis_subject}(본주)로 했고, 이 카드의 가격은 모두 {code ?? ticker} 기준입니다. 본주 가격은 결과 차트의 "본주" 에서 봅니다.
        </p>
      )}
      <OpinionBanner plan={plan} ticker={code ?? ticker} />
      {plan.holder_exit != null && (
        <p className="holder-exit">
          <span className="muted small">이미 보유 중이라면</span>{" "}
          <strong>{code ?? ticker} {price(plan.holder_exit)}</strong> 에서 축소 · 청산
        </p>
      )}
      <PairBlock plan={plan} code={code ?? ticker} profileId={profileId} />
      {plan.valid_until && (
        <p className={`small ${expired ? "plan-expired-note" : "muted"}`}>
          {expired
            ? `진입 유효 시간이 지났습니다(${stamp(plan.valid_until)}까지) — 아래 레벨은 기록용입니다. 새로 분석하세요.`
            : `진입 유효: ${stamp(plan.valid_until)}까지`}
        </p>
      )}
      {rating && <p className="plan-explain">{rating.desc}</p>}
      {action && (
        <p className="muted small">
          트레이더 행동: <strong>{action.ko}</strong> ({plan.trader_action}) — {action.desc}
        </p>
      )}
      <SideHead plan={plan} />
      {plan.trigger_warnings && plan.trigger_warnings.length > 0 && (
        <ul className="plan-trigger-warn small" aria-label="트리거 경고">
          {plan.trigger_warnings.map((w) => <li key={w}><span aria-hidden>⚠</span> {w}</li>)}
        </ul>
      )}
      <div className="plan-body">
        <dl className="plan-grid">
          {live && (
            <>
              <dt>현재가</dt>
              <dd>
                {price(live.price)}
                <span className="src" title={`${live.fetched_at} 에 읽은 값 (${QUOTE_EVERY_MS / 1000}초마다 갱신)`}>
                  {quoteSourceText(live.source) || "출처 미상"} ·{" "}
                  {hhmm(live.fetched_at)}
                </span>
                {est && <span className="muted small"> 장 마감</span>}
              </dd>
            </>
          )}
          {est && (
            <>
              <dt>{est.market_state === "nxt" ? "NXT 실시간" : "추정가"}</dt>
              <dd>
                {price(est.estimate)}
                <span className="muted small">
                  {" "}
                  {est.change_pct > 0 ? "+" : ""}
                  {est.change_pct.toFixed(2)}% (종가 {price(est.last_close)} · {est.last_close_date})
                  {est.market_state === "closed" && est.anchor_source === "nxt" && est.anchor_price != null &&
                    ` · NXT 애프터 ${price(est.anchor_price)} 기준`}
                </span>
                {est.market_state === "nxt" ? (
                  <span className="src" title="NXT(넥스트레이드) 프리 · 애프터마켓의 마지막 체결가입니다. 20:00 이 지나면 추정가로 바뀝니다.">
                    NXT 체결 · {hhmm(est.anchor_at ?? est.fetched_at)}
                  </span>
                ) : (
                  <span
                    className="src"
                    title={`바이낸스 ${est.contract} 무기한 선물이 기준 시각 이후 움직인 비율을 기준가에 곱한 값입니다. 실제 체결가가 아닙니다.`}
                  >
                    바이낸스 {est.contract} · {hhmm(est.fetched_at)}
                  </span>
                )}
              </dd>
            </>
          )}
          <dt>진입가</dt>
          <Level plan={plan} k="entry_price" after={<EntryVsLive plan={plan} g={g} basis={basis} />} />
          <dt>손절가</dt>
          <Level plan={plan} k="stop_loss" />
          <dt>목표가</dt>
          <Level plan={plan} k="price_target" />
          <dt>손익비</dt>
          <dd>
            {plan.reward_risk !== null ? `${plan.reward_risk} : 1` : "—"}
            <span className="muted small">
              {levelsSide(plan) ? (
                <>
                  {plan.stop_loss !== null && ` 손절 ${outcomePct(plan, levelsSide(plan)!, "stop_loss")}`}
                  {plan.price_target !== null && ` · 목표 ${outcomePct(plan, levelsSide(plan)!, "price_target")}`}
                </>
              ) : (
                <>
                  {plan.stop_loss !== null && ` 손절${pct(plan.entry_price, plan.stop_loss)}`}
                  {plan.price_target !== null && ` 목표${pct(plan.entry_price, plan.price_target)}`}
                </>
              )}
            </span>
          </dd>
          {plan.score && (
            <>
              <dt>근거 일치</dt>
              <dd>
                롱 {plan.score.long} · 숏 {plan.score.short}
                <span className="muted small">
                  {" "}/ {Object.keys(plan.checklist ?? {}).length || 6}{dir && plan.filter && ` (기준: 같은 방향 ${plan.filter.thresholds.agreeing}개 이상)`}
                </span>
              </dd>
            </>
          )}
          {plan.invalidation && (
            <>
              <dt>무효화</dt>
              <dd>{plan.invalidation}</dd>
            </>
          )}
          {plan.exit_by && (
            <>
              <dt>청산 기한</dt>
              <dd>{plan.exit_by}</dd>
            </>
          )}
          <dt>보유 기간</dt>
          <dd>{plan.time_horizon ?? "—"}</dd>
          <dt>포지션 크기</dt>
          <dd>{plan.position_sizing ?? "—"}</dd>
        </dl>
        <Ladder plan={plan} />
      </div>
      <Checklist plan={plan} />
      {/* 관망 · 판단 보류도 서버가 invalid 로 채점해 두지만, 레벨 오류처럼 보이지 않게 숨긴다 */}
      {directional && <OutcomeLine outcome={outcome} status={outcomeStatus} />}
      <LiveWarning g={g} basis={basis} />
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
