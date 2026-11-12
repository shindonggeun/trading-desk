import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { BottomEvidence, BottomZone, BottomStep } from "../types";

/** 근거 이름 — 초보자가 읽는 말로 */
const EVIDENCE_KO: Record<BottomEvidence["id"], string> = {
  drawdown: "고점 대비 낙폭",
  weekly_rsi: "주봉 RSI 과매도",
  monthly_rsi: "월봉 RSI 과매도",
  divergence: "하락 힘 약해짐(다이버전스)",
  long_ma: "60 · 120주 장기 이평",
  support: "5년 주봉 큰 저점",
  capitulation: "시장 투매 뒤 진정",
};

const STAGE_HINT: Record<string, string> = {
  watch: "아직 멀다 — 지켜보기",
  approach: "바닥권에 다가가는 중 — 1단계부터 나눠서",
  bottom: "몇 년에 한 번 오는 구간 — 계획대로 나눠 모으기",
};

function evidenceValue(e: BottomEvidence): string {
  const v = e.value as Record<string, unknown> | number | null;
  if (e.id === "drawdown" && v && typeof v === "object") return `−${v.pct}% (과거 낙폭의 ${v.pctl}% 위치)`;
  if ((e.id === "weekly_rsi" || e.id === "monthly_rsi" || e.id === "capitulation") && typeof v === "number") return String(v);
  if (e.id === "support" && typeof v === "number") return price(v);
  if (e.id === "long_ma" && v && typeof v === "object") {
    const parts = [v.ma60 != null && `60주 ${price(v.ma60 as number)}`, v.ma120 != null && `120주 ${price(v.ma120 as number)}`].filter(Boolean);
    return parts.join(" · ");
  }
  return "";
}

/** 단계 배지 — 다른 화면(대시보드 · 차트)도 같은 모양을 쓴다 */
export function BottomStageBadge({ z }: { z: BottomZone }) {
  if (!z.stage || z.stage === "none") return <span className="bz-badge bz-none">해당 없음</span>;
  return <span className={`bz-badge bz-${z.stage}`}>{z.stage_ko}</span>;
}

const PRICE_SOURCE: Record<string, string> = { kis: "한투", toss: "토스", yahoo: "야후", daily_close: "일봉 종가", weekly_close: "주봉 종가" };
const hhmm = (iso: string) => {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const k = new Date(t + 9 * 3600_000);
  return `${k.getUTCMonth() + 1}/${k.getUTCDate()} ${String(k.getUTCHours()).padStart(2, "0")}:${String(k.getUTCMinutes()).padStart(2, "0")}`;
};
/** #310 지금가 대비(새 서버는 from_now_pct · status, 옛 서버는 주봉 종가 대비) */
function stepVsNow(s: BottomStep): string {
  if (s.status === "passed") return "지나감";
  if (s.status === "now") return "지금";
  const v = s.from_now_pct ?? s.from_close_pct;
  return v === 0 ? "지금" : `${v}%`;
}

