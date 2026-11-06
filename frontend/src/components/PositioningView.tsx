import { price } from "../format";
import type { CreditView, Positioning } from "../types";

const LEAN: Record<string, { text: string; tone: string }> = {
  put_heavy: { text: "풋 쏠림", tone: "down" },
  call_heavy: { text: "콜 쏠림", tone: "up" },
  balanced: { text: "풋 · 콜 균형", tone: "" },
};
const TREND: Record<string, { text: string; tone: string }> = {
  rising: { text: "공매도 늘어나는 중", tone: "down" },
  falling: { text: "공매도 줄어드는 중", tone: "up" },
  steady: { text: "공매도 평소 수준", tone: "" },
};

/** 타일에 붙는 짧은 배지(풋 쏠림 · 콜 쏠림 · 공매도↑ · 숏 스퀴즈) */
export function positioningBadges(p: Positioning | undefined): { text: string; tone: string }[] {
  if (!p?.supported) return [];
  const out: { text: string; tone: string }[] = [];
  const lean = p.options?.lean;
  if (lean && lean !== "balanced") out.push(LEAN[lean]);
  if (p.short?.trend === "rising") out.push({ text: "공매도↑", tone: "down" });
  if (p.short?.crowded) out.push({ text: "숏 스퀴즈 여지", tone: "warn" });
  return out;
}

