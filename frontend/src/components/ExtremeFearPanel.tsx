import { useId, useState } from "react";
import { extremeFearChartHref } from "../extremeFear";
import type {
  ExtremeFearCondition, ExtremeFearConditionKey, ExtremeFearHorizon, ExtremeFearMetrics, ExtremeFearOutcomeStatus,
  ExtremeFearResponse, ExtremeFearSignal, ExtremeFearStrategyKey, ExtremeFearSymbol,
} from "../extremeFear";
import { percent, price, tone } from "../format";
import "./ExtremeFearPanel.css";

const SYMBOLS = ["SOXL", "KORU"] as const;
const HORIZONS = ["5", "20", "60", "252"] as const;
const CONDITION_KEYS = ["rsi", "cnn", "vix"] as const;
const OBSERVATION_KEYS = ["cnn", "vix"] as const;
const STRATEGIES = ["triple", "rsi_only", "buy_hold", "soxl_to_koru"] as const;

const CONDITION_LABEL: Record<ExtremeFearConditionKey, string> = {
  rsi: "Wilder RSI(14)", cnn: "CNN 공포·탐욕", vix: "VIX",
};
const STATUS_LABEL: Record<ExtremeFearCondition["status"], string> = {
  matched: "충족", not_matched: "미충족", unverified: "미확정", missing: "결측", delayed: "지연",
};
const CONDITION_REASON: Record<string, string> = {
  point_in_time_input_unavailable: "확정 자료 미확보",
  report_has_no_current_session: "이 거래일의 리포트 없음",
  before_session_close: "정규장 종료 전 자료",
  before_vix_session_close: "VIX 정규장 종료 전 자료",
  vix_session_still_open: "VIX 종가 공표 대기",
  publication_after_window: "판정 기한 이후 공표",
  no_same_session_fixing: "동일 거래일 확정값 없음",
  no_completed_session: "완료 거래일 없음",
  before_inception: "ETF 상장 전",
  missing_adjusted_bar: "조정 일봉 없음",
  rsi_warmup_or_gap: "연속 일봉 부족",
};
const STRATEGY_LABEL: Record<ExtremeFearStrategyKey, string> = {
  triple: "극단 공포", rsi_only: "RSI 단독", buy_hold: "단순 보유", soxl_to_koru: "SOXL 신호로 KORU 진입",
};
const OUTCOME_LABEL: Record<ExtremeFearOutcomeStatus, string> = {
  complete: "완결", not_entered: "미진입", immature: "보유기간 미경과", missing: "구간 결측", overlap_skipped: "중첩 제외",
};

const etFormat = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hourCycle: "h23",
});
const etDayFormat = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
});

