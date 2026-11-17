import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { FAKE_FUNDS } from "../fundsFake";
import type { FundBrief } from "../types";

// #484 새 13F 공시 앱 안 알림: /api/funds 의 각 기관 최신 분기 · 공시일을 이 브라우저가 마지막으로 본 값과 비교한다.
// 처음 열 때는 기억만 하고 알리지 않는다(지난 공시를 한꺼번에 띄우지 않게). 공시는 분기마다라 30분마다 확인하면 충분하다.
// 웹 푸시는 서버에 공시 이벤트가 없어 이번엔 앱 안에서만(#483 계약).

const SEEN_KEY = "desk.funds.seen";
const POLL_MS = 30 * 60_000;
type Seen = Record<string, string>;

const stamp = (f: FundBrief) => (f.latest ? `${f.latest.quarter}|${f.latest.filed_at}` : "");

function readSeen(): Seen | null {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    return raw ? (JSON.parse(raw) as Seen) : null;
  } catch {
    return null;
  }
}
function writeSeen(s: Seen) {
  try { localStorage.setItem(SEEN_KEY, JSON.stringify(s)); } catch { /* 기억 못 하면 다음에 다시 알릴 뿐 */ }
}

type Toast = { id: string; fund: string; stamp: string; text: string; href: string };

export default function FundsAlert() {
  const [shown, setShown] = useState<Toast[]>([]);
  useEffect(() => {
    let alive = true;
    const check = () => {
      if (document.hidden) return;
      // 개발 서버에서 #483 API 가 없으면(404 · 503) 예시 목록으로 알림 흐름을 본다 — 운영은 조용히 넘어간다
      api.funds().catch((e) => {
        if (import.meta.env.DEV && e instanceof ApiError && (e.status === 404 || e.status === 503)) return { funds: FAKE_FUNDS, checked_at: null };
        throw e;
      }).then(({ funds }) => {
        if (!alive) return;
        const seen = readSeen();
        if (!seen) {
          // 처음: 지금 공시를 본 것으로 기억만 한다
          writeSeen(Object.fromEntries(funds.map((f) => [f.id, stamp(f)]).filter(([, s]) => s)));
          return;
        }
        // 본 값은 토스트를 닫거나 눌렀을 때만 적는다 — 다시 그려지거나 새로고침해도 확인할 때까지 남는다
        const fresh: Toast[] = [];
        for (const f of funds) {
          const s = stamp(f);
          if (!s || !f.latest || seen[f.id] === s) continue;
          fresh.push({
            id: `${f.id}-${s}`, fund: f.id, stamp: s,
            text: `${f.name}${f.manager ? `(${f.manager})` : ""} ${f.latest.quarter.replace(/^(\d{4})Q([1-4])$/, "$1년 $2분기")} 13F 공시${f.latest.count != null ? ` — 보유 ${f.latest.count}종목` : ""} 보기`,
            href: `#/funds?fund=${encodeURIComponent(f.id)}&q=${encodeURIComponent(f.latest.quarter)}`,
          });
        }
        if (fresh.length) setShown((cur) => [...cur.filter((t) => !fresh.some((x) => x.id === t.id)), ...fresh]);
      }, () => { /* API 가 없거나 실패하면 조용히 넘어간다 */ });
    };
    check();
    const t = window.setInterval(check, POLL_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  if (!shown.length) return null;
  const close = (id: string) => {
    const t = shown.find((x) => x.id === id);
    if (t) writeSeen({ ...(readSeen() ?? {}), [t.fund]: t.stamp });
    setShown((s) => s.filter((x) => x.id !== id));
  };
  return (
    <div className="flow-toasts fund-toasts" role="status" aria-live="polite">
      {shown.map((t) => (
        <div key={t.id} className="flow-toast fund-toast">
          <a href={t.href} onClick={() => close(t.id)}>{t.text}</a>
          <button type="button" className="flow-toast-close" aria-label="닫기" onClick={() => close(t.id)}>×</button>
        </div>
      ))}
    </div>
  );
}
