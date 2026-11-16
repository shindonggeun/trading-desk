import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import { levelsContradict } from "../listLevels";
import type { Plan, SupportResistance } from "../types";

/**
 * #440 분석 결과 차트 옆 "가격 사다리": 현재가를 가운데 두고 위 · 아래 주요 가격을 가격순으로, 거리와 뜻을 함께.
 * 새 계산은 없다 — 지금 레벨(GET /api/levels, 없으면 분석 당시 plan.support_resistance) · 계획 값을 정리할 뿐.
 * 행을 고르면 onPick 으로 그 가격을 알려 차트가 같은 가격에 강조선을 긋는다.
 */

export type LadderKind = "resistance" | "support" | "entry" | "stop" | "target" | "long" | "short" | "poc" | "fib";

export const KIND_KO: Record<LadderKind, string> = {
  resistance: "저항", support: "지지", entry: "진입", stop: "손절", target: "목표",
  long: "롱 조건", short: "숏 조건", poc: "POC", fib: "피보나치",
};

export interface LadderTag {
  kind: LadderKind;
  /** 주요 = 반응 3번 이상이거나 역할이 바뀐 지지 · 저항, 계획 값 · POC 도 주요 */
  major: boolean;
  /** "반응 5/7" · "61.8%" 같은 짧은 근거 */
  note: string | null;
}

export interface LadderRow {
  price: number;
  /** 현재가 대비 % (현재가가 없으면 null) */
  pct: number | null;
  tags: LadderTag[];
}

/** 같은 가격으로 묶는 폭(±0.3%) */
const SAME = 0.003;
/** 반응 기록이 이만큼이면 주요(desk/sr_zones.MIN_TOUCHES 와 같게) */
const MIN_TOUCHES = 3;
/** 위 · 아래로 보일 지지 · 저항 최대 개수(가까운 순) */
const SR_EACH = 4;

/** 레벨 · 계획에서 사다리 행을 만든다 — 위(높은 가격)부터. */
export function ladderRows(sr: SupportResistance | null, plan: Plan | null, now: number | null): LadderRow[] {
  const raw: { price: number; tag: LadderTag }[] = [];
  const ref = now ?? sr?.reference_price ?? null;
  const levels = sr?.levels ?? [];
  const near = (kind: "support" | "resistance") => levels
    .filter((l) => l.kind === kind)
    .sort((a, b) => (ref == null ? 0 : Math.abs(a.price - ref) - Math.abs(b.price - ref)))
    .slice(0, SR_EACH);
  for (const l of [...near("resistance"), ...near("support")]) {
    const touched = l.touches != null && l.touches > 0;
    raw.push({ price: l.price, tag: {
      kind: l.kind,
      major: (l.touches ?? 0) >= MIN_TOUCHES || !!l.flipped,
      note: touched ? `반응 ${l.held ?? 0}/${l.touches}` : l.timeframe === "1w" ? "주봉" : null,
    } });
  }
  if (plan) {
    // #436 서로 모순인 등급상 타점은 사다리에도 넣지 않는다
    if (!levelsContradict(plan)) {
      const own: [number | null, LadderKind][] = [[plan.entry_price, "entry"], [plan.stop_loss, "stop"], [plan.price_target, "target"]];
      for (const [p, kind] of own) if (p != null) raw.push({ price: p, tag: { kind, major: true, note: null } });
    }
    if (plan.long_trigger != null) raw.push({ price: plan.long_trigger, tag: { kind: "long", major: true, note: "돌파 시" } });
    if (plan.short_trigger != null) raw.push({ price: plan.short_trigger, tag: { kind: "short", major: true, note: "이탈 시" } });
  }
  const poc = sr?.volume_profile?.poc;
  if (poc != null) raw.push({ price: poc, tag: { kind: "poc", major: true, note: "최다 거래" } });
  for (const f of sr?.fibonacci?.levels ?? []) {
    if (f.ratio <= 0 || f.ratio >= 1) continue;   // 0 · 100% 는 고점 · 저점 그 자체
    raw.push({ price: f.price, tag: { kind: "fib", major: false, note: `${(f.ratio * 100).toFixed(1)}%` } });
  }
  raw.sort((a, b) => b.price - a.price);
  const rows: LadderRow[] = [];
  // 현재가 위 · 아래에 걸친 두 가격은 묶지 않는다(아래 지지가 현재가 위 줄에 들어가지 않게)
  const above = (p: number) => ref != null && p >= ref;
  for (const r of raw) {
    const last = rows[rows.length - 1];
    if (last && above(last.price) === above(r.price) && Math.abs(last.price - r.price) / r.price <= SAME) {
      const same = last.tags.find((t) => t.kind === r.tag.kind);
      if (!same) last.tags.push(r.tag);
      else if (r.tag.major && !same.major) Object.assign(same, r.tag);   // 같은 종류면 주요 쪽 근거를 남긴다
      continue;
    }
    rows.push({ price: r.price, pct: ref ? (r.price / ref - 1) * 100 : null, tags: [r.tag] });
  }
  return rows;
}

