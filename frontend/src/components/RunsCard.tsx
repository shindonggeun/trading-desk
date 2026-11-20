import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { price, STATUS_LABEL, time } from "../format";
import { BIAS, HORIZON, OPINION, OUTCOME, RATING, term } from "../korean";
import { listLevels } from "../listLevels";
import type { Plan, Run, RunPage } from "../types";
import { BiasBadge, RatingText } from "./PlanCard";
import { usePoll } from "../poll";
import { queueReason } from "../runQueue";

/** #387 판단 칸에서 같은 말을 거듭 쓰지 않는다 — "보유 · 관망" 등급이면 "관망" 배지 · 의견 이름은 뺀다. */
const ratingKo = (plan: Plan) => term(RATING, plan.rating)?.ko ?? "";
const saysBias = (plan: Plan) => ratingKo(plan).includes(BIAS[plan.bias]?.ko ?? "\u0000");

function opinionText(plan: Plan): string {
  const name = plan.opinion ? OPINION[plan.opinion]?.ko ?? "" : "";
  const shown = `${ratingKo(plan)} ${saysBias(plan) ? "" : BIAS[plan.bias]?.ko ?? ""}`;
  const pct = plan.win_probability != null ? `${Math.round(plan.win_probability * 100)}%` : "";
  return [name && !shown.includes(name) ? name : "", pct].filter(Boolean).join(" ");
}

const PAGE_SIZE = 20;

/** 예전 run(시간축 없음)은 스윙이다(backend desk/horizons.py DEFAULT_HORIZON). */
function horizonOf(r: Run): string {
  return r.plan?.horizon ?? r.options.horizon ?? "swing";
}

function isActive(r: Run) {
  return r.status === "queued" || r.status === "running";
}

/** 페이지 번호 목록 — 처음 · 끝 · 현재 주변만, 사이는 "…". */
function pageNumbers(page: number, pages: number): (number | "…")[] {
  const out: (number | "…")[] = [];
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - page) <= 2) out.push(p);
    else if (out[out.length - 1] !== "…") out.push("…");
  }
  return out;
}