function Bars({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  return (
    <span className="pv-bars" aria-hidden>
      {values.map((v, i) => (
        <span key={i} className="pv-bar" style={{ height: `${hi === lo ? 50 : 15 + ((v - lo) / (hi - lo)) * 85}%` }} />
      ))}
    </span>
  );
}

function Walls({ label, walls }: { label: string; walls: { strike: number; oi: number; distance_pct: number }[] }) {
  return (
    <div className="pv-walls">
      <span className="muted small">{label}</span>
      {walls.length ? walls.map((w) => (
        <span key={w.strike} className="pv-wall num">
          {price(w.strike)} <span className="muted small">{w.distance_pct > 0 ? "+" : ""}{w.distance_pct}% · OI {w.oi.toLocaleString("ko-KR")}</span>
        </span>
      )) : <span className="muted small">없음</span>}
    </div>
  );
}

/**
 * 공매도 · 옵션 포지션(#154) 한 종목 요약 — 섹터 탭 시트(#157)와 분석 · 차트 카드(#158)가 같이 쓴다.
 * 위치(포지션)일 뿐 예측이 아니다. 미국 상장 종목만, 나머지는 이유 한 줄.
 */
export default function PositioningView({ p }: { p: Positioning | null | undefined }) {
  if (p === undefined) return <p className="muted small">공매도 · 옵션 불러오는 중…</p>;
  if (p === null) return <p className="muted small">공매도 · 옵션을 불러오지 못했습니다.</p>;
  if (!p.supported) return <p className="muted small">{p.reason ?? "이 종목은 공매도 · 옵션 요약이 없습니다."}</p>;
  const s = p.short;
  const o = p.options;
  return (
    <div className="pv">
      {p.verdict && <p className="pv-verdict">{p.verdict}</p>}
      {s && s.volume_ratio != null && (
        <div className="pv-row">
          <div>
            <div className="muted small">공매도 거래 비중 <span className="muted">({s.as_of})</span></div>
            <div className="pv-big num">
              {s.volume_ratio}% {s.trend && <span className={`small ${TREND[s.trend].tone}`}>{TREND[s.trend].text}</span>}
            </div>
            <div className="muted small">5일 {s.volume_ratio_5d}% · {s.days}일 {s.volume_ratio_20d}%</div>
          </div>
          <Bars values={(s.history ?? []).map((h) => h.ratio)} />
        </div>
      )}
      {s?.interest && (
        <div className="muted small">
          공매도 잔고 유통주식의 <strong className={s.crowded ? "warn" : ""}>{s.interest.pct_float ?? "—"}%</strong>
          {s.interest.days_to_cover != null && ` · 숏 커버 ${s.interest.days_to_cover}일`}
          {s.interest.change_pct != null && ` · 전월 대비 ${s.interest.change_pct > 0 ? "+" : ""}${s.interest.change_pct}%`}
          {s.interest.as_of && ` (${s.interest.as_of})`}
        </div>
      )}
      {o && (
        <>
          <div className="pv-row">
            <div>
              <div className="muted small">풋/콜 미결제약정 <span className="muted">(~{o.expiries[o.expiries.length - 1]})</span></div>
              <div className="pv-big num">
                {o.put_call_oi ?? "—"} {o.lean && <span className={`small ${LEAN[o.lean].tone}`}>{LEAN[o.lean].text}</span>}
              </div>
              <div className="muted small">거래량 {o.put_call_volume ?? "—"} · 근월 {o.near.expiry.slice(5)} {o.near.put_call_oi ?? "—"}</div>
            </div>
            {o.max_pain.strike != null && (
              <div className="pv-side">
                <div className="muted small">맥스 페인 {o.max_pain.expiry.slice(5)}</div>
                <div className="pv-big num">{price(o.max_pain.strike)}</div>
                <div className="muted small">현재 대비 {o.max_pain.distance_pct != null && o.max_pain.distance_pct > 0 ? "+" : ""}{o.max_pain.distance_pct}%</div>
              </div>
            )}
          </div>
          <Walls label="콜 벽(위 · 저항)" walls={o.call_walls} />
          <Walls label="풋 벽(아래 · 지지)" walls={o.put_walls} />
          {o.implied_move && (
            <div className="muted small">
              {o.implied_move.move_pct != null && <>예상 변동폭 {o.implied_move.expiry?.slice(5)}까지 ±{o.implied_move.move_pct}% ({price(o.implied_move.low ?? null)}~{price(o.implied_move.high ?? null)})</>}
              {o.implied_move.iv30_pct != null && <>{o.implied_move.move_pct != null ? " · " : ""}30일 IV {o.implied_move.iv30_pct}% (한 달 ±{o.implied_move.move_30d_pct}%)</>}
            </div>
          )}
        </>
      )}
      <p className="muted small pv-note">
        {p.sources.join(" · ")} · 공매도 비중은 장외 보고 거래 기준(마켓메이커 포함) · 옵션은 15분 지연 · 위치일 뿐 예측이 아님
        {p.errors?.length ? ` · 일부 실패: ${p.errors.join(", ")}` : ""}
      </p>
    </div>
  );
}

/** 빚투 · 예탁금(#155) 요약 한 줄 묶음 — 국내 섹터 위 · 대시보드(#158) */
export function CreditSummary({ c }: { c: CreditView | null | undefined }) {
  if (c === undefined) return <p className="muted small">빚투 · 예탁금 불러오는 중…</p>;
  if (!c?.summary) return <p className="muted small">빚투 · 예탁금 자료가 아직 없습니다{c?.error ? `(${c.error})` : ""}.</p>;
  const s = c.summary;
  const tone = s.level === "hot" ? "down" : s.level === "cold" ? "up" : "";
  const sign = (v: number | null, unit: string) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v}${unit}`);
  return (
    <div className="pv credit-summary">
      <p className={`pv-verdict ${tone}`}>{s.verdict}</p>
      <div className="credit-grid">
        <div><div className="muted small">빚투 비율</div><div className="pv-big num">{s.ratio}%</div>
          <div className="muted small">1주 {sign(s.ratio_change_1w, "p")} · 1달 {sign(s.ratio_change_1m, "p")}</div></div>
        <div><div className="muted small">신용융자</div><div className="pv-big num">{s.credit_trillion}조</div>
          <div className="muted small">1달 {sign(s.credit_change_1m_pct, "%")}</div></div>
        <div><div className="muted small">투자자예탁금</div><div className="pv-big num">{s.deposits_trillion}조</div>
          <div className="muted small">1달 {sign(s.deposits_change_1m_pct, "%")}</div></div>
        <div><div className="muted small">반대매매</div><div className="pv-big num">{s.forced_sale_eok}억</div>
          <div className={`small ${s.stress ? "warn" : "muted"}`}>미수금 대비 {s.forced_sale_pct ?? "—"}%</div></div>
      </div>
      <p className="muted small pv-note">금융투자협회 · {s.date} 기준 · 1년 백분위 {s.ratio_percentile_1y}</p>
    </div>
  );
}
