import type { ReactNode } from "react";
import { price } from "../format";
import { beginnerGuide } from "../chartBeginner";
import { GLOSSARY } from "../glossary";
import { distText, headline, MARKS, READ_NOTE, type ReadMark, refKinds, watchOf, whyOf } from "../chartRead";
import { LINE_TFS, TF_KO } from "../channelLines";
import type { AccumulationViewKey, ChartRead, ChartReadFrame, ChartReadLevel, LineTf } from "../types";

/** 이 칸에 나온 말만 짧게 푼다 — 용어집(#24)에 있으면 그 뜻, 없으면 여기 한 줄 */
const OWN_TERMS: Record<string, { term: string; desc: string }> = {
  channel: { term: "평행 채널", desc: "고점끼리 · 저점끼리 이은 두 평행선 사이의 길이에요. 위 끝에선 막히고 아래 끝에선 받쳐 주기 쉬워요." },
  trendline: { term: "추세선 · 이탈", desc: "저점(또는 고점)을 이은 선이에요. 종가가 선 반대편에서 마감하면 '이탈' — 흐름이 바뀌었을 수 있다는 뜻이에요." },
  gap: { term: "갭 메움", desc: "봉 사이에 거래 없이 건너뛴 가격대예요. 나중에 그 가격까지 되돌아가 채우는 일이 잦아요." },
  fib: { term: "피보나치 되돌림", desc: "최근 큰 움직임의 0.382 · 0.5 · 0.618 만큼 되돌린 자리예요. 쉬어 가는 곳으로 자주 봐요." },
  pattern: { term: "삼각수렴 · 쐐기", desc: "고점은 낮아지고 저점은 높아지며(또는 한쪽으로 기울며) 좁혀지는 모양이에요. 벗어나는 쪽으로 크게 움직이기 쉬워요." },
};
const glossaryDesc = (id: string) => GLOSSARY.find((g) => g.id === id);

function Level({ m, idPrefix, picked, onPick }: { m: ReadMark; idPrefix: string; picked: number | null; onPick: (n: number | null) => void }) {
  const on = picked === m.n;
  return (
    <li id={`${idPrefix}-${m.n}`}>
      <button type="button" className={`read-level read-${m.side}${on ? " on" : picked != null ? " dim" : ""}`} aria-pressed={on}
        onClick={() => onPick(on ? null : m.n)}>
        <span className={`read-num read-${m.side}`} aria-hidden>{MARKS[m.n]}</span>
        <span className="read-level-body">
          <span className="read-level-head"><b>{price(m.level.price)}</b> {m.level.name} <span className="muted">({distText(m.level)})</span></span>
          <span className="read-level-why">{whyOf(m.level, m.side)}</span>
        </span>
      </button>
    </li>
  );
}

const farText = (l: ChartReadLevel) => `${price(l.price)} ${l.name}(${distText(l)})`;

/**
 * #632 · #672 '지금 이 차트 읽기' 칸 — 차트 옆(넓은 화면) 또는 위(좁은 화면, 접힘).
 * 한 줄 요약 → 위에서 막힐 수 있는 곳 → 아래에서 받쳐 줄 수 있는 곳 → 조심할 때 / 볼 때 → 먼 레벨(접힘) → 예측 · 추천 아님.
 * 번호(①…)를 누르면 차트에서 그 선을 강조하고 나머지 선은 흐리게. 시간봉 탭은 차트 주기와 같이 움직인다.
 */
