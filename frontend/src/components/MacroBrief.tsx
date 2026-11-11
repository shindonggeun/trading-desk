import { useEffect, useState } from "react";
import { api } from "../api";
import { time } from "../format";
import { HORIZON } from "../korean";
import type { DeskConfig, MacroBrief as Brief, MacroLean, MacroTheme } from "../types";
import { Impact, Lean, NewsHeadline } from "./NewsRead";

// 서버는 30분마다 새로 모은다 — 화면은 10분마다 다시 읽는다. 아직 비었거나 못 읽었으면 자주.
const POLL_MS = 10 * 60_000;
const RETRY_MS = 45_000;
// 급증 경보선(desk/macro_news.py 와 같게)
const SPIKE_ALERT = 1.5;

/** 테마를 보고 바로 분석할 대표 종목(ETF · 대형주). 서버엔 관련 종목 목록이 없어 화면이 고른다. */
const THEME_TICKERS: Record<string, { ticker: string; name: string }[]> = {
  us_politics: [{ ticker: "SPY", name: "S&P500 ETF" }, { ticker: "QQQ", name: "나스닥100 ETF" }],
  fed: [{ ticker: "TLT", name: "미 장기채 ETF" }, { ticker: "QQQ", name: "나스닥100 ETF" }],
  tariffs: [{ ticker: "SPY", name: "S&P500 ETF" }, { ticker: "EWY", name: "한국 ETF" }],
  ai_capex: [{ ticker: "NVDA", name: "엔비디아" }, { ticker: "MSFT", name: "마이크로소프트" }],
  chips: [{ ticker: "SOXX", name: "반도체 ETF" }, { ticker: "NVDA", name: "엔비디아" }],
  china: [{ ticker: "FXI", name: "중국 대형주 ETF" }, { ticker: "KWEB", name: "중국 인터넷 ETF" }],
  korea: [{ ticker: "EWY", name: "한국 ETF" }, { ticker: "005930.KS", name: "삼성전자" }],
  geopolitics: [{ ticker: "GLD", name: "금 ETF" }, { ticker: "USO", name: "원유 ETF" }],
};

function count(t: MacroTheme): string {
  return t.saturated ? "100+" : String(t.articles_24h);
}

function spikeText(t: MacroTheme): string {
  if (t.spike == null) return "—";
  return `${t.spike.toFixed(1)}배`;
}

const LEAN_CLASS: Record<MacroLean, string> = { 우호: "up", 부담: "down", 중립: "flat" };
const IMPACT_RANK: Record<string, number> = { high: 0, mid: 1, low: 2 };
// 서버(desk/news_digest.py LEAN_GAP)와 같은 선 — 옛 응답에 lean 이 없을 때만 화면이 센다
const LEAN_GAP = 15;

/** #273 테마의 증시 쪽 방향. 판정이 없으면 null */
function leanOf(t: MacroTheme): MacroLean | null {
  if (!t.digest) return null;
  if (t.digest.lean) return t.digest.lean;
  const { pos, neg } = t.digest.sentiment;
  return pos - neg >= LEAN_GAP ? "우호" : neg - pos >= LEAN_GAP ? "부담" : "중립";
}

/** #274 영향 큰 것 → 급증 → 나머지(서버 순서 유지) */
function rank(themes: MacroTheme[]): MacroTheme[] {
  const key = (t: MacroTheme) => [IMPACT_RANK[t.digest?.impact ?? ""] ?? 3, t.spike != null && t.spike >= SPIKE_ALERT ? 0 : 1];
  return themes.map((t, i) => ({ t, i, k: key(t) }))
    .sort((a, b) => a.k[0] - b.k[0] || a.k[1] - b.k[1] || a.i - b.i)
    .map((x) => x.t);
}

/** 급증 기준을 머리에 한 번 — 서버 spike_basis, 없으면(옛 응답) 테마의 spike_kind 로 */
function spikeBasis(brief: Brief): { short: string; long: string } {
  const kinds = new Set(brief.themes.map((t) => t.spike_kind));
  const b = brief.spike_basis ?? (kinds.size > 1 ? "mixed" : kinds.has("week") ? "24h_vs_prior_6d_avg" : "vs_other_themes");
  if (b === "24h_vs_prior_6d_avg") return { short: "지난 24시간 / 앞 6일 하루 평균", long: "지난 24시간 기사 수 ÷ 그 앞 6일의 하루 평균" };
  if (b === "vs_other_themes") return { short: "다른 테마 대비", long: "지난 24시간 기사 비중을 다른 테마의 중앙값과 비교" };
  return { short: "테마마다 다름(평소 주간 또는 다른 테마 대비)", long: "이 테마는 GDELT 면 평소 주간 대비, 뉴스 피드면 다른 테마 대비" };
}

