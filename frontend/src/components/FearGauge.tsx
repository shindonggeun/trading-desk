import type { FearBand, FearItem } from "../types";

// #475 공포지수 반원 게이지: 왼쪽 = 공포(빨강) → 오른쪽 = 탐욕(초록), 5구간 띠 · 바늘 · 가운데 값.
// VIX 는 값이 클수록 공포라, 서버와 같은 경계(30 · 22 · 17 · 12)로 0~100 지수의 구간 위치에 맞춰 놓는다.

export const BANDS: FearBand[] = ["extreme_fear", "fear", "neutral", "greed", "extreme_greed"];
export const BAND_KO: Record<FearBand, string> = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
};
// 0~100 지수의 구간 경계(desk/fear.py INDEX_EDGES) — 게이지 위의 구간 폭도 이 비율
const STOPS = [0, 25, 45, 56, 76, 100];
// VIX 경계(desk/fear.py VIX_EDGES)를 같은 자리에 둔다: 50 이상은 맨 왼쪽, 8 이하는 맨 오른쪽
const VIX_STOPS = [50, 30, 22, 17, 12, 8];

function isVol(key: string): boolean {
  return key === "vix" || key === "vkospi";
}

/** 서버 band 와 같은 규칙 — 어제 · 지난주 값의 그때 구간 */
export function bandOf(key: string, v: number | null): FearBand | null {
  if (v == null) return null;
  if (isVol(key)) {
    if (v > 30) return "extreme_fear";
    if (v > 22) return "fear";
    if (v > 17) return "neutral";
    if (v > 12) return "greed";
    return "extreme_greed";
  }
  if (v < 25) return "extreme_fear";
  if (v < 45) return "fear";
  if (v < 56) return "neutral";
  if (v < 76) return "greed";
  return "extreme_greed";
}

/** 게이지 위 0(왼쪽)~1(오른쪽) 위치 */
export function position(key: string, v: number): number {
  const stops = isVol(key) ? VIX_STOPS : STOPS;
  for (let i = 0; i < stops.length - 1; i++) {
    const a = stops[i], b = stops[i + 1];
    const lo = Math.min(a, b), hi = Math.max(a, b);
    if (v >= lo && v <= hi) return (STOPS[i] + ((v - a) / (b - a)) * (STOPS[i + 1] - STOPS[i])) / 100;
  }
  // 범위 밖: 공포 쪽 끝 또는 탐욕 쪽 끝
  const fearSide = isVol(key) ? v > VIX_STOPS[0] : v < 0;
  return fearSide ? 0 : 1;
}

const CX = 60, CY = 54, R = 46, W = 10;
function pt(t: number, r = R): [number, number] {
  const a = Math.PI * (1 - t);
  return [CX + r * Math.cos(a), CY - r * Math.sin(a)];
}
function arc(t0: number, t1: number): string {
  const [x0, y0] = pt(t0), [x1, y1] = pt(t1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${R} ${R} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

function fmt(v: number): string {
  return v.toLocaleString("ko-KR", { maximumFractionDigits: Math.abs(v) < 100 ? 1 : 0 });
}

function Past({ label, keyName, v }: { label: string; keyName: string; v: number | null }) {
  const b = bandOf(keyName, v);
  return (
    <div className="fg-past">
      <span className="muted">{label}</span>
      {v == null ? <span className="muted">—</span> : (
        <span><strong>{fmt(v)}</strong> <span className={`fg-band fg-${b}`}>{b ? BAND_KO[b] : ""}</span></span>
      )}
    </div>
  );
}

export default function FearGauge({ item }: { item: FearItem }) {
  const empty = item.value == null;
  const band = item.band ?? bandOf(item.key, item.value);
  const t = empty ? null : position(item.key, item.value!);
  const edges = isVol(item.key) ? VIX_STOPS : STOPS;
  const why = item.key === "vkospi" ? "출처 없음" : "불러오지 못함";
  const label = empty ? `${item.name}: ${why}` : `${item.name} ${fmt(item.value!)} ${band ? BAND_KO[band] : ""}`;
  return (
    <div className={`fear-cell fear-gauge${empty ? " fear-empty" : ""}`}
      title={empty ? item.error ?? why : item.as_of ? `${item.source} · ${item.as_of}` : item.source}>
      <span className="fear-name">{item.name}</span>
      <div className="fg-body">
        <svg className="fg-svg" viewBox="0 0 120 92" role="img" aria-label={label}>
          {BANDS.map((b, i) => (
            <path key={b} d={arc(STOPS[i] / 100 + (i ? 0.006 : 0), STOPS[i + 1] / 100 - (i < 4 ? 0.006 : 0))}
              className={empty ? "fg-seg fg-off" : `fg-seg fg-${b}`} strokeWidth={W} fill="none" />
          ))}
          {/* 눈금은 양 끝 경계 두 개만(가운데 둘은 좁아 겹친다) */}
          {!empty && [1, 4].map((i) => {
            const [x, y] = pt(STOPS[i] / 100, R + W / 2 + 5);
            return <text key={i} x={x} y={y} className="fg-tick" textAnchor="middle">{edges[i]}</text>;
          })}
          {t != null && (() => {
            const [x, y] = pt(t, R - W / 2 - 2);
            return (
              <g className="fg-needle">
                <line x1={CX} y1={CY} x2={x} y2={y} />
                <circle cx={CX} cy={CY} r={3} />
              </g>
            );
          })()}
          <text x={CX} y={CY + 22} textAnchor="middle" className="fg-value">{empty ? "—" : fmt(item.value!)}</text>
          <text x={CX} y={CY + 35} textAnchor="middle" className={`fg-name fg-${empty ? "off" : band ?? "off"}`}>
            {empty ? why : band ? BAND_KO[band] : ""}
          </text>
        </svg>
        {!empty && (
          <div className="fg-side small">
            <Past label="어제" keyName={item.key} v={item.prev_day} />
            <Past label="지난주" keyName={item.key} v={item.prev_week} />
          </div>
        )}
      </div>
      {empty && item.error && <span className="sr-only">{item.error}</span>}
    </div>
  );
}
