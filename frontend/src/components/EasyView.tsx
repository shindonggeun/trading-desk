import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { RunExplain } from "../types";
import ExplainArticle from "./ExplainArticle";

/**
 * #494 "쉽게 보기" — 분석을 초보자용 글(#493 /api/runs/{id}/explain)로. 숫자는 모두 데스크 계산값.
 * 글 모양(그림 · 소제목 · 단계 · 체크리스트)은 리포트(#496)와 같은 ExplainArticle 로 그린다.
 */
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
    <ExplainArticle data={data} busy={busy}
      afterHook={(data.pending || data.source === "rules") && (
        <p className="muted small ev-note">
          {data.pending ? "분석이 돌고 있어 데스크 숫자로 쓴 기본 해설입니다. 잠시 뒤 다시 열면 더 쉽게 풀어 씁니다."
            : "데스크 숫자로 쓴 기본 해설입니다."}
        </p>
      )}
      footer={(
        <p className="muted small">
          숫자는 모두 데스크가 계산한 값이고, 문장은 {data.source === "ai" ? "AI 가 쉽게 풀어 쓴 것" : "데스크 규칙으로 쓴 것"}입니다. 예측이 아니라 조건과 대응입니다.{" "}
          <button type="button" className="link-button" disabled={busy} onClick={() => load(true)}>{busy ? "쓰는 중…" : "다시 쓰기"}</button>
        </p>
      )} />
  );
}