/** 대시보드의 분석 기록 — 페이지로 넘겨 보고, 끝난 기록은 지운다(#36, API #34). */
export default function RunsCard() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<RunPage | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [horizon, setHorizon] = useState<string>("all");
  const pageRef = useRef(page);
  pageRef.current = page;
  const seqRef = useRef(0); // 마지막에 보낸 요청만 반영 — 늦게 온 폴링 응답이 삭제 뒤 목록을 덮지 않게

  const load = useCallback(async () => {
    const p = pageRef.current;
    const seq = ++seqRef.current;
    try {
      const res = await api.runs(p, PAGE_SIZE);
      if (seq !== seqRef.current || p !== pageRef.current) return;
      setLoadFailed(false);
      const pages = Math.max(1, Math.ceil(res.total / res.size));
      if (res.items.length === 0 && p > pages) {
        setPage(pages); // 마지막 쪽이 비었으면(삭제 · 자동 정리) 앞 쪽으로
        return;
      }
      setData(res);
    } catch {
      if (seq === seqRef.current) setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    setError(null);
  }, [page]);
  // #626 진행 중인 행이 저절로 채워지게 5초마다(가려진 탭은 쉰다)
  usePoll(`runs-card:${page}`, load, 5000);

  async function remove(r: Run) {
    const name = r.options.name ? `${r.options.name}(${r.ticker})` : r.ticker;
    if (!window.confirm(`${name} ${r.trade_date} 분석 기록을 지울까요? 되돌릴 수 없습니다.`)) return;
    setDeleting(r.id);
    setError(null);
    try {
      await api.deleteRun(r.id);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      // 404 = 다른 탭에서 이미 지웠다 — 결과는 같으니 조용히 넘어간다.
      if (status === 409) setError("진행 중인 분석은 지울 수 없습니다.");
      else if (status !== 404) setError("지우지 못했습니다. 잠시 뒤 다시 시도하세요.");
    } finally {
      setDeleting(null);
      await load();
    }
  }

  const all = data?.items ?? [];
  const runs = horizon === "all" ? all : all.filter((r) => horizonOf(r) === horizon);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const stale = data !== null && data.page !== page; // 쪽을 바꾼 뒤 응답 전

  return (
    <section className="card runs-card">
      <h2>
        분석 기록 {data && <span className="muted small">{data.total}건</span>}
      </h2>
      <p className="muted small">끝난 분석 기록은 90일 보관한 뒤 자동으로 지웁니다.</p>
      <div className="chips" role="group" aria-label="시간축 필터">
        {[["all", "전체"], ...Object.entries(HORIZON).map(([id, h]) => [id, h.ko])].map(([id, label]) => (
          <button key={id} type="button" className={`chip ${horizon === id ? "chip-on" : ""}`} aria-pressed={horizon === id} onClick={() => setHorizon(id)}>
            {label}
          </button>
        ))}
        {horizon !== "all" && <span className="muted small">이 쪽(현재 페이지) {all.length}건 중 {runs.length}건</span>}
      </div>
      {error && <p className="error small" role="alert">{error}</p>}
      {loadFailed && <p className="error small" role="alert">분석 기록을 불러오지 못했습니다. 5초 뒤 다시 시도합니다.</p>}
      {data && all.length === 0 ? (
        <p className="muted">아직 분석이 없습니다. 왼쪽에서 첫 분석을 시작하세요.</p>
      ) : data && runs.length === 0 ? (
        <p className="muted">이 쪽에 {HORIZON[horizon]?.ko ?? horizon} 분석이 없습니다.</p>
      ) : (
        <div className={stale ? "table-wrap stale" : "table-wrap"} aria-busy={stale}>
          <table role="table" className="runs stack-table">
            <thead role="rowgroup">
              <tr role="row">
                <th role="columnheader">종목</th>
                <th role="columnheader">기준일</th>
                <th role="columnheader">시간축</th>
                <th role="columnheader">판단</th>
                <th role="columnheader" className="num">진입</th>
                <th role="columnheader" className="num">손절</th>
                <th role="columnheader" className="num">목표</th>
                <th role="columnheader" className="num">손익비</th>
                <th role="columnheader">상태</th>
                <th role="columnheader">요청</th>
                <th role="columnheader"><span className="sr-only">삭제</span></th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {runs.map((r) => {
                const label = r.options.name ? `${r.options.name}(${r.ticker})` : r.ticker;
                const lv = listLevels(r.plan);
                return (
                <tr role="row" key={r.id} onClick={() => (window.location.hash = `#/runs/${r.id}`)}>
                  <td role="cell" className="ticker stack-wide" data-label="종목">
                    {r.options.name && r.options.name !== r.ticker ? <>{r.options.name} <span className="muted small">{r.ticker}</span></> : r.ticker}
                  </td>
                  <td role="cell" data-label="기준일">{r.trade_date}</td>
                  <td role="cell" data-label="시간축">{HORIZON[horizonOf(r)]?.ko ?? horizonOf(r)}</td>
                  <td role="cell" data-label="판단">{r.plan ? <><RatingText rating={r.plan.rating} /> {!saysBias(r.plan) && <BiasBadge plan={r.plan} />}
                    {r.plan.opinion && opinionText(r.plan) && (
                      <span className={`opinion-tag opinion-${r.plan.opinion}`} title={OPINION[r.plan.opinion]?.desc}>
                        {opinionText(r.plan)}
                      </span>
                    )}
                    {(r.plan.bias === "long" || r.plan.bias === "short") && r.outcome_status && OUTCOME[r.outcome_status] && (
                      <span className={`outcome-tag outcome-${r.outcome_status}`} title={OUTCOME[r.outcome_status].desc}>
                        {OUTCOME[r.outcome_status].ko}
                      </span>
                    )}</> : "—"}</td>
                  {/* #430 데스크 계획 값(상세와 같은 숫자), 없으면 참고 값 · 관망 조건가, 모순이면 숨김 */}
                  <td role="cell" className="num" data-label="진입">
                    {lv.kind === "watch" && lv.triggers ? (
                      <span className="small" title="관망 — 이 가격을 넘거나 깨면 그때 진입">
                        {[lv.triggers.long != null && `롱 ${price(lv.triggers.long)}↑`, lv.triggers.short != null && `숏 ${price(lv.triggers.short)}↓`].filter(Boolean).join(" · ")}
                      </span>
                    ) : lv.kind === "conflict" ? (
                      <span className="muted small" title="등급상 타점이 방향과 맞지 않아(손절 = 목표 등) 숨겼습니다. 상세 화면의 데스크 계획을 보세요">값 모순</span>
                    ) : (
                      <>{price(lv.entry)}{lv.ref && <span className="muted small" title="지금 지시가 아닌 참고 값"> (참고)</span>}</>
                    )}
                  </td>
                  <td role="cell" className="num" data-label="손절">{price(lv.stop)}</td>
                  <td role="cell" className="num" data-label="목표">{price(lv.target)}</td>
                  <td role="cell" className="num" data-label="손익비">{lv.rr ?? "—"}</td>
                  <td role="cell" data-label="상태"><span className={`status status-${r.status}`} title={queueReason(r, [])?.text}>{STATUS_LABEL[r.status]}</span></td>
                  <td role="cell" className="muted" data-label="요청">{time(r.created_at)}</td>
                  <td role="cell" className="stack-action">
                    <button
                      type="button"
                      className="link run-delete"
                      disabled={isActive(r) || deleting !== null}
                      title={isActive(r) ? "진행 중인 분석은 지울 수 없습니다" : "이 기록 지우기"}
                      aria-label={`${label} ${r.trade_date} 기록 삭제`}
                      onClick={(e) => {
                        e.stopPropagation(); // 행 클릭(분석 화면 이동)과 분리
                        remove(r);
                      }}
                    >
                      {deleting === r.id ? "지우는 중…" : "삭제"}
                    </button>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <nav className="pager" aria-label="분석 기록 페이지">
          <button type="button" className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            이전
          </button>
          {pageNumbers(page, pages).map((p, i) =>
            p === "…" ? (
              <span key={`gap-${i}`} className="muted">…</span>
            ) : (
              <button
                key={p}
                type="button"
                className={p === page ? "" : "secondary"}
                aria-current={p === page ? "page" : undefined}
                aria-label={`${p}쪽`}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            ),
          )}
          <button type="button" className="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>
            다음
          </button>
        </nav>
      )}
    </section>
  );
}
