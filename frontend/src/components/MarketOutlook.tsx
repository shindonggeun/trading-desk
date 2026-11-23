import { useId } from "react";
import { usdKo } from "../format";
import {
  evidenceLabel, OUTLOOK_STATUS, outlookReason, outlookStance, outlookTime, REGIME_LABEL, sourceLink,
} from "../marketOutlook";
import type {
  MarketOutlookEvidence, MarketOutlookFiling, MarketOutlookRegime,
  MarketOutlookResponse, MarketOutlookSource, MarketOutlookView,
} from "../marketOutlook";
import type { FundChange, FundHolding } from "../types";
import "./MarketOutlook.css";

interface Props {
  data: MarketOutlookResponse | null;
  failedAt?: string | null;
}

function Day({ value }: { value: string | null }) {
  return value ? <time dateTime={value}>{value}</time> : <>미확인</>;
}

function Timestamp({ value }: { value: string }) {
  const formatted = outlookTime(value);
  return formatted ? <time dateTime={value}>{formatted}</time> : <>미확인</>;
}

function SourceLink({ url, label, optional = false }: { url: string | null; label: string; optional?: boolean }) {
  const href = sourceLink(url);
  if (!href && optional) return null;
  return href
    ? <a className="link" href={href} target="_blank" rel="noopener noreferrer">{label}</a>
    : <span>{label}</span>;
}

function SourceState({ info, empty }: { info: MarketOutlookSource; empty: boolean }) {
  if (info.status === "available" && !empty) return null;
  const reason = outlookReason(info.reason);
  const status = info.status === "available" ? "missing" : info.status;
  return (
    <p className={`outlook-status outlook-status-${status} small`}>
      {OUTLOOK_STATUS[status]}{reason && ` · ${reason}`}
    </p>
  );
}

