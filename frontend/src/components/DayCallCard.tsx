import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, ApiError, isPartError } from "../api";
import { fakeDayCall } from "../briefsFake";
import { DAY_CALL_KINDS, DAY_CALL_STAGES, KIND_KO, kstStamp, LIVE_RECORD_MIN, MARKET_KO, percent, rate, scoredOutcome, STAGE_KO, STAGE_MARK, verdictText } from "../dayCall";
import type { DayCallEntry, DayCallMarket, DayCallProbs, DayCallReason, DayCallRecord, DayCallStage, DayCallToday } from "../types";

import DayCallSectors, { type DayCallSectorsResponse, type SectorsPreset } from "./DayCallSectors";
const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;
const PENDING_TEXT = "오늘 방향 판정은 준비 중이에요. 판정이 시작되면 여기에 보여요.";
const MARKETS: DayCallMarket[] = ["kr", "us"];
const EFFECT: Record<DayCallReason["effect"], { mark: string; text: string; cls: string }> = {
  long: { mark: "▲", text: "롱 쪽", cls: "up" },
  short: { mark: "▼", text: "숏 쪽", cls: "down" },
  volatile: { mark: "↕", text: "변동 큼", cls: "" },
  calm: { mark: "―", text: "잔잔함", cls: "" },
};

/** 다섯 단계 확률 막대 — 단계 이름 · 기호 · 숫자를 같이 적어 색만으로 읽지 않게. 확률이 가장 큰 단계는 "◀" */
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
          {s === stage && <span className="daycall-pick">◀<span className="daycall-pick-word"> 가장 큼</span></span>}
        </li>
      ))}
    </ol>
  );
}

/** "적중 64% · 늘 롱 43% · 늘 숏 57% · 2차 판정만 59번 · 검증 구간" — 적중률은 언제나 기준선 · 표본 수 · 출처와 같이.
 *  #606 이 회차만 센 숫자라 지난 90일 1 · 2차를 합친 적중 기록 화면(stats)과 다를 수 있다 — 그 이유를 접힌 설명으로 밝힌다. */
export function RecordLine({ r, roundLabel }: { r: DayCallRecord | undefined; roundLabel?: string }) {
  if (!r) return <p className="daycall-record small muted">적중 기록이 아직 없어요.</p>;
  const live = r.basis === "live";
  const round = roundLabel ?? "이 회차";
  return (
    <div className="daycall-record small">
      <p>
        <span className="muted">적중</span> <strong>{rate(r.direction_hit_rate)}</strong>
        <span className="muted"> · 늘 롱 {rate(r.always_long)} · 늘 숏 {rate(r.always_short)} · {round} 판정만 {r.n}번 · </span>
        <span className="daycall-basis">{live ? "실제 판정" : "검증 구간"}</span>
      </p>
      <details className="daycall-record-src">
        <summary className="muted">이 숫자의 기준</summary>
        <p className="muted">
          {live
            ? `지난 90일 동안 실제로 낸 ${round} 판정 ${r.n}번을 채점한 숫자예요.`
            : `모델 학습에 쓰지 않은 날(검증 구간)을 그날 그 시각 기준으로 다시 판정해 채점한 ${round} 판정 ${r.n}번이에요. `
              + `실제 판정이 ${round}에 ${LIVE_RECORD_MIN}번 쌓이면 실제 판정 숫자로 바뀌어요.`}
          {" "}적중 기록 화면은 지난 90일의 1 · 2차와 검증 구간 · 실제 판정을 모두 합쳐 세서 숫자가 다를 수 있어요.
        </p>
      </details>
    </div>
  );
}

/** #618 넓은 화면 맨 위 줄의 압축형 — 다섯 단계를 한 줄 막대로, 아래에 단계 기호 · 확률을 글자로(가장 큰 단계는 굵게 · ◀).
 *  다섯 줄 막대(ProbBars)와 같이 그려 두고 CSS 가 자리에 맞는 쪽만 보인다 */
