import type { TrendGuide, TrendRow, TrendState, TrendStat } from "../types";

/** 초보용 용어 풀이 — 물음표에 마우스를 올리거나 누르면 보인다 */
const TERMS: Record<string, string> = {
  대세: "몇 달 이상 이어지는 큰 흐름. 하루 이틀 오르내림이 아니라 200일선 · 고점 대비 하락 같은 긴 기준으로 본다.",
  "200일선": "최근 200거래일(약 10개월) 종가의 평균. 지수가 이 위에 있으면 긴 흐름이 위, 아래면 아래로 본다.",
  골든크로스: "50일 평균선이 200일 평균선을 위로 뚫는 것. 반대로 아래로 뚫으면 데드크로스.",
  박스장: "뚜렷한 방향 없이 일정 범위 안에서 오르내리는 장.",
  "전환 중": "하락 · 상승 신호가 섞여 있어 방향이 바뀌는 중일 수 있는 상태.",
  손절: "산 값보다 내려가면 손실을 받아들이고 파는 것. 미리 가격을 정해 둔다.",
  비중: "전체 투자금 가운데 한 종목 · 한 번의 매매에 넣는 몫.",
};

export function Term({ word, label }: { word: keyof typeof TERMS | string; label?: string }) {
  const text = TERMS[word];
  if (!text) return <>{label ?? word}</>;
  return (
    <span className="term">
      {label ?? word}
      <button type="button" className="term-q" title={text} aria-label={`${word} 뜻: ${text}`}>?</button>
    </span>
  );
}

// 좁은 화면(휴대폰)은 지수별 근거를 접어 국면 · 할 일이 첫 화면에 들어오게 한다
const WIDE = typeof window === "undefined" || window.matchMedia?.("(min-width: 721px)").matches !== false;

const STATE_CLASS: Record<TrendState, string> = { bull: "trend-bull", bear: "trend-bear", range: "trend-range", turning: "trend-turning" };

function since(r: TrendRow): string {
  if (!r.since_known) return "기록 시작 이후 계속";
  return `${r.since} 부터`;
}

function stat(s: TrendStat | null | undefined): string {
  if (!s || s.median == null) return "—";
  const sign = (v: number) => `${v > 0 ? "+" : ""}${v}%`;
  return `${sign(s.median)} (전체 ${s.all_median == null ? "—" : sign(s.all_median)})`;
}

function Row({ r }: { r: TrendRow }) {
  const rec = r.record?.states?.[r.state];
  return (
    <li className="trend-row">
      <div className="trend-name">
        <strong>{r.name}</strong>
        <span className={`trend-badge ${STATE_CLASS[r.state]}`}>{r.state_ko}</span>
        <span className="muted small">{since(r)}</span>
      </div>
      {r.pending && (
        <p className="small trend-pending">
          {r.pending.state_ko} 신호 {r.pending.sessions}일째({r.pending.needs}일 이어지면 바뀜)
        </p>
      )}
      <details className="trend-more" open={WIDE}>
      <summary className="muted small">근거 {r.evidence.length}개 · 과거 기록</summary>
      <ul className="trend-ev small">
        {r.evidence.map((e) => (
          <li key={e.id} className={e.vote === 1 ? "up" : e.vote === -1 ? "down" : "muted"}>
            <span aria-hidden="true">{e.vote === 1 ? "▲" : e.vote === -1 ? "▼" : "·"}</span> {e.text}
          </li>
        ))}
      </ul>
      {rec && (
        <p className="muted small">
          이 국면 과거 기록: 20거래일 뒤 중앙값 {stat(rec["20"])} · 60거래일 뒤 {stat(rec["60"])}
        </p>
      )}
      </details>
    </li>
  );
}

function Guide({ g, open, markets }: { g: TrendGuide; open: boolean; markets: string }) {
  return (
    <details className={`card trend-guide ${STATE_CLASS[g.state]}-edge`} open={open}>
      <summary><h3>{g.title} <span className="muted small">— {markets}</span></h3></summary>
      <div className="trend-guide-cols">
        <div>
          <h4>지금 할 일</h4>
          <ul>{g.do.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
        <div>
          <h4>하지 말 것</h4>
          <ul>
            {g.dont.map((t) => <li key={t}>{t}</li>)}
            {g.avoid_ko.map((t) => <li key={t}>피할 셋업: {t}</li>)}
          </ul>
        </div>
      </div>
      <p>{g.summary}</p>
      <p className="small"><strong><Term word="비중" /></strong> {g.size}</p>
      <p className="small"><strong><Term word="손절" /> · 익절</strong> {g.stops}</p>
      <p className="muted small">{g.basis}</p>
    </details>
  );
}

/**
 * #461 매매 팁 맨 위: 지수마다 대세 국면(#460) 한 줄과 근거, 그 아래 지금 국면의 지침.
 * 국면은 지금 추세를 설명할 뿐 앞으로의 수익을 예측하지 않는다(과거 기록도 그렇게 적는다).
 */
export default function TrendCard({ trend, guide, error }: {
  trend: TrendRow[];
  guide: Partial<Record<TrendState, TrendGuide>>;
  error: string | null;
}) {
  if (error && trend.length === 0) {
    return <section className="card trend-card" aria-label="대세 국면"><h3><Term word="대세" /> 국면</h3><p className="muted small">{error}</p></section>;
  }
  if (trend.length === 0) return null;
  // 지침은 국면마다 한 번 — 코스피(없으면 첫 지수) 국면만 펼치고 나머지는 접어 첫 화면에 들어오게
  const states = [...new Set(trend.map((r) => r.state))];
  const openState = (trend.find((r) => r.market === "kospi") ?? trend[0]).state;
  const lead = trend.filter((r) => r.market === "kospi" || r.market === "nasdaq100");
  const headline = (lead.length ? lead : trend).map((r) => `${r.name} ${r.state_ko}`).join(" · ");
  const note = trend.find((r) => r.record_note)?.record_note;
  return (
    <>
      <section className="card trend-card" aria-label="대세 국면">
        <h3><Term word="대세" /> 국면 <span className="muted small">— 지금 {headline}</span></h3>
        <ul className="trend-rows">{trend.map((r) => <Row key={r.market} r={r} />)}</ul>
        <p className="muted small">
          기준일 {trend[0].as_of} · 근거: <Term word="200일선" /> 위아래 · 기울기 · <Term word="골든크로스" /> · 고점 · 저점 구조 · 고점 대비 하락.
          {note ? ` ${note}` : " 국면은 지금 추세를 설명할 뿐 앞으로의 수익을 예측하지 않는다."}
        </p>
      </section>
      {states.map((s) => guide[s] && (
        <Guide key={s} g={guide[s]!} open={s === openState}
          markets={trend.filter((r) => r.state === s).map((r) => r.name).join(" · ")} />
      ))}
    </>
  );
}
