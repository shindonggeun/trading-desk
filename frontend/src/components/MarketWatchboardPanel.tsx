import { percent } from "../format";
import {
  watchboardCalendarPoints, watchboardChartHref, watchboardSegments, type CalendarPoint,
  type WatchboardGroup, type WatchboardItem, type WatchboardMarket, type WatchboardMarketState,
  type WatchboardReason, type WatchboardResponse,
} from "../marketWatchboard";
import "./MarketWatchboardPanel.css";

const GROUPS: readonly { id: WatchboardGroup; label: string }[] = [
  { id: "semiconductors", label: "반도체" },
  { id: "crypto", label: "코인" },
  { id: "gold_miners", label: "금광주" },
];

const MARKETS: readonly { id: WatchboardMarket; label: string }[] = [
  { id: "kr", label: "한국 · KRX" },
  { id: "cme", label: "미국 · CME" },
  { id: "us", label: "미국 · 주식시장" },
  { id: "jp", label: "일본 · 도쿄" },
  { id: "tw", label: "대만 · TWSE" },
  { id: "crypto", label: "코인 · UTC" },
];

const MARKET_STATE: Record<WatchboardMarketState, string> = {
  open: "장중", break: "장중 휴식", closed: "장 마감", holiday: "휴장", unknown: "장 상태 미확인",
};
const KIND: Record<WatchboardItem["kind"], string> = {
  index: "지수", future: "연속 선물", stock: "본주", adr: "미국 ADR", crypto: "코인 현물", etf: "ETF",
};
const RELATION: Record<WatchboardItem["relation"], string> = {
  benchmark: "비교 기준", related: "관련 상품", reference: "참고 상품",
};
const STATUS = { available: "조회 가능", delayed: "데이터 지연", missing: "데이터 결측" };
const REASON: Record<WatchboardReason, string> = {
  source_error: "원천 일봉 조회 실패",
  wrong_instrument: "요청과 원천 종목 또는 봉 주기가 다름",
  source_unverified: "일봉 원천 미확인",
  timestamp_unverified: "원천 수신 시각 미확인",
  no_completed_bar: "확정된 종가 없음",
  session_unverified: "최근 세션의 마감 일정 미확인",
  latest_session_missing: "최근 확정 거래일의 일봉 누락",
};

const valueFormat = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 });
const stampFormats = new Map<string, Intl.DateTimeFormat>();

function Stamp({ value, zone }: { value: string | null; zone: string }) {
  if (!value || !Number.isFinite(Date.parse(value))) return <>—</>;
  try {
    let format = stampFormats.get(zone);
    if (!format) {
      format = new Intl.DateTimeFormat("ko-KR", {
        timeZone: zone, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
        hourCycle: "h23", timeZoneName: "short",
      });
      stampFormats.set(zone, format);
    }
    return <time dateTime={value}>{format.format(new Date(value))}</time>;
  } catch {
    return <time dateTime={value}>{value}</time>;
  }
}

function shownPct(value: number | null): string {
  return value !== null && Number.isFinite(value) ? percent(value) : "—";
}

function tone(value: number | null) {
  return value === null || !Number.isFinite(value) || value === 0 ? undefined : value > 0 ? "up" : "down";
}

type Project = (point: CalendarPoint) => string;

function Curve({ points, project, dash }: { points: CalendarPoint[]; project: Project; dash?: string }) {
  return (
    <>
      {watchboardSegments(points).map((segment) => segment.length > 1 ? (
        <polyline key={segment[0].date} points={segment.map(project).join(" ")}
          strokeDasharray={dash} vectorEffect="non-scaling-stroke" />
      ) : null)}
      {points.map((point) => {
        const [cx, cy] = project(point).split(",");
        return <circle key={point.date} cx={cx} cy={cy} r="2.1" vectorEffect="non-scaling-stroke" />;
      })}
    </>
  );
}

function Sparkline({ item }: { item: WatchboardItem }) {
  const points = watchboardCalendarPoints(item.history.map(({ trade_date, close }) => ({ date: trade_date, value: close })));
  if (!points.length) return null;
  const start = points[0], end = points.at(-1)!;
  const high = Math.max(...points.map(({ value }) => value));
  const low = Math.min(...points.map(({ value }) => value));
  const project = ({ day, value }: CalendarPoint) =>
    `${day === start.day && day === end.day ? 90 : 4 + 172 * (day - start.day) / (end.day - start.day)},${high === low ? 18 : 4 + 28 * (high - value) / (high - low)}`;
  return (
    <div className="watchboard-spark">
      <span className="muted small">최근 일봉 {points.length}개</span>
      <svg viewBox="0 0 180 36" preserveAspectRatio="none" role="img"
        aria-label={`${item.name} 종가 흐름 ${start.date}~${end.date}, 최저 ${valueFormat.format(low)}, 최고 ${valueFormat.format(high)}`}>
        <Curve points={points} project={project} />
      </svg>
    </div>
  );
}

