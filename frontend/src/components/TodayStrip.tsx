import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { byAsked } from "../ticker";
import { useFavorites } from "../favorites";
import type { BoardItem, LivePlaybooks, LongShortItem, MarketMonth, Projection } from "../types";
import { fundBadgeText, useFundChanges } from "../fundBadges";
import { useLatestReport } from "../latestReport";
import { usePoll } from "../poll";

/**
 * #317 대시보드 맨 위 "오늘 볼 것" — 즐겨찾기 가운데 지금 손댈 종목만 이유와 함께 칩으로 모은다.
 * 기존 API 만 쓴다: 실시간 플레이북(구역 근처 · 안), 캘린더(실적 D-3), 예상도(지금가가 다음 봉 68% 범위 밖),
 * 롱/숏 비율(24시간 급변). 하나가 실패해도 나머지 이유로 보인다. 칩을 누르면 그 종목 차트 탭으로.
 */

const MAX = 10;                 // 즐겨찾기 앞 10종목만 — 종목마다 플레이북 · 예상도를 부른다
const REFRESH_MS = 5 * 60_000;
// #644 종목마다 플레이북 · 예상도(최대 20개 요청)는 첫 화면이 그려진 뒤에 — 첫 방문 요청이 몰려 위 띠 · 카드가 늦던 것
const PER_TICKER_AFTER_MS = 2_500;
const LS_JUMP = 5;              // 롱 비율 24시간 변화 %p 이상이면 급변
const EARN_DAYS = 3;

type Reason = { kind: "zone" | "earnings" | "range" | "longshort"; text: string };

const kstDay = (d = new Date()) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
const dayDiff = (a: string, b: string) => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
const nextMonth = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo, 1)).toISOString().slice(0, 7);
};

const SIDE = { long: "롱", short: "숏" } as const;
const STATE = { near: "구역 근처", in_zone: "구역 안", confirmed: "확인됨" } as const;

function zoneReason(pb: LivePlaybooks): Reason | null {
  for (const side of ["long", "short"] as const) {
    const s = pb[side];
    if (s.setup === "none" || !s.state || !(s.state in STATE)) continue;
    return { kind: "zone", text: `${SIDE[side]} ${STATE[s.state as keyof typeof STATE]}` };
  }
  return null;
}

/**
 * 오늘 움직임(기준 종가 → 지금가)이 예상도의 한 봉 68% 범위(p16 ~ p84 폭)를 넘었는가. 예상도는 지금가에서 출발하므로
 * 지금가를 원뿔과 바로 비교하지 않고, 원뿔의 하루 폭을 기준 종가에 대어 본다(일봉 예상도만).
 */
function rangeReason(p: Projection, it: BoardItem | undefined): Reason | null {
  const c = p.cone[0];
  if (!c || p.timeframe !== "1d" || !c.p50 || it?.price == null || !it.close) return null;
  const move = it.price / it.close - 1;
  const up = c.p84 / c.p50 - 1;
  const down = 1 - c.p16 / c.p50;
  const pct = `${move > 0 ? "+" : ""}${(move * 100).toFixed(1)}%`;
  if (move > up) return { kind: "range", text: `예상 범위 위로 이탈(${pct})` };
  if (move < -down) return { kind: "range", text: `예상 범위 아래로 이탈(${pct})` };
  return null;
}

function lsReason(it: LongShortItem | undefined): Reason | null {
  const ch = it?.change_24h?.long_pct;
  if (ch == null || Math.abs(ch) < LS_JUMP) return null;
  return { kind: "longshort", text: `롱 비율 ${ch > 0 ? "+" : ""}${ch.toFixed(1)}%p(24시간)` };
}

