import type { ReactNode } from "react";
import { price } from "../format";
import type { ExplainChart } from "../types";

/**
 * 초보자용 글(#493 해설 · #495 리포트 공통): 질문 → 번호 소제목(작은 가격 그림 · 그림 · 문장 · 형광 강조 · 💡 용어 상자)
 * → 단계 카드 → "이것만 확인해요" → 한마디. 데이터만 받아 그린다(불러오기는 부르는 쪽). #494 EasyView 에서 떼어 냈다(#496).
 */
export interface ArticleData {
  hook: string;
  sections: {
    title: string;
    body: string;
    highlight: string;
    term: { name: string; explain: string };
    chart: ExplainChart;
    image?: { url: string; alt: string } | null; // #497 리포트 그림 — 없으면(null) 그리지 않는다
  }[];
  stages: { label: string; low: number | null; high: number | null; why: string }[];
  checklist: { q: string; a: string }[];
  closing: string;
}

/** 소제목의 작은 가격 그림: 세로 가격 축에 선(진입 · 손절 · 목표 · 구역 경계)과 지금가 표시. 차트 라이브러리 없이 SVG. */
export function MiniLevels({ chart }: { chart: ExplainChart | null }) {
  if (!chart || chart.kind === "none" || !chart.levels) return null; // #681 그림 없는 절(일정 · 연결)
  const named = chart.levels.map((v, i) => ({ v, name: chart.labels?.[i] ?? "" })).filter((x) => x.v > 0);
  const levels = named.map((x) => x.v);
  const marks = (chart.marks ?? []).filter((v) => v > 0);
  const all = [...levels, ...marks];
  if (all.length === 0) return null;
  const lo = Math.min(...all), hi = Math.max(...all);
  const pad = (hi - lo) * 0.25 || hi * 0.02;
  const H = 96, top = 8, bottom = 8;
  const y = (v: number) => top + (H - top - bottom) * (1 - (v - (lo - pad)) / (hi + pad - (lo - pad)));
  const zone = chart.kind === "zone" && levels.length >= 2;
  return (
    <svg className="ev-mini" viewBox={`0 0 240 ${H}`} role="img"
      aria-label={`가격 그림: ${named.map((x) => `${x.name} ${price(x.v)}`.trim()).join(", ")}${marks.length ? ` · 지금 ${marks.map(price).join(", ")}` : ""}`}>
      {zone && (
        <rect x={20} y={y(Math.max(...levels))} width={140} height={Math.max(4, y(Math.min(...levels)) - y(Math.max(...levels)))}
          className="ev-zone" />
      )}
      {named.map(({ v, name }, i) => (
        <g key={`l${i}`}>
          <line x1={20} x2={150} y1={y(v)} y2={y(v)} className={`ev-line${name === "손절" ? " ev-line-stop" : ""}`} />
          <text x={154} y={y(v) + 4} className="ev-label">{name ? `${name} ${price(v)}` : price(v)}</text>
        </g>
      ))}
      {marks.map((v, i) => (
        <g key={`m${i}`} className="ev-now">
          <circle cx={90} cy={y(v)} r={4} />
          <text x={96} y={y(v) - 6}>지금 {price(v)}</text>
        </g>
      ))}
    </svg>
  );
}

function range(lo: number | null, hi: number | null): string {
  if (lo == null && hi == null) return "—";
  if (lo == null || hi == null || lo === hi) return price(lo ?? hi);
  return `${price(Math.min(lo, hi))} ~ ${price(Math.max(lo, hi))}`;
}

export default function ExplainArticle({ data, className = "easy-view", busy, afterHook, beforeStages, footer, chartFor }: {
  data: ArticleData;
  /** #682 칸의 그림을 바꿔 그린다(리포트: 저장된 캔들 차트) — null 이면 기본 가격선 그림 */
  chartFor?: (chart: ExplainChart, i: number) => ReactNode | null;
  className?: string;
  busy?: boolean;
  /** 질문 바로 아래(해설: 기본 해설 안내 · 리포트: 고른 이유) */
  afterHook?: ReactNode;
  /** 단계 카드 앞(리포트: 과거 비슷한 장면) */
  beforeStages?: ReactNode;
  /** 한마디 뒤(출처 · 다시 쓰기 등) */
  footer?: ReactNode;
}) {
  return (
    <article className={className} aria-busy={busy}>
      <p className="ev-hook">{data.hook}</p>
      {afterHook}
      {data.sections.map((s, i) => (
        <section key={i} className="ev-section">
          <h3><span className="ev-num">{i + 1}</span>{s.title}</h3>
          {s.image ? <img className="ev-img" src={s.image.url} alt={s.image.alt} loading="lazy"
            onError={(e) => { e.currentTarget.hidden = true; }} /> : (chartFor?.(s.chart, i) ?? <MiniLevels chart={s.chart} />)}
          {s.body.split(/\n{2,}/).map((p, n) => <p key={n}>{p}</p>)}
          {s.highlight && <p className="ev-highlight"><mark>{s.highlight}</mark></p>}
          {s.term?.explain && (
            <aside className="ev-term" aria-label={`용어 풀이: ${s.term.name}`}>
              <span aria-hidden="true">💡</span> <strong>{s.term.name}</strong> — {s.term.explain}
            </aside>
          )}
        </section>
      ))}
      {beforeStages}
      {data.stages.length > 0 && (
        <section className="ev-section">
          <h3>단계별로 이렇게</h3>
          <ol className="ev-stages">
            {data.stages.map((st, i) => (
              <li key={i} className={`ev-stage${st.label === "최대" ? " ev-stage-max" : ""}`} style={{ ["--step" as string]: i }}>
                <span className="ev-stage-label">{st.label}</span>
                <strong>{range(st.low, st.high)}</strong>
                <span className="small">{st.why}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      {data.checklist.length > 0 && (
        <section className="ev-section ev-check">
          <h3>이것만 확인해요</h3>
          <ul>
            {data.checklist.map((c, i) => (
              <li key={i}><span aria-hidden="true">✓</span> <strong>{c.q}</strong> {c.a}</li>
            ))}
          </ul>
        </section>
      )}
      <p className="ev-closing">{data.closing}</p>
      {footer}
    </article>
  );
}
