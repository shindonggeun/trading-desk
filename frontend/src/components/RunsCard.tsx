import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { price, STATUS_LABEL, time } from "../format";
import type { Run, RunPage } from "../types";
import { BiasBadge, RatingText } from "./PlanCard";

const PAGE_SIZE = 20;

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
    load();
    const timer = setInterval(load, 5000); // 진행 중인 행이 저절로 채워진다
    return () => clearInterval(timer);
  }, [page, load]);

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

  const runs = data?.items ?? [];
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  const stale = data !== null && data.page !== page; // 쪽을 바꾼 뒤 응답 전

  return (
    <section className="card runs-card">
      <h2>
        분석 기록 {data && <span className="muted small">{data.total}건</span>}
      </h2>
      <p className="muted small">끝난 분석 기록은 90일 보관한 뒤 자동으로 지웁니다.</p>
      {error && <p className="error small" role="alert">{error}</p>}
      {loadFailed && <p className="error small" role="alert">분석 기록을 불러오지 못했습니다. 5초 뒤 다시 시도합니다.</p>}
      {data && runs.length === 0 ? (
        <p className="muted">아직 분석이 없습니다. 왼쪽에서 첫 분석을 시작하세요.</p>
      ) : (
        <div className={stale ? "table-wrap stale" : "table-wrap"} aria-busy={stale}>
          <table className="runs">
            <thead>
              <tr>
                <th>종목</th>
                <th>기준일</th>
                <th>판단</th>
                <th className="num">진입</th>
                <th className="num">손절</th>
                <th className="num">목표</th>
                <th className="num">손익비</th>
                <th>상태</th>
                <th>요청</th>
                <th><span className="sr-only">삭제</span></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const label = r.options.name ? `${r.options.name}(${r.ticker})` : r.ticker;
                return (
                <tr key={r.id} onClick={() => (window.location.hash = `#/runs/${r.id}`)}>
                  <td className="ticker">
                    {r.options.name ? <>{r.options.name} <span className="muted small">{r.ticker}</span></> : r.ticker}
                  </td>
                  <td>{r.trade_date}</td>
                  <td>{r.plan ? <><RatingText rating={r.plan.rating} /> <BiasBadge plan={r.plan} /></> : "—"}</td>
                  <td className="num">{price(r.plan?.entry_price)}</td>
                  <td className="num">{price(r.plan?.stop_loss)}</td>
                  <td className="num">{price(r.plan?.price_target)}</td>
                  <td className="num">{r.plan?.reward_risk ?? "—"}</td>
                  <td><span className={`status status-${r.status}`}>{STATUS_LABEL[r.status]}</span></td>
                  <td className="muted">{time(r.created_at)}</td>
                  <td>
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
