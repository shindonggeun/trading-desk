import { useId, useState } from "react";
import { money, tone } from "../format";
import {
  KR_FLOW_METRICS, KR_FLOW_STATE, krFlowHistorySegments, krFlowMetricStatus, krFlowMetricValue, krFlowPercent, krFlowScatter,
  type KrFlowBoardLinks, type KrFlowBoardResponse, type KrFlowDay, type KrFlowFavorite, type KrFlowMarket,
  type KrFlowMetric, type KrFlowRankedItem, type KrFlowStatus,
} from "../krFlowBoard";
import { KrFlowHistoryPlot, KrFlowScatterPlot } from "./KrFlowBoardCharts";
import "./KrFlowBoardPanel.css";

const MARKET_NAMES = { kospi: "코스피", kosdaq: "코스닥" } as const;
const METRICS = ["participation", "imbalance", "ownership"] as const;
const SOURCE_NAMES: Record<string, string> = {
  krx_open_api: "KRX OPEN API", kis: "한국투자증권", toss: "토스증권",
};
const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const sourceName = (source: string | null | undefined) => source ? SOURCE_NAMES[source] ?? source : "원천 미확인";

function Basis({ date, source, otherSource, publishedAt }: {
  date: string | null | undefined;
  source: string | null | undefined;
  otherSource?: string | null;
  publishedAt?: string | null;
}) {
  const stamp = publishedAt ? new Date(publishedAt) : null;
  return (
    <p className="kr-flow-basis small">
      {date ? <>기준일 <time dateTime={date}>{date}</time></> : "기준일 없음"}
      {" · "}{sourceName(source)}
      {otherSource && otherSource !== source && ` / ${sourceName(otherSource)}`}
      {stamp && !Number.isNaN(stamp.getTime()) && <>{" · 공표 "}<time dateTime={publishedAt!}>{KST_TIME.format(stamp)} KST</time></>}
    </p>
  );
}

function Quote({ value, signed = false }: { value: number | null | undefined; signed?: boolean }) {
  return <>{value === null || value === undefined || !Number.isFinite(value) ? "자료 없음" : money(value, "KRW", signed)}</>;
}

function TickerLinks({ ticker, name, links }: { ticker: string; name: string; links: KrFlowBoardLinks }) {
  const { chart, analysis, shortSale } = links(ticker);
  return (
    <div className="kr-flow-links" aria-label={`${name} 관련 화면`}>
      <a href={chart} aria-label={`${name} 일봉 차트`}>일봉 차트</a>
      <a href={analysis} aria-label={`${name} 분석`}>분석</a>
      <a href={shortSale} aria-label={`${name} 공매도`}>공매도</a>
    </div>
  );
}

function MarketSummary({ data }: { data: KrFlowBoardResponse }) {
  const { index, flow, status } = data.market_summary;
  const foreign = flow?.investors.foreigner;
  const change = index?.change_pct ?? null;
  return (
    <section className="kr-flow-section card">
      <div className="kr-flow-heading"><h2>{MARKET_NAMES[data.market]} 시장 전체</h2>{status !== "available" && <span className="kr-flow-state small">{KR_FLOW_STATE[status]}</span>}</div>
      <div className="kr-flow-summary-grid">
        <article className="kr-flow-metric">
          <h3>지수 종가</h3>
          <strong className="kr-flow-value">{index ? `${index.value.toLocaleString("ko-KR", { maximumFractionDigits: 2 })} 포인트` : "자료 없음"}</strong>
          <p className="small">등락률 <b className={tone(change)}>{krFlowPercent(change, true)}</b></p>
          <Basis date={index?.as_of} source={index?.source} publishedAt={index?.published_at} />
        </article>
        <article className="kr-flow-metric">
          <h3>시장 외국인 순매수</h3>
          <strong className={`kr-flow-value ${tone(foreign?.net)}`}><Quote value={foreign?.net} signed /></strong>
          <p className="small">매수 <Quote value={foreign?.buy} /> · 매도 <Quote value={foreign?.sell} /></p>
          <Basis date={flow?.as_of} source={flow?.source} publishedAt={flow?.published_at} />
        </article>
      </div>
    </section>
  );
}

function MetricNumber({ value, status, signed }: { value: number | null; status: KrFlowStatus; signed?: boolean }) {
  return <span className={signed ? tone(value) : undefined}>{value === null
    ? KR_FLOW_STATE[status === "available" ? "no_data" : status]
    : krFlowPercent(value, signed)}</span>;
}