const pctText = (v: number | null) => (v == null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`);
const isFloor = (t: LadderTag) => t.kind === "support" || t.kind === "poc";
const isCeiling = (t: LadderTag) => t.kind === "resistance" || t.kind === "poc";

/** "현재 8.29 — 바로 아래 지지 7.68(−7.4%), 위 저항 9.15(+10.4%), 손익비 1 : 2.1" */
export function ladderSummary(rows: LadderRow[], now: number | null, plan: Plan | null, label = "현재"): string | null {
  if (now == null) return null;
  const below = rows.find((r) => r.price < now && r.tags.some(isFloor));
  const above = [...rows].reverse().find((r) => r.price > now && r.tags.some(isCeiling));
  const parts = [`${label} ${price(now)}`];
  const say = (r: LadderRow | undefined, where: string, pick: (t: LadderTag) => boolean) =>
    // 지지 · 저항이 POC 와 같은 줄이면 지지 · 저항이라 부른다
    r ? `${where} ${KIND_KO[(r.tags.find((t) => t.kind !== "poc" && pick(t)) ?? r.tags.find(pick)!).kind]} ${price(r.price)}(${pctText(r.pct)})`
      : `${where} 표시할 레벨 없음`;
  const tail = [say(below, "바로 아래", isFloor), say(above, "위", isCeiling)];
  // 사다리에 보이는 진입 · 손절 · 목표(등급상 타점)의 손익비 — 모순이라 뺐으면 말하지 않는다
  const rr = plan && !levelsContradict(plan) ? plan.reward_risk : null;
  if (rr != null && rr > 0) tail.push(`손익비 1 : ${rr}`);
  return `${parts[0]} — ${tail.join(", ")}`;
}

const OPEN_KEY = "desk.ladder.open";

function loadOpen(): boolean {
  try {
    const v = localStorage.getItem(OPEN_KEY);
    if (v != null) return v === "1";
  } catch { /* 저장소를 못 쓰면 기본값 */ }
  // 세로 패널이 되는 폭(styles.css .chart-with-ladder 1100px)에서만 처음부터 펼친다
  return typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(min-width: 1100px)").matches : true;
}

export default function LevelLadder({ ticker, plan, onPick }: {
  ticker: string;
  plan: Plan | null;
  /** 차트에 강조할 가격(없으면 null) */
  onPick: (price: number | null) => void;
}) {
  const [open, setOpen] = useState(loadOpen);
  // 마우스를 올린 줄은 잠깐, 누른 줄은 다시 누를 때까지 — 휴대폰(탭)은 누르기만 쓴다
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const shown = hover ?? pinned;
  useEffect(() => onPick(shown), [shown, onPick]);
  const [nowSr, setNowSr] = useState<SupportResistance | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setNowSr(null);
    setFailed(false);
    api.levels(ticker).then((v) => live && setNowSr(v)).catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [ticker]);

  const saved = plan?.support_resistance?.levels?.length ? (plan.support_resistance as SupportResistance) : null;
  const sr = nowSr ?? (failed ? saved : null);
  // 지금 레벨을 못 받으면 기준가도 분석 당시 값 — 이름을 "분석 당시" 로 바꿔 지금 시세처럼 읽히지 않게
  const now = nowSr?.reference_price ?? sr?.reference_price ?? plan?.live_price?.price ?? plan?.last_close ?? null;
  const nowLabel = nowSr ? "현재가" : "분석 당시 가격";
  const loading = !nowSr && !failed;
  const rows = loading ? [] : ladderRows(sr, plan, now);
  const summary = loading ? null : ladderSummary(rows, now, plan, nowSr ? "현재" : "분석 당시");

  const toggle = (e: React.SyntheticEvent<HTMLDetailsElement>) => {
    const v = e.currentTarget.open;
    if (v === open) return;   // 처음 그릴 때 open 속성으로 오는 toggle 은 사용자가 고른 것이 아니다
    setOpen(v);
    try { localStorage.setItem(OPEN_KEY, v ? "1" : "0"); } catch { /* 기억 못 해도 동작은 같다 */ }
  };

  // 현재가 줄을 넣을 자리 — 현재가보다 낮은 첫 행 앞
  const cut = now == null ? -1 : rows.findIndex((r) => r.price < now);
  const nowRow = now != null && (
    <li key="now" className="ladder-now" aria-label={`${nowLabel} ${price(now)}`}>
      <span className="ladder-price num">{price(now)}</span>
      <span className="ladder-now-label">{nowLabel}{nowSr?.reference === "close" ? " (종가)" : ""}</span>
    </li>
  );

  return (
    <details className="card level-ladder" open={open} onToggle={toggle}>
      <summary>
        <span className="ladder-title">가격 사다리</span>
        {summary && <span className="ladder-summary small">{summary}</span>}
      </summary>
      {loading ? (
        <p className="muted small">레벨 불러오는 중…</p>
      ) : rows.length === 0 ? (
        <p className="muted small">레벨 없음 — 이 분석에는 지지 · 저항 · 계획 가격이 없습니다.</p>
      ) : (
        <>
          <ol className="ladder-list">
            {rows.map((r, i) => (
              <LadderItem key={`${r.price}-${i}`} row={r} on={shown === r.price} pinned={pinned === r.price}
                onHover={setHover} onPin={() => setPinned((p) => (p === r.price ? null : r.price))} before={i === cut ? nowRow : null} />
            ))}
            {cut === -1 && nowRow}
          </ol>
          <p className="muted small">
            {nowSr ? "지지 · 저항은 지금 기준(차트는 분석 당시 레벨일 수 있습니다)" : "지지 · 저항 · 기준가는 분석 당시 값"} · ±0.3% 안의 가격은 한 줄로 묶었습니다.
            줄을 누르면 차트에 표시되고, 다시 누르면 지웁니다.
          </p>
        </>
      )}
    </details>
  );
}

function LadderItem({ row, on, pinned, onHover, onPin, before }: {
  row: LadderRow;
  on: boolean;
  pinned: boolean;
  onHover: (price: number | null) => void;
  onPin: () => void;
  before: React.ReactNode;
}) {
  // 마우스만 hover 로 미리 보인다 — 터치의 가짜 mouseenter 로 고정이 풀리지 않게
  const mouse = (e: React.PointerEvent) => e.pointerType === "mouse";
  return (
    <>
      {before}
      <li className={on ? "ladder-on" : undefined}>
        <button type="button" className="ladder-row" aria-pressed={pinned}
          onPointerEnter={(e) => mouse(e) && onHover(row.price)} onPointerLeave={(e) => mouse(e) && onHover(null)}
          onClick={onPin}>
          <span className="ladder-price num">{price(row.price)}</span>
          <span className={`ladder-pct num ${row.pct == null ? "" : row.pct >= 0 ? "up" : "down"}`}>{pctText(row.pct)}</span>
          <span className="ladder-tags">
            {row.tags.map((t) => (
              <span key={t.kind} className={`ladder-tag lt-${t.kind} ${t.major ? "" : "lt-minor"}`}
                title={t.major ? "주요" : "보조"}>
                {KIND_KO[t.kind]}{t.note ? <span className="ladder-note"> {t.note}</span> : null}
              </span>
            ))}
          </span>
        </button>
      </li>
    </>
  );
}
