import { useEffect, useState } from "react";
import { api } from "../api";
import { time } from "../format";
import { HORIZON } from "../korean";
import type { DeskConfig, MacroBrief as Brief, MacroTheme } from "../types";

// 서버는 30분마다 새로 모은다 — 화면은 10분마다 다시 읽는다. 아직 비었거나 못 읽었으면 자주.
const POLL_MS = 10 * 60_000;
const RETRY_MS = 45_000;
// 외부 기사 주소는 http(s) 만 링크로 — 그 밖의 주소(javascript: 등)는 제목 글자로만 둔다
const SAFE_URL = /^https?:\/\//i;
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

function Headline({ h }: { h: MacroTheme["headlines"][number] }) {
  return (
    <>
      {SAFE_URL.test(h.url) ? (
        <a href={h.url} target="_blank" rel="noopener noreferrer" className="link">{h.title}</a>
      ) : (
        <span>{h.title}</span>
      )}
      <span className="muted small"> {[h.domain, h.seen && time(h.seen)].filter(Boolean).join(" · ")}</span>
    </>
  );
}

/**
 * 대시보드 맨 위 "오늘의 거시 · 정책 브리핑"(#118): 테마별 24시간 기사 수 · 급증 배수 · 톤 · 대표 헤드라인.
 * 테마를 누르면 헤드라인 3개와 대표 종목 분석 시작 버튼. 실패하면 마지막 값과 시각을 그대로 둔다.
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
  const kinds = new Set(brief.themes.map((t) => t.spike_kind));
  const spikeBase = kinds.size > 1 ? "테마마다 기준이 다름(줄에 마우스를 올리면 표시)" : kinds.has("week") ? "평소 주간 대비" : "다른 테마 대비";

  return (
    <section className="card macro-brief">
      <p className="muted small mx-meta">
        {brief.refreshed_at ? `${time(brief.refreshed_at)} 기준` : "아직 모으는 중"}
        {failedAt && ` · ${time(failedAt)} 다시 읽기 실패 — 마지막 값`}
        {brief.error && " · 일부 테마를 가져오지 못했습니다"}
        {" · "}급증은 {spikeBase}
      </p>
      <ul className="mx-themes">
        {brief.themes.map((t) => {
          const hot = t.spike != null && t.spike >= SPIKE_ALERT;
          const expanded = open === t.id;
          const panel = `mx-theme-${t.id}`;
          return (
            <li key={t.id} className={`mx-theme${hot ? " mx-hot" : ""}${t.stale ? " mx-stale" : ""}`}>
              <button type="button" className="mx-row" aria-expanded={expanded} aria-controls={expanded ? panel : undefined}
                aria-label={`${t.label} · 24시간 ${count(t)}건 · 급증 ${spikeText(t)}${t.tone != null ? ` · 톤 ${t.tone.toFixed(1)}` : ""}`}
                onClick={() => {
                  setOpen(expanded ? null : t.id);
                  setPick(null);   // 다른 테마의 같은 종목 확인 창이 따라오지 않게
                  setError(null);
                }}>
                <span className="mx-label">{t.label}</span>
                <span className="mx-count small num" title="24시간 기사 수">{count(t)}건</span>
                <span className={`mx-spike small num${hot ? " mx-spike-hot" : ""}`}
                  title={t.spike_kind === "week" ? "평소 주간 대비" : "다른 테마 대비"}>{hot ? "▲ " : ""}{spikeText(t)}</span>
                <Tone t={t} />
                <span className="mx-head small">{t.headlines[0] ? t.headlines[0].title : <span className="muted">헤드라인 없음</span>}</span>
              </button>
              {expanded && (
                <div id={panel} className="mx-detail">
                  {t.headlines.length > 0 ? (
                    <ul className="mx-headlines small">
                      {t.headlines.slice(0, 3).map((h, i) => <li key={`${i}-${h.url}`}><Headline h={h} /></li>)}
                    </ul>
                  ) : (
                    <p className="muted small">헤드라인이 없습니다.</p>
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
        })}
      </ul>
      <p className="muted small">
        뉴스 흐름의 양과 톤이지 예측이 아닙니다. 급증 ▲ 는 {SPIKE_ALERT}배 이상.{anyRough && " * 표시 톤은 헤드라인 단어로 센 대략값입니다."}
      </p>
    </section>
  );
}
