import { useRef, type KeyboardEvent } from "react";
import { price } from "../format";
import { type AllHorizonsPlan, FIT_KO, HORIZON_ORDER, LOW_RR, slotLines } from "../allHorizons";
import type { HorizonId } from "../types";
import { DIRECTIONS, hasScenarios, type ScenarioSide } from "../directionScenarios";
import DirectionScenarioCard from "./DirectionScenarioCard";

const pct = (to: number | null, from: number | null) =>
  to == null || from == null || !from ? "" : ` (${to >= from ? "+" : ""}${((to / from - 1) * 100).toFixed(1)}%)`;

/** #671 rText 와 같은 규칙 — ±0.005 안쪽은 0 */
const rText = (r: number) => (Math.abs(r) < 0.005 ? "0.00R" : `${r > 0 ? "+" : ""}${r.toFixed(2)}R`);

/**
 * #697 분석 결과 맨 위 — 네 기간(스캘핑 · 단타 · 짧은 스윙 · 스윙)을 나란히(PC 4칸, 휴대폰은 옆으로 넘김).
 * 종목 성격 배지 · 지금 장세에 맞는 기간 문장, 칸마다 진입 · 손절 · 목표 · 들고 갈 시간 · 손익비 · 과거 R.
 * 장세에 맞는 칸은 강조, 손익비 낮은 칸은 흐리게(#671). 칸을 누르면 차트 탭의 진입 · 손절 · 목표선이 그 기간 것으로.
 */