function ViewCard({ view }: { view: MarketOutlookView }) {
  const label = useId();
  const stance = outlookStance(view.stance);
  return (
    <article className="outlook-tile" aria-labelledby={label}>
      <div className="outlook-row">
        <h4 id={label}>{view.expert}</h4>
        <strong className={stance.tone === "warn" ? "outlook-warning" : stance.tone}>{stance.label}</strong>
      </div>
      <p className="outlook-byline muted small">{view.organization} · {view.horizon}</p>
      {view.status !== "available" && (
        <p className="outlook-warning small">
          {OUTLOOK_STATUS[view.status]}{view.age_days != null && ` · 날짜 기준 ${view.age_days}일 경과`}
        </p>
      )}
      <p className="outlook-view-summary">{view.summary}</p>
      <dl className="outlook-facts small">
        <div><dt>근거</dt><dd>{view.basis}</dd></div>
        <div><dt>이전 관점과 비교</dt><dd>{view.change ?? "확인된 비교 자료 없음"}</dd></div>
      </dl>
      <div className="outlook-provenance small">
        <p><SourceLink url={view.source_url} label={view.source} /></p>
        <dl className="outlook-facts outlook-dates muted">
          <div><dt>발언일</dt><dd><Day value={view.spoken_on} /></dd></div>
          <div><dt>공개일</dt><dd><Day value={view.published_on} /></dd></div>
        </dl>
        <SourceLink url={view.date_source_url} label="날짜 확인 출처" optional />
      </div>
      {view.positions.length > 0 && (
        <div className="outlook-provenance small">
          <p className="muted">공개 발언의 포지션 언급</p>
          <ul className="outlook-list">
            {view.positions.map((position) => (
              <li key={`${position.asset}-${position.side}-${position.as_of}`}>
                {position.asset} {position.side === "short" ? "숏" : position.side === "long" ? "롱" : "방향 미확인"}
                {position.status === "unverified" && " 언급 · 보유 미확인"}
                {position.as_of ? <> · 기준일 <Day value={position.as_of} /></> : " · 시점 미확인"}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

const HOLDING_CHANGE: Record<FundChange, string> = {
  new: "신규 보고", increase: "증가", decrease: "감소", sold: "이번 분기 보고 없음", unchanged: "유지",
};

function Holding({ holding }: { holding: FundHolding }) {
  return (
    <li>
      <p>
        <strong>{holding.ticker || holding.name || holding.cusip}</strong>
        {holding.put_call && ` · ${holding.put_call === "PUT" ? "풋" : "콜"} 옵션`}
      </p>
      {holding.ticker && holding.name && <p className="muted">{holding.name}</p>}
      <p className="muted">
        {holding.change !== "sold" && (
          <>{holding.put_call ? "명목가" : "공시 평가액"} {usdKo(holding.value)}{holding.change && " · "}</>
        )}
        {holding.change && HOLDING_CHANGE[holding.change]}
      </p>
    </li>
  );
}

function quarterName(quarter: string): string {
  return quarter.replace(/^(\d{4})Q([1-4])$/, "$1년 $2분기");
}

function Filing({ filing }: { filing: MarketOutlookFiling }) {
  const label = useId();
  return (
    <article aria-labelledby={label}>
      <h4 id={label}>{filing.name}</h4>
      <p className="outlook-byline muted small">{quarterName(filing.quarter)} · {filing.form}</p>
      <dl className="outlook-facts outlook-dates small">
        <div><dt>보유 기준일</dt><dd><Day value={filing.period_of_report} /></dd></div>
        <div><dt>제출일</dt><dd><Day value={filing.filed_at} /></dd></div>
      </dl>
      <p className="muted small">기준일에서 제출까지 {filing.publication_lag_days}일</p>
      <div className="outlook-links small">
        <SourceLink url={filing.source_url} label="SEC EDGAR 공시 원문" />
        <a className="link" href={`#/funds?fund=${encodeURIComponent(filing.fund_id)}&q=${encodeURIComponent(filing.quarter)}`}>
          기관 공시 상세
        </a>
      </div>
      {filing.holdings_status === "missing" ? (
        <p className="outlook-status small">이 접수 건의 보유 내역 자료가 없습니다.</p>
      ) : filing.holdings.length > 0 ? (
        <details className="outlook-holdings small">
          <summary>{filing.previous_quarter ? "공시 내역 · 직전 분기 비교" : "공시 보유 내역"}</summary>
          <p className="muted">
            {filing.previous_quarter ? `${quarterName(filing.previous_quarter)}와 비교` : "직전 분기 비교 자료 없음"}
          </p>
          <ul>
            {filing.holdings.map((holding) => (
              <Holding key={`${holding.cusip}-${holding.put_call}`} holding={holding} />
            ))}
          </ul>
        </details>
      ) : <p className="muted small">이 접수 건에 표시할 보유 내역이 없습니다.</p>}
    </article>
  );
}

const DIRECTION_LABEL = { positive: "개선", negative: "악화", neutral: "중립" };
const TREND_LABEL = { positive: "상승 구조", negative: "하락 구조", neutral: "혼조 구조" };
const NUMBER = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });
const SIGNED = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2, signDisplay: "exceptZero" });
const UNITS: Partial<Record<string, string>> = { "%": "%", index_points: "pt", bps: "bp" };

function Evidence({ item }: { item: MarketOutlookEvidence }) {
  const { close, sma20, sma50, peak, change5 } = item.details;
  const reason = outlookReason(item.reason);
  const trend = item.id === "index_trend";
  const unit = UNITS[item.unit] ?? item.unit;
  const valueFormat = item.id.startsWith("index_") ? SIGNED : NUMBER;
  return (
    <li className="outlook-evidence-item">
      <h5>{evidenceLabel(item.id)}</h5>
      {item.value != null && (
        <p className="outlook-value">
          {trend && <span className="muted small">50일선 대비 </span>}
          <strong>{valueFormat.format(item.value)}{unit}</strong>
          {item.status === "available" && item.direction && (
            <span className="muted small"> · {(trend ? TREND_LABEL : DIRECTION_LABEL)[item.direction]}</span>
          )}
        </p>
      )}
      {item.status !== "available" && (
        <p className={`outlook-status outlook-status-${item.status} small`}>
          {OUTLOOK_STATUS[item.status]}{reason && ` · ${reason}`}
        </p>
      )}
      {close != null && sma20 != null && sma50 != null && (
        <p className="muted small">종가 {NUMBER.format(close)} · 20일선 {NUMBER.format(sma20)} · 50일선 {NUMBER.format(sma50)}</p>
      )}
      {peak != null && <p className="muted small">기간 내 종가 고점 {NUMBER.format(peak)}</p>}
      {change5 != null && <p className="muted small">5거래일 변화 {SIGNED.format(change5)}pt</p>}
      {(item.source || item.observed_at || item.fetched_at) && (
        <dl className="outlook-facts outlook-measure-times muted small">
          {item.source && <div><dt>원천</dt><dd>{item.source}</dd></div>}
          {item.observed_at && <div><dt>관측</dt><dd><Timestamp value={item.observed_at} /></dd></div>}
          {item.fetched_at && <div><dt>조회</dt><dd><Timestamp value={item.fetched_at} /></dd></div>}
        </dl>
      )}
    </li>
  );
}

function EvidenceList({ ids }: { ids: string[] }) {
  return ids.length > 0
    ? <ul className="outlook-list">{ids.map((id) => <li key={id}>{evidenceLabel(id)}</li>)}</ul>
    : <p className="muted">확인된 근거 없음</p>;
}

const REBOUND_DIRECTION = { positive: "반등 신호 관측", negative: "하락 신호 관측", neutral: "반등 기준 미달" };

function RegimeSignals({ regime }: { regime: MarketOutlookRegime }) {
  const shortLabel = useId();
  const longLabel = useId();
  const rebound = regime.evidence.find((item) => item.id === "index_rebound");
  const trend = regime.evidence.find((item) => item.id === "index_trend");
  let shortStatus = "판정 유보";
  if (rebound?.status === "available" && rebound.direction) {
    shortStatus = regime.state === "rebound_candidate" ? "반등 후보" : REBOUND_DIRECTION[rebound.direction];
  }
  let longStatus = "판정 유보";
  if (trend?.status === "available") {
    longStatus = regime.state === "recovery_watch" ? "추세 회복 관찰" : "회복 미확인";
  }
  return (
    <div className="outlook-signals">
      <section aria-labelledby={shortLabel}>
        <h4 id={shortLabel}>단기 반등</h4>
        <p><strong>{shortStatus}</strong></p>
        <p className="muted small">5거래일 종가 수익률과 보조 지표</p>
      </section>
      <section aria-labelledby={longLabel}>
        <h4 id={longLabel}>장기 추세 확인</h4>
        <p><strong>{longStatus}</strong></p>
        <p className="muted small">20·50일선 구조와 시장 폭·신용</p>
      </section>
    </div>
  );
}

function Regime({ regime }: { regime: MarketOutlookRegime }) {
  const label = useId();
  const undetermined = regime.state === "undetermined";
  return (
    <section className="outlook-tile outlook-regime" aria-labelledby={label}>
      <div className="outlook-row">
        <h3 id={label}>지표로 본 장세</h3>
        <strong className={regime.state === "downtrend" ? "down" : ""}>{REGIME_LABEL[regime.state]}</strong>
      </div>
      <RegimeSignals regime={regime} />
      <div className="outlook-case-grid small">
        <div><h4>{undetermined ? "개선 근거" : "판정 근거"}</h4><EvidenceList ids={regime.supporting} /></div>
        <div><h4>{undetermined ? "악화 근거" : "반대 근거"}</h4><EvidenceList ids={regime.opposing} /></div>
        <div>
          <h4>다음 확인 조건</h4>
          {regime.next_checks.length > 0
            ? <ul className="outlook-list">{regime.next_checks.map((check) => <li key={check}>{check}</li>)}</ul>
            : <p className="muted">확인 조건 없음</p>}
        </div>
      </div>
      {regime.evidence.length > 0 ? (
        <ul className="outlook-evidence-grid">
          {regime.evidence.map((item) => <Evidence key={item.id} item={item} />)}
        </ul>
      ) : <p className="muted small">관측된 지표 자료가 없습니다.</p>}
    </section>
  );
}

function Filings({ filings, info }: { filings: MarketOutlookFiling[]; info: MarketOutlookSource }) {
  const label = useId();
  return (
    <section className="outlook-tile outlook-filings" aria-labelledby={label}>
      <h3 id={label}>기관 보유 공시 · 13F</h3>
      {info.checked_at && (
        <p className="muted small">{info.source ?? "SEC EDGAR"} 확인 <Timestamp value={info.checked_at} /></p>
      )}
      <SourceState info={info} empty={filings.length === 0} />
      {filings.map((filing) => <Filing key={filing.accession} filing={filing} />)}
      <p className="muted small">
        13F는 분기말 보고 대상 증권과 일부 옵션의 기록입니다. 전체 포트폴리오, 발언에 나온 포지션,
        미보고 숏과 분기말 이후의 거래는 확인할 수 없습니다.
      </p>
    </section>
  );
}

export default function MarketOutlook({ data, failedAt }: Props) {
  const title = useId();
  const viewsLabel = useId();
  return (
    <section className="card market-outlook" aria-labelledby={title}>
      <header className="outlook-row">
        <h2 id={title}>미국 시장 전망</h2>
        {data && <p className="muted small">평가 시각 <Timestamp value={data.as_of} /></p>}
      </header>
      {failedAt && (
        <p role="status" className="outlook-warning small">
          {data ? "다시 읽기 실패 · 마지막 평가 결과를 표시합니다." : "시장 전망을 불러오지 못했습니다."}{" "}
          <Timestamp value={failedAt} />
        </p>
      )}
      {data ? (
        <>
          <section className="outlook-section" aria-labelledby={viewsLabel}>
            <h3 id={viewsLabel}>공개 시장 관점</h3>
            <SourceState info={data.sources.views} empty={data.views.length === 0} />
            {data.views.length > 0 && (
              <div className="outlook-view-grid">
                {data.views.map((view) => <ViewCard key={view.id} view={view} />)}
              </div>
            )}
          </section>
          <div className="outlook-lower-grid">
            <Regime regime={data.regime} />
            <Filings filings={data.filings} info={data.sources.filings} />
          </div>
        </>
      ) : !failedAt && <p role="status" className="muted small">시장 전망을 불러오는 중…</p>}
    </section>
  );
}