function RankDetail({ item, links }: { item: KrFlowRankedItem; links: KrFlowBoardLinks }) {
  const { latest, latest_ownership: ownership } = item;
  const name = item.name ?? item.ticker;
  return (
    <article className="kr-flow-selection" aria-live="polite">
      <h3>{item.rank}위 · {name} <span className="muted small">{item.ticker}</span></h3>
      <dl className="kr-flow-selection-data">
        <div><dt>외국인 참여율</dt><dd><MetricNumber value={krFlowMetricValue(latest, "participation")} status={latest.participation_status} /></dd></div>
        <div><dt>매수·매도 우위</dt><dd><MetricNumber value={krFlowMetricValue(latest, "imbalance")} status={latest.imbalance_status} signed /></dd></div>
        <div><dt>거래대금</dt><dd><Quote value={latest.turnover.value_krw} /></dd></div>
        <div><dt>외국인 순매수</dt><dd><Quote value={latest.foreign_flow.net_krw} signed /></dd></div>
      </dl>
      <Basis date={latest.date} source={latest.turnover.source ?? latest.foreign_flow.source} otherSource={latest.foreign_flow.source} />
      <p className="kr-flow-ownership-inline">외국인 지분율 <b><MetricNumber value={ownership.ratio_pct} status={ownership.status} /></b></p>
      <Basis date={ownership.as_of} source={ownership.source} publishedAt={ownership.published_at} />
      <TickerLinks ticker={item.ticker} name={name} links={links} />
    </article>
  );
}

function Ranking({ ranking, links }: { ranking: KrFlowBoardResponse["ranking"]; links: KrFlowBoardLinks }) {
  const picker = useId();
  const [selection, setSelection] = useState<string | null>(null);
  const scatter = krFlowScatter(ranking.items);
  const plottable = new Set(scatter.points.map(({ item }) => item.ticker));
  const selected = ranking.items.find(({ ticker }) => ticker === selection)
    ?? scatter.points[0]?.item ?? ranking.items[0];
  return (
    <section className="kr-flow-section card">
      <div className="kr-flow-heading"><h2>거래대금 Top 50</h2>{ranking.status !== "available" && <span className="kr-flow-state small">{KR_FLOW_STATE[ranking.status]}</span>}</div>
      <Basis date={ranking.as_of} source={ranking.source} publishedAt={ranking.published_at} />
      {!selected ? <p className="kr-flow-empty">거래대금 순위를 표시할 수 없습니다.</p> : (
        <div className="kr-flow-ranking-grid">
          <div>
            <KrFlowScatterPlot scatter={scatter} selectedTicker={selected.ticker} select={setSelection} />
            <p className="muted small kr-flow-plot-count">
              순위 {ranking.items.length}종목 · 산점도 {scatter.points.length}종목
              {scatter.points.length < ranking.items.length && " · 계산에 필요한 관측이 빠진 종목은 순위 목록에서 확인할 수 있습니다."}
            </p>
          </div>
          <div className="kr-flow-rank-side">
            <label className="small" htmlFor={picker}>산점도 종목 선택</label>
            <select id={picker} value={selected.ticker} onChange={(event) => setSelection(event.target.value)}>
              {ranking.items.map((item) => <option key={item.ticker} value={item.ticker}>
                {item.rank}위 · {item.name ?? item.ticker} ({item.ticker}){!plottable.has(item.ticker) ? " · 산점도 자료 없음" : ""}
              </option>)}
            </select>
            <RankDetail item={selected} links={links} />
          </div>
        </div>
      )}
    </section>
  );
}

function HistoryTable({ history }: { history: KrFlowDay[] }) {
  return (
    <div className="kr-flow-history-table">
      <table>
        <thead><tr><th scope="col">거래일</th><th scope="col">참여율</th><th scope="col">매수·매도 우위</th><th scope="col">지분율</th></tr></thead>
        <tbody>{[...history].reverse().map((sample) => <tr key={sample.date}>
          <th scope="row"><time dateTime={sample.date}>{sample.date}</time></th>
          {METRICS.map((metric) => <td key={metric}>
            <MetricNumber value={krFlowMetricValue(sample, metric)} status={krFlowMetricStatus(sample, metric)} signed={metric === "imbalance"} />
          </td>)}
        </tr>)}</tbody>
      </table>
    </div>
  );
}

