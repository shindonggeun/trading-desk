import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { krwShort, money, percent, price, tone } from "../format";
import { sampleEquity, sampleSettings, sampleSummary, sampleTrades } from "../paperSample";
import type { PaperEquity, PaperSettings, PaperStatus, PaperSummary, PaperTrade, PaperTrades } from "../types";

/**
 * #528 모의 투자 — "분석 타점대로 샀다면 지금 얼마가 됐을까"(#527). 실제 주문은 없다: 화면 곳곳에 "모의" 를 밝힌다.
 * 숫자는 모두 API 값 그대로(이미 채점된 계획 결과로 계산), 여기서는 보여 주기만.
 */

type Data = { settings: PaperSettings; summary: PaperSummary; equity: PaperEquity; trades: PaperTrades; sample: boolean };

const STATUS: { id: PaperStatus | ""; label: string }[] = [
  { id: "", label: "전체" }, { id: "closed", label: "끝남" }, { id: "open", label: "보유 중" },
  { id: "waiting", label: "진입 대기" }, { id: "skipped", label: "건너뜀" },
];
const EXIT: Record<string, string> = { target: "목표 도달", stop: "손절", both: "손절(같은 봉)", expired: "기간 만료" };
const STATUS_KO: Record<PaperStatus, string> = { closed: "끝남", open: "보유 중", waiting: "진입 대기", skipped: "건너뜀" };

function won(v: number | null | undefined, signed = false): string {
  return money(v ?? null, "KRW", signed);
}

function dateKo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00+09:00` : iso);
  return d.toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul", year: "2-digit", month: "numeric", day: "numeric" });
}

async function loadAll(status: string, direction: string, ticker: string): Promise<Data> {
  try {
    const [settings, summary, equity, trades] = await Promise.all([
      api.paperSettings(), api.paperSummary(), api.paperEquity(),
      api.paperTrades({ status, direction, ticker, limit: 200 }),
    ]);
    return { settings, summary, equity, trades, sample: false };
  } catch (e) {
    // 개발 모드에서 API(#527)가 아직 없으면 계약 모양의 예시로(운영 빌드는 오류를 그대로 보인다)
    if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) {
      const t = sampleTrades(status);
      const items = t.items.filter((x) => (!direction || x.direction === direction)
        && (!ticker || x.ticker.toUpperCase().includes(ticker.toUpperCase())));
      return { settings: sampleSettings(), summary: sampleSummary(), equity: sampleEquity(), trades: { ...t, items }, sample: true };
    }
    throw e;
  }
}

/** 쉬운 말 요약 2~3줄 — 숫자는 API 값 */
function easy(s: PaperSummary): string[] {
  const closed = s.trades.closed;
  const lines: string[] = [];
  if (closed === 0) {
    lines.push("아직 끝난 모의 거래가 없어요. 분석이 채점되면 여기에 쌓여요.");
  } else {
    lines.push(`끝난 ${closed}번 가운데 ${s.wins}번 수익을 냈어요${s.win_rate != null ? `(${Math.round(s.win_rate * 100)}%)` : ""}.`);
  }
  lines.push(`${won(s.start_capital)}으로 시작해 지금 ${won(s.balance)}이에요(${won(s.total_pnl, true)}, ${percent(s.total_pnl_pct)}).`);
  lines.push("지난 분석 계획대로 샀다고 가정한 과거 성적이에요. 앞으로의 수익을 보장하지 않아요.");
  return lines;
}

/** 잔고 곡선 — 시작 자금 기준선, 마지막 점에 지금 잔고. 차트 라이브러리 없이 SVG */
function EquityChart({ eq }: { eq: PaperEquity }) {
  const pts = eq.points;
  if (pts.length < 2) return <p className="muted small">잔고 곡선은 거래가 쌓이면 그려져요.</p>;
  const W = 720, H = 220, L = 8, R = 8, T = 14, B = 22;
  const vals = pts.map((p) => p.balance).concat(eq.start_capital);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = (hi - lo) * 0.08 || hi * 0.01;
  const x = (i: number) => L + (W - L - R) * (i / (pts.length - 1));
  const y = (v: number) => T + (H - T - B) * (1 - (v - (lo - pad)) / (hi + pad - (lo - pad)));
  const line = pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.balance).toFixed(1)}`).join("");
  const area = `${line}L${x(pts.length - 1).toFixed(1)},${y(eq.start_capital).toFixed(1)}L${x(0).toFixed(1)},${y(eq.start_capital).toFixed(1)}Z`;
  const last = pts[pts.length - 1];
  const up = last.balance >= eq.start_capital;
  return (
    <svg className={`paper-equity ${up ? "is-up" : "is-down"}`} viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`모의 잔고 곡선: ${dateKo(pts[0].date)} ${won(pts[0].balance)}에서 ${dateKo(last.date)} ${won(last.balance)}, 시작 자금 ${won(eq.start_capital)}`}>
      <path d={area} className="pe-area" />
      <line x1={L} x2={W - R} y1={y(eq.start_capital)} y2={y(eq.start_capital)} className="pe-base" />
      <text x={L + 2} y={y(eq.start_capital) - 5} className="pe-label">시작 {krwShort(eq.start_capital, false)}</text>
      <path d={line} className="pe-line" />
      <circle cx={x(pts.length - 1)} cy={y(last.balance)} r={3.5} className="pe-dot" />
      <text x={L} y={H - 6} className="pe-label">{dateKo(pts[0].date)}</text>
      <text x={W - R} y={H - 6} className="pe-label" textAnchor="end">{dateKo(last.date)}</text>
    </svg>
  );
}

