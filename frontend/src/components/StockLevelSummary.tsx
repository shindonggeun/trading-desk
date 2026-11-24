import { useEffect, useState } from "react";
import { api } from "../api";
import { percent } from "../format";
import {
  STOCK_LEVEL_INTERVAL_LABEL,
  stockDailyClose,
  stockFallbackFrame,
  stockLevelChartHref,
  stockLevelQuote,
  stockNearestLevels,
  stockReadFrame,
} from "../stockLevelSummary";
import type { StockLevelFrame, StockLevelSide } from "../stockLevelSummary";
import { findTicker, sameTicker } from "../ticker";
import "./StockLevelSummary.css";

const number = new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 8 });
const timestamp = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const QUOTE_LABEL = { live: "실시간 시세", delayed: "시세 · 지연 가능", close: "최근 일봉 종가" } as const;
const SIDE_LABEL = { resistance: "저항", support: "지지" } as const;

type Load<T> =
  | { status: "loading" | "error"; data: null }
  | { status: "ready"; data: T };

function useLoad<T>(ticker: string, read: (ticker: string) => Promise<T>, enabled = true) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<Load<T>>({ status: "loading", data: null });
  useEffect(() => {
    if (!enabled) return;
    let mounted = true;
    read(ticker).then(
      (data) => { if (mounted) setState({ status: "ready", data }); },
      () => { if (mounted) setState({ status: "error", data: null }); },
    );
    return () => { mounted = false; };
  }, [ticker, read, enabled, attempt]);
  return {
    ...state,
    retry() {
      setState({ status: "loading", data: null });
      setAttempt((previous) => previous + 1);
    },
  };
}

async function boardQuote(ticker: string) {
  const { items } = await api.board([ticker]);
  return findTicker(items, ticker) ?? null;
}

const recentDaily = (ticker: string) => api.candles(ticker, "1d", 3);

function Moment({ value }: { value: string | null }) {
  if (!value) return <>시각 미제공</>;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return <time dateTime={value}>{value}</time>;
  const date = new Date(value);
  return Number.isFinite(date.getTime())
    ? <time dateTime={value}>{timestamp.format(date)} KST</time>
    : <>시각 미제공</>;
}

function Retry({ label, retry }: { label: string; retry: () => void }) {
  return <button className="link" type="button" onClick={retry}>{label}</button>;
}

function Rows({ frame, side, quote }: {
  frame: StockLevelFrame;
  side: StockLevelSide;
  quote: ReturnType<typeof stockLevelQuote>;
}) {
  const label = SIDE_LABEL[side];
  const tf = STOCK_LEVEL_INTERVAL_LABEL[frame.interval];
  return (
    <tbody>
      {stockNearestLevels(frame, side, quote).map((level, index) => {
        const name = `${label} ${index + 1}`;
        if (!level) return <tr key={name}><th scope="row">{name}</th><td colSpan={3}>미제공</td></tr>;
        const marker = number.format(level.price);
        return (
          <tr className="stock-levels__row" key={name}>
            <th scope="row">
              <a
                href={stockLevelChartHref(frame.ticker, frame.interval, level.price)}
                aria-label={`${name} ${tf} ${marker} 차트에서 강조`}
                title={level.name}
              >{name}</a>
            </th>
            <td>
              {level.zone ? (
                <>
                  <span>{number.format(level.zone[0])}~{number.format(level.zone[1])}</span>
                  <small>강조 {marker}</small>
                </>
              ) : marker}
            </td>
            <td>{level.distancePct == null ? "—" : percent(level.distancePct)}</td>
            <td>{tf}</td>
          </tr>
        );
      })}
    </tbody>
  );
}

