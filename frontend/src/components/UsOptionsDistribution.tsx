import { useEffect, useId, useRef, useState } from "react";
import type { LoadUsOptions, UsOptionsMetric, UsOptionsRank, UsOptionsResponse, UsOptionsStrike, UsOptionsTotals } from "../usOptionsTypes";
import "./UsOptionsDistribution.css";

const TICKERS = ["NVDA", "AMD", "AAPL", "SPY", "QQQ", "MSFT", "TSLA"];
const METRIC_LABEL: Record<UsOptionsMetric, string> = { volume: "당일 거래량", oi: "미결제약정 (OI)" };
const CONTRACTS = new Intl.NumberFormat("ko-KR");
const COMPACT_CONTRACTS = new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 1 });
const PRICE = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const RATIO = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 3 });
const NEW_YORK_TIME = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  hourCycle: "h23", timeZone: "America/New_York",
});

type Selection = { ticker: string; expiration: string | null };
type RequestResult = { selection: Selection; data: UsOptionsResponse | null };

function formatCount(count: number | null) {
  return count === null ? "—" : CONTRACTS.format(count);
}

function Timestamp({ value }: { value: string | null }) {
  if (value === null) return <>미제공</>;
  return <time dateTime={value}>{NEW_YORK_TIME.format(new Date(value))}</time>;
}

function formatDelay(seconds: number | null) {
  if (seconds === null) return "미제공";
  if (seconds < 60) return `${seconds}초`;
  const minutes = Math.floor(seconds / 60);
  return seconds % 60 === 0 ? `${minutes}분` : `${minutes}분 ${seconds % 60}초`;
}

function messageFor(data: UsOptionsResponse) {
  switch (data.status) {
    case "unavailable": return data.reason === "no_provider"
      ? "데이터 원천이 구성되지 않았습니다."
      : "데이터 원천에서 옵션을 불러오지 못했습니다.";
    case "no_options": return "이 종목에 상장된 옵션 만기가 없습니다.";
    case "invalid_expiration": return "선택한 만기를 조회할 수 없습니다. 목록에서 만기를 다시 선택하세요.";
    case "no_data": return data.reason === "no_current_expirations"
      ? "조회할 수 있는 만기가 없습니다."
      : "선택한 만기에 표시할 행사가 데이터가 없습니다.";
    case "ok": return null;
  }
}

function MetricSummary({ totals, spot }: { totals: UsOptionsTotals; spot: number | null }) {
  const ratio = totals.put_call_ratio;
  const missingRatio = { ok: "", zero_calls: "콜 합계 0", partial: "일부 수치 누락", missing: "집계 수치 없음" }[totals.status];
  return (
    <dl className="us-opt-summary">
      <div>
        <dt>풋/콜 비율</dt>
        <dd>{ratio === null ? "—" : ratio > 0 && ratio < 0.001 ? "<0.001" : RATIO.format(ratio)}</dd>
        {missingRatio && <dd className="us-opt-summary-note">{missingRatio}</dd>}
      </div>
      <div><dt>콜 합계</dt><dd>{formatCount(totals.calls)}</dd></div>
      <div><dt>풋 합계</dt><dd>{formatCount(totals.puts)}</dd></div>
      <div><dt>현재가</dt><dd>{spot === null ? "—" : PRICE.format(spot)}</dd></div>
    </dl>
  );
}