export default function HorizonsBoard({ plan, selected, selectedSide, onSelect, onDirection }: {
  plan: AllHorizonsPlan;
  selected: HorizonId | null;
  selectedSide: ScenarioSide | null;
  onSelect: (h: HorizonId) => void;
  onDirection: (h: HorizonId, side: ScenarioSide) => void;
}) {
  const grid = useRef<HTMLDivElement>(null);
  const hs = plan.horizons;
  if (!hs) {
    // 지난 날짜 분석은 일부러 안 붙인다 — 백엔드 문장을 그대로(실패처럼 앞말을 덧붙이지 않게)
    return plan.horizons_note ? <p className="muted small hz-note">네 기간 칸: {plan.horizons_note}</p> : null;
  }
  const slots = HORIZON_ORDER.map((h) => hs[h]).filter((s): s is NonNullable<typeof s> => !!s);
  const directional = slots.some(hasScenarios);
  const fit = plan.horizon_fit;
  const cls = plan.instrument_class;
  const now = slots.map((s) => s.map?.price_now).find((v) => v != null) ?? null;
  // radiogroup — 탭 정지는 고른 칸 하나, 화살표 · Home · End 로 옮기며 고른다(RunView 탭과 같은 방식)
  const focusIdx = Math.max(0, slots.findIndex((s) => s.horizon === selected));
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const n = slots.length;
    const to = e.key === "ArrowRight" || e.key === "ArrowDown" ? (i + 1) % n
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? (i - 1 + n) % n
      : e.key === "Home" ? 0 : e.key === "End" ? n - 1 : null;
    if (to == null) return;
    e.preventDefault();
    onSelect(slots[to].horizon);
    grid.current?.querySelectorAll<HTMLButtonElement>(".hz-cell")[to]?.focus();
  };
  return (
    <section className="card hz-board" aria-label="네 기간 한눈에">
      <header className="hz-head">
        <h3>네 기간 한눈에</h3>
        {cls && (
          <span className={`hz-class hz-class-${cls.id}`} title={cls.basis ?? undefined}>
            {cls.label}{cls.risk ? ` · ${cls.risk}` : ""}
          </span>
        )}
        {now != null && <span className="muted small">지금 {price(now)}</span>}
      </header>
      {fit?.sentence && <p className="hz-fit">{fit.sentence}</p>}
      {plan.horizons_fake && <p className="app-notice notice-warn small">대본 실행의 예시 값이에요 — 실제 계산이 아닙니다.</p>}
      <div className={`hz-grid${directional ? " hz-with-scenarios" : ""}`}
        role={directional ? "group" : "radiogroup"} aria-label="차트에 그릴 기간" ref={grid}>
        {slots.map((s, i) => {
          const l = slotLines(s);
          const f = fit?.fit?.[s.horizon] ?? "unknown";
          const best = fit?.best?.includes(s.horizon);
          const low = l.rr != null && l.rr < LOW_RR;
          const on = selected === s.horizon;
          const r = s.map?.expected_r ?? fit?.record?.[s.horizon]?.r ?? null;
          const reason = l.side && hasScenarios(s) ? (s.source === "model" ? s.model?.why : s.map?.why?.[0]) : null;
          const period = (
            <button key={s.horizon} type="button" role={directional ? undefined : "radio"}
              aria-checked={directional ? undefined : on} aria-pressed={directional ? on && !selectedSide : undefined}
              tabIndex={directional || i === focusIdx ? 0 : -1}
              className={`hz-cell${on ? " on" : ""}${best ? " hz-best" : ""}${low ? " hz-low" : ""}`}
              onClick={() => onSelect(s.horizon)} onKeyDown={directional ? undefined : (e) => onKey(e, i)}>
              <span className="hz-cell-head">
                <strong>{s.label}</strong>
                <span className={`hz-fit-chip hz-fit-${f}`}>{best ? "지금 장세에 맞음" : FIT_KO[f]}</span>
              </span>
              <span className="muted small">들고 갈 시간 {s.holding}{s.flat_by_close ? " · 장 마감 전 정리" : ""}</span>
              <span className="hz-side small">
                <span className={`hz-side-${l.side ?? "flat"}`}>{l.sideKo}</span>
                {l.kindKo && ` · ${l.kindKo}`}
                <span className="muted"> · {s.source === "model" ? "에이전트 분석" : "데스크 지도"}</span>
              </span>
              {reason && <span className="small muted hz-aside">{reason}</span>}
              {l.standAside ? (
                <span className="small muted hz-aside">{l.standAside}</span>
              ) : (
                // button 안에는 구문 요소(dl · div)를 둘 수 없어 span 으로 줄을 만든다
                <span className="hz-facts small">
                  <span><span className="hz-k">진입</span><span>{price(l.entry)}{pct(l.entry, now)}</span></span>
                  <span><span className="hz-k">손절</span><span>{price(l.stop)}{pct(l.stop, l.entry)}</span></span>
                  <span><span className="hz-k">목표</span><span>{price(l.target)}{pct(l.target, l.entry)}</span></span>
                  <span><span className="hz-k">손익비</span><span className={low ? "hz-rr-low" : undefined}>{l.rr != null ? l.rr.toFixed(1) : "—"}{low ? " · 낮음" : ""}</span></span>
                  <span><span className="hz-k">과거 R</span><span>{r != null ? rText(r) : "기록 없음"}</span></span>
                </span>
              )}
              {s.errors && Object.keys(s.errors).length > 0 && (
                <span className="muted small">일부 계산 빠짐({Object.keys(s.errors).join(" · ")})</span>
              )}
            </button>
          );
          return hasScenarios(s) ? (
            <article key={s.horizon} className="hz-scenario-slot" aria-label={s.label}>
              {period}
              <div className="hz-scenario-pair">
                {DIRECTIONS.map((side) => (
                  <DirectionScenarioCard key={side} side={side} scenario={s.scenarios?.[side]}
                    selected={on && selectedSide === side} onSelect={() => onDirection(s.horizon, side)} />
                ))}
              </div>
            </article>
          ) : period;
        })}
      </div>
      <p className="muted small">{directional && "롱·숏을 고르면 차트 탭에서 해당 방향의 가격선을 볼 수 있어요. "}
        칸을 누르면 차트 탭의 진입 · 손절 · 목표선이 그 기간 것으로 바뀌어요. 데스크 지도 칸은 모델 없이 계산한 값이에요. 예측 · 추천 아님.</p>
    </section>
  );
}
