import { useId } from "react";
import { percent } from "../format";
import type { WeeklyCheck, WeeklyDrawdownObservation, WeeklyEvidence, WeeklyEvidenceKey } from "../weeklyDrawdownTypes";
import "./WeeklyDrawdownGuide.css";

const EVIDENCE_LABEL = {
  bollinger: "볼린저밴드 하단",
  rsi: "주봉 RSI",
  channel: "주봉 채널 하단",
  fib: "피보나치 2.618 하락 확장",
} satisfies Record<WeeklyEvidenceKey, string>;

const EVIDENCE_KEYS: WeeklyEvidenceKey[] = ["bollinger", "rsi", "channel", "fib"];

const EVIDENCE_STATE: Record<WeeklyEvidence["state"], string> = {
  near: "접근",
  oversold: "과매도",
  below: "지지 이탈",
  normal: "해당 없음",
  missing: "근거 결측",
};

const CANDLE_LABEL = { closed: "종가 확정", forming: "진행 중", unknown: "확정 여부 미제공" };
const CHECK_STATE = {
  watching: "관찰 중",
  invalidated: "조건 무효화",
  not_confirmed: "확인되지 않음",
  missing: "판단 불가",
} satisfies Record<Exclude<WeeklyCheck["state"], "confirmed">, string>;

const POINTS = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const NY_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "America/New_York",
  dateStyle: "medium",
  timeStyle: "short",
});

type AvailableObservation = Extract<WeeklyDrawdownObservation, { status: "available" }>;

function validPrice(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value > 0;
}

function validEvidence(key: WeeklyEvidenceKey, value: number | null): value is number {
  return key === "rsi"
    ? value !== null && Number.isFinite(value) && value >= 0 && value <= 100
    : validPrice(value);
}

// 양수는 현재가 위, 음수는 현재가 아래다. 신호 판정 자체는 입력에서 받는다.
function relativeDistance(level: number, current: number): number {
  return (level - current) / current * 100;
}

function CurrentTime({ asOf }: { asOf: string | null }) {
  const ms = asOf ? Date.parse(asOf) : NaN;
  return Number.isFinite(ms) ? <time dateTime={asOf ?? undefined}>{NY_TIME.format(ms)}</time> : "미제공";
}

function EvidenceRow({ kind, item, current }: { kind: WeeklyEvidenceKey; item: WeeklyEvidence; current: number | null }) {
  const value = validEvidence(kind, item.value) && item.state !== "missing" ? item.value : null;
  const distance = kind !== "rsi" && value !== null && validPrice(current)
    ? relativeDistance(value, current)
    : null;
  return (
    <li>
      <div className="weekly-drawdown-evidence-row">
        <strong>{EVIDENCE_LABEL[kind]}</strong>
        <span className="weekly-drawdown-state">{EVIDENCE_STATE[value === null ? "missing" : item.state]}</span>
        <span className="weekly-drawdown-value">{value === null ? "미제공" : POINTS.format(value)}</span>
      </div>
      {distance !== null && <small className="muted">현재가 대비 {percent(distance)}</small>}
      {item.reason && <small className="muted">{item.reason}</small>}
    </li>
  );
}

function Overlap({ overlap, current }: { overlap: AvailableObservation["overlap"]; current: number | null }) {
  const { low, high } = overlap;
  const hasRange = overlap.state === "overlap" && validPrice(low) && validPrice(high) && low <= high;
  return (
    <div className="weekly-drawdown-block">
      <h5>지지 겹침 구간</h5>
      <p>
        {hasRange ? (low === high ? POINTS.format(low) : `${POINTS.format(low)} ~ ${POINTS.format(high)}`)
          : overlap.state === "none" ? "겹침 없음" : "가격 구간 미제공"}
        {hasRange && validPrice(current) && (
          <small className="muted weekly-drawdown-distance">
            현재가 대비 {percent(relativeDistance(low, current))}
            {low !== high && ` ~ ${percent(relativeDistance(high, current))}`}
          </small>
        )}
      </p>
      {overlap.state === "overlap" && (
        <p className="muted small">{overlap.basis.length ? overlap.basis.map(key => EVIDENCE_LABEL[key]).join(" · ") : "겹침 근거 미제공"}</p>
      )}
    </div>
  );
}