function StrikeChart({ rows, metric, spot }: { rows: UsOptionsStrike[]; metric: UsOptionsMetric; spot: number | null }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, resize] = useState(0);
  const title = useId();
  const description = useId();
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => resize(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const points = rows.map(row => metric === "volume"
    ? { strike: row.strike, calls: row.call_volume, puts: row.put_volume }
    : { strike: row.strike, calls: row.call_oi, puts: row.put_oi });
  const hasCounts = points.some(point => point.calls !== null || point.puts !== null);
  const maximum = Math.max(1, ...points.flatMap(({ calls, puts }) => [calls ?? 0, puts ?? 0]));
  const minStrike = points[0].strike;
  const maxStrike = points[points.length - 1].strike;
  const spotInRange = spot !== null && spot >= minStrike && spot <= maxStrike;
  const plotLeft = 48;
  const plotRight = width - 14;
  const plotTop = 9;
  const plotBottom = 174;
  const plotWidth = Math.max(1, plotRight - plotLeft);
  const barInset = Math.min(13, plotWidth / 4);
  const x = (strike: number) => minStrike === maxStrike ? (plotLeft + plotRight) / 2
    : plotLeft + barInset + (strike - minStrike) / (maxStrike - minStrike) * (plotWidth - 2 * barInset);
  const y = (count: number) => plotBottom - count / maximum * (plotBottom - plotTop);
  const strikeTicks = points.length > 2 && width >= 460
    ? [minStrike, minStrike + (maxStrike - minStrike) / 2, maxStrike]
    : [...new Set([minStrike, maxStrike])];
  const countTicks = maximum > 1 ? [0, Math.floor(maximum / 2), maximum] : [0, maximum];
  const barWidth = Math.min(11, plotWidth / (3 * points.length));

  return (
    <figure className="us-opt-figure">
      <figcaption>
        <span>행사가별 계약 수</span>
        <span className="us-opt-legend"><i className="us-opt-key-call" aria-hidden="true" />콜</span>
        <span className="us-opt-legend"><i className="us-opt-key-put" aria-hidden="true" />풋</span>
        {spotInRange && <span className="us-opt-legend"><i className="us-opt-key-spot" aria-hidden="true" />현재가</span>}
      </figcaption>
      <div ref={box} className="us-opt-plot">
        {!hasCounts && <p className="muted small">이 지표의 행사가별 수치가 없습니다.</p>}
        {hasCounts && width > 80 && (
          <svg width={width} height={205} role="img" aria-labelledby={title} aria-describedby={description}>
            <title id={title}>행사가별 콜·풋 분포</title>
            <desc id={description}>행사가마다 왼쪽 막대는 콜, 오른쪽 막대는 풋 계약 수입니다. 점선은 표시 범위 안의 현재가입니다.</desc>
            {countTicks.map(count => (
              <g key={count} className="us-opt-axis">
                <line x1={plotLeft} y1={y(count)} x2={plotRight} y2={y(count)} />
                <text x={plotLeft - 7} y={y(count) + 4} textAnchor="end">{COMPACT_CONTRACTS.format(count)}</text>
              </g>
            ))}
            {points.map(({ strike, calls, puts }, index) => {
              const center = x(strike);
              const previous = points[index - 1];
              const next = points[index + 1];
              const leftGap = previous ? center - x(previous.strike) : Infinity;
              const rightGap = next ? x(next.strike) - center : Infinity;
              const pairWidth = Math.min(barWidth, leftGap / 2, rightGap / 2);
              return (
                <g key={strike}>
                  <title>{PRICE.format(strike)} · 콜 {formatCount(calls)} · 풋 {formatCount(puts)}</title>
                  {calls !== null && calls > 0 && <rect className="us-opt-bar-call" x={center - pairWidth} y={y(calls)} width={pairWidth} height={plotBottom - y(calls)} />}
                  {puts !== null && puts > 0 && <rect className="us-opt-bar-put" x={center} y={y(puts)} width={pairWidth} height={plotBottom - y(puts)} />}
                </g>
              );
            })}
            {spotInRange && <line className="us-opt-spot-line" x1={x(spot)} x2={x(spot)} y1={plotTop} y2={plotBottom} />}
            {strikeTicks.map((strike, index) => (
              <text key={strike} className="us-opt-axis" x={x(strike)} y={plotBottom + 17}
                textAnchor={strikeTicks.length === 1 ? "middle" : index === 0 ? "start" : index === strikeTicks.length - 1 ? "end" : "middle"}>
                {PRICE.format(strike)}
              </text>
            ))}
          </svg>
        )}
      </div>
      {spot !== null && !spotInRange && <p className="muted small">현재가는 표시 행사가 범위 {spot < minStrike ? "아래" : "위"}에 있습니다.</p>}
    </figure>
  );
}

function TopStrikes({ title, rows, total }: { title: string; rows: UsOptionsRank[]; total: number | null }) {
  return (
    <table className="us-opt-ranks">
      <caption>{title}</caption>
      <thead><tr><th scope="col">행사가</th><th scope="col">계약 수</th></tr></thead>
      <tbody>{rows.length ? rows.map(({ strike, count }) => (
        <tr key={strike}><th scope="row">{PRICE.format(strike)}</th><td>{formatCount(count)}</td></tr>
      )) : <tr><td colSpan={2}>{total === null ? "집계 수치 없음" : "집중 행사가 없음"}</td></tr>}</tbody>
    </table>
  );
}

function Timing({ data, totals }: { data: UsOptionsResponse; totals: UsOptionsTotals }) {
  return (
    <dl className="us-opt-timing">
      <div><dt>출처</dt><dd>{data.source ?? "미제공"}</dd></div>
      <div><dt>기준일</dt><dd>{totals.trade_date ?? "미제공"}</dd></div>
      <div><dt>기준 시각 (뉴욕)</dt><dd><Timestamp value={totals.as_of} /></dd></div>
      <div><dt>지연</dt><dd>{formatDelay(totals.delay_sec)}</dd></div>
      <div><dt>현재가 기준 시각 (뉴욕)</dt><dd><Timestamp value={data.spot.as_of} /></dd></div>
      <div><dt>현재가 지연</dt><dd>{formatDelay(data.spot.delay_sec)}</dd></div>
      <div><dt>조회 시각 (뉴욕)</dt><dd><Timestamp value={data.fetched_at} /></dd></div>
    </dl>
  );
}