/** -10..+10 을 가운데 0 기준 막대로. */
function Tone({ t }: { t: MacroTheme }) {
  if (t.tone == null) return <span className="muted small">톤 —</span>;
  const v = Math.max(-10, Math.min(10, t.tone));
  const pct = Math.abs(v) * 5; // 반쪽 50% 까지
  const label = `${t.tone_kind === "headline-words" ? "톤(헤드라인 단어로 센 대략값)" : "톤"} ${v > 0 ? "+" : ""}${v.toFixed(1)}`;
  return (
    <span className="mx-tone" role="img" aria-label={label} title={label}>
      <span className="mx-tone-track">
        <span className={`mx-tone-bar ${v >= 0 ? "mx-tone-pos" : "mx-tone-neg"}`} style={v >= 0 ? { left: "50%", width: `${pct}%` } : { right: "50%", width: `${pct}%` }} />
      </span>
      <span className="small num">{v > 0 ? "+" : ""}{v.toFixed(1)}{t.tone_kind === "headline-words" ? "*" : ""}</span>
    </span>
  );
}


/**
 * 대시보드 맨 위 "오늘의 거시 · 정책 브리핑"(#118, #274): 테마마다 방향 배지(우호 · 부담 · 중립) · 한 줄 결론 ·
 * 급증일 때만 배수. 기준(최근 3일 · 급증 비교 대상)은 머리에 한 번. 테마를 누르면 기사 수 · 톤 · 긍정/부정 ·
 * 헤드라인 3개(N시간 전)와 대표 종목 분석 시작 버튼. 조용한 테마는 맨 아래 접는다. 실패하면 마지막 값을 그대로 둔다.
 */
