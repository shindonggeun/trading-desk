import { useEffect, useState } from "react";
import { api, type Alignment, type ReviewResponse, type ReviewRow } from "../api";
import { price } from "../format";
import { RATING, term } from "../korean";

const PERIODS = [30, 90, 180];

const ALIGN: Record<Alignment, { ko: string; desc: string; cls: string }> = {
  with_plan: { ko: "계획대로", desc: "분석 방향과 같은 매매(롱 계획에 매수, 숏 계획에 매도)", cls: "align-with" },
  against_plan: { ko: "반대 방향", desc: "분석 방향과 반대 매매", cls: "align-against" },
  plan_flat: { ko: "관망 중", desc: "분석이 관망(Hold)이었을 때의 매매", cls: "align-flat" },
  no_plan: { ko: "분석 없음", desc: "체결 전 30일 안에 이 종목 분석이 없다", cls: "align-none" },
};

const OUTCOME: Record<string, { ko: string; cls: string }> = {
  target: { ko: "목표 도달", cls: "out-target" },
  stop: { ko: "손절 도달", cls: "out-stop" },
  both: { ko: "같은 날 둘 다", cls: "out-both" },
  open: { ko: "아직 미도달", cls: "out-open" },
};

function when(iso: string): string {
  // 토스 체결 시각은 KST 다(+09:00). 표에는 날짜 · 시각까지만.
  return iso.replace("T", " ").slice(0, 16);
}

