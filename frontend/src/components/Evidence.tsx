import { price } from "../format";
import type { Plan, SupportResistance, Trendline } from "../types";

const TREND_NAME: Record<Trendline["kind"], string> = { support: "상승 지지선", resistance: "하락 저항선" };
const TREND_STATE: Record<Trendline["state"], string> = { intact: "유지", testing: "시험 중", broken: "이탈" };

function tf(t: string): string {
  if (t === "1d") return "일봉";
  if (t === "1w") return "주봉";
  const m = /^(\d+)m$/.exec(t);
  return m ? `${m[1]}분봉` : t;
}

function day(iso: string): string {
  return new Date(new Date(iso).getTime() + 9 * 3600_000).toISOString().slice(0, 10);
}

/** 분석할 때 에이전트가 받은 지지 · 저항 · 추세선 · 피보나치(계획에 저장된 값) — 글 목록. */
function Levels({ sr, title }: { sr: Partial<SupportResistance>; title: string }) {
  const levels = sr.levels ?? [];
  const trends = sr.trendlines ?? [];
  const fib = sr.fibonacci;
  const ref = sr.reference_price != null ? `${price(sr.reference_price)}(${sr.reference === "live" ? "실시간가" : "종가"}${sr.as_of ? ` · ${day(sr.as_of)}` : ""})` : null;
  return (
    <section className="card evidence-levels">
      <h3>{title}</h3>
      <p className="muted small">
        분석할 때 에이전트가 받은 구역입니다{ref ? ` — 기준가 ${ref} 아래는 지지, 위는 저항` : ""}
        {sr.atr != null && ` · ATR ${price(sr.atr)}`}. 가격 기록의 계산이지 예측이 아닙니다.
      </p>
      {levels.length > 0 ? (
        <ul className="sr-list" aria-label={`${title} 지지 · 저항`}>
          {levels.map((lv) => (
            <li key={`${lv.kind}-${lv.price}`} className={`sr-${lv.kind}`}>
              <span className="sr-kind">{lv.kind === "support" ? "지지" : "저항"}</span>
              <strong className="num">{price(lv.price)}</strong>
              <span className="muted small">{tf(lv.timeframe)} · 강도 {lv.strength}</span>
              <span className="small">{lv.basis}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small">기록된 지지 · 저항이 없습니다.</p>
      )}
      {trends.length > 0 && (
        <ul className="sr-list trend-list" aria-label={`${title} 추세선`}>
          {trends.map((t) => (
            <li key={`${t.kind}-${t.timeframe}-${t.anchors[0]?.at}`} className={`sr-${t.kind} trend-${t.state}`}>
              <span className="sr-kind">{t.kind === "support" ? "지지" : "저항"}</span>
              <strong className="num">{price(t.price_now)}</strong>
              <span className="muted small">{tf(t.timeframe)} · 터치 {t.touches.length}회</span>
              <span className="small">
                {TREND_NAME[t.kind]} · 가격은 선에서 {t.distance_atr > 0 ? "+" : ""}{t.distance_atr} ATR · <strong>{TREND_STATE[t.state]}</strong>
              </span>
            </li>
          ))}
        </ul>
      )}
      {trends.length > 0 && (
        <p className="muted small">추세선은 로그 가격에서 직선, 차트의 번호는 선에 닿은 저점 · 고점 순서입니다.</p>
      )}
      {fib && (
        <p className="muted small">
          피보나치 되돌림 — {fib.direction === "up" ? "상승" : "하락"} {price(fib.from.price)} → {price(fib.to.price)}:{" "}
          {fib.levels.filter((lv) => lv.ratio > 0 && lv.ratio < 1).map((lv) => `${lv.ratio} ${price(lv.price)}`).join(" · ")}
        </p>
      )}
    </section>
  );
}

/** 근거 탭의 레벨 목록(#99): 상품(또는 종목)과, 레버리지 상품이면 본주. 예전 run 은 기록이 없다고 알린다. */
export default function EvidenceLevels({ plan, ticker }: { plan: Plan | null; ticker: string }) {
  const own = plan?.support_resistance;
  const u = plan?.underlying;
  const under = u?.support_resistance;
  const any = (sr?: Partial<SupportResistance>) => !!(sr?.levels?.length || sr?.trendlines?.length || sr?.fibonacci);
  if (!any(own) && !any(under)) {
    return (
      <section className="card evidence-levels">
        <h3>지지 · 저항</h3>
        <p className="muted small">
          {plan ? "이 분석에는 에이전트가 받은 레벨 기록이 없습니다 — 차트 탭에서 '지금' 기준 구역을 보세요." : "분석이 끝나면 에이전트가 받은 레벨이 여기에 나옵니다."}
        </p>
      </section>
    );
  }
  return (
    <>
      {own && any(own) ? <Levels sr={own} title={`${ticker} 지지 · 저항`} /> : null}
      {u && under && any(under) ? <Levels sr={under} title={`${u.ticker} 지지 · 저항(본주)`} /> : null}
    </>
  );
}