/** 주 · 월 손익 막대(실현). 빨강 이익 · 파랑 손실(국내 관례) */
function PnlBars({ rows, unit }: { rows: { start: string; pnl: number }[]; unit: "week" | "month" }) {
  if (!rows.length) return <p className="muted small">끝난 거래가 생기면 {unit === "week" ? "주" : "월"}별 손익이 보여요.</p>;
  const show = rows.slice(-(unit === "week" ? 16 : 12));
  const W = 720, H = 160, T = 10, B = 22, gap = 4;
  const max = Math.max(...show.map((r) => Math.abs(r.pnl)), 1);
  const bw = (W - gap * (show.length - 1)) / show.length;
  const mid = T + (H - T - B) / 2;
  const h = (v: number) => (Math.abs(v) / max) * ((H - T - B) / 2);
  const label = (s: string) => unit === "month" ? `${Number(s.slice(5, 7))}월` : `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
  return (
    <svg className="paper-bars" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`${unit === "week" ? "주" : "월"}별 실현 손익: ${show.map((r) => `${label(r.start)} ${won(r.pnl, true)}`).join(", ")}`}>
      <line x1={0} x2={W} y1={mid} y2={mid} className="pe-base" />
      {show.map((r, i) => (
        <g key={r.start}>
          <rect x={i * (bw + gap)} width={bw} y={r.pnl >= 0 ? mid - h(r.pnl) : mid} height={Math.max(1, h(r.pnl))}
            className={r.pnl >= 0 ? "pb-up" : "pb-down"}><title>{`${label(r.start)} ${won(r.pnl, true)}`}</title></rect>
          {(show.length <= 12 || i % 2 === 0) && (
            <text x={i * (bw + gap) + bw / 2} y={H - 6} textAnchor="middle" className="pe-label">{label(r.start)}</text>
          )}
        </g>
      ))}
    </svg>
  );
}

function Stat({ label, value, sub, cls }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="paper-stat">
      <span className="muted small">{label}</span>
      <strong className={cls}>{value}</strong>
      {sub && <span className="muted small">{sub}</span>}
    </div>
  );
}

function TradeRow({ t }: { t: PaperTrade }) {
  const go = () => { window.location.hash = `#/runs/${encodeURIComponent(t.run_id)}`; };
  const why = t.status === "skipped" ? t.skip?.text ?? "건너뜀"
    : t.status === "closed" ? EXIT[t.exit_reason ?? ""] ?? "—"
    : t.status === "open" ? (t.marked ? "보유 중(마지막 채점 종가로 평가)" : "보유 중") : "진입가 대기";
  return (
    <tr className="paper-row" tabIndex={0} onClick={go} onKeyDown={(e) => { if (e.key === "Enter") go(); }}
      aria-label={`${t.name ?? t.ticker} 분석 열기`}>
      <td><strong>{t.name ?? t.ticker}</strong><span className="muted small"> {t.name ? t.ticker : ""}</span>
        <div className="small"><span className={t.direction === "long" ? "up" : "down"}>{t.direction === "long" ? "롱" : "숏"}</span>
          <span className="muted"> · {STATUS_KO[t.status]} · {dateKo(t.plan_at)}</span></div></td>
      <td className="num">{t.entry ? <>{price(t.entry.fill)}<div className="muted small">{dateKo(t.entry.at)}</div></> : "—"}</td>
      <td className="num">{t.exit ? <>{price(t.exit.fill)}<div className="muted small">{dateKo(t.exit.at)}</div></> : "—"}</td>
      <td className="num">{t.qty ?? "—"}<div className="muted small">{t.amount_krw != null ? krwShort(t.amount_krw, false) : ""}</div></td>
      <td className={`num ${tone(t.pnl_krw)}`}>{t.pnl_krw != null ? won(t.pnl_krw, true) : "—"}
        <div className="small">{t.r != null ? `${t.r > 0 ? "+" : ""}${t.r.toFixed(2)}R` : ""}{t.pnl_pct != null ? ` · ${percent(t.pnl_pct)}` : ""}</div></td>
      <td className="small">{why}{t.fx?.entry ? <div className="muted">환율 {t.fx.entry.toLocaleString("ko-KR")}</div> : null}</td>
    </tr>
  );
}