function money(v: number | null | undefined, currency: string): string {
  if (v === null || v === undefined) return "—";
  // 자릿수는 절댓값으로 정한다 — price() 는 1000 이상만 소수점을 버려 음수와 어긋난다.
  const sign = v > 0 ? "+" : v < 0 ? "-" : "";
  return `${sign}${price(Math.abs(v))} ${currency}`;
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function Realized({ row }: { row: ReviewRow }) {
  const r = row.realized;
  if (row.side !== "SELL" || !r) return <span className="muted">—</span>;
  if (r.realized_pnl === null) {
    return <span className="muted" title="이 기간 안에 산 기록이 없어 매입가를 모릅니다">알 수 없음</span>;
  }
  return (
    <span className={r.realized_pnl >= 0 ? "pnl-up" : "pnl-down"}>
      {money(r.realized_pnl, row.currency)} <span className="small">({pct(r.realized_rate)})</span>
      {r.unmatched_quantity > 0 && (
        <span className="muted small" title="기간 전에 산 물량은 손익에서 뺐습니다"> · {r.unmatched_quantity}주 제외</span>
      )}
    </span>
  );
}

function Plan({ row }: { row: ReviewRow }) {
  const p = row.plan;
  if (!p) return <span className="muted">—</span>;
  const rating = term(RATING, p.rating);
  return (
    <a href={`#/runs/${p.run_id}`} className="review-plan" title="그때의 분석 보기">
      <span>{rating ? rating.ko : p.rating ?? "?"}</span>
      <span className="muted small">
        {p.trade_date} · 진입 {price(p.entry_price)} · 손절 {price(p.stop_loss)} · 목표 {price(p.price_target)}
      </span>
    </a>
  );
}

/** 매매 복기 — 내가 실제로 산 · 판 체결을 그 시점의 분석 계획과 나란히 본다(#48). */
export default function Review() {
  const [days, setDays] = useState(90);
  const [data, setData] = useState<ReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    setFailed(null);
    setData(null); // 이전 기간의 숫자가 새 기간 버튼 아래 남지 않게
    api
      .review(days)
      .then((d) => live && setData(d))
      .catch((err) => live && setFailed(err instanceof Error ? err.message : "불러오지 못했습니다."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [days]);

  const s = data?.summary;
  return (
    <div className="review">
      <section className="card review-head">
        <div className="review-title">
          <h2>매매 복기</h2>
          <div className="review-periods" role="group" aria-label="기간">
            {PERIODS.map((d) => (
              <button key={d} type="button" className={d === days ? "" : "secondary"} onClick={() => setDays(d)}>
                {d}일
              </button>
            ))}
          </div>
        </div>
        <p className="muted small">
          실제 체결(토스증권)을 체결 전 30일 안의 가장 최근 분석과 묶습니다. 결과는 체결 다음 날부터 일봉으로 손절 · 목표 중
          먼저 닿은 쪽입니다. 주문은 연결되어 있지 않습니다.
        </p>
        {data?.source === "mock" && <p className="pill pill-warn">샘플 데이터 — 서버가 mock 브로커입니다</p>}
        {data && !data.supported && <p className="login-error">이 브로커는 체결 내역을 제공하지 않습니다.</p>}
        {failed && <p className="login-error">{failed}</p>}
        {data?.errors.map((e) => (
          <p key={e} className="muted small">⚠ {e}</p>
        ))}

        {s && (
          <div className="review-summary">
            <div><strong>{s.fills}</strong><span className="muted small">체결</span></div>
            <div title={ALIGN.with_plan.desc}><strong>{s.with_plan}</strong><span className="muted small">계획대로</span></div>
            <div title="계획과 반대 방향 매매 — 청산(차익 · 손절) 포함"><strong>{s.against_plan}</strong><span className="muted small">반대 방향 · 청산</span></div>
            <div title={ALIGN.plan_flat.desc}><strong>{s.plan_flat}</strong><span className="muted small">관망 중</span></div>
            <div title={ALIGN.no_plan.desc}><strong>{s.no_plan}</strong><span className="muted small">분석 없음</span></div>
            <div><strong className="pnl-up">{s.outcomes.target}</strong><span className="muted small">목표 도달</span></div>
            <div><strong className="pnl-down">{s.outcomes.stop}</strong><span className="muted small">손절 도달</span></div>
            <div className="review-pnl">
              {Object.keys(s.realized_pnl).length === 0 ? (
                <span className="muted small">실현 손익 없음</span>
              ) : (
                Object.entries(s.realized_pnl).map(([cur, v]) => (
                  <strong key={cur} className={v >= 0 ? "pnl-up" : "pnl-down"}>{money(v, cur)}</strong>
                ))
              )}
              <span className="muted small">실현 손익</span>
            </div>
          </div>
        )}
      </section>

      <section className="card">
        {loading ? (
          <p className="muted">불러오는 중…</p>
        ) : !data || !data.summary ? (
          <p className="muted">체결 내역을 보여 줄 수 없습니다.</p>
        ) : data.fills.length === 0 ? (
          <p className="muted">이 기간에 체결이 없습니다.</p>
        ) : (
          <div className="table-wrap">
            <table className="review-table">
              <thead>
                <tr>
                  <th>체결 시각</th>
                  <th>종목</th>
                  <th>구분</th>
                  <th className="num">수량</th>
                  <th className="num">체결가</th>
                  <th>그때의 계획</th>
                  <th>계획과</th>
                  <th className="num">진입가 대비</th>
                  <th>이후 결과</th>
                  <th className="num">실현 손익</th>
                </tr>
              </thead>
              <tbody>
                {data.fills.map((row) => {
                  // 롱 계획 중 매도 · 숏 계획 중 매수는 보통 차익 실현 · 청산이다 — "반대 방향" 만으로 읽히지 않게.
                  const closing = row.alignment === "against_plan" && (
                    (row.side === "SELL" && row.plan?.bias === "long") || (row.side === "BUY" && row.plan?.bias === "short"));
                  const a = closing
                    ? { ko: "청산", desc: "계획 방향 포지션을 줄인 매매(롱 계획 중 매도 · 숏 계획 중 되사기) — 차익 · 손절 모두", cls: "align-flat" }
                    : row.alignment === "no_plan" && row.plan
                      ? { ko: "계획 못 읽음", desc: "분석은 있지만 진입 · 손절 · 목표를 읽지 못했다", cls: "align-none" }
                      : ALIGN[row.alignment];
                  const o = row.outcome ? OUTCOME[row.outcome.result] : null;
                  return (
                    <tr key={row.order_id}>
                      <td className="muted small">{when(row.filled_at)}</td>
                      <td>
                        {row.name ?? row.symbol} <span className="muted small">{row.ticker}</span>
                      </td>
                      <td className={row.side === "BUY" ? "side-buy" : "side-sell"}>{row.side === "BUY" ? "매수" : "매도"}</td>
                      <td className="num">{row.quantity}</td>
                      <td className="num">{price(row.price)} <span className="muted small">{row.currency}</span></td>
                      <td><Plan row={row} /></td>
                      <td><span className={`pill ${a.cls}`} title={a.desc}>{a.ko}</span></td>
                      <td className="num">{pct(row.entry_gap_pct)}</td>
                      <td>
                        {o ? (
                          <span className={o.cls}>
                            {o.ko}
                            {row.outcome?.on && <span className="muted small"> {row.outcome.on}</span>}
                          </span>
                        ) : (
                          <span className="muted small" title="계획대로 한 매매이고 손절 · 목표가 있을 때만 봅니다">—</span>
                        )}
                      </td>
                      <td className="num"><Realized row={row} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
