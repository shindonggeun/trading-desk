import { useCallback } from "react";
import ChatCore from "../components/ChatCore";

/** #315 메뉴 "채팅" — `#/chat?thread=…&t=KORU&run=…` 로 대화 · 종목 문맥이 주소에 남는다. */
export default function Chat({ query }: { query: URLSearchParams }) {
  const t = query.get("t");
  const ticker = t && /^[A-Za-z0-9.\-^=]{1,15}$/.test(t) ? t.toUpperCase() : null;
  const run = query.get("run");
  const onThread = useCallback((id: string | null) => {
    const q = new URLSearchParams();
    if (id) q.set("thread", id);
    if (ticker) q.set("t", ticker);
    if (run) q.set("run", run);
    window.history.replaceState(null, "", `#/chat${q.toString() ? `?${q}` : ""}`);
  }, [ticker, run]);
  return (
    <main className="page chat-page">
      <ChatCore ticker={ticker} run={run} initialThread={query.get("thread")} onThread={onThread} />
    </main>
  );
}
