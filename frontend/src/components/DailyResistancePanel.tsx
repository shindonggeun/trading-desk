import { useId } from "react";
import {
  dailyResistanceFocus, dailyResistancePresentation, dailyResistanceProduct,
  formatDailyDistance, formatDailyPrice, formatDailyRatio, type DailyResistanceObservation,
} from "../dailyResistance";
import DailyResistanceContext from "./DailyResistanceContext";
import "./DailyResistancePanel.css";

const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const MARKET = { us: "미국 정규장", cme: "CME Globex" };

function Stamp({ value }: { value: string | null | undefined }) {
  const at = value ? Date.parse(value) : NaN;
  return value && Number.isFinite(at) ? <time dateTime={value}>{KST_TIME.format(at)}</time> : <>미제공</>;
}

type FocusControl = { available: boolean; active: boolean; onToggle: () => void };

export default function DailyResistancePanel({ observation, focus }: {
  observation: DailyResistanceObservation;
  focus?: FocusControl;
}) {
  const title = useId();
  const { ticker, session, bar, metrics, resistance, source, observed_at, fetched_at } = observation;
  const { state, reaction, closeLabel, detail, next } = dailyResistancePresentation(observation);
  const product = dailyResistanceProduct(observation);
  const target = dailyResistanceFocus(observation);
  const waitingForClose = observation.status === "in_progress" || observation.reason === "source_delayed"
    || observation.reason === "confirmation_unverified";

  const priceEvidence = (
    <section className="daily-resistance-panel" aria-labelledby={title}>
      <div className="daily-resistance-heading">
        <h4 id={title}>일봉 마감과 저항 반응</h4>
        <span className="daily-resistance-state small">{state}</span>
      </div>
      <p className="muted small">
        {ticker}{product.name && " · " + product.name}{product.unit && " · " + product.unit}
        {session && <> · {MARKET[session.market]} {session.trade_date}</>}
      </p>

      {reaction && <p className="daily-resistance-reaction">{reaction}</p>}
      {detail && <p className="small">{detail}</p>}

      {(bar || resistance || metrics || observation.status === "in_progress") && (
        <dl className="daily-resistance-grid">
          <div><dt>{closeLabel}</dt><dd>{formatDailyPrice(bar?.close, product.unit)}</dd></div>
          <div>
            <dt>저항 가격</dt>
            <dd>{formatDailyPrice(resistance?.price, product.unit)}</dd>
          </div>
          <div><dt>저항 대비 종가</dt><dd>{formatDailyDistance(metrics?.close_distance_pct)}</dd></div>
          <div><dt>윗꼬리 비중</dt><dd>{formatDailyRatio(metrics?.upper_wick_ratio)}</dd></div>
          <div><dt>몸통 비중</dt><dd>{formatDailyRatio(metrics?.body_ratio)}</dd></div>
          <div><dt>봉 안 종가 위치</dt><dd>{formatDailyRatio(metrics?.close_position)}</dd></div>
        </dl>
      )}

      {resistance && (
        <p className="muted small">
          {resistance.kind === "channel_upper" ? "채널 상단" : "스윙 고점"} · 투영 봉 <Stamp value={resistance.projected_at} /> (한국 시각)
        </p>
      )}

      {focus && target && (
        <div className="daily-resistance-focus">
          <button type="button" className="chip chip-sub" aria-pressed={focus.available && focus.active}
            disabled={!focus.available} onClick={focus.onToggle}>
            {target.resistance ? "봉·저항 강조" : "봉 강조"}
          </button>
          {!focus.available && <span className="muted small">차트에서 같은 시각의 봉을 찾지 못했어요.</span>}
        </div>
      )}

      {waitingForClose && session && (
        <p className="muted small">확인 가능 (한국 시각) <Stamp value={session.confirm_after} /></p>
      )}
      {next && <p className="muted small">{next}</p>}
      <details className="daily-resistance-basis">
        <summary>판정 기준과 출처</summary>
        <p className="muted small">
          비중은 그날 고가와 저가 사이에서 차지하는 비율이에요.
          저항의 위아래 0.35 ATR 구간에 닿고 윗꼬리가 40% 이상이며 종가가 그 구간 아래면 윗꼬리 밀림으로 봐요.
          강한 양봉은 몸통 60% 이상, 종가가 봉의 상단 25% 안에 있고 몸통이 직전 ATR의 0.8배 이상인 봉이에요.
          저항에서 직전 ATR의 0.3배를 넘겨 마감하면 종가 돌파예요.
        </p>
        <dl className="daily-resistance-grid">
          <div><dt>원천</dt><dd>{source === "yahoo" ? "Yahoo Finance" : source ?? "미제공"}</dd></div>
          <div><dt>조회 기준 (한국 시각)</dt><dd><Stamp value={observed_at} /></dd></div>
          <div><dt>일봉 수신 (한국 시각)</dt><dd><Stamp value={fetched_at} /></dd></div>
          {session && <>
            <div><dt>세션 마감 (한국 시각)</dt><dd><Stamp value={session.closes_at} /></dd></div>
            {!waitingForClose && <div><dt>확인 가능 (한국 시각)</dt><dd><Stamp value={session.confirm_after} /></dd></div>}
          </>}
          {resistance && <div><dt>저항 계산 기준 봉 (한국 시각)</dt><dd><Stamp value={resistance.basis_at} /></dd></div>}
        </dl>
      </details>
    </section>
  );

  return (
    <>
      {priceEvidence}
      <DailyResistanceContext ticker={ticker} observedAt={observed_at} />
    </>
  );
}