function FavoriteMetric({ item, sessions, metric }: {
  item: KrFlowFavorite;
  sessions: string[];
  metric: KrFlowMetric;
}) {
  const segments = krFlowHistorySegments(sessions, item.history, metric);
  const last = segments.at(-1)?.at(-1);
  const latest = item.history.at(-1);
  const ownership = metric === "ownership" ? item.latest_ownership : null;
  const value = ownership ? ownership.ratio_pct : last?.value ?? null;
  const date = ownership ? ownership.as_of : last?.sample.date;
  const source = ownership ? ownership.source : last?.sample.foreign_flow.source;
  const turnoverSource = metric === "participation" ? last?.sample.turnover.source : null;
  const status = ownership?.status ?? (latest ? krFlowMetricStatus(latest, metric) : "no_data");
  const missingLatest = latest && krFlowMetricValue(latest, metric) === null && value !== null;
  return (
    <div className="kr-flow-metric kr-flow-favorite-metric">
      <h4>{KR_FLOW_METRICS[metric]}</h4>
      <strong className="kr-flow-value"><MetricNumber value={value} status={status} signed={metric === "imbalance"} /></strong>
      <Basis date={date} source={source ?? turnoverSource} otherSource={turnoverSource}
        publishedAt={ownership ? ownership.published_at : metric === "imbalance" ? last?.sample.foreign_flow.published_at : null} />
      <KrFlowHistoryPlot segments={segments} sessionCount={sessions.length} metric={metric} ticker={item.ticker} />
      {missingLatest && <p className="kr-flow-latest-gap small">최근 거래일 {latest.date} · {KR_FLOW_STATE[krFlowMetricStatus(latest, metric)]}</p>}
    </div>
  );
}

function Favorites({ data, links }: { data: KrFlowBoardResponse; links: KrFlowBoardLinks }) {
  const { favorites, sessions } = data;
  return (
    <section className="kr-flow-section card">
      <div className="kr-flow-heading"><h2>관심종목 22거래일</h2>{favorites.status !== "available" && <span className="kr-flow-state small">{KR_FLOW_STATE[favorites.status]}</span>}</div>
      {sessions.length > 0 && <p className="muted small kr-flow-favorite-range">{sessions[0]} ~ {sessions.at(-1)} · {sessions.length}거래일 · 결측일에서 선을 끊어 표시합니다.</p>}
      {!favorites.items.length ? <p className="kr-flow-empty">이 시장에 저장된 관심종목이 없습니다.</p> : (
        <div className="kr-flow-favorites-grid">
          {favorites.items.map((item) => {
            const name = item.name ?? item.ticker;
            return (
              <article className="kr-flow-favorite" key={item.ticker}>
                <div className="kr-flow-heading">
                  <h3>{name} <span className="muted small">{item.ticker}</span></h3>
                  <TickerLinks ticker={item.ticker} name={name} links={links} />
                </div>
                <div className="kr-flow-favorite-metrics">
                  {METRICS.map((metric) => <FavoriteMetric key={metric} item={item} sessions={sessions} metric={metric} />)}
                </div>
                <details className="kr-flow-history">
                  <summary>{name} 거래일별 수치</summary>
                  <HistoryTable history={item.history} />
                </details>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function KrFlowBoardPanel({ market, onMarketChange, data, loading, error, onRetry, links }: {
  market: KrFlowMarket;
  onMarketChange: (market: KrFlowMarket) => void;
  data: KrFlowBoardResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  links: KrFlowBoardLinks;
}) {
  const board = data?.market === market ? data : null;
  return (
    <main className="page kr-flow-board">
      <header className="kr-flow-board-header">
        <div>
          <h1>국장 수급 현황판</h1>
          <p className="muted">KRX 확정 거래일의 외국인 매매와 보유 지분을 확인합니다.</p>
        </div>
        <div className="chips kr-flow-tabs" role="group" aria-label="시장 선택">
          {(Object.keys(MARKET_NAMES) as KrFlowMarket[]).map((key) => <button key={key} type="button"
            aria-pressed={key === market} className={`chip${key === market ? " chip-on" : ""}`} onClick={() => onMarketChange(key)}>
            {MARKET_NAMES[key]}
          </button>)}
        </div>
      </header>
      {loading ? <p role="status" className="card muted">수급 현황판을 불러오는 중…</p> : error ? (
        <div className="card" role="alert"><p>{error}</p><button type="button" onClick={onRetry}>다시 시도</button></div>
      ) : board ? (
        <>
          <div className="kr-flow-overview">
            <p>최근 확정 거래일 <strong><time dateTime={board.latest_session}>{board.latest_session}</time></strong></p>
            <p className="muted small">
              거래대금 {KR_FLOW_STATE[board.sources.turnover.status]} · 외국인 매매 {KR_FLOW_STATE[board.sources.foreign_flow.status]} · 지분율 {KR_FLOW_STATE[board.sources.ownership.status]}
            </p>
          </div>
          <MarketSummary data={board} />
          <Ranking key={market} ranking={board.ranking} links={links} />
          <Favorites data={board} links={links} />
        </>
      ) : null}
    </main>
  );
}