export default function UsOptionsDistribution({ loadOptions }: { loadOptions: LoadUsOptions }) {
  const [selection, setSelection] = useState<Selection>({ ticker: TICKERS[0], expiration: null });
  const [metric, setMetric] = useState<UsOptionsMetric>("volume");
  const [result, setResult] = useState<RequestResult | null>(null);
  const [catalog, setCatalog] = useState<Pick<UsOptionsResponse, "ticker" | "expirations"> | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void loadOptions(selection.ticker, selection.expiration, controller.signal).then(
      data => {
        if (controller.signal.aborted) return;
        const { ticker, expirations } = data;
        setCatalog({ ticker, expirations });
        setResult({ selection, data });
      },
      () => {
        if (!controller.signal.aborted) setResult({ selection, data: null });
      },
    );
    return () => controller.abort();
  }, [selection, loadOptions]);

  const current = result?.selection === selection ? result : null;
  const data = current?.data;
  const currentCatalog = catalog?.ticker === selection.ticker ? catalog : null;
  const expirations = currentCatalog?.expirations ?? [];
  const selectedDate = data?.expiration ?? selection.expiration ?? "";
  const status = data ? messageFor(data) : null;
  const canRetry = current !== null && (!data || (data.status === "unavailable" && data.reason !== "no_provider"));
  const totals = data?.[metric];
  const hasSnapshot = data?.status === "ok" || (data?.status === "no_data" && data.reason !== "no_current_expirations");

  return (
    <section className="us-opt" aria-label="미장 옵션 만기별 분포">
      <div className="us-opt-controls">
        <label>종목
          <select value={selection.ticker} onChange={event => {
            setCatalog(null);
            setSelection({ ticker: event.target.value, expiration: null });
          }}>
            {TICKERS.map(ticker => <option key={ticker}>{ticker}</option>)}
          </select>
        </label>
        <label>만기
          <select value={expirations.includes(selectedDate) ? selectedDate : ""} disabled={!expirations.length}
            onChange={event => setSelection({ ...selection, expiration: event.target.value })}>
            <option value="" disabled>만기 선택</option>
            {expirations.map(date => <option key={date}>{date}</option>)}
          </select>
        </label>
        <div className="seg us-opt-metrics" role="group" aria-label="집계 지표">
          {(Object.keys(METRIC_LABEL) as UsOptionsMetric[]).map(key => (
            <button key={key} type="button" className={metric === key ? "on" : ""} aria-pressed={metric === key}
              onClick={() => setMetric(key)}>{METRIC_LABEL[key]}</button>
          ))}
        </div>
      </div>
      {!current && <p className="muted small" role="status">옵션 데이터를 불러오는 중…</p>}
      {current && !data && <p className="error small" role="alert">옵션 데이터를 불러오지 못했습니다.</p>}
      {status && <p className="muted small" role="status">{status}</p>}
      {canRetry && <button className="link us-opt-retry" type="button" onClick={() => setSelection({ ...selection })}>다시 시도</button>}
      {data && totals && hasSnapshot && (
        <>
          <MetricSummary totals={totals} spot={data.spot.price} />
          {data.strikes.length > 0 && (
            <div className="us-opt-breakdown">
              <StrikeChart rows={data.strikes} metric={metric} spot={data.spot.price} />
              <div className="us-opt-top">
                <TopStrikes title="상위 콜 행사가" rows={data.top[metric].calls} total={totals.calls} />
                <TopStrikes title="상위 풋 행사가" rows={data.top[metric].puts} total={totals.puts} />
              </div>
            </div>
          )}
          <Timing data={data} totals={totals} />
          {(data.range.truncated || data.expirations_truncated || data.excluded_contracts > 0) && (
            <ul className="us-opt-coverage muted small">
              {data.range.truncated && <li>행사가 {formatCount(data.range.matching_strikes)}개 중 {formatCount(data.strikes.length)}개 표시 · 상위 행사가 집계는 전체 범위 기준</li>}
              {data.expirations_truncated && <li>가까운 만기 {formatCount(data.expirations.length)}개 표시</li>}
              {data.excluded_contracts > 0 && <li>집계 제외 계약 {formatCount(data.excluded_contracts)}건</li>}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
