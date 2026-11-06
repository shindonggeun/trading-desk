import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api, followRun } from "../api";
import AgentRail from "../components/AgentRail";
import { RelatedTable, ResultChart, type Related } from "../components/AnalysisResult";
import { ChatMessage, SystemLine, ToolRow } from "../components/ChatMessage";
import ConsensusCard from "../components/ConsensusCard";
import PlaybookCard from "../components/PlaybookCard";
import EvidenceLevels from "../components/Evidence";
import { GlossaryPanel, GlossaryProvider } from "../components/Glossary";
import PlanCard from "../components/PlanCard";
import FavoriteStar from "../components/FavoriteStar";
import RunSummaryBar from "../components/RunSummaryBar";
import TradeMap from "../components/TradeMap";
import PositioningCard from "../components/PositioningCard";
import { duration, STATUS_LABEL } from "../format";
import type { DeskEvent, Plan, Run } from "../types";

// 백엔드 desk/profile.py 의 label 과 같다. 옛 분석(성향 없음)은 표시하지 않는다
const PROFILE_LABEL: Record<string, string> = { aggressive_leverage: "공격형 · 레버리지", balanced: "균형형" };

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

// 탭(#99): 주소 #/runs/<id>/<탭> 에 남아 새로 고침 · 공유해도 같은 탭이 열린다.
const TABS = [
  { id: "summary", label: "요약" },
  { id: "chart", label: "차트" },
  { id: "debate", label: "토론" },
  { id: "evidence", label: "근거" },
] as const;
type Tab = (typeof TABS)[number]["id"];