function Day({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{value}</time> : <>미확인</>;
}

function Timestamp({ value }: { value: string | null }) {
  if (!value) return <>시각 미확인</>;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return <><Day value={value} /> · 시각 미확인</>;
  const instant = Date.parse(value);
  return Number.isFinite(instant) ? <time dateTime={value}>{etFormat.format(instant)} ET</time> : <>시각 미확인</>;
}

function VixObservationDay({ value }: { value: string | null }) {
  const instant = value ? Date.parse(value) : NaN;
  const day = value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value
    : Number.isFinite(instant) ? etDayFormat.format(instant) : null;
  return <>일봉 기준일 <Day value={day} /> · 공표 시각 미확인</>;
}

function CurrentConditions({ data }: { data: ExtremeFearResponse }) {
  const current = data.current;
  const confirmed = CONDITION_KEYS.filter((key) =>
    current.conditions[key].status === "matched" || current.conditions[key].status === "not_matched").length;
  return (
    <div className="extreme-fear-section">
      <div className="extreme-fear-heading">
        <h4>최근 완료일 조건</h4>
        <span className="muted small"><Day value={current.date} /> · 미국 정규장</span>
      </div>
      <p className="extreme-fear-verdict">
        <strong className={current.status === "matched" ? "extreme-fear-confirmed" : undefined}>
          {confirmed ? `3개 중 ${current.matched_count}개 충족` : "확정 판정 없음"}
        </strong>
        <span className="muted small">확정 {confirmed}/3 · 전체 {STATUS_LABEL[current.status]}</span>
      </p>
      <ul className="extreme-fear-conditions">
        {CONDITION_KEYS.map((key) => {
          const condition = current.conditions[key];
          const why = condition.reason && (CONDITION_REASON[condition.reason] ?? "판정 근거 미확인");
          return (
            <li key={key} className="extreme-fear-tile">
              <div className="extreme-fear-heading">
                <strong>{CONDITION_LABEL[key]}</strong>
                <span className={`small extreme-fear-status-${condition.status}`}>{STATUS_LABEL[condition.status]}</span>
              </div>
              <p className="extreme-fear-level">
                <strong>{condition.value === null ? "—" : `${condition.value}점`}</strong>
                <span className="muted small">기준 {condition.threshold}점 {condition.operator === "<=" ? "이하" : "이상"}</span>
              </p>
              <p className="muted small extreme-fear-source">{condition.source} · <Timestamp value={condition.available_at} /></p>
              {why && <p className="muted small">{why}</p>}
            </li>
          );
        })}
      </ul>
      {OBSERVATION_KEYS.some((key) => data.observations[key] != null) && (
        <div className="extreme-fear-observations">
          <p className="small"><strong>최신 원천 관측</strong> <span className="muted">· 확정 판정 집계 제외</span></p>
          <ul>
            {OBSERVATION_KEYS.map((key) => {
              const observation = data.observations[key];
              if (!observation) return null;
              return (
                <li key={key} className="small">
                  <strong>{CONDITION_LABEL[key]} {price(observation.value)}점</strong>
                  <span className="muted">
                    {observation.source ?? "원천 미확인"} · {key === "vix"
                      ? <VixObservationDay value={observation.as_of} />
                      : <Timestamp value={observation.as_of} />}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

function rate(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function StrategyMetrics({ title, metrics }: { title: string; metrics: ExtremeFearMetrics }) {
  const omitted = [
    [metrics.immature, "보유기간 미경과"], [metrics.not_entered, "미진입"],
    [metrics.missing, "결측"], [metrics.overlap_skipped, "중첩 제외"],
  ] as const;
  const noSample = metrics.immature > 0 ? "완결 표본 부족" : "완결 표본 없음";
  return (
    <article className="extreme-fear-tile extreme-fear-metrics">
      <h5>{title}</h5>
      <p className="extreme-fear-level">
        <strong>{metrics.n === 0 || metrics.win_rate === null ? "—" : rate(metrics.win_rate)}</strong>
        <span className="muted small">과거 이익 비율</span>
      </p>
      <p className="small">
        이익 {metrics.wins} / 완결 {metrics.n}건 · 손실 {metrics.losses} · 보합 {metrics.flat}
      </p>
      {metrics.n === 0 && <p className="muted small">{noSample}</p>}
      {metrics.win_rate_ci95 && (
        <p className="muted small">95% 구간 {rate(metrics.win_rate_ci95[0])} ~ {rate(metrics.win_rate_ci95[1])}</p>
      )}
      <dl className="extreme-fear-metric-list small">
        <div><dt>수익률 중앙값</dt><dd className={tone(metrics.median_return_pct)}>{percent(metrics.median_return_pct)}</dd></div>
        <div><dt>손실 중앙값</dt><dd className={tone(metrics.median_loss_pct)}>{percent(metrics.median_loss_pct)}</dd></div>
        <div><dt>최저 청산 손익</dt><dd className={tone(metrics.worst_return_pct)}>{percent(metrics.worst_return_pct)}</dd></div>
        <div><dt>진입 후 최대 추가 하락</dt><dd className={tone(metrics.worst_mae_pct)}>{percent(metrics.worst_mae_pct)}</dd></div>
      </dl>
      {metrics.loss_quartiles_pct && metrics.losses > 1 && (
        <p className="muted small">
          손실 25–75% 분위 {percent(metrics.loss_quartiles_pct[0])} ~ {percent(metrics.loss_quartiles_pct[2])}
        </p>
      )}
      {omitted.some(([count]) => count > 0) && (
        <p className="muted small">{omitted.filter(([count]) => count > 0).map(([count, label]) => `${label} ${count}건`).join(" · ")}</p>
      )}
    </article>
  );
}

function SignalHistory({ symbol, signals }: { symbol: ExtremeFearSymbol; signals: ExtremeFearSignal[] }) {
  return (
    <div className="extreme-fear-section">
      <h4>신호 이력</h4>
      {signals.length === 0 ? <p className="muted small">표시할 검증 신호가 없습니다.</p> : (
        <ul className="extreme-fear-signals">
          {signals.map((signal) => (
            <li key={signal.signal_date} className="extreme-fear-tile">
              <div className="extreme-fear-heading">
                <a className="link" href={extremeFearChartHref(symbol, signal.signal_date)}>
                  <Day value={signal.signal_date} /> 일봉 차트
                </a>
                <span className="muted small">진입 기준일 <Day value={signal.entry_date} /></span>
              </div>
              <details>
                <summary className="small">결과 보기</summary>
                <p className="muted small">조건 확정 <Timestamp value={signal.available_at} /></p>
                <dl className="extreme-fear-metric-list small">
                  {HORIZONS.map((horizon) => {
                    const outcome = signal.outcomes[horizon];
                    return (
                      <div key={horizon}>
                        <dt>{horizon}거래일</dt>
                        <dd className={tone(outcome.return_pct)}>
                          {outcome.status === "complete"
                            ? <>{percent(outcome.return_pct)} · 최대 추가 하락 {percent(outcome.mae_pct)}</>
                            : OUTCOME_LABEL[outcome.status]}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
                <p className="small">252거래일 내 사후 최고 {signal.ex_post_252.status === "complete"
                  ? percent(signal.ex_post_252.max_return_pct) : OUTCOME_LABEL[signal.ex_post_252.status]}</p>
              </details>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HistoricalResults({ data }: { data: ExtremeFearResponse }) {
  const [horizon, setHorizon] = useState<ExtremeFearHorizon>("5");
  const triple = data.strategies.triple;
  const comparable = data.status === "available" && triple;
  return (
    <div className="extreme-fear-section">
      <div className="extreme-fear-heading">
        <h4>고정 보유기간 성과</h4>
        {data.dataset && <span className="muted small">
          자료 기간 <Day value={data.dataset.start} /> ~ <Day value={data.dataset.end} />
        </span>}
      </div>
      {comparable ? (
        <>
          <div className="chips extreme-fear-horizons" role="group" aria-label="보유 거래일">
            {HORIZONS.map((days) => (
              <button key={days} type="button" className={`chip${horizon === days ? " chip-on" : ""}`}
                aria-pressed={horizon === days} onClick={() => setHorizon(days)}>{days}거래일</button>
            ))}
          </div>
          <p className="muted small">해당 거래일의 종가 청산{data.cost_bps != null && ` · 편도 비용 ${data.cost_bps}bps`}</p>
          <div className="extreme-fear-strategies">
            {STRATEGIES.map((key) => {
              const strategy = data.strategies[key];
              return strategy && <StrategyMetrics key={key} title={STRATEGY_LABEL[key]} metrics={strategy.horizons[horizon]} />;
            })}
          </div>
          <div className="extreme-fear-post">
            <strong>252거래일 내 사후 최고</strong>
            <p className="small">
              극단 공포 표본 {triple.ex_post_252.n}건 · 최고 수익률 중앙값 {percent(triple.ex_post_252.median_max_return_pct)}
            </p>
            <p className="muted small">진입 뒤 확인한 구간 최고가 기준 참고값입니다.</p>
          </div>
        </>
      ) : <p className="extreme-fear-post muted small">미검증 · 당시 공표 시각이 확인된 과거 자료가 없어 성과를 표시할 수 없습니다.</p>}
      <p className="muted small">두 ETF의 일일 3배 목표는 누적 경로에 따라 장기 성과가 달라집니다.</p>
    </div>
  );
}

interface Props {
  symbol: ExtremeFearSymbol;
  onSymbolChange: (symbol: ExtremeFearSymbol) => void;
  data: ExtremeFearResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}

export default function ExtremeFearPanel({ symbol, onSymbolChange, data, loading, error, onRetry }: Props) {
  const title = useId();
  return (
    <section className="card extreme-fear" aria-labelledby={title}>
      <header className="extreme-fear-heading">
        <h3 id={title}>극단 공포 조건과 백테스트</h3>
        <a className="link small extreme-fear-chart" href={extremeFearChartHref(symbol)}>{symbol} 일봉 차트</a>
      </header>
      <div className="chips" role="group" aria-label="극단 공포 종목">
        {SYMBOLS.map((item) => (
          <button key={item} type="button" className={`chip${symbol === item ? " chip-on" : ""}`}
            aria-pressed={symbol === item} onClick={() => onSymbolChange(item)}>{item}</button>
        ))}
      </div>
      {loading ? <p className="muted small" role="status">극단 공포 조건을 불러오는 중…</p> : error ? (
        <div className="extreme-fear-heading">
          <p className="error small" role="alert">{error}</p>
          <button className="link small" type="button" onClick={onRetry}>다시 시도</button>
        </div>
      ) : data && data.symbol === symbol ? (
        <>
          <CurrentConditions data={data} />
          <HistoricalResults data={data} />
          <SignalHistory symbol={symbol} signals={data.status === "available" ? data.signal_history : []} />
        </>
      ) : null}
    </section>
  );
}
