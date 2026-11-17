import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { WeeklyMap, WeeklyMapError } from "../types";
import { nextLines, weeklyLink, weeklyWords, weeklyZone } from "../weekly";
import WeeklyPositionHelp from "./WeeklyPositionHelp";

/**
 * #227 대시보드의 주간 지도(#202) 한 줄 요약 — 지수마다 위치 · 이번 주봉 · 다음 선 ↓↑.
 * #490 위치는 쉬운 이름(상승 흐름 유지 · 쉬어 가는 구간 · 긴 하락 흐름)과 지금 할 일 한 줄, ↓↑ 는
 * "아래 받침까지 · 위 저항까지" 로 무엇까지의 거리인지 붙인다(그 선 이름은 툴팁).
 * 줄을 누르면 그 지수의 지도 카드(#205)로. 한 지수 실패는 그 줄만, 전체 실패는 카드 안 한 줄.
 */

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
        {maps.map((m) => {
          if (!isMap(m)) {
            return (
              <li key={m.symbol}>
                <span className="wmb-row wmb-error">
                  <span className="wmb-name">{m.name}</span>
                  <span className="muted small">불러오지 못함</span>
                </span>
              </li>
            );
          }
          const w = weeklyWords(m);
          const lines = nextLines(m);
          return (
            <li key={m.symbol}>
              <a className={`wmb-row${m.cross ? " wmb-cross" : ""}`} href={weeklyLink(m.symbol)}
                aria-label={`${m.name} — ${w.name}(${w.where}). ${w.todo} ${m.candle.label}, 아래 받침 ${lines.down.text}, 위 저항 ${lines.up.text}${m.cross === "broke_black" ? ", 이번 주 검정선 이탈" : m.cross === "reclaimed_black" ? ", 이번 주 검정선 되찾음" : ""}`}>
                <span className="wmb-name">{m.name}</span>
                <span className={`wm-pos wm-pos-${weeklyZone(m)}`} title={`${w.where} — ${w.meaning}`}>{w.name}</span>
                <span className="wmb-candle muted small">
                  {m.candle.label}
                  {m.cross === "broke_black" && <strong className="down"> · 이번 주 검정선 이탈</strong>}
                  {m.cross === "reclaimed_black" && <strong className="up"> · 이번 주 검정선 되찾음</strong>}
                </span>
                <span className="wmb-lines num small">
                  <span className="down" title={`아래 받침 — ${lines.down.what} ${price(lines.down.price)}`}>↓ {lines.down.text}</span>
                  <span className="up" title={`위 저항 — ${lines.up.what} ${price(lines.up.price)}`}>↑ {lines.up.text}</span>
                </span>
                <span className="wmb-todo small">{w.todo}</span>
              </a>
            </li>
          );
        })}
      </ul>
      <p className="muted small wmb-foot">
        {week ? `${week.week} 주봉 기준${week.week_complete ? "" : "(마감 전)"} · ` : ""}
        <a href="#/weekly">주간 지도 전체 보기 →</a>
      </p>
      <WeeklyPositionHelp />
    </div>
  );
}
