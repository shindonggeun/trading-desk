import { useEffect, useState } from "react";
import { api } from "../api";
import { percent, price, tone } from "../format";
import { contracts, hidden, isLive, kstStamp, LIVE_REFRESH_MS, nextRefresh, SESSION_KO, sessionBadge } from "../futures";
import type { FuturesCandles, FuturesQuote, FuturesSession } from "../types";

/**
 * #444 대시보드 "코스피200 선물" 카드 — 지금 세션(닫혔으면 마지막 세션)의 가격 · 등락 · 거래량 · 미결제약정과
 * 그 세션 5분봉 미니 차트. 장중이면 15초마다 다시 읽는다. 누르면 전용 화면(#/futures).
 */
/** #713 주간 · 야간 두 칸 — 칸마다 진행 중 · 종료와 끝난 시각, 가격 · 등락률. 끝난 세션은 다음 세션이 열릴 때까지 그대로 */
function SessionPair({ sessions, current }: { sessions: Record<FuturesSession, FuturesQuote>; current: FuturesSession }) {
  return (
    <div className="fq-pair" role="group" aria-label="주간 · 야간 시세">
      {(["day", "night"] as const).map((k) => {
        const s = sessions[k];
        const open = s.state === "open";
        const ended = !open && s.end ? `${kstStamp(s.end).slice(6)} 종료` : "마감";
        return (
          <div key={k} className={`fq-cell${k === current ? " fq-current" : ""}`}>
            <span className="small muted">
              {SESSION_KO[k]} <span className={`fq-badge ${open ? "fq-live" : ""}`}>{open ? "진행 중" : ended}</span>
            </span>
            {s.price == null ? <span className="small muted">{s.error ? "값 없음" : "—"}</span> : (
              <span className={`num ${tone(s.change)}`}>
                {price(s.price)} {s.change_pct == null ? "" : percent(s.change_pct)}
                {(s.held || s.from_bars) && <span className="muted small" title={s.from_bars ? "분봉 마지막 값" : "끝나기 전 마지막 값"}> *</span>}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function FuturesQuoteCard() {
  const [q, setQ] = useState<FuturesQuote | "error" | null>(null);
  const [bars, setBars] = useState<FuturesCandles["bars"]>([]);
  const [failed, setFailed] = useState(false);   // 마지막 읽기가 실패해 이전 값을 보이는 중

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    const later = (ms: number | null) => {
      if (alive && ms != null) timer = window.setTimeout(load, ms);
    };
    const load = async () => {
      if (hidden()) return later(LIVE_REFRESH_MS);
      try {
        const quote = await api.futuresQuote();
        if (!alive) return;
        setQ(quote);
        setFailed(false);
        if (quote.supported) {
          const c = await api.futuresCandles(quote.session, "5m").catch(() => null);
          if (alive && c) setBars(c.session === quote.session ? c.bars : []);
        }
        // 닫혀 있어도 길게 다시 묻는다 — 켜 둔 대시보드가 다음 세션 시작을 놓치지 않게
        later(nextRefresh(quote));
      } catch (e) {
        if (!alive) return;
        setFailed(true);
        setQ((prev) => (prev && prev !== "error" ? prev : "error"));
        later(nextRefresh(null, e));
      }
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);

  if (q === null) return <p className="empty-state">불러오는 중…</p>;
  if (q === "error") return <p className="empty-state">선물 시세를 불러오지 못했습니다. 잠시 뒤 새로 고쳐 주세요.</p>;
  if (!q.supported) {
    return <p className="empty-state">선물 시세를 받을 수 없습니다{q.error ? ` — ${q.error}` : ""}.</p>;
  }
  const live = isLive(q);
  return (
    <a className="card fq-card" href="#/futures" title="코스피200 선물 시세 크게 보기">
      <div className="fq-head">
        <span className={`fq-badge ${live ? "fq-live" : ""}`}>{sessionBadge(q)}</span>
        <span className="muted small">{q.contract?.name ?? "코스피200 선물"}{q.contract ? ` · 만기 D-${q.contract.days_left}` : ""}</span>
      </div>
      <div className="fq-price-row">
        <strong className={`fq-price num ${tone(q.change)}`}>{price(q.price)}</strong>
        <span className={`num ${tone(q.change)}`}>
          {q.change == null ? "—" : `${q.change > 0 ? "▲" : q.change < 0 ? "▼" : ""}${price(Math.abs(q.change))}`} {percent(q.change_pct)}
        </span>
      </div>
      <Spark bars={bars} base={q.prev_close ?? null} />
      <div className="fq-meta small muted">
        <span>거래량 <b className="num">{contracts(q.volume)}</b></span>
        <span>미결제 <b className="num">{contracts(q.open_interest)}</b>
          {q.open_interest_change != null && <span className={`num ${tone(q.open_interest_change)}`}> ({contracts(q.open_interest_change, true)})</span>}</span>
        <span>{kstStamp(q.as_of)} 기준</span>
      </div>
      {q.sessions && <SessionPair sessions={q.sessions} current={q.current ?? q.session} />}
      {q.stale && <p className="small error">마지막으로 받은 값입니다{q.error ? ` — ${q.error}` : ""}</p>}
      {failed && !q.stale && <p className="small error">새로 읽지 못해 이전 값입니다 — 잠시 뒤 다시 읽습니다</p>}
      <span className="sr-only">{SESSION_KO[q.session]} 분봉 크게 보기</span>
    </a>
  );
}

/** 그 세션 종가 선(기준가 점선) — 축 없는 작은 그림. 가로는 시각이 아니라 봉 순서(흐름만 보인다 — 시각은 전용 화면에서) */
function Spark({ bars, base }: { bars: FuturesCandles["bars"]; base: number | null }) {
  if (bars.length < 2) return <div className="fq-spark fq-spark-empty muted small">아직 봉이 없습니다</div>;
  const W = 300, H = 56;
  const vals = bars.map((b) => b.c);
  const lo = Math.min(...vals, base ?? Infinity), hi = Math.max(...vals, base ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => (i / (bars.length - 1)) * W;
  const y = (v: number) => H - 3 - ((v - lo) / span) * (H - 6);
  const d = vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const last = vals[vals.length - 1];
  const cls = base == null ? "" : last >= base ? "up" : "down";
  return (
    <svg className={`fq-spark ${cls}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
      aria-label={`세션 흐름 ${price(vals[0])} → ${price(last)}`}>
      {base != null && <line x1="0" x2={W} y1={y(base)} y2={y(base)} className="fq-base" />}
      <path d={d} fill="none" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