function ItemCard({ item }: { item: WatchboardItem }) {
  const chart = watchboardChartHref(item);
  const close = item.latest?.close ?? null;
  const source = item.source === "yahoo" ? "Yahoo Finance" : item.source;
  return (
    <article className="watchboard-instrument">
      <header className="watchboard-instrument-head">
        <div>
          <h3>{item.name}</h3>
          <p className="muted">{item.ticker} · {KIND[item.kind]} · {RELATION[item.relation]}</p>
        </div>
        {item.status !== "available" && <span className="watchboard-data-status" data-state={item.status}>{STATUS[item.status]}</span>}
      </header>
      <div className="watchboard-quote">
        <strong>
          {close !== null && Number.isFinite(close) ? valueFormat.format(close) : "—"}
          {close !== null && Number.isFinite(close) && <span> {item.unit === "points" ? "포인트" : item.currency}</span>}
        </strong>
        <div className="watchboard-change">
          <span className="muted small">전일 대비</span>
          <strong className={tone(item.change_pct)}>{shownPct(item.change_pct)}</strong>
        </div>
      </div>
      {item.reason && <p className="watchboard-reason">{REASON[item.reason]}</p>}
      <Sparkline item={item} />
      <dl className="watchboard-meta">
        <div>
          <dt>거래일</dt>
          <dd>{item.latest ? <time dateTime={item.latest.trade_date}>{item.latest.trade_date}</time> : "—"}</dd>
        </div>
        <div>
          <dt>현지 예정 마감</dt>
          <dd><Stamp value={item.latest?.closed_at ?? null} zone={item.timezone} /></dd>
        </div>
        <div>
          <dt>출처</dt>
          <dd>{source ?? "미확인"}</dd>
        </div>
        <div>
          <dt>현지 수신 시각</dt>
          <dd><Stamp value={item.fetched_at} zone={item.timezone} /></dd>
        </div>
        {item.source_delay_min !== null && <div><dt>원천 지연 기준</dt><dd>{item.source_delay_min}분</dd></div>}
      </dl>
      <footer className="watchboard-instrument-footer">
        {chart ? <a href={chart} aria-label={`${item.ticker} 일봉 차트`}>일봉 차트 →</a> : <span className="muted">차트 연결 없음</span>}
      </footer>
    </article>
  );
}

const DASHES = [undefined, "6 3", "2 3", "8 3 2 3", "11 4"] as const;

function Comparison({ data }: { data: WatchboardResponse }) {
  const { base_date: baseDate, status, excluded } = data.comparison;
  const items = new Map(data.items.map((item) => [item.ticker, item]));
  const series = data.comparison.series.map(({ ticker, points }, index) => ({
    ticker, index, points: watchboardCalendarPoints(points.map(({ trade_date, return_pct }) => ({
      date: trade_date, value: return_pct,
    }))),
  })).filter((curve) => curve.points.length > 0);
  const canCompare = status !== "unavailable" && baseDate !== null && series.length >= 2;
  const excludedNames = excluded.map((ticker) => items.get(ticker)?.name ?? ticker);

  return (
    <section className="card watchboard-comparison">
      <div className="watchboard-section-head">
        <h2>공통 기준일 성과 비교</h2>
        {canCompare && <p className="small">공통 기준일 <time dateTime={baseDate!}>{baseDate}</time> · 0%</p>}
      </div>
      {canCompare ? (
        <>
          <p className="watchboard-comparison-note muted">
            각 상품의 현지 통화 종가를 같은 거래일의 0%로 맞췄습니다. 시장마다 마감 시각이 다르고 관측이 없는 날짜는 비워 둡니다.
          </p>
          {status === "partial" && <p className="watchboard-partial">지연 또는 결측이 있어 일부 자료로 비교합니다.</p>}
          <ComparisonPlot series={series} />
          <ul className="watchboard-series">
            {series.map(({ ticker, points, index }) => {
              const last = points.at(-1)!;
              return (
                <li key={ticker} data-series={index % DASHES.length}>
                  <div className="watchboard-series-head">
                    <span className="watchboard-series-key" aria-hidden="true" />
                    <strong>{items.get(ticker)?.name ?? ticker}</strong>
                    <strong className={tone(last.value)}>{shownPct(last.value)}</strong>
                  </div>
                  <p className="muted small">
                    최근 관측 <time dateTime={last.date}>{last.date}</time> · {points.length}일
                  </p>
                </li>
              );
            })}
          </ul>
          <details className="watchboard-observations">
            <summary>계열별 관측값</summary>
            <div>
              {series.map(({ ticker, points }) => (
                <table key={ticker}>
                  <caption>{items.get(ticker)?.name ?? ticker}</caption>
                  <thead><tr><th scope="col">거래일</th><th scope="col">기준일 대비</th></tr></thead>
                  <tbody>{points.map((point) => (
                    <tr key={point.date}><td>{point.date}</td><td>{shownPct(point.value)}</td></tr>
                  ))}</tbody>
                </table>
              ))}
            </div>
          </details>
        </>
      ) : <p className="watchboard-comparison-note muted">같은 기준일에 관측된 상품이 부족해 비교할 수 없습니다.</p>}
      {excludedNames.length > 0 && <p className="watchboard-excluded muted small">비교 제외 · {excludedNames.join(", ")}</p>}
    </section>
  );
}