export default function Paper() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<PaperStatus | "">("");
  const [direction, setDirection] = useState("");
  const [ticker, setTicker] = useState("");
  const [unit, setUnit] = useState<"week" | "month">("week");
  const [tick, setTick] = useState(0);

  const load = useCallback(() => {
    loadAll(status, direction, ticker.trim()).then((d) => { setData(d); setError(null); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "불러오지 못했습니다"));
  }, [status, direction, ticker]);
  useEffect(() => { load(); }, [load, tick]);

  // 설정을 바꾼 직후: 새 설정(version)으로 계산된 값이 올 때까지 몇 초마다 다시 읽는다
  const stale = !!data && !data.sample && data.summary.settings_version < data.settings.version;
  useEffect(() => {
    if (!stale) return;
    const id = window.setTimeout(() => setTick((n) => n + 1), 3000);
    return () => window.clearTimeout(id);
  }, [stale, data]);

  const s = data?.summary;
  const lines = useMemo(() => (s ? easy(s) : []), [s]);

  return (
    <div className="paper-page">
      <section className="card paper-head">
        <div className="paper-title">
          <h2>모의 투자</h2>
          <span className="paper-badge" title="실제 주문은 나가지 않아요">모의 · 실제 주문 없음</span>
          <a className="paper-settings-link small" href="#/settings?paper">자금 · 규칙 설정</a>
        </div>
        <p className="muted small">지난 분석의 진입 · 손절 · 목표대로 원화 모의 계좌로 샀다면 어땠을지, 데스크가 채점한 결과로 계산했어요.
          기록은 지금 설정으로 처음부터 다시 계산해서, 늦게 채점된 분석이 들어오면 지난 결과도 바뀔 수 있어요.</p>
        {data?.sample && <p className="paper-note small">개발용 예시 데이터예요(모의 투자 API 준비 중).</p>}
        {stale && <p className="paper-note small" role="status">새 설정으로 다시 계산하는 중이에요…</p>}
        {error && <p className="down small" role="alert">모의 투자 결과를 불러오지 못했습니다: {error} <button type="button" onClick={load}>다시 시도</button></p>}
        {s && <ul className="paper-easy">{lines.map((l) => <li key={l}>{l}</li>)}</ul>}
      </section>

      {s && data && (
        <>
          <section className="paper-stats" aria-label="모의 계좌 요약">
            <Stat label="모의 잔고" value={won(s.balance)} sub={`${won(s.total_pnl, true)} · ${percent(s.total_pnl_pct)}`} cls={tone(s.total_pnl)} />
            {/* 원화 손익 > 0 비율 — 계획 적중률(/api/stats/plans hit_rate)과 정의가 달라 이름을 다르게 */}
            <Stat label="수익 낸 비율" value={s.win_rate != null ? `${Math.round(s.win_rate * 100)}%` : "—"} sub={`${s.wins}번 수익 · ${s.losses}번 손실 · 끝난 ${s.trades.closed}건`} />
            <Stat label="평균 R" value={s.avg_r != null ? `${s.avg_r > 0 ? "+" : ""}${s.avg_r.toFixed(2)}R` : "—"}
              sub={s.payoff != null ? `손익비 ${s.payoff.toFixed(2)}` : undefined} cls={tone(s.avg_r)} />
            <Stat label="최대 낙폭" value={s.max_drawdown ? won(s.max_drawdown.krw, true) : "—"}
              sub={s.max_drawdown ? (s.max_drawdown.peak_at ? `${percent(s.max_drawdown.pct)} · ${dateKo(s.max_drawdown.peak_at)}→${dateKo(s.max_drawdown.trough_at)}` : "낙폭 없음") : undefined}
              cls={s.max_drawdown && s.max_drawdown.krw < 0 ? "down" : undefined} />
            <Stat label="이번 달" value={won(s.this_month.pnl, true)} sub={s.this_month.pct != null ? percent(s.this_month.pct) : undefined} cls={tone(s.this_month.pnl)} />
            <Stat label="보유 · 현금" value={`${s.trades.open}건 보유`} sub={`현금 ${krwShort(s.cash, false)} · 평가 ${won(s.unrealized_pnl, true)}`} />
          </section>

          <section className="card">
            <h3>잔고 곡선 <span className="muted small">점선은 시작 자금</span></h3>
            <EquityChart eq={data.equity} />
          </section>

          <section className="card">
            <div className="paper-row-head">
              <h3>{unit === "week" ? "주" : "월"}별 실현 손익</h3>
              <div className="seg seg-small" role="group" aria-label="기간">
                <button type="button" className={unit === "week" ? "on" : ""} aria-pressed={unit === "week"} onClick={() => setUnit("week")}>주</button>
                <button type="button" className={unit === "month" ? "on" : ""} aria-pressed={unit === "month"} onClick={() => setUnit("month")}>월</button>
              </div>
            </div>
            <PnlBars rows={unit === "week" ? data.equity.weekly : data.equity.monthly} unit={unit} />
          </section>

          <section className="card">
            <div className="paper-row-head">
              <h3>모의 거래</h3>
              <div className="chips" role="group" aria-label="상태">
                {STATUS.map((x) => (
                  <button key={x.id} type="button" className={`chip${status === x.id ? " chip-on" : ""}`} aria-pressed={status === x.id}
                    onClick={() => setStatus(x.id)}>
                    {x.label}{x.id && data.trades.counts[x.id] != null ? ` ${data.trades.counts[x.id]}` : ""}
                  </button>
                ))}
              </div>
            </div>
            <div className="paper-filters">
              <select value={direction} onChange={(e) => setDirection(e.target.value)} aria-label="방향">
                <option value="">롱 · 숏</option><option value="long">롱</option><option value="short">숏</option>
              </select>
              <input value={ticker} onChange={(e) => setTicker(e.target.value)} placeholder="종목(티커)" aria-label="종목 찾기" />
            </div>
            {data.trades.items.length === 0 ? <p className="muted small">조건에 맞는 모의 거래가 없어요.</p> : (
              <div className="table-wrap">
                <table className="stats-table paper-table">
                  <thead><tr><th>종목</th><th className="num">진입</th><th className="num">청산</th><th className="num">수량</th>
                    <th className="num">손익</th><th>사유</th></tr></thead>
                  <tbody>{data.trades.items.map((t) => <TradeRow key={t.run_id} t={t} />)}</tbody>
                </table>
              </div>
            )}
            <p className="muted small">가격은 슬리피지를 반영한 체결가(현지 통화), 손익은 수수료 · 세금을 뺀 원화예요. 행을 누르면 그 분석으로 가요.</p>
          </section>
        </>
      )}
      {!data && !error && <p className="muted">모의 투자 결과를 계산하는 중…</p>}
    </div>
  );
}
