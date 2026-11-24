import { useEffect, useId, useState } from "react";
import { api } from "../api";
import { canReadDailyResistanceNews, dailyResistanceNews, dailyResistanceNewsUnavailable } from "../dailyResistanceContext";
import type { StockSocialResponse } from "../stockSocial";
import "./DailyResistanceContext.css";

const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", dateStyle: "medium", timeStyle: "short",
});

type Props = {
  ticker: string;
  observedAt: string | null | undefined;
};

type NewsState =
  | { kind: "loading" | "error" }
  | { kind: "ready"; response: StockSocialResponse };

function Stamp({ value }: { value: string | null | undefined }) {
  const at = value ? Date.parse(value) : NaN;
  if (!value || !Number.isFinite(at)) return <>미제공</>;
  return <time dateTime={value}>{KST_TIME.format(at)} KST</time>;
}

function ContextForTicker({ ticker, observedAt }: Props) {
  const title = useId();
  const eligible = canReadDailyResistanceNews(ticker, observedAt);
  const [attempt, setAttempt] = useState(0);
  const [news, setNews] = useState<NewsState>({ kind: "loading" });

  useEffect(() => {
    if (!eligible || attempt === 0) return;
    const request = new AbortController();
    void api.stockSocial(ticker, request.signal).then((response) => {
      if (!request.signal.aborted) setNews({ kind: "ready", response });
    }, () => {
      if (!request.signal.aborted) setNews({ kind: "error" });
    });
    return () => request.abort();
  }, [attempt, eligible, ticker]);

  const response = news.kind === "ready" ? news.response : undefined;
  const headlines = dailyResistanceNews(ticker, observedAt, response);

  function retry() {
    setNews({ kind: "loading" });
    setAttempt((count) => count + 1);
  }

  return (
    <section className="daily-resistance-context" aria-labelledby={title}>
      <details onToggle={(event) => {
        if (event.currentTarget.open && eligible) retry();
      }}>
        <summary id={title}>뉴스·사용자 메모</summary>
        <p className="small muted">가격 판정은 일봉과 저항선의 관찰에 근거해요.</p>
        <div className="daily-resistance-context__group">
          <h5>뉴스</h5>
          <p className="small muted">{ticker} · 일봉 관찰 기준 <Stamp value={observedAt} /></p>
          {response?.ticker === ticker && (
            <p className="small muted">뉴스 조회 시각 <Stamp value={response.generated_at} /></p>
          )}
          {!eligible ? (
            <p className="small muted">이 상품의 뉴스 관찰 자료가 제공되지 않았어요.</p>
          ) : news.kind === "loading" ? (
            <p className="small muted" role="status">뉴스를 불러오는 중…</p>
          ) : news.kind === "error" ? (
            <p className="small muted">
              뉴스를 불러오지 못했어요. <button type="button" className="link" onClick={retry}>뉴스 다시 조회</button>
            </p>
          ) : headlines.length ? (
            <ul className="daily-resistance-context__headlines">
              {headlines.map((item, index) => (
                <li key={index}>
                  <p className="small">{item.title}</p>
                  <p className="small muted">
                    {item.publisher || "매체 미제공"} · GDELT 발견 <Stamp value={item.at} />
                    {item.stale && " · 지연 자료"}
                  </p>
                </li>
              ))}
            </ul>
          ) : response && dailyResistanceNewsUnavailable(ticker, response) ? (
            <p className="small muted">
              뉴스 원천 자료를 확인할 수 없어요. <button type="button" className="link" onClick={retry}>뉴스 다시 조회</button>
            </p>
          ) : (
            <p className="small muted">조회한 자료에서 관찰 시각까지 발견된 같은 상품의 뉴스를 찾지 못했어요.</p>
          )}
        </div>
        <div className="daily-resistance-context__group">
          <h5>사용자 메모</h5>
          <p className="small muted">이 상품에 연결된 사용자 메모가 제공되지 않았어요.</p>
        </div>
      </details>
    </section>
  );
}

export default function DailyResistanceContext(props: Props) {
  return <ContextForTicker key={props.ticker} {...props} />;
}
