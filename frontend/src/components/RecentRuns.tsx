import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { STATUS_LABEL, time } from "../format";
import { HORIZON, OPINION, OUTCOME } from "../korean";
import type { Run } from "../types";
import { usePoll } from "../poll";
import { queueReason } from "../runQueue";

const COUNT = 6;
const POLL_MS = 5000;

const active = (r: Run) => r.status === "queued" || r.status === "running";

/** 최근 분석 카드 줄(#100) — 종목 · 의견 · 승률 · 상태 · 채점. 전체 목록 · 삭제는 "분석 기록" 화면. */
export default function RecentRuns() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [total, setTotal] = useState(0);
  const [failed, setFailed] = useState(false);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const page = await api.runs(1, COUNT);
      if (mine !== seq.current) return;
      setRuns(page.items);
      setTotal(page.total);
      setFailed(false);
    } catch {
      if (mine === seq.current) setFailed(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 진행 중인 분석이 있을 때만, 화면이 보일 때만 다시 읽는다
  const running = !!runs?.some(active);
  usePoll(running ? "recent-runs" : null, load, POLL_MS, { delay: POLL_MS });

  if (failed && !runs) return <p className="empty-state">최근 분석을 불러오지 못했습니다. 잠시 뒤 새로 고쳐 주세요.</p>;
  if (!runs) return <p className="empty-state">불러오는 중…</p>;
  if (runs.length === 0) return <p className="empty-state">아직 분석이 없습니다. 위 검색 줄에서 종목을 고르면 시작합니다.</p>;

  return (
    <>
      <ul className="recent-runs">
        {runs.map((r) => {
          const plan = r.plan;
          const opinion = plan?.opinion;
          const horizon = plan?.horizon ?? r.options.horizon ?? "swing";
          const outcome = r.outcome_status ? OUTCOME[r.outcome_status]?.ko : null;
          const waiting = queueReason(r, []);
          return (
            <li key={r.id}>
              <a className={`recent-run${opinion ? ` recent-${opinion}` : ""}`} href={`#/runs/${r.id}`}>
                <span className="recent-name">
                  <strong>{r.options.name || r.ticker}</strong>
                  {r.options.name && <span className="muted small"> {r.ticker}</span>}
                </span>
                <span className="recent-opinion">
                  {active(r) || r.status === "failed" ? (
                    <span className={`recent-status recent-status-${r.status}`}>{STATUS_LABEL[r.status]}</span>
                  ) : opinion ? (
                    <span title={OPINION[opinion]?.desc}>{OPINION[opinion]?.ko ?? opinion}</span>
                  ) : (
                    <span className="muted">{plan?.rating ?? "—"}</span>
                  )}
                  {plan?.win_probability != null && <span className="recent-prob">승률 {Math.round(plan.win_probability * 100)}%</span>}
                </span>
                <span className="muted small recent-meta">
                  {HORIZON[horizon]?.ko ?? horizon} · {r.trade_date}
                  {outcome ? ` · ${outcome}` : r.status === "done" ? "" : ` · ${time(r.created_at)}`}
                </span>
                {waiting && <span className="run-queue-detail small" title={waiting.title}>{waiting.text}</span>}
              </a>
            </li>
          );
        })}
      </ul>
      <p className="recent-more">
        <a href="#/history">분석 기록 전체 {total}건 보기 →</a>
      </p>
    </>
  );
}