/** 분석 화면 요약 탭의 인생 타점(장기 바닥권, #240) 카드. 지원하지 않거나 실패하면 그리지 않는다. */
export default function BottomZoneCard({ ticker }: { ticker: string }) {
  const [z, setZ] = useState<BottomZone | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setZ(undefined);
    api.bottomZone(ticker).then((v) => alive && setZ(v), () => alive && setZ(null));
    return () => { alive = false; };
  }, [ticker]);
  if (!z || !z.supported || !z.stage) return null;
  const known = (z.evidence ?? []).filter((e) => e.met !== null);
  return (
    <section className="card bz-card" aria-label="인생 타점(장기 바닥권)">
      <div className="bz-head">
        <h3>인생 타점 · 장기 바닥권</h3>
        <BottomStageBadge z={z} />
      </div>
      {z.stage === "none" ? (
        <p className="muted small">고점 대비 −{z.drawdown_pct}% — 몇 년에 한 번 오는 깊은 바닥권과는 거리가 있습니다.</p>
      ) : (
        <>
          <div className="bz-score">
            <span className="num bz-score-n">{z.score}</span>
            <span className="muted small">/ 100 · 고점 {price(z.high ?? 0)} 대비 −{z.drawdown_pct}% · {STAGE_HINT[z.stage]}</span>
          </div>
          <div className="bz-meter" aria-hidden="true"><span style={{ width: `${z.score}%` }} /></div>

          <h4 className="bz-sub">분할 매수(줍줍) 계획{z.stage === "watch" && " — 참고용"}</h4>
          {z.stage === "watch" && (
            <p className="small muted">아직 "관찰" 단계라 지금 사라는 뜻이 아닙니다. "접근" 단계가 되면 1차부터 나눠서 봅니다.</p>
          )}
          <p className="small muted bz-now">
            {z.price_now != null ? (
              <>지금 <strong className="num">{price(z.price_now)}</strong>
                {(z.price_source || z.price_at) && ` (${[z.price_source ? PRICE_SOURCE[z.price_source] ?? z.price_source : null, z.price_at ? hhmm(z.price_at) : null].filter(Boolean).join(" · ")})`}
                {z.close != null && ` · 주봉 종가 ${price(z.close)}`}</>
            ) : z.close != null ? `주봉 종가 ${price(z.close)} 기준` : null}
          </p>
          {z.first_step_gap_pct != null && z.first_step_gap_pct < 0 && (
            <p className="small bz-gap">지금가는 1차보다 위 — 1차까지 {z.first_step_gap_pct}%. 지금 사는 단계가 아닙니다.</p>
          )}
          <table className={`bz-ladder${z.stage === "watch" ? " bz-ladder-ref" : ""}`}>
            <thead><tr><th scope="col">단계</th><th scope="col">가격</th><th scope="col">비중</th><th scope="col">지금가 대비</th></tr></thead>
            <tbody>
              {(z.ladder ?? []).map((s, i) => (
                <tr key={`${s.status ?? ""}-${s.step}-${i}`} className={s.status === "passed" ? "bz-passed" : undefined}>
                  <td>{s.step}차{s.status === "passed" && <span className="sr-only"> (지나간 단계)</span>}</td>
                  <td className="num">{price(s.price)}</td>
                  <td className="num">{s.weight_pct}%</td>
                  <td className="num muted">{stepVsNow(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(z.ladder ?? []).some((s) => s.status === "passed") && (
            <p className="small muted">줄 그은 단계는 지금가가 이미 아래로 지나간 옛 계획 — 남은 비중을 지금가부터 다시 나눴습니다.</p>
          )}
          {z.invalidation && (
            <p className="small bz-void">
              <strong>계획 접기:</strong> 월봉 종가 {z.invalidation.monthly_close_below != null ? price(z.invalidation.monthly_close_below) : "—"} 아래
            </p>
          )}
          {z.targets && z.targets.length > 0 && (
            <p className="small muted">
              목표: {z.targets.filter((t) => t.price != null).map((t) => `${t.text} ${price(t.price as number)}`).join(" · ")}
            </p>
          )}
          {z.leveraged_note && <p className="small bz-warn">{z.leveraged_note}</p>}
        </>
      )}

      <details className="bz-why">
        <summary className="small">근거 {known.filter((e) => e.met).length} / {known.length}</summary>
        <ul className="bz-evidence">
          {(z.evidence ?? []).map((e) => (
            <li key={e.id} className={e.met === null ? "muted" : e.met ? "bz-met" : ""}>
              <span aria-hidden="true">{e.met === null ? "–" : e.met ? "✓" : "✗"}</span>
              <span className="sr-only">{e.met === null ? "모름" : e.met ? "충족" : "미충족"}</span>{" "}
              <span>{EVIDENCE_KO[e.id]}</span>
              {evidenceValue(e) && <span className="num muted"> {evidenceValue(e)}</span>}
              <span className="muted small"> · 기준 {e.threshold}</span>
              {e.met === null && <span className="muted small"> (모름 — 점수에서 뺌)</span>}
            </li>
          ))}
        </ul>
      </details>
      <p className="muted small">주봉 {z.as_of?.slice(0, 10)} 기준 · 규칙에 따른 판단이며 예측이 아닙니다.</p>
    </section>
  );
}
