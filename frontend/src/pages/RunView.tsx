import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { api, followRun } from "../api";
import AgentRail from "../components/AgentRail";
import { ChatMessage, SystemLine, ToolRow } from "../components/ChatMessage";
import PlanCard from "../components/PlanCard";
import SectionBoundary from "../components/SectionBoundary";

// 백엔드 desk/profile.py 의 label 과 같다. 옛 분석(성향 없음)은 표시하지 않는다
const PROFILE_LABEL: Record<string, string> = { aggressive_leverage: "공격형 · 레버리지", balanced: "균형형" };
import { duration, STATUS_LABEL } from "../format";
import type { DeskEvent, Plan, Run } from "../types";

// 차트 라이브러리(~160kB)는 분석 화면에서만 받는다.
const PriceChart = lazy(() => import("../components/PriceChart"));

type Item = { kind: "event"; event: DeskEvent } | { kind: "tools"; events: DeskEvent[] };

/** Fold runs of consecutive tool calls into one row so the chat stays readable. */
function toItems(events: DeskEvent[]): Item[] {
  const items: Item[] = [];
  for (const e of events) {
    if (e.type === "plan" || e.type === "done") continue;
    const prev = items[items.length - 1];
    if (e.type === "tool") {
      if (prev?.kind === "tools") prev.events.push(e);
      else items.push({ kind: "tools", events: [e] });
    } else {
      items.push({ kind: "event", event: e });
    }
  }
  return items;
}

// 계획의 보유 기간은 길어야 몇 주다. 그보다 오래된 · 과거 날짜 분석은 지금 시세로 판단하지 않는다.
const LIVE_DAYS = 21;

function stillOpen(tradeDate: string): boolean {
  const days = (Date.now() - new Date(`${tradeDate}T00:00:00`).getTime()) / 86_400_000;
  return days < LIVE_DAYS + 1;
}

export default function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [, tick] = useState(0);
  const bottom = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    api.run(runId).then(setRun).catch((e) => setError(String(e)));
    const seen = new Set<number>();
    const stop = followRun(runId, (e) => {
      if (seen.has(e.seq)) return;
      seen.add(e.seq);
      setEvents((cur) => [...cur, e]);
      if (e.type === "done" || e.type === "status") api.run(runId).then(setRun).catch(() => {});
    });
    return stop;
  }, [runId]);

  // Elapsed time ticks while running.
  useEffect(() => {
    if (run?.status !== "running") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [run?.status]);

  // Follow the conversation unless the reader scrolled up to read.
  useEffect(() => {
    const onScroll = () => {
      stick.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (stick.current) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (error) return <main className="run"><p className="error">{error}</p></main>;
  if (!run) return <main className="run"><p className="muted">불러오는 중…</p></main>;

  const plan: Plan | null = run.plan ?? (events.find((e) => e.type === "plan")?.data as Plan | undefined) ?? null;
  const status = (events.some((e) => e.type === "done") && run.status === "running") ? "done" : run.status;

  return (
    <main className="run">
      <div className="run-head">
        <a href="#/" className="link">← 대시보드</a>
        <h1>{run.options.name ?? run.ticker} <span className="muted">{run.options.name ? `${run.ticker} · ` : ""}{run.trade_date}</span></h1>
        <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>
        <span className="muted small">{duration(run.started_at, run.finished_at)}</span>
      </div>
      <SectionBoundary key={run.id} fallback={<section className="card price-chart"><p className="muted small">차트를 불러오지 못했습니다. 새로고침해 보세요.</p></section>}>
        <Suspense fallback={<section className="card price-chart"><p className="muted small">차트 불러오는 중…</p></section>}>
          <PriceChart ticker={run.ticker} tradeDate={run.trade_date} plan={plan} />
        </Suspense>
      </SectionBoundary>
      <div className="run-body">
        <AgentRail events={events} status={status} analysts={run.options.analysts} />
        <div className="chat">
          {toItems(events).map((item) =>
            item.kind === "tools" ? (
              <ToolRow key={`t${item.events[0].seq}`} events={item.events} />
            ) : item.event.type === "message" ? (
              <ChatMessage key={item.event.seq} event={item.event} />
            ) : (
              <SystemLine key={item.event.seq} event={item.event} />
            ),
          )}
          {status === "running" && <div className="typing"><span /><span /><span /></div>}
          {plan && (
            <PlanCard
              plan={plan}
              symbol={stillOpen(run.trade_date) ? run.ticker : undefined}
              ticker={run.options.name ? `${run.options.name} (${run.ticker})` : run.ticker}
              profile={PROFILE_LABEL[run.options.profile ?? ""]}
            />
          )}
          <div ref={bottom} />
        </div>
      </div>
    </main>
  );
}
