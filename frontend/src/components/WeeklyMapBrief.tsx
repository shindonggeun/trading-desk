import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { WeeklyMap, WeeklyMapError } from "../types";
import { weeklyLink } from "../weekly";

/**
 * #227 대시보드의 주간 지도(#202) 한 줄 요약 — 지수마다 위치 · 이번 주봉 · 다음 선 ↓↑.
 * 줄을 누르면 그 지수의 지도 카드(#205)로. 한 지수 실패는 그 줄만, 전체 실패는 카드 안 한 줄.
 */

const POSITION: Record<WeeklyMap["position"], string> = {
  above_black: "검정선 위", between: "선 사이", below_red: "빨간선 아래",
};
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const isMap = (m: WeeklyMap | WeeklyMapError): m is WeeklyMap => !("error" in m);

export default function WeeklyMapBrief() {
  const [maps, setMaps] = useState<(WeeklyMap | WeeklyMapError)[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    api.weeklyMaps().then((r) => alive && setMaps(r.maps), () => alive && setFailed(true));
    return () => { alive = false; };
  }, []);

  if (failed) return <p className="muted small">주간 지도를 불러오지 못했습니다.</p>;
  if (!maps) return <p className="muted small">주봉을 받는 중…</p>;
  const week = maps.find(isMap);
  return (
    <div className="wmb">
      <ul className="wmb-list">
        {maps.map((m) => (
          <li key={m.symbol}>
            {isMap(m) ? (
              <a className={`wmb-row${m.cross ? " wmb-cross" : ""}`} href={weeklyLink(m.symbol)}
                aria-label={`${m.name} — ${POSITION[m.position]}, ${m.candle.label}, 다음 선 아래 ${pct(m.scenario.down.pct)} 위 ${pct(m.scenario.up.pct)}${m.cross === "broke_black" ? ", 이번 주 검정선 이탈" : m.cross === "reclaimed_black" ? ", 이번 주 검정선 되찾음" : ""}`}>
                <span className="wmb-name">{m.name}</span>
                <span className={`wm-pos wm-pos-${m.position}`}>{POSITION[m.position]}</span>
                <span className="wmb-candle muted small">
                  {m.candle.label}
                  {m.cross === "broke_black" && <strong className="down"> · 이번 주 검정선 이탈</strong>}
                  {m.cross === "reclaimed_black" && <strong className="up"> · 이번 주 검정선 되찾음</strong>}
                </span>
                <span className="wmb-lines num small">
                  <span className="down">↓ {price(m.scenario.down.price)} {pct(m.scenario.down.pct)}</span>
                  <span className="up">↑ {price(m.scenario.up.price)} {pct(m.scenario.up.pct)}</span>
                </span>
              </a>
            ) : (
              <span className="wmb-row wmb-error">
                <span className="wmb-name">{m.name}</span>
                <span className="muted small">불러오지 못함</span>
              </span>
            )}
          </li>
        ))}
      </ul>
      <p className="muted small wmb-foot">
        {week ? `${week.week} 주봉 기준${week.week_complete ? "" : "(마감 전)"} · ` : ""}
        <a href="#/weekly">주간 지도 전체 보기 →</a>
      </p>
    </div>
  );
}