function Check({ kind, check, usableWeek }: {
  kind: "rebound" | "recovery";
  check: WeeklyCheck;
  usableWeek: boolean;
}) {
  const title = kind === "rebound" ? "단기 반등 관찰" : "장기 주봉 구조 회복";
  const confirmed = kind === "rebound" ? "반등 확인" : "주봉 구조 회복 확인";
  const basis = check.state === "invalidated" ? check.invalidation : check.condition;
  const state = usableWeek && basis?.trim() ? check.state : "missing";
  return (
    <section className="weekly-drawdown-block" aria-label={title}>
      <h5>{title}</h5>
      <p className="weekly-drawdown-state">{state === "confirmed" ? confirmed : CHECK_STATE[state]}</p>
      <dl>
        <div><dt>확인 조건</dt><dd>{check.condition || "미제공"}</dd></div>
        <div><dt>무효화 조건</dt><dd>{check.invalidation || "미제공"}</dd></div>
      </dl>
    </section>
  );
}

export default function WeeklyDrawdownGuide({ observation }: { observation: WeeklyDrawdownObservation }) {
  const id = useId();
  return (
    <section className="weekly-drawdown" aria-labelledby={id}>
      <h4 id={id}>주봉 급락 관찰</h4>
      {observation.status === "unavailable" ? (
        <p className="muted small">
          {observation.reason?.trim() ? `주봉 관찰 자료를 볼 수 없어요. ${observation.reason}` : "주봉 관찰 자료가 아직 연결되지 않았어요."}
        </p>
      ) : (
        <details open>
          <summary>관찰 근거와 확인 조건</summary>
          <Observation observation={observation} />
        </details>
      )}
    </section>
  );
}

function Observation({ observation }: { observation: AvailableObservation }) {
  const { week, current, evidence, overlap, rebound, recovery } = observation;
  const start = week.start?.trim();
  const source = week.source?.trim();
  const usableWeek = Boolean(start && source && week.candle === "closed");
  return (
    <div className="weekly-drawdown-body">
      <dl className="weekly-drawdown-meta">
        <div><dt>기준 주 (뉴욕)</dt><dd>{start ? <time dateTime={start}>{start}</time> : "미제공"}</dd></div>
        <div><dt>주봉 출처</dt><dd>{source || "미제공"}</dd></div>
        <div><dt>주봉 상태</dt><dd>{CANDLE_LABEL[week.candle]}</dd></div>
        <div><dt>현재가</dt><dd>{validPrice(current.price) ? POINTS.format(current.price) : "미제공"}</dd></div>
        <div><dt>현재가 출처</dt><dd>{current.source?.trim() || "미제공"}</dd></div>
        <div><dt>현재가 기준 시각 (뉴욕)</dt><dd><CurrentTime asOf={current.asOf} /></dd></div>
      </dl>
      {!usableWeek && (
        <p className="muted small">기준 주와 출처가 확인된 확정 주봉으로 반등과 주봉 구조 회복을 판단해요.</p>
      )}
      <ul className="weekly-drawdown-evidence" aria-label="주봉 관찰 근거">
        {EVIDENCE_KEYS.map(key => <EvidenceRow key={key} kind={key} item={evidence[key]} current={current.price} />)}
      </ul>
      <Overlap overlap={overlap} current={current.price} />
      <div className="weekly-drawdown-checks">
        <Check kind="rebound" check={rebound} usableWeek={usableWeek} />
        <Check kind="recovery" check={recovery} usableWeek={usableWeek} />
      </div>
    </div>
  );
}

export function WeeklyDrawdownTip() {
  return (
    <article id="tip-weekly-drawdown" className="card tip-card weekly-drawdown-tip">
      <header className="tip-head"><h3>급락 뒤 주봉에서 확인할 것</h3></header>
      <ol className="small">
        <li>주봉 RSI로 최근 하락 강도를 보고 볼린저밴드 하단, 채널 하단, 피보나치 하락 확장이 모인 가격대를 찾아요.</li>
        <li>단기 반등은 지지 후보에 접근한 뒤 주봉 종가가 반등 확인 가격에 닿는지 봐요. 먼저 무효화 가격 아래로 내려가면 반등 관찰을 멈춰요.</li>
        <li>장기 회복은 주봉 상승 흐름과 이전 고점 위 종가가 이어지는지 따로 확인해요.</li>
      </ol>
      <p className="muted small">겹침을 관찰한 표본이 적었고 일반 조정보다 우위가 있는지는 확인되지 않았어요.</p>
      <a className="small" href="#/chart/NQ%3DF?interval=1w&amp;read=1">나스닥100 선물 주봉과 차트 읽기 열기</a>
    </article>
  );
}
