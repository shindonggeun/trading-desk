import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import WeeklyMapChart from "../components/WeeklyMapChart";
import { price } from "../format";
import type { WeeklyHistoryRow, WeeklyMap as Map, WeeklyMapError } from "../types";

/**
 * #205 주간 시장 지도(#202) — 지수마다 "지금 어디쯤인지, 어디까지 빠질 수 있고 어디가 바닥 후보인지"를
 * 결론 한 줄 · 주봉 차트 · 숫자 · 과거 기록으로. 맨 아래 용어 풀이와 계획 체크리스트.
 * 한 지수가 실패하면 그 카드만 "불러오지 못함".
 */

const POSITION: Record<Map["position"], string> = {
  above_black: "검정선 위", between: "검정선 아래 · 빨간선 위", below_red: "빨간선 아래",
};
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "up" : v < 0 ? "down" : "");
const isMap = (m: Map | WeeklyMapError): m is Map => !("error" in m);

/** 지난 주 대비: 위치가 바뀌었는지와 종가 변화 */
function useLastWeek(symbol: string): WeeklyHistoryRow | null {
  const [prev, setPrev] = useState<WeeklyHistoryRow | null>(null);
  useEffect(() => {
    let alive = true;
    api.weeklyHistory(symbol, 2).then((h) => alive && setPrev(h.weeks[1] ?? null), () => {});
    return () => { alive = false; };
  }, [symbol]);
  return prev;
}

function Fact({ name, value, sub, cls }: { name: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="wm-fact">
      <dt className="muted small">{name}</dt>
      <dd><span className="num">{value}</span>{sub && <span className={`small ${cls ?? "muted"}`}> {sub}</span>}</dd>
    </div>
  );
}

function MapCard({ map, focused }: { map: Map; focused: boolean }) {
  const prev = useLastWeek(map.symbol);
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "start" });
  }, [focused]);
  const weekChange = prev ? ((map.close / prev.close - 1) * 100) : null;
  return (
    <section ref={ref} id={`wm-${map.symbol}`} className={`card wm-card wm-${map.position}${focused ? " wm-focused" : ""}`}
      aria-labelledby={`wm-title-${map.symbol}`}>
      <div className="wm-head">
        <h3 id={`wm-title-${map.symbol}`}>{map.name}</h3>
        <span className={`wm-pos wm-pos-${map.position}`}>{POSITION[map.position]}</span>
        <span className={`wm-candle wm-candle-${map.candle.kind}`}>{map.candle.label}</span>
        <span className="muted small">
          {price(map.close)} · {map.week} 주{!map.week_complete && " (마감 전)"}
          {map.source_symbol && ` · ${map.source_symbol} 로 계산`}
        </span>
      </div>
      <p className="wm-headline">{map.text.headline}</p>
      {prev && (
        <p className="muted small wm-prev">
          지난주 대비 <span className={tone(weekChange)}>{pct(weekChange)}</span>
          {prev.position !== map.position
            ? ` · 위치가 바뀜: ${POSITION[prev.position]} → ${POSITION[map.position]}`
            : ` · 위치 그대로(${POSITION[map.position]})`}
        </p>
      )}
      <WeeklyMapChart map={map} />
      <dl className="wm-facts">
        <Fact name="검정선(20주)" value={price(map.lines.black.price)} sub={pct(map.lines.black.pct)} cls={tone(map.lines.black.pct)} />
        <Fact name="빨간선(60주)" value={price(map.lines.red.price)} sub={pct(map.lines.red.pct)} cls={tone(map.lines.red.pct)} />
        <Fact name="50주 볼린저" value={`${price(map.bollinger.lower)} ~ ${price(map.bollinger.upper)}`}
          sub={`하단 ${pct(map.bollinger.lower_pct)}`} />
        <Fact name="52주 고 · 저" value={`${price(map.range_52w.high)} / ${price(map.range_52w.low)}`}
          sub={`고점 대비 ${pct(map.range_52w.from_high_pct)}`} />
        <Fact name="다음 선 ↓" value={price(map.scenario.down.price)} sub={pct(map.scenario.down.pct)} cls="down" />
        <Fact name="다음 선 ↑" value={price(map.scenario.up.price)} sub={pct(map.scenario.up.pct)} cls="up" />
        {map.supports.length > 0 && <Fact name="지지(주봉 저점)" value={map.supports.map((p) => price(p)).join(" · ")} />}
        {map.resistances.length > 0 && <Fact name="저항(주봉 고점)" value={map.resistances.map((p) => price(p)).join(" · ")} />}
      </dl>
      <ul className="wm-lines small">
        {map.text.lines.map((l) => <li key={l}>{l}</li>)}
      </ul>
    </section>
  );
}

function ErrorCard({ m }: { m: WeeklyMapError }) {
  return (
    <section className="card wm-card wm-error">
      <div className="wm-head"><h3>{m.name}</h3></div>
      <p className="muted small">불러오지 못함 — {m.error}</p>
    </section>
  );
}

export default function WeeklyMap({ query }: { query: URLSearchParams }) {
  const [maps, setMaps] = useState<(Map | WeeklyMapError)[] | null>(null);
  const [failed, setFailed] = useState(false);
  const focus = query.get("symbol");
  useEffect(() => {
    let alive = true;
    api.weeklyMaps().then((r) => alive && setMaps(r.maps), () => alive && setFailed(true));
    return () => { alive = false; };
  }, []);
  const first = maps?.find(isMap);
  return (
    <main className="page weekly-map">
      <section className="card wm-intro">
        <h2>주간 시장 지도 <span className="muted small">주봉 기준 · 매주 같은 자리에서 다시 본다</span></h2>
        <p className="small muted">
          검정선(20주선) · 빨간선(60주선) · 50주 볼린저밴드로 지금 위치와 다음 선을 보고, 그 선이 과거에 몇 번 맞았고 몇 번
          예외였는지를 함께 적었습니다. 예측이 아니라 가격이 과거에 멈춘 자리입니다.
        </p>
      </section>
      {failed && <p className="error small">주간 지도를 불러오지 못했습니다.</p>}
      {!maps && !failed && <p className="muted small">주봉을 받는 중…</p>}
      {maps?.map((m) => (isMap(m) ? <MapCard key={m.symbol} map={m} focused={m.symbol === focus} /> : <ErrorCard key={m.symbol} m={m} />))}
      {first && (
        <div className="wm-help">
          <section className="card">
            <h3>용어 풀이</h3>
            <dl className="wm-glossary small">
              {first.text.glossary.map((g) => (
                <div key={g.term}><dt>{g.term}</dt><dd className="muted">{g.text}</dd></div>
              ))}
            </dl>
          </section>
          <section className="card">
            <h3>사기 전에 — 계획 체크리스트</h3>
            <ol className="wm-checklist small">
              {first.text.checklist.map((q) => <li key={q}>{q}</li>)}
            </ol>
          </section>
        </div>
      )}
    </main>
  );
}
