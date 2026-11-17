import { price } from "../format";
import type { EntryCandidate, EntryNow } from "../types";
import { pctText } from "./chartEntry";

/**
 * #313 차트 위 한 줄 — 지금가 기준 최적 타점(#312). 방향은 색과 글자(롱 · 숏)를 함께 쓴다.
 * 분석(run)으로 연 차트면 "분석 당시 타점선과 다를 수 있다"를 붙인다(차트의 진입 · 손절 · 목표선은 분석 당시 값).
 */
export default function EntrySummary({ entry, failed, fromRun, elapsed = null }: {
  entry: EntryNow | null; failed: boolean; fromRun: boolean;
  /** #559 일봉 마지막 봉의 장 경과 비율(진행 중일 때만) — 서버는 거래량을 이 비율로 하루치 환산해 판정한다(#432) */
  elapsed?: number | null;
}) {
  if (failed && !entry) return <p className="muted small entry-sum">지금가 기준 타점을 받지 못했습니다.</p>;
  if (!entry) return null;
  const best = entry.best;
  const when = fromRun ? "지금 기준" : "타점";
  const today = todayVolume(entry, elapsed);
  if (!best) {
    return (
      <div className="small entry-sum entry-aside">
        <p><strong>관망</strong> — {entry.stand_aside?.reason ?? "타점 근거가 없다"}</p>
        {today && <p className="muted">{today}</p>}
      </div>
    );
  }
  const aside = entry.stand_aside;
  return (
    // 1분마다 % 가 바뀌므로 role="status" 를 두지 않는다(화면 낭독기가 매번 다시 읽는다)
    <div className={`small entry-sum${aside ? " entry-aside" : ""}`}>
      <p>
        {aside ? (
          <><strong>관망</strong> — {aside.reason} <span className="muted">· 참고: {line(best)}</span></>
        ) : (
          <>
            <span className="muted">{when} </span>
            <strong className={best.side === "long" ? "entry-long" : "entry-short"}>{best.side_ko}</strong>{" "}
            {best.kind === "now" ? <strong className="entry-now">지금 구역 안</strong> : best.kind_ko} · {line(best)}
            {best.ev_r != null && <> · 계획당 기대 <strong className="num">{best.ev_r > 0 ? "+" : ""}{best.ev_r.toFixed(2)}R</strong></>}
            {best.reach_prob != null && <> · 체결 {Math.round(best.reach_prob * 100)}%</>}
            <span className="muted"> (n={best.n})</span>
          </>
        )}
      </p>
      {entry.other && (
        <p className="muted">
          반대 {entry.other.side_ko}: {line(entry.other)}
          {entry.other.ev_r != null && ` · 기대 ${entry.other.ev_r > 0 ? "+" : ""}${entry.other.ev_r.toFixed(2)}R`}
        </p>
      )}
      {today && <p className="muted">{today}</p>}
      {fromRun && <p className="muted">차트의 진입 · 손절 · 목표선은 분석 당시 값, 색 띠는 지금 기준입니다.</p>}
      {failed && <p className="muted">갱신하지 못해 마지막으로 받은 값입니다.</p>}
      <details>
        <summary className="muted">근거</summary>
        <ul>{best.why.map((w) => <li key={w}>{w}</li>)}</ul>
        <p className="muted">{entry.basis}{entry.stale ? " · 실시간 가격이 없어 마지막 종가 기준" : ""}</p>
      </details>
    </div>
  );
}

/** #559 문구에 거래량 판정이 있고 오늘 봉이 진행 중이면, 그 거래량이 하루치 환산 값이거나 아직 판정 전임을 밝힌다 */
function todayVolume(entry: EntryNow, elapsed: number | null): string | null {
  if (elapsed == null) return null;
  const texts = [entry.stand_aside?.reason ?? "", ...(entry.best?.why ?? [])];
  if (!texts.some((t) => t.includes("거래량"))) return null;
  return elapsed < 0.15
    ? "오늘 봉은 장 전 · 장 초반이라 거래량은 아직 판정하지 않습니다."
    : `오늘 봉은 진행 중 — 거래량은 지금까지 양을 경과 시간(${Math.round(elapsed * 100)}%) 비례로 하루치 환산해 20일 평균과 견줍니다.`;
}

function line(k: EntryCandidate): string {
  const [lo, hi] = k.zone;
  const zone = lo != null && hi != null ? `${price(lo)}~${price(hi)}` : price(k.first);
  const t1 = k.targets[0];
  const now = k.from_now_pct != null ? ` (지금 ${pctText(k.from_now_pct)})` : "";
  return `${zone}${now} · 손절 ${price(k.stop)} · 목표 ${t1 ? price(t1.price) : "—"}`;
}
