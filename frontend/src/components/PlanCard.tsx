import { type ReactNode, useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import { ACTION, BIAS, RATING, term } from "../korean";
import type { Estimate, Plan } from "../types";

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
function useLiveQuote(symbol: string | undefined, fallback: Live | undefined, enabled: boolean): Live | null {
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
function useEstimate(symbol: string | undefined, enabled: boolean): ClosedEstimate | null {
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
  return est && est.supported && est.market_state === "closed" ? est : null;
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

export default function PlanCard({ plan, ticker, symbol, profile }: {
  plan: Plan;
  ticker: string;
  /** 시세를 읽을 티커(NVDA, 005930.KS). 없으면 계획에 저장된 현재가만 쓴다 — 오래된 · 과거 날짜 분석. */
  symbol?: string;
  profile?: string;
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
          {live && (
            <>
              <dt>현재가</dt>
              <dd>
                {price(live.price)}
                <span className="src" title={`${live.fetched_at} 에 읽은 값 (${QUOTE_EVERY_MS / 1000}초마다 갱신)`}>
                  {live.source === "toss" ? "토스" : "야후"} ·{" "}
                  {hhmm(live.fetched_at)}
                </span>
                {est && <span className="muted small"> 장 마감</span>}
              </dd>
            </>
          )}
          {est && (
            <>
              <dt>추정가</dt>
              <dd>
                {price(est.estimate)}
                <span className="muted small">
                  {" "}
                  {est.change_pct > 0 ? "+" : ""}
                  {est.change_pct.toFixed(2)}% (종가 {price(est.last_close)} · {est.last_close_date})
                </span>
                <span
                  className="src"
                  title={`바이낸스 ${est.contract} 무기한 선물이 종가 이후 움직인 비율을 종가에 곱한 값입니다. 실제 체결가가 아닙니다.`}
                >
                  바이낸스 {est.contract} · {hhmm(est.fetched_at)}
                </span>
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
