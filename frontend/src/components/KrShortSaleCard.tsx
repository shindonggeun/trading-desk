import { useId, useState, type FormEvent } from "react";
import { api } from "../api";
import { usePoll } from "../poll";
import type { KrShortPoint, KrShortSeries, KrShortStatus } from "../types";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";
import "./KrShortSaleCard.css";

const QUICK_PICKS = [
  { ticker: "005930.KS", name: "삼성전자" },
  { ticker: "000660.KS", name: "SK하이닉스" },
  { ticker: "247540.KQ", name: "에코프로비엠" },
];
const KR_TICKER = /^\d{6}\.(KS|KQ)$/;
const REFRESH_MS = 60_000;
const STATUS_LABEL: Record<KrShortStatus, string> = {
  available: "공표",
  not_published: "미공표",
  no_data: "자료 없음",
  zero_denominator: "분모 0",
  source_error: "원천 조회 오류",
  source_unavailable: "원천 미구성",
  unsupported: "미지원 종목",
};
const RATIO_FORMAT = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 2 });

function formatRatio(ratio: number | null) {
  if (ratio === null) return "—";
  if (ratio > 0 && ratio < 0.01) return "0.01% 미만";
  return `${RATIO_FORMAT.format(ratio)}%`;
}

function RatioMetric({ label, dateLabel, series }: { label: string; dateLabel: string; series: KrShortSeries }) {
  return (
    <div className="kr-short-metric">
      <dt>{label}</dt>
      <dd>
        <strong className="kr-short-ratio">{formatRatio(series.latest?.ratio_pct ?? null)}</strong>
        <span className="muted small">{dateLabel} {series.latest?.as_of ?? "—"}</span>
        {series.status !== "available" && (
          <span className={`pill ${series.status === "source_error" ? "pill-bad" : series.stale ? "pill-warn" : ""}`}>
            {series.stale && "지연 · "}{STATUS_LABEL[series.status]}
          </span>
        )}
      </dd>
    </div>
  );
}

function TradeHistory({ points }: { points: KrShortPoint[] }) {
  if (!points.length) return null;
  const max = Math.max(0, ...points.map((point) => point.ratio_pct ?? 0));
  return (
    <div className="kr-short-history">
      <div className="kr-short-history-head">
        <h5>최근 20거래일 거래 비중</h5>
        <span className="muted small">{points[0].as_of} ~ {points[points.length - 1].as_of}</span>
      </div>
      <div className="kr-short-plot" aria-hidden="true">
        {points.map((point) => {
          const ratio = point.ratio_pct;
          const kind = ratio === null ? "missing" : ratio === 0 ? "zero" : "value";
          return (
            <div key={point.as_of} title={`${point.as_of} ${ratio === null ? STATUS_LABEL[point.status] : formatRatio(ratio)}`}>
              <span className={`kr-short-${kind}`} style={max > 0 && ratio !== null && ratio > 0 ? { height: `${ratio / max * 100}%` } : undefined} />
            </div>
          );
        })}
      </div>
      <details>
        <summary>거래일별 비중과 공표 상태</summary>
        <table>
          <thead><tr><th scope="col">거래일</th><th scope="col">비중</th><th scope="col">상태</th></tr></thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.as_of}>
                <th scope="row">{point.as_of}</th>
                <td>{formatRatio(point.ratio_pct)}</td>
                <td>{STATUS_LABEL[point.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

function ShortSaleData({ ticker }: { ticker: string }) {
  const { data, failing } = usePoll(`kr-short-sale:${ticker}`, () => api.positioning(ticker), REFRESH_MS);
  const short = data?.kr_short;
  return (
    <div className="kr-short-data" aria-busy={!data && !failing}>
      {failing && (
        <p className="error small" role="status">
          {short ? "갱신에 실패했습니다. 마지막 조회 결과를 표시합니다." : "공매도 자료를 불러오지 못했습니다."}
        </p>
      )}
      {!data && !failing && <p className="muted small" role="status">공매도 자료를 불러오는 중…</p>}
      {data && !short && !failing && <p className="muted small" role="status">국내 공매도 응답이 없습니다.</p>}
      {short && (
        <>
          <dl className="kr-short-metrics">
            <RatioMetric label="공매도 거래대금 비중" dateLabel="최신 공표일" series={short.trade} />
            <RatioMetric label="공매도 순보유잔고 비중" dateLabel="잔고 기준일" series={short.balance} />
          </dl>
          <TradeHistory points={short.trade.history} />
        </>
      )}
    </div>
  );
}

export default function KrShortSaleCard() {
  const headingId = useId();
  const inputId = useId();
  const [selected, setSelected] = useState<Picked>(QUICK_PICKS[0]);
  const [draft, setDraft] = useState<Picked>({ ticker: "" });
  const [searchVersion, setSearchVersion] = useState(0);
  const [invalid, setInvalid] = useState(false);

  function select(candidate: Picked) {
    const ticker = candidate.ticker.trim().toUpperCase();
    if (!KR_TICKER.test(ticker)) {
      setInvalid(true);
      return;
    }
    setSelected({ ...candidate, ticker });
    setInvalid(false);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    select(draft);
  }

  return (
    <section className="kr-short-card" aria-labelledby={headingId}>
      <header>
        <h4 id={headingId}>국내 공매도</h4>
        <p className="muted small">거래대금 비중은 전체 거래대금 중 공매도 거래대금의 비율입니다.</p>
      </header>
      <div className="kr-short-picks" role="group" aria-label="국내 공매도 종목 빠른 선택">
        {QUICK_PICKS.map((item) => (
          <button
            key={item.ticker}
            type="button"
            className={`chip ${selected.ticker === item.ticker ? "chip-on" : ""}`}
            aria-pressed={selected.ticker === item.ticker}
            onClick={() => {
              select(item);
              setDraft({ ticker: "" });
              setSearchVersion((version) => version + 1);
            }}
          >
            {item.name}
          </button>
        ))}
      </div>
      <form className="kr-short-search" onSubmit={submit}>
        <label htmlFor={inputId}>국내 종목 검색</label>
        <InstrumentSearch
          key={searchVersion}
          inputId={inputId}
          value={draft}
          placeholder="종목 이름 · 코드"
          onChange={(candidate) => {
            setDraft(candidate);
            setInvalid(false);
          }}
          onPick={select}
        />
        <button type="submit">조회</button>
        {invalid && <p className="error small" role="alert">코스피·코스닥 종목을 선택하거나 6자리 코드 뒤에 .KS 또는 .KQ를 붙여 입력하세요.</p>}
      </form>
      <p className="kr-short-selection" aria-live="polite">
        <strong>{selected.name ?? selected.ticker}</strong>
        {selected.name && <span className="muted small">{selected.ticker}</span>}
        <span className="muted small">{selected.ticker.endsWith(".KS") ? "코스피" : "코스닥"}</span>
      </p>
      <ShortSaleData key={selected.ticker} ticker={selected.ticker} />
    </section>
  );
}
