import { useEffect, useRef, useState } from "react";
import { price } from "../format";
import type { LineAnchor, LineBasis, Plan, PlanLineKey, SupportResistance } from "../types";

// #466 차트 옆(휴대폰은 아래 접기) 초보자용 해설: 지금 가격이 어디쯤이고 위 · 아래로 가면 무엇을 하는지,
// 익절 · 손절 자리의 이유, 안 메운 갭, 계획선마다 시나리오 · 근거, 용어 풀이. 문장은 #465 /api/levels?run= 이 준다.

const WIDE_Q = "(min-width: 1100px)";

/** 선 라벨에 붙일 근거 한 단어 — 갭 메움 · 피보 0.382 · 주봉 저항 · 추세선 */
export function anchorWord(a: LineAnchor | undefined): string | null {
  if (!a) return null;
  if (a.kind === "gap") return "갭 메움";
  if (a.kind === "trendline") return "추세선";
  if (a.kind === "fib") {
    const m = /피보나치\s*([\d.]+)/.exec(a.text);
    return m ? `피보 ${m[1]}` : "피보나치";
  }
  const head = a.text.split(" 구역")[0];
  return head && head.length <= 8 ? head : "지지 · 저항";
}

/** 선 제목: "롱 진입 · 갭 메움", 관망 계획은 "관망·숏 목표 · 주봉 지지", 조건선은 "롱 조건 · 피보 0.382" */
export function lineTitle(b: LineBasis): string {
  const word = anchorWord(b.anchors[0]);
  const trigger = b.line === "long_trigger" || b.line === "short_trigger";
  const scen = trigger ? "" : b.scenario_ko ? `${b.hold_plan ? "관망·" : ""}${b.scenario_ko} ` : b.hold_plan ? "관망 " : "";
  return `${scen}${b.label}${word ? ` · ${word}` : ""}`;
}

const SCEN_CLASS = (b: LineBasis) => (b.scenario === "long" ? "ex-long" : b.scenario === "short" ? "ex-short" : "ex-none");

const TERMS: [string, string][] = [
  ["지지 · 저항 구역", "과거에 여러 번 멈추거나 되돌아선 가격대. 한 가격선이 아니라 폭이 있는 띠로 본다."],
  ["갭 · 메움", "전날(전주) 봉과 이어지지 않고 건너뛴 가격 구간. 나중에 그 구간으로 다시 돌아오는 것을 '메운다'고 한다."],
  ["피보나치 되돌림", "큰 오름 · 내림 폭의 0.382 · 0.5 · 0.618 지점. 되돌림이 자주 쉬어 가는 자리로 쓴다."],
  ["진입 · 손절 · 목표", "들어갈 가격, 틀렸다고 보고 나올 가격, 이익을 챙길 가격. 미리 정해 두고 지킨다."],
  ["롱 조건 · 숏 조건", "관망 계획에서 이 가격을 넘거나(롱) 깨면(숏 · 비중 축소) 그때 다시 검토한다는 기준."],
  ["관망 계획의 시나리오 값", "관망(Hold) 계획에도 진입 · 손절 · 목표가 적혀 있을 수 있다. 지금 할 일이 아니라 그 방향으로 갈 때의 참고 숫자다."],
];

/**
 * 해설 문장 나누기(#465 desk/line_basis.explain): 위 구역 · 아래 구역 문장은 그 구역이 있을 때만 오고 "지금가" 문장만 늘 온다.
 * 계획선 문장은 맨 뒤 basis 개수만큼 — 앞부분은 시작말(위로는 · 지금가 · 아래로는)로 가르고 남는 하나가 갭 문장.
 */
export function splitExplain(explain: string[], lines: number) {
  const head = explain.slice(0, Math.max(0, explain.length - lines));
  const pick = (prefix: string) => head.find((s) => s.startsWith(prefix)) ?? null;
  const above = pick("위로는 ");
  const at = pick("지금가 ");
  const below = pick("아래로는 ");
  const gap = head.find((s) => s !== above && s !== at && s !== below) ?? null;
  return { above, at, below, gap };
}

function nowLine(plan: Plan | null, entry: LineBasis | undefined): string | null {
  if (!plan) return null;
  if (plan.bias === "flat") {
    const parts = [
      plan.long_trigger != null ? `${price(plan.long_trigger)} 위로 회복 · 돌파하면 롱을 검토` : null,
      plan.short_trigger != null ? `${price(plan.short_trigger)} 아래로 이탈하면 숏 · 비중 축소를 검토` : null,
    ].filter(Boolean);
    return `지금은 관망 — ${parts.length ? parts.join(", ") : "새 신호가 나올 때까지 기다린다"}.`;
  }
  const side = plan.bias === "short" ? "숏" : "롱";
  // 등급 방향과 가격 배치(계획선 시나리오)가 어긋나면 알린다 — 칩은 가격 배치를 따른다
  const conflict = entry?.scenario_ko && entry.scenario_ko !== side
    ? ` 단, 진입 · 손절 · 목표의 배치는 ${entry.scenario_ko} 시나리오라 등급(${side})과 어긋난다 — 계획 카드를 확인한다.` : "";
  const bits = [
    plan.entry_price != null ? `진입 ${price(plan.entry_price)}` : null,
    plan.stop_loss != null ? `손절 ${price(plan.stop_loss)}` : null,
    plan.price_target != null ? `목표 ${price(plan.price_target)}` : null,
  ].filter(Boolean);
  return `${side} 계획 — ${bits.join(" · ") || "가격 없음"}. 진입 전이면 그 가격까지 기다린다.${conflict}`;
}

