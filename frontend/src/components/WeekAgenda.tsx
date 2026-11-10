import { useEffect, useState } from "react";
import { api } from "../api";
import { addDays, addMonths, analyzeLink, closures, FLAG, todayKst, WHEN } from "../calendar";
import type { CalDay } from "../types";

/**
 * #246 대시보드 "이번 주 일정" — 오늘부터 7일 안의 휴장 · 조기 폐장, 중요도 상 발표, 만기, 관심 종목 실적 중
 * 가까운 5개. 누르면 캘린더의 그날로.
 */

const LIMIT = 5;

type Row = { key: string; date: string; sort: string; when: string; text: string; market: "kr" | "us"; top: boolean; href: string };

function rows(days: CalDay[], from: string, to: string): Row[] {
  const out: Row[] = [];
  for (const d of days) {
    if (d.date < from || d.date > to) continue;
    const href = `#/calendar?m=${d.date.slice(0, 7)}&d=${d.date}`;
    for (const c of closures(d)) {
      out.push({ key: `${d.date}-${c.text}`, date: d.date, sort: `${d.date} 00:00`, when: c.early ? "조기 폐장" : "휴장",
        text: c.text, market: c.market, top: true, href });
    }
    for (const e of d.events) {
      if (e.importance < 2 && e.kind !== "expiry") continue;
      out.push({ key: `${e.id}-${e.at}`, date: d.date, sort: `${d.date} ${e.time_kst}`, when: `${e.time_kst}${e.next_day_kst ? "(+1)" : ""}`,
        text: e.label, market: e.market, top: e.importance >= 2, href });
    }
    for (const r of d.earnings) {
      if (!r.watched) continue;
      out.push({ key: `${r.ticker}-${r.date}`, date: d.date, sort: `${d.date} 99:${r.ticker}`, when: `실적 ${WHEN[r.when]}`,
        text: r.name || r.ticker, market: r.market, top: false, href: analyzeLink(r) });
    }
  }
  return out.sort((a, b) => a.sort.localeCompare(b.sort));
}

export default function WeekAgenda() {
  const [days, setDays] = useState<CalDay[] | null>(null);
  const [failed, setFailed] = useState(false);
  const today = todayKst();
  const end = addDays(today, 6);

  useEffect(() => {
    let alive = true;
    const months = [today.slice(0, 7), ...(end.slice(0, 7) !== today.slice(0, 7) ? [addMonths(today.slice(0, 7), 1)] : [])];
    Promise.all(months.map((m) => api.marketMonth(m))).then(
      (all) => alive && setDays(all.flatMap((m) => m.days)),
      () => alive && setFailed(true),
    );
    return () => { alive = false; };
  }, [today, end]);

  if (failed) return <p className="muted small">일정을 불러오지 못했습니다.</p>;
  if (!days) return <p className="muted small">일정을 받는 중…</p>;
  const list = rows(days, today, end);
  return (
    <div className="wa">
      {list.length === 0 ? <p className="muted small">7일 안에 휴장 · 중요 발표 · 만기 · 관심 종목 실적이 없습니다.</p> : (
        <ul className="wa-list">
          {list.slice(0, LIMIT).map((r) => (
            <li key={r.key}>
              <a className={`wa-row${r.top ? " wa-top" : ""}`} href={r.href}>
                <span className="wa-date num">{Number(r.date.slice(5, 7))}/{Number(r.date.slice(8))}{r.date === today ? " 오늘" : ""}</span>
                <span className="wa-when num muted">{r.when}</span>
                <span className="wa-text">{FLAG[r.market]} {r.text}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
      <p className="small wa-foot">
        {list.length > LIMIT && <span className="muted">외 {list.length - LIMIT}건 · </span>}
        <a href="#/calendar">캘린더 전체 보기 →</a>
      </p>
    </div>
  );
}
