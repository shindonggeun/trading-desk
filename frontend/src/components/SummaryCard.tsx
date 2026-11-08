import { price } from "../format";
import type { HorizonId, PlanSummary } from "../types";
import { useLivePlaybooks } from "./PlaybookCard";

/**
 * #222 한눈에 요약 — 분석 결과 맨 위의 큰 글씨 카드. 숫자는 모두 데스크 계산(plan.summary, #221).
 * 결론 → 분할 매수 → 한 번에 산다면 → 손절 · 목표 순서. 장이 열린 동안은 지금가와 거리만 몇 초마다 다시 잰다.
 */
const SHARE = (s: number | null | undefined) => (s == null ? "" : `${Math.round(s * 100)}%`);
const PCT = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`);
const ROLE: Record<string, string> = { t1: "1차 목표", t2: "2차 목표", t3: "3차 목표" };

function tone(v: PlanSummary["verdict"]): string {
  if (v === "buy_now") return "v-buy";
  if (v === "short_now") return "v-short";
  if (v === "wait_for_zone" || v === "wait_short") return "v-wait";
  return "";
}

export default function SummaryCard({ summary, runId, horizon, live: liveOn }: {
  summary: PlanSummary;
  runId: string;
  horizon?: HorizonId;
  /** 분석일이 아직 열려 있으면 지금가로 거리를 다시 잰다 */
  live: boolean;
}) {
  const live = useLivePlaybooks(runId, horizon, liveOn && summary.side != null);
  const s = summary;
  const now = live?.frame?.reference_price ?? s.price ?? null;
  const first = s.single_entry?.price ?? s.ladder?.[0]?.price ?? null;
  const dist = now && first ? ((first / now) - 1) * 100 : s.distance_pct ?? null;
  const broke = s.side && live?.[s.side]?.state === "invalid";
  const up = s.side === "long";
  const buyWord = up ? "매수" : "매도(숏)";

  return (
    <section className="card sum-card" aria-label="한눈에 요약">
      <div className={`sum-verdict ${broke ? "" : tone(s.verdict)}`}>
        {broke ? "구역을 뚫고 나가 이 타점은 무효 — 관망" : s.text}
      </div>
      {s.why && <p className={s.verdict === "stand_aside" && s.side ? "error" : "muted"}>{s.why}</p>}
      {s.side && (
        <p className="muted small">
          지금가 <b>{price(now)}</b>
          {first != null && <> · {s.single_entry ? "최적 타점" : "1차"}까지 <b>{PCT(dist)}</b></>}
          {s.zone?.low != null && <> · 구역 {price(s.zone.low)} ~ {price(s.zone.high)}</>}
          {s.valid_until && <> · 유효 {new Date(s.valid_until).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}까지</>}
        </p>
      )}
      {s.notes && s.notes.length > 0 && <p className="small error">{s.notes.join(" · ")}</p>}

      {s.side && (
        <div className={`sum-grid ${s.verdict === "watch" || s.verdict === "stand_aside" ? "sum-ref" : ""}`}>
          <div className="sum-box">
            <h4>나눠서 {buyWord}한다면</h4>
            {(s.ladder ?? []).map((l) => (
              <div key={l.step} className="sum-row">
                <span>{l.step}차 <b>{price(l.price)}</b></span>
                <span className="muted">{SHARE(l.share)} · {l.when}</span>
              </div>
            ))}
            {s.avg_entry != null && <p className="muted small">평균 단가 {price(s.avg_entry)}</p>}
          </div>

          <div className="sum-box">
            <h4>한 번에 {buyWord}한다면</h4>
            {s.single_entry ? (
              <>
                <div className="sum-big">{price(s.single_entry.price)}</div>
                <div className="sum-row">
                  <span>손익비 <b>{s.single_entry.rr.toFixed(1)}</b></span>
                  <span className="muted">
                    {s.single_entry.thin || s.single_entry.p_t1 == null
                      ? "실적 표본 부족"
                      : `1차 목표 먼저 ${Math.round(s.single_entry.p_t1 * 100)}%`}
                  </span>
                </div>
                <div className="sum-why">
                  {s.single_entry.why.map((w) => {
                    // 지지 · 저항 레벨의 근거는 "주봉 스윙 저점 ×4 · … · 최근 일봉 저가" 처럼 길다 — 칩엔 첫 항목만
                    const parts = w.split(" · ");
                    const short = parts[0].length > 24 ? `${parts[0].slice(0, 23)}…` : parts[0];
                    return (
                      <span key={w} className="chip chip-sub" title={w}>
                        {short}{parts.length > 1 ? ` 외 ${parts.length - 1}` : ""}
                      </span>
                    );
                  })}
                </div>
                {s.single_entry.note && <p className="muted sum-note">{s.single_entry.note}</p>}
              </>
            ) : (
              <p className="muted">근거가 겹치면서 손익비 1.5 이상인 가격이 없습니다 — 분할로만.</p>
            )}
          </div>

          <div className="sum-box">
            <h4>손절 · 목표</h4>
            <div className="sum-row">
              <span>손절 <b>{price(s.stop)}</b></span>
              <span className="muted">{PCT(s.stop_pct)}</span>
            </div>
            {(s.targets ?? []).map((t) => (
              <div key={t.role} className="sum-row">
                <span>{ROLE[t.role] ?? t.role} <b>{price(t.price)}</b></span>
                <span className="muted">손익비 {t.rr ?? "—"}</span>
              </div>
            ))}
            {s.risk_pct != null && (
              <p className="muted small">
                한 번에 잃는 돈은 계좌의 {s.risk_pct}% 이내{s.position_pct != null ? ` — 비중 ${s.position_pct}%` : ""}
                {s.conviction ? " · 승부 타점" : ""}
              </p>
            )}
            {s.record && !s.record.thin && s.record.exp_r != null && (
              <p className={`small ${s.record.negative ? "error" : "muted"}`}>
                이 셋업 데스크 실적: {s.record.n}건 · 1차 목표 먼저 {Math.round((s.record.p_t1 ?? 0) * 100)}% · 기대 {s.record.exp_r > 0 ? "+" : ""}{s.record.exp_r.toFixed(2)}R
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
