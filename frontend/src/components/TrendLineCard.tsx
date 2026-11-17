import { useEffect, useRef } from "react";
import { price } from "../format";
import { KIND_KO, pctText, roleFlipped, SCALE_KO, SCENARIO_KO, STATUS_KO } from "../trendLines";
import type { Trendline, TrendScenario } from "../types";

/**
 * #531 차트에서 누른 추세선 하나의 카드 — 무엇인가 · 지금 위치 · 돌파/거절(받침/이탈) 시나리오 · 앞으로 선 값.
 * 숫자는 모두 서버 계산값(#530)이고 문장만 초보자용. 넓은 화면은 오른쪽 해설 자리, 휴대폰은 아래 시트(CSS).
 */
const NAME: Record<Trendline["kind"], string> = { support: "상승 지지선", resistance: "하락 저항선" };
const barWord = (tf: string) => (tf === "1d" ? "일" : tf === "1w" ? "주" : tf === "1mo" || tf === "1M" ? "달" : "봉");
/** 부호 없는 % — "5.0% 아래" 처럼 위 · 아래를 말로 할 때 */
const absPct = (v: number) => `${Math.abs(v) >= 10 ? Math.round(Math.abs(v)) : Math.abs(v).toFixed(1)}%`;
/** 거래량을 짧게 — 683,204,020 → "6.8억" */
const qtyKo = (n: number) => (n >= 1e8 ? `${(n / 1e8).toFixed(1)}억` : n >= 1e4 ? `${Math.round(n / 1e4).toLocaleString("ko-KR")}만` : Math.round(n).toLocaleString("ko-KR"));
/** #560 서버 문장의 날 거래량 "20봉 평균(683,204,020)" 을 표와 같은 "20봉 평균(6.8억주)" 로 */
const tidyVolume = (text: string) =>
  text.replace(/20봉 평균\((\d{1,3}(?:,\d{3})+)\)/g, (_, n: string) => `20봉 평균(${qtyKo(Number(n.replace(/,/g, "")))}주)`);
const tfWord = (tf: string) => (tf === "1d" ? "일봉" : tf === "1w" ? "주봉" : tf === "1mo" || tf === "1M" ? "월봉" : tf);

function Scenario({ s }: { s: TrendScenario }) {
  const close = s.kind === "breakout" ? `종가가 ${price(s.trigger)} 위` : s.kind === "breakdown" ? `종가가 ${price(s.trigger)} 아래`
    : s.kind === "reject" ? `${price(s.trigger)} 근처에서 막힘` : `${price(s.trigger)} 근처에서 받침`;
  const good = s.kind === "breakout" || s.kind === "bounce";
  return (
    <section className={`tl-scn ${good ? "tl-scn-up" : "tl-scn-down"}`}>
      <h5>{SCENARIO_KO[s.kind]}</h5>
      <p>{tidyVolume(s.text)}</p>
      <dl className="small">
        <div><dt>조건</dt><dd>{close}{s.volume_min != null ? ` · 거래량 ${qtyKo(s.volume_min)}주 이상(20봉 평균)` : ""}</dd></div>
        {s.target != null && <div><dt>{good ? "다음 목표" : "되돌림 받침"}</dt><dd>{price(s.target)}{s.target_basis ? <span className="muted"> · {s.target_basis}</span> : null}</dd></div>}
        {s.invalid != null && <div><dt>틀렸다고 볼 때</dt><dd>종가 {price(s.invalid)} {s.kind === "breakout" || s.kind === "bounce" ? "아래" : "위"}</dd></div>}
      </dl>
    </section>
  );
}

