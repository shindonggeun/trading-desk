import { useContext, useEffect, useId, useRef, useState } from "react";
import { api, ApiError, type ChartMemoResponse } from "../api";
import { ChartMemoAccount } from "../chartMemoAccount";

const MAX_LENGTH = 2000;
const KST_TIME = new Intl.DateTimeFormat("ko-KR", {
  timeZone: "Asia/Seoul", dateStyle: "medium", timeStyle: "short",
});

type Memo = ChartMemoResponse["memo"];
type State = { status: "loading" } | { status: "error" } | { status: "unavailable" }
  | { status: "account-changed" } | { status: "ready"; memo: Memo };

function isAccountChanged(error: unknown) {
  return error instanceof ApiError && error.status === 409;
}

function loaded(ticker: string, response: ChartMemoResponse): boolean {
  if (response?.ticker !== ticker.trim().toUpperCase()) return false;
  const memo = response.memo;
  return memo === null || (memo?.source === "user" && typeof memo.text === "string"
    && Number.isFinite(Date.parse(memo.created_at)) && Number.isFinite(Date.parse(memo.updated_at)));
}

function Stamp({ value }: { value: string }) {
  return <time dateTime={value}>{KST_TIME.format(Date.parse(value))} KST</time>;
}

export default function ChartMemoPanel({ ticker }: { ticker: string }) {
  const account = useContext(ChartMemoAccount);
  return account ? <MemoForAccount key={account} ticker={ticker} account={account} /> : null;
}

function MemoForAccount({ ticker, account }: { ticker: string; account: string }) {
  const id = useId();
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<State>({ status: "loading" });
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveFailed, setSaveFailed] = useState(false);
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    const request = new AbortController();
    void api.chartMemo(ticker, account, request.signal).then((response) => {
      if (request.signal.aborted) return;
      if (!loaded(ticker, response)) {
        setState({ status: "unavailable" });
        return;
      }
      setState({ status: "ready", memo: response.memo });
      setDraft(response.memo?.text ?? "");
    }, (error: unknown) => {
      if (request.signal.aborted) return;
      if (isAccountChanged(error)) setState({ status: "account-changed" });
      else setState({ status: error instanceof ApiError && error.status === 404 ? "unavailable" : "error" });
    });
    return () => {
      request.abort();
      pending.current?.abort();
    };
  }, [account, attempt, ticker]);

  async function save() {
    if (state.status !== "ready" || pending.current) return;
    const request = new AbortController();
    pending.current = request;
    setSaving(true);
    setSaveFailed(false);
    try {
      const response = await api.saveChartMemo(ticker, draft, account, request.signal);
      if (request.signal.aborted) return;
      if (!loaded(ticker, response)) throw new Error("Invalid chart memo");
      setState({ status: "ready", memo: response.memo });
      setDraft(response.memo?.text ?? "");
    } catch (error) {
      if (request.signal.aborted) return;
      if (isAccountChanged(error)) {
        setDraft("");
        setState({ status: "account-changed" });
      } else {
        setSaveFailed(true);
      }
    } finally {
      if (!request.signal.aborted) {
        pending.current = null;
        setSaving(false);
      }
    }
  }

  return (
    <section className="daily-resistance-context__group" aria-labelledby={id + "-title"}>
      <h5 id={id + "-title"}>사용자 메모</h5>
      {state.status === "loading" ? <p className="small muted" role="status">메모를 불러오는 중…</p>
        : state.status === "unavailable" ? <p className="small muted">이 상품에 연결된 사용자 메모가 제공되지 않았어요.</p>
          : state.status === "account-changed" ? <p className="small" role="alert">로그인 계정이 바뀌었어요. 화면을 새로 열어 주세요.</p>
          : state.status === "error" ? (
            <p className="small muted">
              메모를 불러오지 못했어요. <button type="button" className="link" onClick={() => {
                setState({ status: "loading" });
                setAttempt((count) => count + 1);
              }}>메모 다시 조회</button>
            </p>
          ) : (
            <form className="chart-memo-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
              {state.memo ? <p className="small muted">
                출처 내 메모 · 작성 <Stamp value={state.memo.created_at} />
                {state.memo.updated_at !== state.memo.created_at && <> · 수정 <Stamp value={state.memo.updated_at} /></>}
              </p> : <p className="small muted">저장된 메모가 없어요.</p>}
              <label htmlFor={id} className="small">{ticker} 메모</label>
              <textarea id={id} maxLength={MAX_LENGTH} rows={3} value={draft} disabled={saving}
                onChange={(event) => setDraft(event.currentTarget.value)} />
              {state.memo && <p className="small muted">내용을 비우고 저장하면 메모를 지워요.</p>}
              {saveFailed && <p className="small" role="alert">메모를 저장하지 못했어요.</p>}
              <button type="submit" disabled={saving || draft.trim() === (state.memo?.text ?? "")}>
                {saving ? "저장 중…" : "메모 저장"}
              </button>
            </form>
          )}
    </section>
  );
}
