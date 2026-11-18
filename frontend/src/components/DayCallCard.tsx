import { useEffect, useState, type KeyboardEvent } from "react";
import { api, ApiError } from "../api";
import { fakeDayCall, fakeDayCallStats } from "../briefsFake";
import { DAY_CALL_LABELS, estimateNote, kstStamp, LABEL_KO, LABEL_MARK, MARKET_KO, pct } from "../dayCall";
import type { DayCall, DayCallMarket, DayCallProbs, DayCallLabel, DayCallStats } from "../types";

const dev404 = (e: unknown) => import.meta.env.DEV && e instanceof ApiError && e.status === 404;
const MARKETS: DayCallMarket[] = ["kr", "us"];

/** 다섯 단계 확률 막대 — 단계 이름 · 기호 · 숫자를 같이 적어 색만으로 읽지 않게. 판정한 단계는 "◀ 판정" */
export function ProbBars({ probs, label }: { probs: DayCallProbs; label: DayCallLabel }) {
  return (
    <ol className="daycall-bars">
      {DAY_CALL_LABELS.map((l) => (
        <li key={l} className={`daycall-row daycall-${l}${l === label ? " is-call" : ""}`}>
          <span className="daycall-name"><span aria-hidden="true" className="daycall-mark">{LABEL_MARK[l]}</span>{LABEL_KO[l]}</span>
          <span className="daycall-track" aria-hidden="true"><span className="daycall-fill" style={{ width: `${Math.max(0, Math.min(1, probs[l])) * 100}%` }} /></span>
          <span className="daycall-pct">{pct(probs[l])}</span>
          {l === label && <span className="daycall-pick">◀ 판정</span>}
        </li>
      ))}
    </ol>
  );
}

/** #573 오늘 방향(#572) — 국장 · 미장 탭, 5단계 확률, 회차 · 판정 시각, 근거 5개, 지난 90일 적중률 */
export default function DayCallCard() {
  const [call, setCall] = useState<DayCall | null | undefined>(undefined);
  const [failed, setFailed] = useState(false);
  const [stats, setStats] = useState<Partial<Record<DayCallMarket, DayCallStats>>>({});
  const [market, setMarket] = useState<DayCallMarket>(() => {
    const kstHour = (new Date().getUTCHours() + 9) % 24;
    return kstHour >= 16 || kstHour < 6 ? "us" : "kr";      // 국장이 끝난 뒤 · 새벽은 미장부터
  });

  useEffect(() => {
    let alive = true;
    api.dayCall().then((c) => alive && setCall(c)).catch((e: unknown) => {
      if (!alive) return;
      if (dev404(e)) setCall(fakeDayCall());
      else setFailed(true);
    });
    for (const m of MARKETS) {
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

  if (failed) return <p className="muted small">오늘 방향을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>;
  if (call === undefined) return <p className="muted small">불러오는 중…</p>;
  const side = call?.[market] ?? null;
  const st = stats[market];
  return (
    <div className="daycall">
      <div className="seg seg-small" role="tablist" aria-label="시장">
        {MARKETS.map((m) => (
          <button key={m} id={`daycall-tab-${m}`} type="button" role="tab" aria-selected={market === m} tabIndex={market === m ? 0 : -1}
            aria-controls="daycall-panel" className={market === m ? "on" : ""} onClick={() => setMarket(m)} onKeyDown={onKey}>
            오늘 {MARKET_KO[m]}
          </button>
        ))}
      </div>
      <div id="daycall-panel" role="tabpanel" aria-labelledby={`daycall-tab-${market}`}>
        {!side ? (
          <p className="muted small">
            {call ? `오늘 ${MARKET_KO[market]} 판정이 아직 없어요.` : "오늘 판정이 아직 없어요."}{" "}
            {market === "kr" ? "국장은 08시 전 1차 · 개장 30분 뒤 2차예요." : "미장은 프리마켓 뒤 1차 · 개장 뒤 2차예요."}
          </p>
        ) : (
          <>
            <p className="daycall-head">
              <strong className={`daycall-verdict daycall-${side.label}`}>
                <span aria-hidden="true">{LABEL_MARK[side.label]} </span>{LABEL_KO[side.label]} {pct(side.probs[side.label])}
              </strong>
              <span className="muted small">{side.round ? `${side.round}차 판정` : "판정"}{side.at ? ` · ${kstStamp(side.at)}` : ""}</span>
            </p>
            <ProbBars probs={side.probs} label={side.label} />
            {side.reasons.length > 0 && (
              <div className="daycall-reasons">
                <h4 className="small muted">근거</h4>
                <ol>{side.reasons.slice(0, 5).map((r, i) => <li key={i}>{r}</li>)}</ol>
              </div>
            )}
          </>
        )}
        <p className="daycall-note small muted">{estimateNote(st?.hit_rate, st?.days ?? 90, st?.n)} · <a href="#/day-call">적중 기록</a></p>
      </div>
    </div>
  );
}
