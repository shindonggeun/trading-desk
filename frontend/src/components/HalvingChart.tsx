import { useLayoutEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { cycleName, pct } from "../halving";
import type { Halving, HalvingSeries } from "../types";

/**
 * #515 반감기 사이클 겹쳐 그리기(SVG) — 가로는 반감기(또는 고점) 뒤 일수, 세로는 그날 가격 ÷ 기준 가격(로그 척도).
 * 지금 사이클은 굵게, 오늘 점 · 지난 사이클 바닥이 나온 일수 범위는 옅은 띠. 색은 사이클마다 고정(지금 = 1번).
 * 색만으로 구분하지 않게 범례 · 선 끝 이름표를 같이 두고, 가리키면 그 일수의 사이클별 값을 보인다.
 */
export type HalvingMode = "halving" | "peak";

const M = { l: 48, r: 54, t: 24, b: 30 };

function niceLogTicks(lo: number, hi: number): number[] {
  const out: number[] = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
    for (const m of [1, 2, 5]) {
      const v = m * 10 ** e;
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  return out.length > 7 ? out.filter((v) => Math.abs(Math.log10(v) % 1) < 1e-9) : out;
}
const fmtRatio = (v: number) => `×${v >= 10 ? Math.round(v) : v >= 1 ? +v.toFixed(1) : +v.toPrecision(2)}`;

const DAY_MS = 86_400_000;
const daysBetween = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / DAY_MS);
/**
 * 반감기 0일 그래프의 x 는 "반감기 뒤 일수"(계약). 자료가 사이클 중간부터인 사이클(2012)은 비율 기준만 base_date 로 바뀐다.
 * 서버가 x 를 base_date 기준(0부터)으로 주면 반감기 기준으로 옮긴다 — 바닥 범위 띠 · 표의 일수와 같은 자리에 오게.
 */
function alignToHalving(s: HalvingSeries): HalvingSeries {
  if (!s.base_date || s.base_date === s.halving || !s.points.length || s.points[0][0] !== 0) return s;
  const off = daysBetween(s.halving, s.base_date);
  return off > 0 ? { ...s, points: s.points.map(([d, v]) => [d + off, v] as [number, number]) } : s;
}

function nearest(points: [number, number][], day: number): [number, number] | null {
  let best: [number, number] | null = null;
  for (const p of points) if (!best || Math.abs(p[0] - day) < Math.abs(best[0] - day)) best = p;
  return best && Math.abs(best[0] - day) <= 10 ? best : null;
}

export default function HalvingChart({ data, mode }: { data: Halving; mode: HalvingMode }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);   // 가리킨 일수
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    setWidth(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const cur = data.current;
  const list: HalvingSeries[] = (mode === "halving" ? data.series.by_halving.map(alignToHalving) : data.series.by_peak).filter((s) => s.points.length > 0);
  // 색은 사이클에 붙는다(최근 → 오래된 순, 지금 = 1번) — 모드를 바꿔도 같은 사이클은 같은 색
  const slot = useMemo(() => {
    const all = [...new Set([...data.series.by_halving, ...data.series.by_peak].map((s) => s.halving))].sort().reverse();
    return (h: string) => Math.min(4, all.indexOf(h) + 1);
  }, [data]);
  const ends = Object.fromEntries(data.cycles.map((c) => [c.halving, c.end]));
  const curH = cur?.halving ?? null;   // 서버가 지금 사이클을 못 내면 null — 오늘 점 · 강조 없이 지난 사이클만
  const name = (h: string) => cycleName(h, h === curH ? null : ends[h]);

  const height = width < 560 ? 260 : 340;
  const iw = Math.max(10, width - M.l - M.r);
  const ih = height - M.t - M.b;
  const xs = list.flatMap((s) => s.points.map((p) => p[0]));
  const ys = list.flatMap((s) => s.points.map((p) => p[1])).filter((v) => v > 0);
  if (!xs.length || !ys.length) return <div ref={wrap} className="hv-chart"><p className="muted small">그릴 자료가 없습니다.</p></div>;
  const x0 = Math.min(...xs, mode === "peak" ? 0 : Infinity);
  const x1 = Math.max(...xs);
  const ly0 = Math.log10(Math.min(...ys)) - 0.06;
  const ly1 = Math.log10(Math.max(...ys)) + 0.06;
  const X = (d: number) => M.l + ((d - x0) / (x1 - x0 || 1)) * iw;
  const Y = (v: number) => M.t + (1 - (Math.log10(v) - ly0) / (ly1 - ly0 || 1)) * ih;
  const step = iw < 420 ? 365 : 180;
  const xTicks: number[] = [];
  for (let d = Math.ceil(x0 / step) * step; d <= x1; d += step) xTicks.push(d);
  const yTicks = niceLogTicks(10 ** ly0, 10 ** ly1);

  const band = mode === "halving" ? data.ranges.bottom_days_from_halving : data.ranges.bottom_days_from_peak;
  // 띠 이름표 — 오른쪽으로 넘치면 띠 오른쪽 끝에 맞추고, 좁으면 "(고점 뒤)" 를 뺀다
  const bandLabel = !band ? "" : `과거 바닥 ${band.min === band.max ? band.min : `${band.min}~${band.max}`}일${mode === "peak" && iw >= 360 ? "(고점 뒤)" : ""}`;
  const bandLabelW = bandLabel.length * 6.6;
  const bandLabelAt = !band ? {} : X(band.min) + 4 + bandLabelW <= M.l + iw
    ? { x: X(band.min) + 4 } : { x: Math.max(M.l + bandLabelW, Math.min(M.l + iw, X(band.max))), textAnchor: "end" as const };
  const today = !cur ? null : mode === "halving" ? cur.days_since_halving : cur.days_since_high;
  const curSeries = curH ? list.find((s) => s.halving === curH) : undefined;
  const lastCur = curSeries?.points[curSeries.points.length - 1] ?? null;

  // 선 끝 이름표 — 세로로 겹치지 않게 14px 씩 민다
  const labels = list.map((s) => {
    const last = s.points[s.points.length - 1];
    return { h: s.halving, x: X(last[0]), y: Y(last[1]) };
  }).sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 14) labels[i].y = labels[i - 1].y + 14;
  labels.forEach((l) => { l.y = Math.min(M.t + ih, Math.max(M.t + 4, l.y)); });
  for (let i = labels.length - 2; i >= 0; i--) if (labels[i + 1].y - labels[i].y < 14) labels[i].y = labels[i + 1].y - 14;   // 아래 끝에 몰린 이름표는 위로 다시 민다

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    if (px < M.l || px > M.l + iw) { setHover(null); return; }
    setHover(Math.round(x0 + ((px - M.l) / iw) * (x1 - x0)));
  };
  const hoverRows = hover == null ? [] : list
    .map((s) => ({ s, p: nearest(s.points, hover) }))
    .filter((r): r is { s: HalvingSeries; p: [number, number] } => !!r.p)
    .sort((a, b) => slot(a.s.halving) - slot(b.s.halving));
  // 가리킨 곳 오른쪽(넘치면 왼쪽)에, 그래프 밖으로 나가지 않게
  const TIP_W = 200;
  const hx = hover == null ? 0 : X(hover);
  const tipLeft = Math.max(0, Math.min(width - TIP_W, hx + 12 + TIP_W > width ? hx - 12 - TIP_W : hx + 12));
  const zeroName = mode === "halving" ? "반감기 날 가격" : "그 사이클 고점";

  return (
    <div ref={wrap} className="hv-chart">
      {width > 0 && (
        <svg width={width} height={height} role="img" onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)}
          aria-label={`사이클 ${list.length}개를 ${mode === "halving" ? "반감기" : "고점"} 0일 기준으로 겹친 그래프. 세로는 ${zeroName} 대비 배수(로그). 숫자는 아래 표에 있습니다.`}>
          {/* 지난 사이클 바닥 일수 범위 */}
          {band && band.cycles.length > 0 && (
            <g className="hv-band">
              <rect x={X(band.min)} y={M.t} width={Math.max(3, X(band.max) - X(band.min))} height={ih} />
              <text {...bandLabelAt} y={M.t - 8}>{bandLabel}</text>
            </g>
          )}
          {/* 눈금 */}
          <g className="hv-grid">
            {yTicks.map((v) => (
              <g key={v}>
                <line x1={M.l} x2={M.l + iw} y1={Y(v)} y2={Y(v)} className={v === 1 ? "hv-one" : undefined} />
                <text x={M.l - 6} y={Y(v) + 4} textAnchor="end">{fmtRatio(v)}</text>
              </g>
            ))}
            {xTicks.map((d) => (
              <text key={d} x={X(d)} y={M.t + ih + 18} textAnchor="middle">{d === 0 ? (mode === "peak" ? "고점" : "반감기") : d < 0 ? `−${-d}일` : `${d}일`}</text>
            ))}
            {mode === "peak" && <line x1={X(0)} x2={X(0)} y1={M.t} y2={M.t + ih} className="hv-zero" />}
          </g>
          {/* 사이클 선(지금 사이클을 맨 위에) */}
          {[...list].sort((a, b) => slot(b.halving) - slot(a.halving)).map((s) => (
            <path key={s.halving} className={`hv-line hv-c${slot(s.halving)}${s.halving === curH ? " hv-now" : ""}`}
              d={s.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("")} />
          ))}
          {/* 오늘 */}
          {lastCur && today != null && curH && (
            <g className="hv-today">
              <line x1={X(today)} x2={X(today)} y1={M.t} y2={M.t + ih} />
              <circle cx={X(lastCur[0])} cy={Y(lastCur[1])} r={5} className={`hv-c${slot(curH)}`} />
            </g>
          )}
          {/* 선 끝 이름표 */}
          {labels.map((l) => (
            <text key={l.h} className="hv-label" x={Math.min(l.x + 6, M.l + iw + 4)} y={l.y + 4}>{l.h.slice(0, 4)}</text>
          ))}
          {/* 가리킨 일수 */}
          {hover != null && <line className="hv-cross" x1={X(hover)} x2={X(hover)} y1={M.t} y2={M.t + ih} />}
          {hoverRows.map(({ s, p }) => <circle key={s.halving} className={`hv-dot hv-c${slot(s.halving)}`} cx={X(p[0])} cy={Y(p[1])} r={4} />)}
        </svg>
      )}
      {hover != null && hoverRows.length > 0 && (
        <div className="hv-tip" aria-hidden="true" style={{ left: tipLeft }}>
          <strong>{mode === "halving" ? `반감기 뒤 ${hover}일` : hover === 0 ? "고점" : hover < 0 ? `고점 전 ${-hover}일` : `고점 뒤 ${hover}일`}</strong>
          {hoverRows.map(({ s, p }) => (
            <span key={s.halving}><i className={`hv-sw hv-c${slot(s.halving)}`} aria-hidden="true" />{name(s.halving)} <b>{fmtRatio(p[1])}</b> <span className="muted">({pct((p[1] - 1) * 100)}{mode === "halving" && s.base_date && s.base_date !== s.halving ? " · 자료 첫날 기준" : ""})</span></span>
          ))}
        </div>
      )}
      <ul className="hv-legend" aria-label="사이클">
        {[...list].sort((a, b) => slot(a.halving) - slot(b.halving)).map((s) => (
          <li key={s.halving}><i className={`hv-sw hv-c${slot(s.halving)}${s.halving === curH ? " hv-sw-now" : ""}`} aria-hidden="true" />
            {name(s.halving)}{mode === "halving" && s.base_date && s.base_date !== s.halving ? <span className="muted small"> · {s.base_date} 부터 자료</span> : null}
          </li>
        ))}
        <li><i className="hv-sw hv-sw-band" aria-hidden="true" />과거 바닥 일수 범위</li>
      </ul>
      <p className="muted small hv-axis-note">세로: {zeroName} = ×1 로 본 배수(로그 척도){mode === "halving" && data.series.by_halving.some((s) => s.base_date && s.base_date !== s.halving) ? " · 자료가 반감기 뒤부터인 사이클은 자료 첫날 가격 = ×1" : ""}</p>
    </div>
  );
}
