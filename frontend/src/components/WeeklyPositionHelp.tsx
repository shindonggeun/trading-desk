import { useId, useState } from "react";
import { WEEKLY_WORDS, type WeeklyZone } from "../weekly";

type Position = Exclude<WeeklyZone, "rebound" | "reclaimed_red">;
/** 선 순서가 뒤집힌(역배열) 때의 두 구간 — 그림에 점을 찍지 않고 목록 · 문장으로 말한다 */
const INVERTED: { pos: Exclude<WeeklyZone, Position>; when: string }[] = [
  { pos: "rebound", when: "긴 하락 뒤 검정선이 빨간선 아래로 내려와 있을 때" },
  { pos: "reclaimed_red", when: "역배열인 채 빨간선까지 되찾았을 때" },
];

const ZONES: { pos: Position; y: number }[] = [
  { pos: "above_black", y: 20 }, { pos: "between", y: 58 }, { pos: "below_red", y: 96 },
];

/**
 * #490 ⓘ — 주가 · 검정선 · 빨간선 세 줄과 세 위치를 그림 한 장으로. ``position`` 이 있으면 그 자리에 "지금" 점.
 * 숫자 없는 개념도라 어떤 지수에도 같다(실제 선은 지도 카드의 차트). "반등 시도" 는 선 순서가 뒤집힌 때라
 * 그림에 점을 찍지 않고 아래 목록 · 문장으로 말한다.
 */
export default function WeeklyPositionHelp({ position }: { position?: WeeklyZone }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const now = position ? ` 지금은 ${WEEKLY_WORDS[position].name}.` : "";
  return (
    <div className="wph">
      <button type="button" className="link small wph-toggle" aria-expanded={open} aria-controls={id}
        onClick={() => setOpen((v) => !v)}><span aria-hidden="true">ⓘ </span>위치 읽는 법</button>
      <div id={id} className="wph-body small" hidden={!open}>
        <svg className="wph-svg" viewBox="0 0 300 116" role="img"
          aria-label={`보통은 검정선(20주 평균)이 위, 빨간선(60주 평균)이 아래에 있고, 주가가 어디 있느냐로 세 구간을 나눈 그림.${now}`}>
          <line x1="8" y1="40" x2="292" y2="34" className="wph-black" />
          <line x1="8" y1="78" x2="292" y2="74" className="wph-red" />
          <text x="292" y="30" textAnchor="end" className="wph-label">검정선(20주 평균)</text>
          <text x="292" y="70" textAnchor="end" className="wph-label">빨간선(60주 평균)</text>
          {ZONES.map((z) => (
            <g key={z.pos} className={`wph-zone wph-${z.pos}${position === z.pos ? " wph-now" : ""}`}>
              <circle cx="24" cy={z.y} r={position === z.pos ? 6 : 4} />
              <text x="36" y={z.y + 4}>{WEEKLY_WORDS[z.pos].name}{position === z.pos ? " ← 지금" : ""}</text>
            </g>
          ))}
        </svg>
        <ul className="wph-list">
          {ZONES.map((z) => (
            <li key={z.pos}><strong className={`wm-pos-${z.pos}`}>{WEEKLY_WORDS[z.pos].name}</strong>
              {" "}({WEEKLY_WORDS[z.pos].where}) — {WEEKLY_WORDS[z.pos].todo}</li>
          ))}
          {INVERTED.map((z) => (
            <li key={z.pos}><strong className={`wm-pos-${z.pos}`}>{WEEKLY_WORDS[z.pos].name}</strong>
              {" "}({z.when}, {WEEKLY_WORDS[z.pos].where})
              {position === z.pos && <strong> ← 지금</strong>} — {WEEKLY_WORDS[z.pos].todo}</li>
          ))}
        </ul>
        <p className="muted">선은 주봉 종가로 본다. 하루 이틀 넘었다 돌아오는 것은 아직 바뀐 것이 아니다.</p>
      </div>
    </div>
  );
}