export default function ChartReadPanel({ read, failed, frame, interval, onInterval, marks, picked, onPick, beginner, applied, onBeginner, idPrefix, open, onToggle, accumulation, selectedAccumulation, weeklyDrawdown, dailyResistance }: {
  read: ChartRead | null;
  accumulation?: ReactNode;
  weeklyDrawdown?: ReactNode;
  dailyResistance?: ReactNode;
  selectedAccumulation: AccumulationViewKey;
  failed: boolean;
  frame: ChartReadFrame | null;
  interval: string;
  onInterval: (tf: LineTf) => void;
  marks: ReadMark[];
  picked: number | null;
  onPick: (n: number | null) => void;
  beginner: boolean;
  /** 초보 모드가 실제로 차트에 걸렸다(가까운 선이 있을 때만) */
  applied: boolean;
  onBeginner: (on: boolean) => void;
  idPrefix: string;
  open: boolean;
  onToggle: (open: boolean) => void;
}) {
  const tfKo = TF_KO[interval] ?? interval;
  const above = marks.filter((m) => m.side === "above");
  const below = marks.filter((m) => m.side === "below");
  const watch = frame ? watchOf(frame, tfKo) : null;
  const far = frame?.levels?.far;
  const kinds = refKinds(marks);
  const terms = [
    ...(kinds.has("channel") ? [OWN_TERMS.channel] : []),
    ...(kinds.has("trendline") ? [OWN_TERMS.trendline] : []),
    ...(kinds.has("pattern") ? [OWN_TERMS.pattern] : []),
    ...(kinds.has("gap") ? [OWN_TERMS.gap] : []),
    ...(kinds.has("fib") ? [OWN_TERMS.fib] : []),
    ...(["sr", "rsi"].map(glossaryDesc).filter((g) => !!g).map((g) => ({ term: g!.term, desc: g!.desc }))),
  ];
  const otherFrames = read?.timeframes.filter((f) => !f.error && f.timeframe !== interval && f.summary) ?? [];
  const guide = beginner && frame ? beginnerGuide(frame, read?.accumulation?.[selectedAccumulation]) : null;
  return (
    <details className="chart-read" open={open} onToggle={(e) => {
      if (e.target === e.currentTarget) onToggle(e.currentTarget.open);
    }}>
      <summary>
        <span className="chart-read-title">지금 이 차트 읽기</span>
        {frame && <span className="muted small"> · {tfKo}</span>}
      </summary>
      <div className="chart-read-body">
        <div className="chart-read-bar">
          <div className="chips" role="group" aria-label="설명 시간봉">
            {LINE_TFS.map((tf) => (
              <button key={tf} type="button" className={`chip chip-small${tf === interval ? " chip-on" : ""}`} aria-pressed={tf === interval}
                onClick={() => onInterval(tf)}>
                {TF_KO[tf].replace("봉", "")}
              </button>
            ))}
          </div>
          <label className="chart-read-beginner small" title="켜면 설명에 나온 선만 차트에 남깁니다">
            <input type="checkbox" checked={beginner} onChange={(e) => onBeginner(e.target.checked)} /> 초보 모드
          </label>
        </div>
        {!read && !failed && <p className="muted small">차트를 읽는 중…</p>}
        {failed && !read && <p className="muted small">차트를 읽지 못했어요. 잠시 뒤 다시 시도해요.</p>}
        {read && !weeklyDrawdown && !LINE_TFS.includes(interval as LineTf) && (
          <p className="muted small">이 주기는 설명이 없어요 — 5분 · 15분 · 1시간 · 4시간 · 일봉 중에서 골라 주세요.</p>
        )}
        {read && LINE_TFS.includes(interval as LineTf) && !frame && <p className="muted small">이 시간봉은 지금 읽지 못했어요.</p>}
        {guide && <section className="read-beginner-guide" aria-label="초보 모드 가격과 진입 조건">
          <h4>지금 가격 → 위에서 조심할 곳 → 진입 전에 볼 것</h4>
          <p>{guide.now} {guide.resistance}</p>
          <p><strong>이미 갖고 있다면</strong> {guide.holder}</p>
          <p><strong>새로 들어가려면</strong> {guide.entry}</p>
        </section>}
        {dailyResistance}
        {weeklyDrawdown}
        {accumulation}
        {frame && (
          <>
            <p className="chart-read-head">{headline(frame)}</p>
            {read?.bubble?.caution && (
              <p className="chart-read-caution" role="note">
                <strong>주봉 '{read.bubble.label}' — 진입 조심</strong> {read.bubble.explain}
              </p>
            )}
            {(frame.hs ?? []).map((p) => (
              <p key={p.kind} className="muted small">{p.explain}</p>
            ))}
            <h4>위에서 막힐 수 있는 곳</h4>
            {above.length ? <ol className="read-levels">{above.map((m) => <Level key={m.n} m={m} idPrefix={idPrefix} picked={picked} onPick={onPick} />)}</ol>
              : <p className="muted small">가까운 위 선이 없어요.</p>}
            <h4>아래에서 받쳐 줄 수 있는 곳</h4>
            {below.length ? <ol className="read-levels">{below.map((m) => <Level key={m.n} m={m} idPrefix={idPrefix} picked={picked} onPick={onPick} />)}</ol>
              : <p className="muted small">가까운 아래 선이 없어요.</p>}
            {watch && (watch.caution || watch.look) && (
              <div className="read-watch">
                {watch.caution && <p><span className="read-tag read-caution">조심할 때</span> {watch.caution}</p>}
                {watch.look && <p><span className="read-tag read-look">볼 때</span> {watch.look}</p>}
              </div>
            )}
            {read && read.conflicts.length > 0 && (
              <p className="small read-conflict">{read.conflicts.join(" ")}</p>
            )}
            {far && (far.above.length > 0 || far.below.length > 0) && (
              <details className="read-far small">
                <summary>먼 레벨 {far.above.length + far.below.length}개</summary>
                {far.above.length > 0 && <p>위: {far.above.map(farText).join(" · ")}</p>}
                {far.below.length > 0 && <p>아래: {far.below.map(farText).join(" · ")}</p>}
              </details>
            )}
            {otherFrames.length > 0 && (
              <details className="read-far small">
                <summary>다른 시간봉</summary>
                {otherFrames.map((f) => (
                  <p key={f.timeframe}>
                    <button type="button" className="link" onClick={() => onInterval(f.timeframe as LineTf)}>{TF_KO[f.timeframe] ?? f.timeframe}</button>{" "}
                    {(f.summary ?? "").replace(/^[^—]*—\s*/, "")}
                  </p>
                ))}
              </details>
            )}
            {terms.length > 0 && (
              <details className="read-far small">
                <summary>말 풀이</summary>
                <dl className="read-terms">
                  {terms.map((t) => (
                    <div key={t.term}><dt>{t.term}</dt><dd>{t.desc}</dd></div>
                  ))}
                </dl>
              </details>
            )}
            {applied && <p className="muted small">초보 모드 — 설명에 나온 선 · 이동평균 20 만 차트에 남겼어요. 끄면 전부 보여요.</p>}
          </>
        )}
        <p className="muted small read-note">{read?.note ?? READ_NOTE}</p>
      </div>
    </details>
  );
}
