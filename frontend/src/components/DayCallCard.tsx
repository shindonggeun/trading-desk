import { useEffect, useState, type KeyboardEvent } from "react";
import { api, ApiError } from "../api";
import { fakeDayCall, fakeDayCallStats } from "../briefsFake";
import { DAY_CALL_STAGES, estimateNote, kstStamp, MARKET_KO, percent, STAGE_KO, STAGE_MARK } from "../dayCall";
import type { DayCallMarket, DayCallProbs, DayCallReason, DayCallStage, DayCallStats, DayCallToday } from "../types";

const dev404 = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;
const MARKETS: DayCallMarket[] = ["kr", "us"];
const EFFECT: Record<DayCallReason["effect"], { mark: string; text: string; cls: string }> = {
  long: { mark: "▲", text: "롱 쪽", cls: "up" },
  short: { mark: "▼", text: "숏 쪽", cls: "down" },
  volatile: { mark: "↕", text: "변동 큼", cls: "" },
  calm: { mark: "―", text: "잔잔함", cls: "" },
};

/** 다섯 단계 확률 막대 — 단계 이름 · 기호 · 숫자를 같이 적어 색만으로 읽지 않게. 판정한 단계는 "◀ 판정" */
export function ProbBars({ probs, stage }: { probs: DayCallProbs; stage: DayCallStage }) {
  return (
    <ol className="daycall-bars">
      {DAY_CALL_STAGES.map((s) => (
        <li key={s} className={`daycall-row daycall-${s}${s === stage ? " is-call" : ""}`}>
          <span className="daycall-name"><span aria-hidden="true" className="daycall-mark">{STAGE_MARK[s]}</span>{STAGE_KO[s]}</span>
          <span className="daycall-track" aria-hidden="true">
            <span className="daycall-fill" style={{ width: `${Math.max(0, Math.min(100, probs[s] ?? 0))}%` }} />
          </span>
          <span className="daycall-pct">{percent(probs[s], 0)}</span>
          {s === stage && <span className="daycall-pick">◀<span className="daycall-pick-word"> 판정</span></span>}
        </li>
      ))}
    </ol>
  );
}

/** #573 오늘 방향(#572) — 국장 · 미장 탭, 5단계 확률, 회차 · 판정 시각, 근거 5개, 지난 90일 적중률 */
export default function DayCallCard() {
  const [today, setToday] = useState<Partial<Record<DayCallMarket, DayCallToday | "failed">>>({});
  const [stats, setStats] = useState<Partial<Record<DayCallMarket, DayCallStats>>>({});
  const [market, setMarket] = useState<DayCallMarket>(() => {
    const kstHour = (new Date().getUTCHours() + 9) % 24;
    return kstHour >= 16 || kstHour < 6 ? "us" : "kr";      // 국장이 끝난 뒤 · 새벽은 미장부터
  });

  useEffect(() => {
    let alive = true;
    for (const m of MARKETS) {
      api.dayCall(m).then((t) => alive && setToday((cur) => ({ ...cur, [m]: t })))
        .catch((e: unknown) => { if (alive) setToday((cur) => ({ ...cur, [m]: dev404(e) ? fakeDayCall(m) : "failed" })); });
      api.dayCallStats(m).then((s) => alive && setStats((cur) => ({ ...cur, [m]: s })))
        .catch((e: unknown) => { if (alive && dev404(e)) setStats((cur) => ({ ...cur, [m]: fakeDayCallStats(m) })); });
    }
    return () => { alive = false; };
  }, []);

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = market === "kr" ? "us" : "kr";
    setMarket(next);
    document.getElementById(`daycall-tab-${next}`)?.focus();
  };

  const t = today[market];
  const call = t && t !== "failed" ? t.call : null;
  const st = stats[market];
  return (
    <div className="daycall">
      <div className="seg seg-small" role="tablist" aria-label="시장">
        {MARKETS.map((m) => (
          <button key={m} id={`daycall-tab-${m}`} type="button" role="tab" aria-selected={market === m} tabIndex={market === m ? 0 : -1}
            aria-controls="daycall-panel" className={market === m ? "on" : ""} onClick={() => setMarket(m)} onKeyDown={onKey}>
            {MARKET_KO[m]}
          </button>
        ))}
      </div>
      <div id="daycall-panel" role="tabpanel" aria-labelledby={`daycall-tab-${market}`}>
        {t === undefined ? <p className="muted small">불러오는 중…</p>
          : t === "failed" ? <p className="muted small">{MARKET_KO[market]} 방향을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>
          : !call ? (
            <p className="muted small">
              {MARKET_KO[market]} 판정이 아직 없어요.{" "}
              {market === "kr" ? "국장은 07:30 에 1차, 09:30 에 2차 판정이 나와요." : "미장은 뉴욕 09:00 에 1차, 10:00 에 2차 판정이 나와요."}
            </p>
          ) : (
            <>
              <p className="daycall-head">
                <strong className={`daycall-verdict daycall-${call.stage}`}>
                  <span aria-hidden="true">{STAGE_MARK[call.stage]} </span>{call.label} {percent(call.probs[call.stage], 0)}
                </strong>
                <span className="muted small">{call.round_label} 판정 · {kstStamp(call.called_at)}</span>
              </p>
              <ProbBars probs={call.probs} stage={call.stage} />
              {call.outcome && (
                <p className="small">
                  <span className="muted">결과</span> {call.outcome.label} ({call.outcome.return_pct > 0 ? "+" : ""}{call.outcome.return_pct.toFixed(2)}%) ·{" "}
                  {call.outcome.hit_direction ? <span className="daycall-hit">✓ 방향 맞음</span> : <span className="daycall-miss">✗ 방향 틀림</span>}
                </p>
              )}
              {(call.reasons?.length ?? 0) > 0 && (
                <div className="daycall-reasons">
                  <h4 className="small muted">근거</h4>
                  <ol>{call.reasons!.slice(0, 5).map((r) => (
                    <li key={r.key}>
                      <span className={`daycall-effect ${EFFECT[r.effect]?.cls ?? ""}`}>
                        <span aria-hidden="true">{EFFECT[r.effect]?.mark ?? "·"}</span> {EFFECT[r.effect]?.text ?? r.effect}
                      </span>{" "}{r.text}
                    </li>
                  ))}</ol>
                </div>
              )}
            </>
          )}
        <p className="daycall-note small muted">
          {estimateNote(st?.direction_hit_rate, st?.days ?? 90, st?.n)} · <a href="#/day-call">적중 기록</a>
        </p>
      </div>
    </div>
  );
}
