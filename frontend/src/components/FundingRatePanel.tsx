import { useEffect, useState } from "react";
import { FUNDING_SYMBOLS, type FundingDays, type FundingRateResponse, type FundingSymbol, type LoadFundingRate } from "../fundingRateTypes";
import FundingRateHistory, { formatFundingRate, formatMark } from "./FundingRateHistory";
import "./FundingRatePanel.css";

const SYMBOL_NAME: Record<FundingSymbol, string> = {
  BTCUSDT: "비트코인", ETHUSDT: "이더리움", XRPUSDT: "XRP", SOLUSDT: "솔라나",
  SAMSUNGUSDT: "삼성전자", SKHYNIXUSDT: "SK하이닉스", NVDAUSDT: "엔비디아",
  AMDUSDT: "AMD", MUUSDT: "마이크론", SOXXUSDT: "SOXX ETF",
};
const KIND_LABEL = { crypto: "암호화폐", stock: "주식 기초자산", etf: "ETF 기초자산" };
const MESSAGE: Record<FundingRateResponse["status"], string | null> = {
  available: null,
  stale: "수집 지연으로 마지막으로 받은 데이터를 표시합니다.",
  no_data: "선택한 기간에 표시할 확정 결제 이력이 없습니다.",
  unsupported: "지원하지 않는 바이낸스 계약입니다.",
  inactive: "바이낸스 계약이 현재 거래 중이 아닙니다.",
  source_error: "바이낸스에서 펀딩비를 불러오지 못했습니다.",
};
const UTC_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "UTC", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

type Selection = { symbol: FundingSymbol; days: FundingDays };
type Result = { selection: Selection; data: FundingRateResponse | null };

function Timestamp({ value }: { value: string | null }) {
  if (value === null || !Number.isFinite(Date.parse(value))) return <>미제공</>;
  return <time dateTime={value}>{UTC_TIME.format(new Date(value))}</time>;
}

function FundingSummary({ data }: { data: FundingRateResponse }) {
  const { latest_settlement: latest, current_day: today, negative_streak_days: streak } = data;
  const recentDay = data.daily.at(-1);
  let streakText = "연속 음수 산정 불가";
  if (streak > 0) {
    streakText = `펀딩비 ${data.negative_streak_truncated ? "최소 " : ""}${streak}일 연속 음수`;
  } else if (recentDay?.status === "complete" && recentDay.total_rate !== null) {
    streakText = "연속 음수 없음";
  }
  return (
    <dl className="funding-summary">
      <div>
        <dt>최신 확정 결제율</dt>
        <dd>{formatFundingRate(latest?.rate ?? null)}</dd>
        <dd className="muted small">
          <Timestamp value={latest?.settled_at ?? null} />{latest ? " UTC" : ""}
          {latest?.rate_type === "special" && " · 특별 결제"}
        </dd>
      </div>
      <div>
        <dt>최신 결제 마크 가격</dt>
        <dd>{formatMark(latest?.mark_price ?? null)}{latest?.mark_price != null ? " USDT" : ""}</dd>
      </div>
      <div>
        <dt>연속 음수 · 완료된 UTC 날짜</dt>
        <dd>{streakText}</dd>
      </div>
      <div>
        <dt>{data.status === "stale" ? "미완료 UTC 날짜 · 지연 자료" : "오늘 (UTC) · 미완료"}</dt>
        <dd>{today ? formatFundingRate(today.total_rate) : "미제공"}</dd>
        {today && <dd className="muted small"><time dateTime={today.date}>{today.date}</time> · 확정 결제 {today.settlement_count}건</dd>}
      </div>
    </dl>
  );
}

export default function FundingRatePanel({ loadFunding }: { loadFunding: LoadFundingRate }) {
  const [selection, setSelection] = useState<Selection>({ symbol: "BTCUSDT", days: 7 });
  const [result, setResult] = useState<Result | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void loadFunding(selection.symbol, selection.days, controller.signal).then(
      data => {
        if (controller.signal.aborted) return;
        const matches = data.symbol === selection.symbol && data.days === selection.days;
        setResult({ selection, data: matches ? data : null });
      },
      () => {
        if (!controller.signal.aborted) setResult({ selection, data: null });
      },
    );
    return () => controller.abort();
  }, [selection, loadFunding]);

  const current = result?.selection === selection ? result : null;
  const data = current?.data;
  const message = data ? MESSAGE[data.status] : null;
  const showHistory = data?.status === "available" || data?.status === "stale" || data?.status === "no_data";
  const canRetry = current && (!data || data.status === "source_error" || data.status === "stale");

  return (
    <section className="funding-panel" aria-label="바이낸스 펀딩비">
      <div className="funding-controls">
        <label>종목
          <select value={selection.symbol}
            onChange={event => setSelection({ ...selection, symbol: event.currentTarget.value as FundingSymbol })}>
            {FUNDING_SYMBOLS.map(symbol => <option key={symbol} value={symbol}>{SYMBOL_NAME[symbol]} · {symbol}</option>)}
          </select>
        </label>
        <div className="seg funding-period" role="group" aria-label="조회 기간">
          {([7, 30] as const).map(days => (
            <button key={days} type="button" className={selection.days === days ? "on" : ""}
              aria-pressed={selection.days === days} onClick={() => {
                if (selection.days !== days) setSelection({ ...selection, days });
              }}>{days}일</button>
          ))}
        </div>
      </div>
      {!current && <p className="muted small" role="status">펀딩비를 불러오는 중…</p>}
      {current && !data && <p className="error small" role="alert">펀딩비 조회에 실패했습니다.</p>}
      {message && <p className={data?.status === "source_error" ? "error small" : "muted small"}
        role={data?.status === "source_error" ? "alert" : "status"}>{message}</p>}
      {canRetry && <button className="link funding-retry" type="button"
        onClick={() => setSelection({ ...selection })}>다시 시도</button>}
      {data && (
        <dl className="funding-meta small">
          <div><dt>기초자산</dt><dd>{data.underlying.name} · {data.underlying.ticker} · {KIND_LABEL[data.underlying.kind]}</dd></div>
          <div><dt>계약 · 원천</dt><dd>{data.symbol} · Binance USDⓈ-M</dd></div>
          <div><dt>조회 시각 (UTC)</dt><dd><Timestamp value={data.fetched_at} /></dd></div>
          <div><dt>결제 주기</dt><dd>{data.funding_interval_hours === null ? "미제공" : `현재 ${data.funding_interval_hours}시간`}</dd></div>
        </dl>
      )}
      {data && showHistory && (
        <>
          <FundingSummary data={data} />
          <FundingRateHistory daily={data.daily} settlements={data.settlements} />
        </>
      )}
      <details className="funding-help small">
        <summary>펀딩비 읽는 법</summary>
        <p>음수일 때 숏 보유자가 롱 보유자에게 지급합니다. 펀딩비만으로 시세 하락, 숏커버링, 매수 시점을 판단할 수 없습니다.</p>
        <p>펀딩비는 바이낸스 무기한 계약 결제율입니다. 현물 주식의 공매도 비중이나 순보유잔고는 별도 지표입니다.</p>
      </details>
    </section>
  );
}