export function ProbStrip({ probs, stage }: { probs: DayCallProbs; stage: DayCallStage }) {
  return (
    <div className="daycall-strip">
      <span className="daycall-strip-bar" aria-hidden="true">
        {DAY_CALL_STAGES.map((s) => (
          <span key={s} className={`daycall-seg daycall-${s}${s === stage ? " is-call" : ""}`}
            style={{ flexGrow: Math.max(0, probs[s] ?? 0) }} />
        ))}
      </span>
      <ol className="daycall-strip-legend">
        {DAY_CALL_STAGES.map((s) => (
          <li key={s} className={s === stage ? "is-call" : undefined}>
            <span aria-hidden="true">{STAGE_MARK[s]} </span><span className="sr-only">{STAGE_KO[s]} </span>{percent(probs[s], 0)}
            {s === stage && <span className="daycall-pick"><span aria-hidden="true"> ◀</span><span className="sr-only"> 가장 큼</span></span>}
          </li>
        ))}
      </ol>
    </div>
  );
}

function CallBlock({ c, kind }: { c: DayCallEntry | null | undefined; kind: "prev_close" | "open" }) {
  if (!c) {
    return (
      <div className="daycall-call">
        <h4 className="daycall-call-title">{KIND_KO[kind]}</h4>
        <p className="muted small">아직 판정이 없어요.</p>
      </div>
    );
  }
  const v = verdictText(c);
  const out = scoredOutcome(c.outcome);
  return (
    <div className="daycall-call">
      <h4 className="daycall-call-title">{c.call_label || KIND_KO[kind]} <span className="muted small">{c.round_label} · {kstStamp(c.called_at)}</span></h4>
      <p className="daycall-head">
        <strong className={`daycall-verdict ${v.cls}`}><span aria-hidden="true">{v.mark} </span>{v.text}</strong>
        <span className="small">{c.label} {percent(c.probs[c.stage], 0)}</span>
      </p>
      {c.verdict === "hold" && <p className="small muted">롱 · 숏 확률 차가 10%p 안이라 방향을 고르지 않았어요.</p>}
      {c.fallback && <p className="small muted">야간 선물이 없어 선물 없이 낸 판정이에요.</p>}
      <ProbBars probs={c.probs} stage={c.stage} />
      <ProbStrip probs={c.probs} stage={c.stage} />
      <RecordLine r={c.record} roundLabel={c.round_label} />
      {c.outcome?.unscorable && <p className="small muted">결과 채점 불가 — 지수 자료가 오지 않았어요.</p>}
      {out && (
        <p className="small">
          <span className="muted">결과</span> {out.label} ({out.return_pct > 0 ? "+" : ""}{out.return_pct.toFixed(2)}%) ·{" "}
          {out.hit_direction == null ? <span className="muted">{c.verdict === "hold" ? "보류라 방향 표시 안 함" : "시가 = 종가라 방향 채점 안 함"}</span>
            : out.hit_direction ? <span className="daycall-hit">✓ 방향 맞음</span> : <span className="daycall-miss">✗ 방향 틀림</span>}
        </p>
      )}
      {(c.reasons?.length ?? 0) > 0 && (
        <details className="daycall-reasons">
          <summary className="small muted">근거 {Math.min(5, c.reasons!.length)}개</summary>
          <ol>{c.reasons!.slice(0, 5).map((r) => (
            <li key={r.key}>
              <span className={`daycall-effect ${EFFECT[r.effect]?.cls ?? ""}`}>
                <span aria-hidden="true">{EFFECT[r.effect]?.mark ?? "·"}</span> {EFFECT[r.effect]?.text ?? r.effect}
              </span>{" "}{r.text}
            </li>
          ))}</ol>
        </details>
      )}
    </div>
  );
}

