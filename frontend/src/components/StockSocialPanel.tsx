import { useEffect, useId, useState } from "react";
import type {
  LoadStockSocial, StockSocialItem, StockSocialResponse, StockSocialScore, StockSocialSource,
} from "../stockSocial";
import "./StockSocialPanel.css";

const STATUS = {
  available: "산정됨",
  partial: "일부 자료 사용",
  insufficient: "표본 부족",
  stale: "자료 지연",
  unavailable: "자료 없음",
} as const;

const REASONS: Readonly<Record<string, string>> = {
  snapshot_stale: "최신 기사 확인 시각이 오래됐습니다.",
  snapshot_unavailable: "확인 가능한 기사 스냅샷이 없습니다.",
  partial_snapshot: "확인된 최신 자료만 사용했습니다.",
  news_source_unavailable: "확인 가능한 뉴스 자료가 없습니다.",
  news_snapshot_stale: "기사 확인 시각이 오래돼 현재 점수를 산정할 수 없습니다.",
  too_few_unique_headlines: "중복을 제외한 기사나 매체가 산정 기준에 미달합니다.",
  sources_not_authorized: "사용할 수 있는 커뮤니티 출처가 연결되어 있지 않습니다.",
  usage_not_verified: "이용 범위 미확인",
  not_authorized: "이용 경로 미연결",
  not_configured: "출처 미연결",
  unsupported_symbol: "대상 시장 미지원",
  symbol_unmapped: "확인된 종목·발행사 연결 없음",
  access_denied: "출처 조회 거절",
  not_found: "출처에서 자료를 찾지 못함",
  rate_limited: "출처 조회 한도 도달",
  source_timeout: "출처 응답 지연",
  source_error: "출처 조회 오류",
};

const SOURCES: Readonly<Record<string, string>> = {
  gdelt: "GDELT",
  sec_edgar: "SEC EDGAR",
  google_news: "Google News",
  yahoo_news: "Yahoo News",
  open_dart: "Open DART",
  issuer_ir: "발행사 IR",
  reddit: "Reddit",
  stocktwits: "Stocktwits",
};

const TOPICS: Readonly<Record<string, string>> = {
  earnings: "실적",
  guidance: "가이던스",
  regulation: "규제",
  supply_demand: "수급",
  filing: "공시",
};

const KIND_LABEL = { positive: "긍정", neutral: "중립", negative: "부정" } as const;
const NUMBER = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 3 });
const TIMESTAMP = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", dateStyle: "medium", timeStyle: "short",
});

type PanelState =
  | { kind: "loading" }
  | { kind: "error" }
  | { kind: "ready"; data: StockSocialResponse };

function DateTime({ value }: { value: string | null }) {
  if (!value) return <>시각 미제공</>;
  const stamp = new Date(value);
  if (!Number.isFinite(stamp.getTime())) return <>시각 미제공</>;
  return <time dateTime={value}>{TIMESTAMP.format(stamp)} KST</time>;
}

function EvidenceTime({ item }: { item: StockSocialItem }) {
  if (item.time_basis === "gdelt_seen") {
    return <>발견 시각 <DateTime value={item.at} /></>;
  }
  if (item.time_basis === "edgar_acceptance") {
    return <>접수 시각 <DateTime value={item.at} /></>;
  }
  return <>공시일 {item.filed_date ? <time dateTime={item.filed_date}>{item.filed_date}</time> : "미제공"}</>;
}

function sourceLabel(id: string) {
  return SOURCES[id] ?? id;
}

function reasonText(reason: string | null) {
  return reason ? REASONS[reason] : null;
}

function usableScore(data: StockSocialScore) {
  return (data.status === "available" || data.status === "partial")
    && data.score != null && Number.isFinite(data.score) ? data.score : null;
}