export default function ExplainPanel({ data, failed, plan, focus, onFocusDone }: {
  data: SupportResistance | null;
  failed: boolean;
  plan: Plan | null;
  /** 차트에서 누른 계획선 — 그 근거를 펼치고 보이게 한다 */
  focus: PlanLineKey | null;
  onFocusDone: () => void;
}) {
  const [wide, setWide] = useState(() => window.matchMedia?.(WIDE_Q).matches !== false);
  useEffect(() => {
    const m = window.matchMedia?.(WIDE_Q);
    if (!m) return;
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  const panel = useRef<HTMLDetailsElement>(null);
  const rows = useRef<Partial<Record<PlanLineKey, HTMLDetailsElement | null>>>({});
  useEffect(() => {
    if (!focus) return;
    if (panel.current) panel.current.open = true;
    const el = rows.current[focus];
    if (el) {
      el.open = true;
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    onFocusDone();
  }, [focus, onFocusDone]);

  if (failed) return <p className="muted small explain-failed">차트 해설을 불러오지 못했습니다.</p>;
  if (!data) return <p className="muted small explain-failed">차트 해설 불러오는 중…</p>;
  const explain = data.explain;
  if (!explain || explain.length === 0) return null; // 해설을 주지 않는 예전 서버
  const basis = data.basis ?? [];
  const byLine = (k: PlanLineKey) => basis.find((b) => b.line === k);
  const { above, at, below, gap: gapSentence } = splitExplain(explain, basis.length);
  const target = byLine("price_target");
  const stop = byLine("stop_loss");
  const longT = byLine("long_trigger");
  const shortT = byLine("short_trigger");
  const gaps = data.gaps ?? [];
  const todo = nowLine(plan, byLine("entry_price"));

  return (
    <details className="explain-panel card" ref={panel} open={wide} aria-label="차트 해설">
      <summary><strong>차트 해설</strong> <span className="muted small">지금 가격 · 할 일 · 근거</span></summary>
      {todo && <p className="explain-todo">{todo}</p>}
      <section>
        <h4>지금 가격 위치</h4>
        {at && <p>{at}</p>}
      </section>
      <section>
        <h4>위로 가면</h4>
        <p>{above ?? "위쪽 가까이에 저항 구역이 없다(고점 근처) — 막힐 자리를 미리 정하기 어렵다."}</p>
        {longT && <p className="ex-long-text">{longT.text}</p>}
      </section>
      <section>
        <h4>아래로 가면</h4>
        <p>{below ?? "아래쪽 가까이에 지지 구역이 없다(저점 근처) — 받쳐 줄 자리를 미리 정하기 어렵다."}</p>
        {shortT && <p className="ex-short-text">{shortT.text}</p>}
      </section>
      {(target || stop) && (
        <section>
          <h4>익절 · 손절 자리</h4>
          {target && <p>{target.text}</p>}
          {stop && <p>{stop.text}</p>}
        </section>
      )}
      {(gapSentence || gaps.length > 0 || data.gaps_error) && (
        <section>
          <h4>안 메운 갭</h4>
          {gapSentence && <p>{gapSentence}</p>}
          {gaps.length > 0 && (
            <ul className="explain-gaps small">
              {gaps.slice(0, 3).map((g) => <li key={`${g.timeframe}-${g.formed_at}-${g.fill_price}`}>{g.text}</li>)}
            </ul>
          )}
          {data.gaps_error && <p className="muted small">{data.gaps_error}</p>}
        </section>
      )}
      {basis.length > 0 && (
        <section>
          <h4>계획선 근거 <span className="muted small">(차트의 선을 눌러도 펼쳐진다)</span></h4>
          {basis.map((b) => (
            <details key={b.line} className="explain-line" ref={(el) => { rows.current[b.line] = el; }}>
              <summary>
                {/* 조건선은 이름에 롱 · 숏이 들어 있어 칩을 생략 */}
                {b.line !== "long_trigger" && b.line !== "short_trigger" && <span className={`ex-chip ${SCEN_CLASS(b)}`}>{b.scenario_ko ?? "방향 미정"}</span>}
                {b.label}{anchorWord(b.anchors[0]) ? ` · ${anchorWord(b.anchors[0])}` : ""} <span className="muted small">{price(b.price)} {b.from_now_pct != null && `(${b.from_now_pct > 0 ? "+" : ""}${b.from_now_pct.toFixed(1)}%)`}</span>
                {b.hold_plan && <span className="ex-hold small">{b.scenario_ko ? `관망 계획의 ${b.scenario_ko} 시나리오 값` : "관망 계획의 참고 값(방향 미정)"}</span>}
              </summary>
              <p className="small">{b.text}</p>
              {b.anchors.length > 0 ? (
                <ul className="small muted">{b.anchors.map((a, i) => <li key={i}>{a.text}</li>)}</ul>
              ) : (
                <p className="small muted">맞는 지지 · 저항 · 피보나치 · 추세선 · 갭이 없다 — 분석이 고른 숫자다.</p>
              )}
            </details>
          ))}
        </section>
      )}
      <details className="explain-terms">
        <summary className="small">용어 풀이</summary>
        <dl className="small">
          {TERMS.map(([t, d]) => (<div key={t}><dt>{t}</dt><dd>{d}</dd></div>))}
        </dl>
      </details>
    </details>
  );
}