function tabInHash(runId: string): Tab | null {
  const m = window.location.hash.match(/^#\/runs\/([\w-]+)\/(\w+)/);
  return m && m[1] === runId && TABS.some((t) => t.id === m[2]) ? (m[2] as Tab) : null;
}

export default function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [, tick] = useState(0);
  const bottom = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const [glossary, setGlossary] = useState<{ id: string | null; seq: number }>({ id: null, seq: 0 });
  const focusTerm = useCallback((id: string) => setGlossary((g) => ({ id, seq: g.seq + 1 })), []);
  // 고른 탭. null 이면 상태를 따른다: 분석 중엔 토론, 끝나면 요약.
  const [chosen, setChosen] = useState<Tab | null>(() => tabInHash(runId));
  const tabs = useRef<HTMLDivElement>(null);
  const runBar = useRef<HTMLDivElement>(null);

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

  // 뒤로 가기 · 주소 직접 수정
  useEffect(() => {
    const onHash = () => setChosen(tabInHash(runId));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [runId]);

  // 결론 줄은 상단 바 바로 아래에 붙고, 토론 탭의 에이전트 목록 · 용어 사전은 그 아래에 붙는다 —
  // 두 높이(좁은 화면은 상단 바가 두 줄, 결론 줄은 펼침에 따라)를 재서 CSS 로 넘긴다.
  const ready = !!run;
  useEffect(() => {
    const top = document.querySelector<HTMLElement>(".topbar");
    const bar = runBar.current;
    if (typeof ResizeObserver === "undefined") return;
    const root = document.documentElement.style;
    const set = () => {
      if (top) root.setProperty("--topbar-h", `${top.offsetHeight}px`);
      if (bar) root.setProperty("--runbar-h", `${bar.offsetHeight}px`);
    };
    set();
    const ro = new ResizeObserver(set);
    if (top) ro.observe(top);
    if (bar) ro.observe(bar);
    return () => ro.disconnect();
  }, [ready]);

  // Elapsed time ticks while running.
  useEffect(() => {
    if (run?.status !== "running") return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [run?.status]);

  const plan: Plan | null = run?.plan ?? (events.find((e) => e.type === "plan")?.data as Plan | undefined) ?? null;
  const status = run ? ((events.some((e) => e.type === "done") && run.status === "running") ? "done" : run.status) : null;
  const working = status === "running" || status === "queued";
  const tab: Tab = chosen ?? (working ? "debate" : "summary");

  // 분석이 끝나면 토론 탭에서 요약으로 넘어간다(차트 · 근거를 보던 중이면 그대로).
  const wasWorking = useRef(working);
  useEffect(() => {
    if (wasWorking.current && !working && chosen === "debate") {
      setChosen(null);
      window.history.replaceState(null, "", `#/runs/${runId}`);   // 새로 고침 · 공유도 요약으로
    }
    wasWorking.current = working;
  }, [working, chosen, runId]);

  // Follow the conversation unless the reader scrolled up to read.
  useEffect(() => {
    const onScroll = () => {
      stick.current = window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
    };
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (tab === "debate" && working && stick.current) bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length, tab, working]);

  const pick = (t: Tab) => {
    setChosen(t);
    // 탭마다 기록을 쌓지 않는다 — 공유 · 새로 고침용 주소만 바꾼다
    window.history.replaceState(null, "", `#/runs/${runId}/${t}`);
    window.scrollTo({ top: 0 });
  };
  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === tab);
    const at = { ArrowRight: i + 1, ArrowLeft: i - 1 + TABS.length, Home: 0, End: TABS.length - 1 }[e.key];
    if (at === undefined) return;
    e.preventDefault();
    const next = TABS[at % TABS.length].id;
    pick(next);
    tabs.current?.querySelector<HTMLButtonElement>(`#run-tab-${next}`)?.focus();
  };

  if (error) return <main className="run"><p className="error">{error}</p></main>;
  if (!run || !status) return <main className="run"><p className="muted">불러오는 중…</p></main>;

  const related = (plan as (Plan & { related?: Related }) | null)?.related;
  const ticker = run.options.name ? `${run.options.name} (${run.ticker})` : run.ticker;

  return (
    <main className="run run-wide run-tabbed">
      <div className="run-head">
        <a href="#/" className="link">← 대시보드</a>
        <FavoriteStar ticker={run.ticker} name={run.options.name} />
        <h1>{run.options.name ?? run.ticker} <span className="muted">{run.options.name ? `${run.ticker} · ` : ""}{run.trade_date}</span></h1>
        <span className={`status status-${status}`}>{STATUS_LABEL[status]}</span>
        <span className="muted small">{duration(run.started_at, run.finished_at)}</span>
      </div>
      <RunSummaryBar ref={runBar} run={run} plan={plan} status={status} events={events} />
      <div className="run-tabs" role="tablist" aria-label="분석 보기" ref={tabs} onKeyDown={onTabKey}>
        {TABS.map((t) => (
          <button
            key={t.id}
            id={`run-tab-${t.id}`}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            aria-controls={tab === t.id ? `run-panel-${t.id}` : undefined}
            tabIndex={tab === t.id ? 0 : -1}
            className={`run-tab ${tab === t.id ? "run-tab-on" : ""}`}
            onClick={() => pick(t.id)}
          >
            {t.label}
            {t.id === "debate" && working && <span className="run-tab-live" aria-label="진행 중" />}
          </button>
        ))}
      </div>

      <GlossaryProvider onFocus={focusTerm}>
        <section id={`run-panel-${tab}`} role="tabpanel" aria-labelledby={`run-tab-${tab}`} className={`run-panel run-panel-${tab}`}>
          {tab === "summary" && (
            plan ? (
              <div className="run-summary">
                {status === "done" && <ConsensusCard plan={plan} ticker={run.ticker} />}
                <PlaybookCard plan={plan} symbol={stillOpen(run.trade_date) ? run.ticker : undefined} outcome={run.outcome} runId={run.id} horizon={plan.horizon ?? run.options.horizon ?? undefined} />
                <TradeMap plan={plan} ticker={run.ticker} />
                {/* #158 미국: 공매도 · 옵션, 국내: 빚투 · 예탁금(레버리지 상품은 본주 기준) */}
                <PositioningCard ticker={run.ticker} underlying={plan.underlying?.ticker} />
                <PlanCard
                  plan={plan}
                  symbol={stillOpen(run.trade_date) ? run.ticker : undefined}
                  ticker={ticker}
                  code={run.ticker}
                  profile={PROFILE_LABEL[run.options.profile ?? ""]}
                  profileId={run.options.profile ?? undefined}
                  outcome={run.outcome}
                  outcomeStatus={run.outcome_status}
                />
              </div>
            ) : (
              <section className="card run-empty">
                {status === "failed" ? (
                  <p className="error">{run.error ?? "분석이 실패했습니다."}</p>
                ) : (
                  <p className="muted">분석이 끝나면 결론과 타점이 여기에 나옵니다. 지금 진행 상황은 <button type="button" className="link" onClick={() => pick("debate")}>토론 탭</button>에서 볼 수 있습니다.</p>
                )}
              </section>
            )
          )}
          {tab === "chart" && <ResultChart plan={plan} ticker={run.ticker} tradeDate={run.trade_date} />}
          {tab === "debate" && (
            <div className="run-body has-glossary">
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
                <div ref={bottom} />
              </div>
              <div className="run-debate-glossary">
                <GlossaryPanel events={events} focusId={glossary.id} focusSeq={glossary.seq} />
              </div>
            </div>
          )}
          {tab === "evidence" && (
            <div className="run-evidence">
              <EvidenceLevels plan={plan} ticker={run.ticker} />
              {related && related.instruments.length > 0 && <RelatedTable related={related} />}
              <GlossaryPanel events={events} focusId={glossary.id} focusSeq={glossary.seq} />
            </div>
          )}
        </section>
      </GlossaryProvider>
    </main>
  );
}