/** #573 오늘 방향(#572) — 국장 · 미장 탭, 시장마다 두 판정(전일 종가 대비 · 시가 대비)을 나란히. 적중률은 기준선 · 표본 수와 같이 */
export default function DayCallCard() {
  const [today, setToday] = useState<Partial<Record<DayCallMarket, DayCallToday | "failed" | "pending">>>({});
  const [market, setMarket] = useState<DayCallMarket>(() => {
    const kstHour = (new Date().getUTCHours() + 9) % 24;
    return kstHour >= 16 || kstHour < 6 ? "us" : "kr";      // 국장이 끝난 뒤 · 새벽은 미장부터
  });

  // #705 처음 보이는 시장의 섹터는 판정과 같은 요청(/api/day-call/board)으로 받는다 — 받기 전엔 섹터 칸이 기다린다
  const firstMarket = useRef(market).current;
  const [sectors, setSectors] = useState<SectorsPreset>("wait");
  // 탭을 한 번 옮긴 뒤엔 처음 시장으로 돌아와도 섹터를 새로 묻는다(장 마감 뒤 세션 적중 같은 갱신이 보이게)
  const [switched, setSwitched] = useState(false);
  useEffect(() => {
    if (market !== firstMarket) setSwitched(true);
  }, [market, firstMarket]);

  useEffect(() => {
    let alive = true;
    // API(#572)가 아직 없으면(404) 개발 모드는 예시, 운영은 "준비 중" — 오류처럼 보이지 않게
    const missing = (m: DayCallMarket, notFound: boolean) =>
      setToday((cur) => ({ ...cur, [m]: notFound ? (import.meta.env.DEV ? fakeDayCall(m) : "pending") : "failed" }));
    const one = (m: DayCallMarket) =>
      api.dayCall(m).then((t) => alive && setToday((cur) => ({ ...cur, [m]: t })))
        .catch((e: unknown) => { if (alive) missing(m, is404(e)); });
    api.dayCallBoard(firstMarket).then((b) => {
      if (!alive) return;
      const calls = b?.calls ?? {};
      for (const m of MARKETS) {
        const got = calls[m];
        if (got === undefined) void one(m);
        else if (isPartError(got)) missing(m, got.error === 404);
        else setToday((cur) => ({ ...cur, [m]: got }));
      }
      // 섹터: 받는 중(loading)이면 섹터 칸이 섹터 경로로 묻고(같은 작업을 기다린다), 실패면 다시 묻지 않는다
      const s = b?.sectors;
      setSectors(s === undefined ? null : isPartError(s) ? "failed"
        : (s as DayCallSectorsResponse).ready || !(s as { loading?: boolean }).loading ? (s as DayCallSectorsResponse) : null);
    }).catch(() => {
      // 묶음 경로가 없는 서버(배포 사이 404) · 실패 · 뜻밖의 응답: 예전처럼 시장마다
      if (!alive) return;
      setSectors(null);
      for (const m of MARKETS) void one(m);
    });
    return () => { alive = false; };
  }, [firstMarket]);

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const next = market === "kr" ? "us" : "kr";
    setMarket(next);
    document.getElementById(`daycall-tab-${next}`)?.focus();
  };

  const t = today[market];
  if (MARKETS.every((m) => today[m] === "pending")) return <p className="muted small">{PENDING_TEXT}</p>;
  const ok = t && t !== "failed" && t !== "pending" ? t : null;
  const calls = ok?.calls ?? {};
  const none = ok && DAY_CALL_KINDS.every((k) => !calls[k]);
  const note = ok?.note?.[0];
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
          : t === "pending" ? <p className="muted small">{PENDING_TEXT}</p>
          : none ? (
            <p className="muted small">
              {MARKET_KO[market]} 판정이 아직 없어요.{" "}
              {market === "kr" ? "국장은 07:30 에 1차, 09:30 에 2차 판정이 나와요." : "미장은 뉴욕 09:00 에 1차, 10:00 에 2차 판정이 나와요."}
            </p>
          ) : (
            <>
            <p className="daycall-session small">
              <span className="muted">{ok?.date ? `${Number(ok.date.slice(5, 7))}/${Number(ok.date.slice(8, 10))} 세션` : ""}</span>
              {ok?.today === false && <span className="daycall-past"> · 지난 세션 판정</span>}
            </p>
            <div className="daycall-calls">
              {DAY_CALL_KINDS.map((k) => <CallBlock key={k} kind={k} c={calls[k]} />)}
            </div>
            </>
          )}
        <DayCallSectors key={market} market={market} preset={market === firstMarket && !switched ? sectors : null} />
        <p className="daycall-note small muted">
          확률은 추정이에요{note ? ` — ${note}` : ""}. <a href="#/day-call">적중 기록</a>
        </p>
      </div>
    </div>
  );
}