function StockLevelSummaryForTicker({ ticker }: { ticker: string }) {
  const board = useLoad(ticker, boardQuote);
  const read = useLoad(ticker, api.chartRead);
  const readFrame = stockReadFrame(ticker, read.data);
  const useFallback = read.status !== "loading" && readFrame == null;
  const support = useLoad(ticker, api.levels, useFallback);
  const frame = readFrame ?? (useFallback ? stockFallbackFrame(ticker, support.data) : null);
  const primaryQuote = stockLevelQuote(ticker, board.data, read.data);
  const needsDaily = !primaryQuote || (primaryQuote.kind === "close" && primaryQuote.changePct == null);
  const useDaily = needsDaily && board.status !== "loading" && read.status !== "loading";
  const daily = useLoad(ticker, recentDaily, useDaily);
  const dailyQuote = useDaily ? stockDailyClose(ticker, daily.data) : null;
  const sameClose = dailyQuote?.changePct != null && dailyQuote.at
    && sameTicker(primaryQuote?.ticker, dailyQuote.ticker) && primaryQuote?.at?.slice(0, 10) === dailyQuote.at;
  const quote = sameClose ? dailyQuote : primaryQuote ?? dailyQuote;
  const sameQuote = sameTicker(frame?.ticker, quote?.ticker) ? quote : null;
  let distanceLabel: string | null = null;
  if (sameQuote) {
    distanceLabel = sameQuote.kind === "close" ? "종가 대비 거리" : "시세 대비 거리";
  } else if (frame?.reference != null) {
    distanceLabel = frame.referenceKind === "close" ? "기존 종가 대비 거리" : "차트 기준가 대비 거리";
  }
  const waitingForLevels = read.status === "loading" || (useFallback && support.status === "loading");

  return (
    <section className="stock-levels" aria-label={`${ticker} 가격과 지지·저항`}>
      <div className="stock-levels__quote">
        {quote ? (
          <>
            <div className="stock-levels__price">
              <strong>{number.format(quote.price)}</strong>
              <span>{quote.changePct == null ? "등락률 미제공" : percent(quote.changePct)}</span>
            </div>
            <p className="stock-levels__meta">
              {QUOTE_LABEL[quote.kind]} · {quote.atKind === "trade" ? "체결" : quote.atKind === "observed" ? "조회" : "기준"} <Moment value={quote.at} />
            </p>
          </>
        ) : <p role="status">{(board.status === "loading" || read.status === "loading" || (useDaily && daily.status === "loading")) ? "시세를 불러오는 중…" : "시세 및 일봉 종가 미제공"}</p>}
        {board.status === "error" && <Retry label="시세 다시 조회" retry={board.retry} />}
        {useDaily && daily.status === "error" && <Retry label="일봉 종가 다시 조회" retry={daily.retry} />}
      </div>
      <h3>지지·저항 가격</h3>
      {frame ? (
        <>
          <p className="stock-levels__meta">
            {frame.source === "chart-read" ? "차트 읽기" : "기존 지지·저항"} 기준 <Moment value={frame.at} />
            {frame.inProgress && <> · 진행 봉</>}
            {distanceLabel && <> · {distanceLabel}</>}
            {!sameQuote && frame.reference != null && <> ({number.format(frame.reference)})</>}
          </p>
          <table className="stock-levels__table" aria-label="가까운 지지·저항 가격">
            <thead><tr><th scope="col">단계</th><th scope="col">가격</th><th scope="col">거리</th><th scope="col">시간봉</th></tr></thead>
            <Rows frame={frame} side="resistance" quote={sameQuote} />
            <Rows frame={frame} side="support" quote={sameQuote} />
          </table>
        </>
      ) : <p role="status">{waitingForLevels ? "지지·저항을 불러오는 중…" : "지지·저항 미제공"}</p>}
      {read.status === "error" && <Retry label="차트 읽기 다시 조회" retry={read.retry} />}
      {useFallback && support.status === "error" && <Retry label="지지·저항 다시 조회" retry={support.retry} />}
    </section>
  );
}

export default function StockLevelSummary({ ticker }: { ticker: string }) {
  return <StockLevelSummaryForTicker key={ticker} ticker={ticker} />;
}
