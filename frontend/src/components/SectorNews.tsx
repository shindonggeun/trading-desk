import { useEffect, useState } from "react";
import { api } from "../api";
import { sameTicker } from "../ticker";
import { time } from "../format";
import type { SectorNewsView } from "../types";
import { Impact, Lean, NewsHeadline } from "./NewsRead";

/**
 * #245 섹터 · 인물 이슈(#244 GET /api/sector-news) — 섹터별 기사 급증 이슈 카드: 유형 태그 · 급증 배수 · 영향 ·
 * 대표 헤드라인(한글) · 관련 종목 → 바로 분석. 대시보드는 섹터 칩으로 고르고(이 브라우저에 기억),
 * 섹터 탭은 고른 섹터의 종목과 겹치는 이슈 섹터를 보여 준다.
 */

const POLL_MS = 10 * 60_000;
const PICK_KEY = "desk.sectornews.pick";

function loadPick(): string | null {
  try {
    return localStorage.getItem(PICK_KEY);
  } catch {
    return null;
  }
}
function savePick(id: string) {
  try {
    localStorage.setItem(PICK_KEY, id);
  } catch {
    /* storage blocked: the choice is not kept */
  }
}

const analyze = (t: string) => `#/?ticker=${encodeURIComponent(t)}`;

export default function SectorNews({ tickers, name }: { tickers?: string[]; name?: string } = {}) {
  const [data, setData] = useState<SectorNewsView | null>(null);
  const [failed, setFailed] = useState(false);
  const [pick, setPick] = useState<string | null>(loadPick);
  const linked = tickers !== undefined;

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const load = () => api.sectorNews().then(
      (d) => { if (alive) { setData(d); setFailed(false); } },
      () => alive && setFailed(true),
    ).finally(() => { if (alive) timer = window.setTimeout(load, POLL_MS); });
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, []);

  if (!data) return <p className="muted small">{failed ? "섹터 이슈를 불러오지 못했습니다." : "섹터 이슈를 받는 중…"}</p>;

  // 섹터 탭: 고른 섹터의 종목과 이슈 종목이 겹치거나 이름이 같은 이슈 섹터만
  const sectors = linked
    ? data.sectors.filter((s) => (name && (name.includes(s.label) || s.label.split(" · ").some((w) => name.includes(w))))
        || s.issues.some((i) => i.tickers.some((t) => tickers!.some((x) => sameTicker(x, t)))))
    : data.sectors;
  if (linked && sectors.length === 0) return null;
  const current = linked ? null : sectors.find((s) => s.id === pick) ?? sectors[0];
  const shown = current ? [current] : sectors;

  return (
    <div className={`sn${linked ? " card" : ""}`}>
      {linked && <h3 className="sn-title">섹터 이슈</h3>}
      {!linked && sectors.length > 1 && (
        <div className="chips sn-chips" role="group" aria-label="섹터">
          {sectors.map((s) => (
            <button key={s.id} type="button" className={`chip${s.id === current?.id ? " chip-on" : ""}`} aria-pressed={s.id === current?.id}
              onClick={() => { setPick(s.id); savePick(s.id); }}>
              {s.label}{s.issues.length > 0 && <span className="sn-count"> {s.issues.length}</span>}
            </button>
          ))}
        </div>
      )}
      {shown.map((s) => (
        <div key={s.id} className="sn-sector">
          {linked && <p className="muted small sn-sub">{s.label}</p>}
          {s.issues.length === 0 ? (
            <p className="muted small">
              지금 기사가 몰린 이슈가 없습니다.{" "}
              {s.topics.length > 0 && `보는 주제: ${s.topics.map((t) => `${t.label} ${t.articles_24h}건`).join(" · ")}`}
            </p>
          ) : (
            <ul className="sn-issues">
              {s.issues.map((i) => (
                <li key={i.id} className="sn-issue">
                  <div className="sn-head">
                    <strong>{i.label}</strong>
                    {i.tag_ko && <span className="pill">{i.tag_ko}</span>}
                    {i.spike != null && <span className="sn-spike small num">▲ {i.spike.toFixed(1)}배</span>}
                    <span className="muted small">24시간 {i.articles_24h}건{i.story_size > 1 ? ` · 같은 이야기 ${i.story_size}건` : ""}</span>
                    {i.digest && <><Lean s={i.digest.sentiment} /><Impact v={i.digest.impact} /></>}
                  </div>
                  {i.headlines[0] && <NewsHeadline h={i.headlines[0]} />}
                  {i.headlines.length > 1 && (
                    <details className="small sn-more">
                      <summary>헤드라인 {i.headlines.length - 1}건 더</summary>
                      <ul>{i.headlines.slice(1, 4).map((h) => <li key={h.url}><NewsHeadline h={h} /></li>)}</ul>
                    </details>
                  )}
                  {i.tickers.length > 0 && (
                    <div className="chips sn-tickers" role="group" aria-label={`${i.label} 관련 종목`}>
                      <span className="muted small">관련 종목 · 바로 분석</span>
                      {i.tickers.slice(0, 6).map((t) => <a key={t} className="chip" href={analyze(t)}>{t}</a>)}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      <p className="muted small sn-foot">
        {data.refreshed_at ? `${time(data.refreshed_at)} 기준` : "아직 모으는 중"}{data.error && " · 일부 주제를 가져오지 못했습니다"} · {data.note}
      </p>
    </div>
  );
}
