import { marketsFor, TREND_CLASS, useTrend } from "../marketTrend";
import type { TrendRow } from "../types";

// #461 지수별 대세 국면 칩. 국면은 그 지수의 "지금 추세"를 설명할 뿐 종목의 앞날을 예측하지 않는다 — 문구도 그렇게.

function title(r: TrendRow): string {
  const pend = r.pending ? ` · ${r.pending.state_ko} 신호 ${r.pending.sessions}일째(${r.pending.needs}일 이어지면 바뀜)` : "";
  const since = r.since_known ? `${r.since} 부터` : "기록 시작 이후 계속";
  return `${r.name} 지금 추세: ${r.state_ko}(${since})${pend} — 지수의 현재 상태 설명이며 앞으로의 수익 예측이 아닙니다`;
}

function Chip({ r, note }: { r: TrendRow; note?: string }) {
  return (
    <a href="#/tips" className={`trend-chip ${TREND_CLASS[r.state]}`} title={title(r)}>
      {r.name} <strong>{r.state_ko}</strong>{note ? <span className="muted"> {note}</span> : null}
      {r.pending && <span className="trend-chip-pend" aria-label={`${r.pending.state_ko} 신호 ${r.pending.sessions}일째`}>·{r.pending.state_ko}?</span>}
    </a>
  );
}

/** 대시보드 시장 구역 위: 지수 넷의 지금 추세 한 줄 */
export function TrendStrip() {
  const rows = useTrend();
  if (!rows.length) return null;
  return (
    <div className="trend-strip" role="group" aria-label="지수별 지금 추세">
      <span className="muted small">지수 지금 추세</span>
      {rows.map((r) => <Chip key={r.market} r={r} />)}
    </div>
  );
}

/** 분석 화면 팁 배지 옆: 이 종목 시장의 지금 추세(한국 → 코스피, 미국 → 나스닥100 · 반도체는 SOX 도) */
export function MarketTrendBadges({ ticker }: { ticker: string }) {
  const rows = useTrend();
  const want = marketsFor(ticker);
  const shown = want.map((m) => rows.find((r) => r.market === m)).filter((r): r is TrendRow => !!r);
  if (!shown.length) return null;
  const kosdaq = /\.KQ$/i.test(ticker);
  return (
    <span className="trend-badges" aria-label="이 종목 시장의 지금 추세">
      <span className="muted small">시장 지금 추세</span>
      {shown.map((r) => <Chip key={r.market} r={r} note={kosdaq && r.market === "kospi" ? "(코스피 기준)" : undefined} />)}
    </span>
  );
}

/** 휴대폰 요약 줄용 짧은 칩(코스피 · 나스닥100) */
export function TrendSummaryItems() {
  const rows = useTrend().filter((r) => r.market === "kospi" || r.market === "nasdaq100");
  return (
    <>
      {rows.map((r) => (
        <span key={r.market} className={`dash-sum-item ${TREND_CLASS[r.state]}`} title={title(r)}>
          <span className="muted">{r.name}</span> {r.state_ko}
        </span>
      ))}
    </>
  );
}