export default function TodayStrip() {
  const favs = useFavorites().list;
  const list = (favs ?? []).slice(0, MAX);
  const key = list.map((f) => f.ticker).join(",");
  const [found, setFound] = useState<Record<string, Reason[]> | null>(null);
  const fundCh = useFundChanges(list.map((x) => x.ticker));   // #484 기관 13F 배지
  const report = useLatestReport();   // #496 최신 리포트 한 줄
  const firstRun = useRef(false);

  useEffect(() => {
    if (!key) setFound(favs ? {} : null);
  }, [key, favs]);
  // #626 5분마다(가려진 탭은 쉰다) — 시세 · 롱숏 · 실적 일정 · 플레이북 · 예상 범위를 한 번에
  usePoll(key ? `today-strip:${key}` : null, async () => {
    const tickers = key.split(",");
    const today = kstDay();
    const month = today.slice(0, 7);
    const months = dayDiff(today, `${nextMonth(month)}-01`) <= EARN_DAYS ? [month, nextMonth(month)] : [month];
    // #644 종목별 요청은 처음 한 번만 잠깐 뒤에 — 그사이 시세 · 롱숏 · 일정으로 먼저 그린다
    const later = !firstRun.current;
    firstRun.current = true;
    const perP = (async () => {
      if (later) await new Promise((r) => window.setTimeout(r, PER_TICKER_AFTER_MS));
      return Promise.allSettled(tickers.map((t) => Promise.allSettled([api.playbooksFor(t), api.projection(t, "swing")])));
    })();
    const [board, ls, cal] = await Promise.allSettled([
      api.board(tickers),
      api.longShort(tickers),
      Promise.all(months.map((m) => api.marketMonth(m))),
    ]);
    const quotes: Record<string, BoardItem> = {};
    if (board.status === "fulfilled") Object.assign(quotes, byAsked(tickers, board.value.items as BoardItem[]));
    const lsBy: Record<string, LongShortItem> = {};
    if (ls.status === "fulfilled") Object.assign(lsBy, byAsked(tickers, ls.value.items));
    const earn: Record<string, string> = {};
    if (cal.status === "fulfilled") {
      for (const d of (cal.value as MarketMonth[]).flatMap((m) => m.days)) {
        const left = dayDiff(today, d.date);
        if (left < 0 || left > EARN_DAYS) continue;
        for (const r of d.earnings) if (tickers.includes(r.ticker) && !earn[r.ticker]) earn[r.ticker] = left === 0 ? "오늘 실적" : `실적 D-${left}`;
      }
    }
    const build = (per: PromiseSettledResult<unknown>[]) => {
    const out: Record<string, Reason[]> = {};
    tickers.forEach((t, i) => {
      const reasons: Reason[] = [];
      const got = per[i];
      if (got?.status === "fulfilled") {
        const [pb, proj] = got.value as [PromiseSettledResult<LivePlaybooks>, PromiseSettledResult<Projection>];
        const z = pb.status === "fulfilled" ? zoneReason(pb.value) : null;
        if (z) reasons.push(z);
        const r = proj.status === "fulfilled" ? rangeReason(proj.value, quotes[t]) : null;
        if (r) reasons.push(r);
      }
      if (earn[t]) reasons.push({ kind: "earnings", text: earn[t] });
      const l = lsReason(lsBy[t]);
      if (l) reasons.push(l);
      if (reasons.length) out[t] = reasons;
    });
    return out;
    };
    if (later) setFound(build([]));
    return build(await perP);
  }, REFRESH_MS, { onData: (out) => setFound(out) });

  if (favs === null) return null;
  const names = Object.fromEntries(list.map((f) => [f.ticker, f.name ?? f.ticker]));
  // #484 기관 13F 변화(신규 · 확대 · 축소 · 전량 매도)도 "볼 이유"로 — 다른 이유가 없는 종목은 뒤에 붙인다
  const fundWhy = (t: string) => (fundCh[t] ?? []).map((c) => ({ c, text: fundBadgeText(c) })).filter((x) => x.text);
  const base = found ? Object.entries(found) : [];
  const fundOnly = found ? list.map((f) => f.ticker).filter((t) => !found[t] && fundWhy(t).length) : [];
  const hits: [string, typeof base[number][1]][] = [...base, ...fundOnly.map((t): [string, typeof base[number][1]] => [t, []])];
  return (
    <section className="today-strip" aria-label="오늘 볼 것" data-tour="today">
      <span className="today-title">오늘 볼 것</span>
      {report && (
        <a className="today-chip today-report" href={`#/reports/${encodeURIComponent(report.id)}`} title={report.summary}>
          <span aria-hidden="true">📝</span> <strong>리포트</strong> <span className="today-why">{report.title}</span>
        </a>
      )}
      {!list.length ? (
        <span className="muted small">즐겨찾기를 더하면 구역 근처 · 실적 임박 · 범위 이탈 종목을 여기 모읍니다.</span>
      ) : found === null ? (
        <span className="muted small">즐겨찾기 {list.length}종목을 보는 중…</span>
      ) : hits.length === 0 ? (
        <span className="muted small">지금 볼 종목 없음</span>
      ) : (
        <span className="today-chips">
          {hits.map(([t, rs]) => (
            <a key={t} className="today-chip" href={`#/charts?t=${encodeURIComponent(t)}`} title={`${t} 차트 열기`}>
              <strong>{names[t]}</strong>
              {rs.map((r) => <span key={r.kind} className={`today-why today-${r.kind}`}>{r.text}</span>)}
              {fundWhy(t).map(({ c, text }) => <span key={`fund-${c.fund}`} className="today-why today-fund">{text}</span>)}
            </a>
          ))}
        </span>
      )}
    </section>
  );
}