function ComparisonPlot({ series }: { series: { ticker: string; index: number; points: CalendarPoint[] }[] }) {
  const all = series.flatMap(({ points }) => points);
  const first = all.reduce((prior, point) => point.day < prior.day ? point : prior);
  const last = all.reduce((prior, point) => point.day > prior.day ? point : prior);
  const values = all.map(({ value }) => value);
  const rawTop = Math.max(0, ...values), rawBottom = Math.min(0, ...values);
  const range = rawTop - rawBottom || 1;
  const approx = range / 4;
  const magnitude = 10 ** Math.floor(Math.log10(approx));
  const tick = Math.max(0.01, ([1, 2, 2.5, 5, 10].find((factor) => factor * magnitude >= approx) ?? 10) * magnitude);
  const top = rawTop === rawBottom ? 2 * tick : Math.ceil(rawTop / tick) * tick;
  const bottom = rawTop === rawBottom ? -2 * tick : Math.floor(rawBottom / tick) * tick;
  const ticks = Array.from({ length: Math.round((top - bottom) / tick) + 1 }, (_, i) => top - i * tick);
  const y = (value: number) => 8 + 224 * (top - value) / (top - bottom);
  const project = ({ day, value }: CalendarPoint) =>
    `${first.day === last.day ? 400 : 8 + 784 * (day - first.day) / (last.day - first.day)},${y(value)}`;
  return (
    <div className="watchboard-plot" role="img" aria-label={`${first.date}~${last.date} ${series.length}개 상품의 기준일 대비 종가 수익률`}>
      <div className="watchboard-y-axis" aria-hidden="true">
        {ticks.map((value) => (
          <span key={value} style={{ top: `${y(value) / 240 * 100}%` }}>
            {valueFormat.format(Math.abs(value) < tick / 2 ? 0 : value)}%
          </span>
        ))}
      </div>
      <svg viewBox="0 0 800 240" preserveAspectRatio="none" aria-hidden="true">
        {ticks.map((value) => (
          <line key={value} x1="0" x2="800" y1={y(value)} y2={y(value)}
            className={Math.abs(value) < tick / 2 ? "watchboard-zero" : "watchboard-gridline"} vectorEffect="non-scaling-stroke" />
        ))}
        {series.map(({ ticker, points, index }) => (
          <g key={ticker} data-series={index % DASHES.length}>
            <Curve points={points} project={project} dash={DASHES[index % DASHES.length]} />
          </g>
        ))}
      </svg>
      <div className="watchboard-x-axis muted small" aria-hidden="true">
        <time dateTime={first.date}>{first.date}</time>
        {first.date !== last.date && <time dateTime={last.date}>{last.date}</time>}
      </div>
    </div>
  );
}

export default function MarketWatchboardPanel({ group, onGroupChange, data, loading, error, onRetry }: {
  group: WatchboardGroup;
  onGroupChange: (group: WatchboardGroup) => void;
  data: WatchboardResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  const current = data?.group === group ? data : null;
  return (
    <main className="page market-watchboard">
      <header className="card watchboard-top">
        <div className="watchboard-section-head">
          <h1>시장 모니터</h1>
          {current && <p className="muted small">관찰 시각 <Stamp value={current.as_of} zone="Asia/Seoul" /> · 최근 {current.days}일</p>}
        </div>
        <div className="chips watchboard-groups" role="group" aria-label="관심군">
          {GROUPS.map(({ id, label }) => (
            <button type="button" key={id} className={`chip ${group === id ? "chip-on" : ""}`}
              aria-pressed={group === id} onClick={() => onGroupChange(id)}>{label}</button>
          ))}
        </div>
      </header>
      {loading ? <p className="card muted" role="status">시장 자료를 불러오는 중입니다.</p>
        : error ? <div className="card watchboard-error"><p className="error" role="alert">{error}</p>
          <button type="button" className="secondary" onClick={onRetry}>다시 시도</button></div>
          : current && (
            <>
              <Comparison data={current} />
              {MARKETS.map(({ id, label }) => {
                const items = current.items.filter((item) => item.market === id);
                if (!items.length) return null;
                const market = items[0];
                return (
                  <section key={id} className="watchboard-market">
                    <div className="watchboard-section-head">
                      <h2>{label}</h2>
                      <p className="watchboard-session small">
                        <strong data-state={market.market_state}>{MARKET_STATE[market.market_state]}</strong>
                        <span className="muted">현지 <Stamp value={current.as_of} zone={market.timezone} /></span>
                      </p>
                    </div>
                    <div className="watchboard-instruments">
                      {items.map((item) => <ItemCard key={item.ticker} item={item} />)}
                    </div>
                  </section>
                );
              })}
            </>
          )}
    </main>
  );
}