function requestedTicker(ticker: string, returned: string): boolean {
  const requested = ticker.trim().toUpperCase();
  const actual = returned.toUpperCase();
  if (requested === actual) return true;
  // The API can add the market suffix to a bare KRX code.
  return /^[0-9][0-9A-Z]{5}$/.test(requested) && (actual === `${requested}.KS` || actual === `${requested}.KQ`);
}

function ScoreCard({ label, data, baseline, publishers }: {
  label: string;
  data: StockSocialScore;
  baseline: number;
  publishers?: number;
}) {
  const score = usableScore(data);
  const direction = score == null ? null : score === baseline ? "neutral" : score > baseline ? "positive" : "negative";
  const reason = reasonText(data.reason);
  return (
    <div className="stock-social__score">
      <h4>{label}</h4>
      <p className="stock-social__score-value">
        {score == null
          ? <strong>점수 미제공</strong>
          : <><strong data-direction={direction}>{NUMBER.format(score)}점</strong><span>{direction && KIND_LABEL[direction]}</span></>}
        <span className="stock-social__state">{STATUS[data.status]}</span>
      </p>
      <p className="stock-social__meta">표본 {data.sample_count}건{publishers != null && <> · 매체 {publishers}곳</>}</p>
      {data.updated_at && <p className="stock-social__meta">자료 기준 <DateTime value={data.updated_at} /></p>}
      {reason && <p className="stock-social__meta">{reason}</p>}
    </div>
  );
}