export default function MacroBrief() {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [failedAt, setFailedAt] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [config, setConfig] = useState<DeskConfig | null>(null);
  const [pick, setPick] = useState<{ ticker: string; name: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const load = () =>
      api.macroBrief()
        .then((b) => {
          if (!alive) return;
          setBrief(b);
          setFailedAt(null);
          return b.themes.length > 0;
        })
        .catch(() => {
          if (alive) setFailedAt(new Date().toISOString());
          return false;
        })
        .then((full) => {
          // 첫 회차 전 · 실패 중이면 45초 뒤, 채워졌으면 10분 뒤
          if (alive) timer = window.setTimeout(load, full ? POLL_MS : RETRY_MS);
        });
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (pick && !config) api.config().then(setConfig).catch(() => {});
  }, [pick, config]);

  async function start() {
    if (!pick) return;
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        ticker: pick.ticker,
        name: pick.name,
        profile: config?.default_profile,
        horizon: config?.default_horizon ?? "swing",
        analysts: config?.analysts ?? ["market", "social", "news", "fundamentals"],
        use_portfolio: false,
        output_language: "Korean",
      });
      window.location.hash = `#/runs/${run.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  if (!brief) {
    return <p className="empty-state">{failedAt ? "브리핑을 불러오지 못했습니다. 잠시 뒤 다시 읽습니다." : "불러오는 중…"}</p>;
  }
  if (!brief.themes.length) {
    return (
      <p className="empty-state">
        {brief.error
          ? "뉴스 흐름을 가져오지 못했습니다. 서버가 다시 모으면 이어서 보입니다."
          : brief.refreshed_at ? "모은 테마가 없습니다." : "서버가 첫 뉴스 흐름을 모으는 중입니다. 잠시 뒤 자동으로 다시 읽습니다."}
      </p>
    );
  }
  const horizonId = config?.default_horizon ?? "swing";
  const horizonLabel = config?.horizons?.find((h) => h.id === horizonId)?.label ?? HORIZON[horizonId]?.ko ?? horizonId;
  const anyRough = brief.themes.some((t) => t.tone_kind === "headline-words");
  const basis = spikeBasis(brief);
  const loud = rank(brief.themes.filter((t) => !t.quiet));
  const quiet = brief.themes.filter((t) => t.quiet);
  const at = brief.refreshed_at ?? brief.as_of;

  const row = (t: MacroTheme) => {
          const hot = t.spike != null && t.spike >= SPIKE_ALERT;
          const expanded = open === t.id;
          const panel = `mx-theme-${t.id}`;
          const lean = leanOf(t);
          const line = t.digest?.line || (t.headlines[0] ? t.headlines[0].title_ko || t.headlines[0].title : null);
          return (
            <li key={t.id} className={`mx-theme${hot ? " mx-hot" : ""}${t.stale ? " mx-stale" : ""}`}>
              <button type="button" className="mx-row mx-compact" aria-expanded={expanded} aria-controls={expanded ? panel : undefined}
                aria-label={`${t.label} · ${lean ?? "판정 없음"} · ${line ?? "헤드라인 없음"}${hot ? ` · 급증 ${spikeText(t)}` : ""}`}
                onClick={() => {
                  setOpen(expanded ? null : t.id);
                  setPick(null);   // 다른 테마의 같은 종목 확인 창이 따라오지 않게
                  setError(null);
                }}>
                <span className={`mx-lean mx-lean-${lean ? LEAN_CLASS[lean] : "none"}`}>{lean ?? "—"}</span>
                <span className="mx-name">{t.label}</span>
                <span className="mx-line">{line ?? <span className="muted">최근 3일 헤드라인 없음</span>}</span>
                {hot && <span className="mx-spike small num mx-spike-hot" title={basis.long}>▲ {spikeText(t)}</span>}
              </button>
              {expanded && (
                <div id={panel} className="mx-detail">
                  {t.about_ko && <p className="muted small mx-about">{t.about_ko}</p>}
                  <div className="mx-stats small">
                    <span className="num">24시간 {count(t)}건</span>
                    <span className={`num${hot ? " mx-spike-hot" : ""}`} title={basis.long}>평소 대비 {spikeText(t)}</span>
                    <Tone t={t} />
                    {t.digest && <Lean s={t.digest.sentiment} />}
                    {t.digest && <Impact v={t.digest.impact} />}
                  </div>
                  {t.headlines.length > 0 ? (
                    <ul className="mx-headlines small">
                      {t.headlines.slice(0, 3).map((h, i) => <li key={`${i}-${h.url}`}><NewsHeadline h={h} /></li>)}
                    </ul>
                  ) : (
                    <p className="muted small">최근 3일 헤드라인이 없습니다.</p>
                  )}
                  {t.stale && <p className="muted small">6시간 넘은 값입니다({time(t.fetched_at)}).</p>}
                  {THEME_TICKERS[t.id] && (
                    <div className="mx-run chips" role="group" aria-label={`${t.label} 관련 분석`}>
                      <span className="muted small">바로 분석</span>
                      {THEME_TICKERS[t.id].map((x) => (
                        <button key={x.ticker} type="button" className={`chip${pick?.ticker === x.ticker ? " chip-on" : ""}`}
                          aria-pressed={pick?.ticker === x.ticker} onClick={() => setPick(x)}>
                          {x.name} <span className="muted">{x.ticker}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {pick && THEME_TICKERS[t.id]?.some((x) => x.ticker === pick.ticker) && (
                    <div className="mx-confirm" role="group" aria-label="분석 시작 확인" aria-live="polite">
                      <p className="small">
                        <strong>{pick.name} ({pick.ticker})</strong> 분석을 시작할까요? 시간축 {horizonLabel} · LLM 쿼터를 한 번 씁니다.
                      </p>
                      {error && <p className="error small">{error}</p>}
                      <div className="mx-confirm-actions">
                        <button type="button" disabled={busy} onClick={start}>{busy ? "시작하는 중…" : "분석 시작"}</button>
                        <button type="button" className="secondary" onClick={() => setPick(null)}>취소</button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </li>
          );
  };

  return (
    <section className="card macro-brief">
      <p className="muted small mx-meta">
        최근 {Math.round((brief.window_h ?? 72) / 24)}일 기사 · 급증 = {basis.short}
        {at ? ` · ${time(at)} 기준` : " · 아직 모으는 중"}
        {failedAt && ` · ${time(failedAt)} 다시 읽기 실패 — 마지막 값`}
        {brief.error && " · 일부 테마를 가져오지 못했습니다"}
      </p>
      <ul className="mx-themes">{loud.map(row)}</ul>
      {quiet.length > 0 && (
        <details className="mx-quiet small">
          <summary>조용한 테마 {quiet.length}개</summary>
          <ul className="mx-themes">{quiet.map(row)}</ul>
        </details>
      )}
      <p className="muted small">
        방향(우호 · 부담 · 중립)과 한 줄 결론은 헤드라인을 모델이 읽은 증시 쪽 해석이지 예측이 아닙니다. 급증 ▲ 는 {SPIKE_ALERT}배 이상.
        {anyRough && " * 표시 톤은 헤드라인 단어로 센 대략값입니다."}
        {brief.digest && brief.themes.some((t) => t.digest) && ` 긍정/부정 % · 영향은 ${brief.digest.note}.`}
      </p>
    </section>
  );
}
