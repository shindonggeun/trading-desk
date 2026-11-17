import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { ExplainChart, RunExplain } from "../types";

/**
 * #494 "쉽게 보기" — 분석을 초보자용 글(#493 /api/runs/{id}/explain)로: 질문 → 번호 소제목(작은 가격 그림 ·
 * 문장 · 형광 강조 · 💡 용어 상자) → 단계 카드 → "이것만 확인해요" → 한마디. 숫자는 모두 데스크 계산값.
 */

/** 소제목의 작은 가격 그림: 세로 가격 축에 선(진입 · 손절 · 목표 · 구역 경계)과 지금가 표시. 차트 라이브러리 없이 SVG. */
function MiniLevels({ chart }: { chart: ExplainChart }) {
  const named = chart.levels.map((v, i) => ({ v, name: chart.labels?.[i] ?? "" })).filter((x) => x.v > 0);
  const levels = named.map((x) => x.v);
  const marks = chart.marks.filter((v) => v > 0);
  const all = [...levels, ...marks];
  if (chart.kind === "none" || all.length === 0) return null;
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

export default function EasyView({ runId }: { runId: string }) {
  const [data, setData] = useState<RunExplain | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);

  const load = useCallback((refresh = false) => {
    setBusy(true);
    setFailed(null);
    api.runExplain(runId, refresh)
      .then((d) => setData(d))
      .catch((e: unknown) => setFailed(e instanceof Error ? e.message : "불러오지 못했습니다"))
      .finally(() => setBusy(false));
  }, [runId]);
  useEffect(() => { load(); }, [load]);

  if (!data) {
    return (
      <div className="easy-view">
        {busy ? <p className="muted">쉬운 해설을 쓰는 중입니다… 처음 한 번은 수십 초 걸릴 수 있어요.</p>
          : <p className="muted">해설을 불러오지 못했습니다{failed ? `: ${failed}` : ""} <button type="button" onClick={() => load()}>다시 시도</button></p>}
      </div>
    );
  }
  return (
    <article className="easy-view" aria-busy={busy}>
      <p className="ev-hook">{data.hook}</p>
      {(data.pending || data.source === "rules") && (
        <p className="muted small ev-note">
          {data.pending ? "분석이 돌고 있어 데스크 숫자로 쓴 기본 해설입니다. 잠시 뒤 다시 열면 더 쉽게 풀어 씁니다."
            : "데스크 숫자로 쓴 기본 해설입니다."}
        </p>
      )}
      {data.sections.map((s, i) => (
        <section key={i} className="ev-section">
          <h3><span className="ev-num">{i + 1}</span>{s.title}</h3>
          <MiniLevels chart={s.chart} />
          <p>{s.body}</p>
          {s.highlight && <p className="ev-highlight"><mark>{s.highlight}</mark></p>}
          {s.term?.explain && (
            <aside className="ev-term" aria-label={`용어 풀이: ${s.term.name}`}>
              <span aria-hidden="true">💡</span> <strong>{s.term.name}</strong> — {s.term.explain}
            </aside>
          )}
        </section>
      ))}
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
      <p className="muted small">
        숫자는 모두 데스크가 계산한 값이고, 문장은 {data.source === "ai" ? "AI 가 쉽게 풀어 쓴 것" : "데스크 규칙으로 쓴 것"}입니다. 예측이 아니라 조건과 대응입니다.{" "}
        <button type="button" className="link-button" disabled={busy} onClick={() => load(true)}>{busy ? "쓰는 중…" : "다시 쓰기"}</button>
      </p>
    </article>
  );
}