/** when: "지금" · "분석 당시"(차트의 기준) — 모르면 비운다 */
export default function TrendLineCard({ line, when = "", onClose }: { line: Trendline; when?: string; onClose: () => void }) {
  const box = useRef<HTMLElement>(null);
  const key = line.id ?? `${line.timeframe}-${line.kind}-${line.anchors[0]?.at}`;
  // 새 선을 고를 때만 카드로 초점 · 스크롤(값만 새로 와서 다시 그릴 때는 그대로)
  useEffect(() => {
    box.current?.focus({ preventScroll: true });
    box.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [key]);
  // 선의 과거 역할(막힌 · 받친 기록)은 kind, 지금 역할은 시나리오로 — 깨진 지지선은 이제 위에서 막는 저항 역할(#530 보완)
  const res = line.kind === "resistance";
  const nowRes = line.scenarios?.length ? line.scenarios.some((s) => s.kind === "breakout" || s.kind === "reject") : res;
  const example = line.scenarios?.some((s) => s.target_basis?.includes("(예시)"));
  const d = line.distance_pct;
  const where = d == null ? null
    : nowRes ? (d > 0 ? `닿으려면 ${absPct(d)} 올라야 해요` : `가격이 선보다 ${absPct(d)} 위예요`)
      : (d < 0 ? `가격에서 ${absPct(d)} 아래에 받침이 있어요` : `가격이 선보다 ${absPct(d)} 아래예요`);
  return (
    <section ref={box} tabIndex={-1} className={`card tl-card tl-${line.kind}`} aria-label={`${NAME[line.kind]} 시나리오`} aria-live="polite">
      <header className="tl-head">
        <strong>{line.scale ? `${SCALE_KO[line.scale]} ` : ""}{NAME[line.kind]}</strong>
        {line.status && <span className={`pill tl-status tl-${line.status}`}>{STATUS_KO[line.status]}</span>}
        {example && <span className="pill pill-warn">예시</span>}
        <button type="button" className="link small tl-close" onClick={onClose} aria-label="선 카드 닫기">닫기 ✕</button>
      </header>
      <p className="tl-now">
        {when ? `${when} ` : ""}선 값 <b>{price(line.price_now)}</b>{d != null && <> · {where}</>}
      </p>
      {roleFlipped(line) && (
        <p className="small tl-flip">{res ? "깨진 저항선 — 이제는 아래에서 받치는 자리로 봐요." : "깨진 지지선 — 이제는 위에서 막는 자리로 봐요."}</p>
      )}
      <p className="muted small">
        {tfWord(line.timeframe)} · {res ? "막힌" : "받친"} 자리 {line.touches.length}번 · 기울기 {line.slope_pct_per_bar > 0 ? "+" : ""}{line.slope_pct_per_bar}%/{barWord(line.timeframe)}
        {line.anchors[0] ? ` · ${line.anchors[0].at.slice(0, 10)}부터` : ""}{line.swing_k ? ` · 스윙 ${line.swing_k}${barWord(line.timeframe)}` : ""}
      </p>
      {line.summary && line.summary.length > 0 && <ul className="tl-summary">{line.summary.map((t, i) => <li key={i}>{tidyVolume(t)}</li>)}</ul>}
      {line.scenarios && line.scenarios.length > 0 ? (
        <div className="tl-scns">{line.scenarios.map((s) => <Scenario key={s.kind} s={s} />)}</div>
      ) : (
        <p className="muted small">이 선의 시나리오가 없습니다(예전 분석 · 서버) — '지금' 기준으로 보면 나옵니다.</p>
      )}
      {line.ahead && line.ahead.length > 0 && (
        <table className="tl-ahead small">
          <caption className="muted">앞으로 선 값({KIND_KO[line.kind]}선은 기울어 있어 날마다 바뀌어요)</caption>
          <thead><tr><th scope="col">언제</th><th scope="col">선 값</th><th scope="col">{when === "분석 당시" ? "그때 가격" : "지금가"} 대비</th></tr></thead>
          <tbody>
            {line.ahead.map((a) => (
              <tr key={a.bars}><th scope="row">{a.bars}{barWord(line.timeframe)} 뒤</th><td>{price(a.price)}</td><td className="muted">{pctText(a.distance_pct)}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted small">숫자는 데스크가 가격 기록으로 계산한 값이에요. 예측이 아니라 조건과 대응입니다.</p>
    </section>
  );
}