function externalLink(value: string): string | null {
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

function Evidence({ item }: { item: StockSocialItem }) {
  const filing = item.kind === "filing";
  const source = sourceLabel(item.source);
  const link = externalLink(item.url);
  const contribution = item.contribution;
  return (
    <li className="stock-social__item">
      <article>
        <p className="stock-social__meta">
          {filing ? "공시" : "뉴스"} · {item.publisher === source ? source : `${item.publisher} · ${source}`}
        </p>
        <h5>{item.title}</h5>
        <p className="stock-social__meta"><EvidenceTime item={item} /></p>
        <ul className="stock-social__labels" role="list" aria-label="근거 분류">
          <li>{item.relevance === "direct" ? "종목 직접" : "섹터·구성종목 간접"}</li>
          {item.sentiment && <li>{KIND_LABEL[item.sentiment]}</li>}
          {item.tags.filter((tag) => tag !== "filing").map((tag) => <li key={tag}>{TOPICS[tag] ?? tag}</li>)}
          {item.stale && <li>지연 자료</li>}
        </ul>
        <p className="stock-social__summary">{item.summary}</p>
        <div className="stock-social__item-footer">
          <strong>
            {contribution == null
              ? filing ? "공시 · 점수 미반영" : "점수 미반영"
              : `점수 반영 ${contribution > 0 ? "+" : ""}${NUMBER.format(contribution)}점`}
          </strong>
          {item.weight != null && <span className="stock-social__meta">가중치 {NUMBER.format(item.weight)}</span>}
          {link && <a href={link} target="_blank" rel="noopener noreferrer" aria-label={`원문 열기 · ${item.title} (새 탭)`}>원문 열기</a>}
        </div>
      </article>
    </li>
  );
}

function SourceStatus({ source }: { source: StockSocialSource }) {
  return (
    <li className="stock-social__source">
      <strong>{sourceLabel(source.id)}</strong><span> · {source.status === "available" ? "사용 가능" : STATUS[source.status]}</span>
      {reasonText(source.reason) && <span> · {reasonText(source.reason)}</span>}
      {source.updated_at && <span> · {source.id === "sec_edgar" ? "조회 기준" : "자료 기준"} <DateTime value={source.updated_at} /></span>}
      {source.prior_http_status != null && <span> · 과거 응답 HTTP {source.prior_http_status}</span>}
    </li>
  );
}

function SocialDetails({ data }: { data: StockSocialResponse }) {
  const { baseline, min_samples: samples, min_publishers: publishers, version } = data.method;
  return (
    <>
      <details>
        <summary>뉴스·공시 근거 <span className="stock-social__meta">{data.items.length}건</span></summary>
        {data.items.length ? (
          <ul className="stock-social__items" role="list">
            {data.items.map((item) => <Evidence key={`${item.kind}:${item.url}:${item.at ?? item.filed_date ?? item.title}`} item={item} />)}
          </ul>
        ) : <p className="stock-social__meta">이 기간에 표시할 뉴스·공시 근거가 없습니다.</p>}
      </details>
      <details>
        <summary>점수 산식과 출처</summary>
        <div className="stock-social__method">
          <p>제목 표현을 긍정(+1), 부정(−1), 중립(0)으로 분류합니다. 관련도·최근성·주제로 가중치를 정하고 가중평균을 0~100에 맞춥니다.</p>
          {version === "headline-rules-v1" && <>
            <p>산식 {baseline} + {baseline} × 가중평균. 직접 기사는 1, 간접 기사는 0.5에서 시작합니다. 최근성 가중치는 72시간에 걸쳐 1에서 0.25로 줄고, 주제 태그가 있으면 1.25를 곱합니다.</p>
            <p>중복 기사는 한 번만 셉니다. 매체당 최신 기사 최대 2건과 총 가중치 상한 60%를 적용하며, 공시와 자료 기준 시각이 6시간 넘은 기사는 근거 목록에 표시합니다.</p>
          </>}
          <p>최소 기사 {samples}건 · 서로 다른 매체 {publishers}곳 · 산정 버전 <code>{version}</code></p>
          <p>응답 산정 시각 <DateTime value={data.generated_at} /></p>
          <ul className="stock-social__sources" role="list" aria-label="출처 상태">
            {data.sources.map((source) => <SourceStatus key={source.id} source={source} />)}
          </ul>
        </div>
      </details>
    </>
  );
}

function StockSocialForTicker({ ticker, load }: { ticker: string; load: LoadStockSocial }) {
  const headingId = useId();
  const [state, setState] = useState<PanelState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();

    async function request() {
      try {
        const data = await load(ticker, controller.signal);
        if (controller.signal.aborted) return;
        setState(requestedTicker(ticker, data.ticker) ? { kind: "ready", data } : { kind: "error" });
      } catch {
        if (!controller.signal.aborted) setState({ kind: "error" });
      }
    }

    void request();
    return () => controller.abort();
  }, [ticker, load, attempt]);

  const data = state.kind === "ready" ? state.data : null;
  return (
    <section className="stock-social" aria-labelledby={headingId}>
      <h3 id={headingId}>심리 지수</h3>
      {state.kind === "loading" && <p className="stock-social__meta" role="status">심리 지수 불러오는 중…</p>}
      {state.kind === "error" && (
        <div className="stock-social__error">
          <p role="status">심리 지수를 불러오지 못했습니다.</p>
          <button type="button" className="link" onClick={() => {
            setState({ kind: "loading" });
            setAttempt((previous) => previous + 1);
          }}>다시 시도</button>
        </div>
      )}
      {data && (
        <>
          <p className="stock-social__meta stock-social__coverage">
            요청 {data.coverage.requested_days}일 · 뉴스 최대 {data.coverage.news_max_days * 24}시간 · {data.method.baseline}점 기준
          </p>
          <div className="stock-social__scores">
            <ScoreCard label="뉴스" data={data.news} baseline={data.method.baseline} publishers={data.news.publisher_count} />
            <ScoreCard label="커뮤니티" data={data.community} baseline={data.method.baseline} />
          </div>
          {usableScore(data.news) != null && data.community.sample_count === 0 && (
            <p className="stock-social__meta">커뮤니티 표본이 없어 뉴스 기반 점수입니다.</p>
          )}
          <SocialDetails data={data} />
        </>
      )}
    </section>
  );
}

export default function StockSocialPanel(props: { ticker: string; load: LoadStockSocial }) {
  return <StockSocialForTicker key={props.ticker} {...props} />;
}
