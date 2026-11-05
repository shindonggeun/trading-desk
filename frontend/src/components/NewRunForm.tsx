import { useEffect, useRef, useState } from "react";
import { ANALYST_CHOICES } from "../agents";
import { api } from "../api";
import { HORIZON } from "../korean";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const SEARCH_ID = "quick-run-search";
// 옵션을 펴 둔 채로 쓰는 사람은 다음에도 펴진 채로(사생활 모드 · 막힌 저장소에서도 동작)
const PREFS_KEY = "desk.quickRun.v1";
function loadOpen(): boolean {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) ?? "{}").options === true;
  } catch {
    return false;
  }
}
function saveOpen(options: boolean) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ options }));
  } catch {
    /* 기억만 못 한다 */
  }
}

/** 입력 중인 칸이면 "/" 단축키를 가로채지 않는다. */
function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

/**
 * 대시보드 맨 위 검색 한 줄(#100): 종목을 고르고 "분석 시작" — 성향 · 시간축 · 애널리스트는 기본값.
 * "옵션" 을 펴면 기준일 · 애널리스트 · 성향 · 시간축 · 언어 · 보유 반영을 바꾼다. "/" 로 검색 칸에 바로 간다.
 */
export default function NewRunForm() {
  const [instrument, setInstrument] = useState<Picked>({ ticker: "" });
  const [date, setDate] = useState(today());
  const [analysts, setAnalysts] = useState<string[]>(ANALYST_CHOICES.map((c) => c.key));
  const [usePortfolio, setUsePortfolio] = useState(true);
  const [language, setLanguage] = useState("Korean");
  const [profiles, setProfiles] = useState<{ id: string; label: string }[]>([]);
  const [profile, setProfile] = useState("");
  const [horizons, setHorizons] = useState(
    Object.entries(HORIZON).map(([id, h]) => ({ id, label: h.ko, holding: h.holding })),
  );
  const [horizon, setHorizon] = useState("swing");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState(loadOpen);
  const startRef = useRef<HTMLButtonElement>(null);
  const justPicked = useRef(false);
  // 목록에서 고르면 "분석 시작" 으로 — 버튼이 활성으로 다시 그려진 뒤에 옮긴다(Enter 한 번으로 시작)
  useEffect(() => {
    if (!justPicked.current) return;
    justPicked.current = false;
    startRef.current?.focus();
  }, [instrument]);

  useEffect(() => {
    api
      .config()
      .then((c) => {
        setProfiles(c.profiles);
        setProfile(c.default_profile);
        if (c.horizons?.length) setHorizons(c.horizons);
        if (c.default_horizon) setHorizon(c.default_horizon);
      })
      .catch(() => {});
  }, []);

  // "/" → 검색 칸(다른 입력 칸에서 친 "/" 는 그대로 글자)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      const input = document.getElementById(SEARCH_ID) as HTMLInputElement | null;
      if (!input) return;
      e.preventDefault();
      input.focus();
      input.select();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const toggle = (key: string) =>
    setAnalysts((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));
  const flipOptions = () => {
    saveOpen(!options);
    setOptions(!options);
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        ticker: instrument.ticker,
        name: instrument.name,
        profile: profile || undefined,
        horizon,
        trade_date: date,
        analysts,
        use_portfolio: usePortfolio,
        output_language: language,
      });
      window.location.hash = `#/runs/${run.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  const profileLabel = profiles.find((p) => p.id === profile)?.label;
  const horizonInfo = horizons.find((h) => h.id === horizon);
  const custom = date !== today() || analysts.length !== ANALYST_CHOICES.length || language !== "Korean" || !usePortfolio;
  const ready = !!instrument.ticker.trim() && analysts.length > 0;

  return (
    <form className="card quick-run" onSubmit={submit} aria-label="새 분석">
      <div className="quick-run-line">
        <label className="sr-only" htmlFor={SEARCH_ID}>분석할 종목</label>
        <InstrumentSearch
          value={instrument}
          onChange={setInstrument}
          onPick={() => { justPicked.current = true; }}
          inputId={SEARCH_ID}
          placeholder="분석할 종목 — 엔비디아 · 삼성전자 · NVDA · 005930   ( / )"
        />
        <button ref={startRef} type="submit" disabled={busy || !ready}>
          {busy ? "등록 중…" : "분석 시작"}
        </button>
        <button type="button" className="secondary" aria-expanded={options} aria-controls="quick-run-options" onClick={flipOptions}>
          옵션
        </button>
      </div>
      <p className="muted small quick-run-summary">
        {horizonInfo?.label ?? horizon}
        {horizonInfo ? ` · 보유 ${horizonInfo.holding}` : ""}
        {profileLabel ? ` · ${profileLabel}` : ""}
        {custom ? " · 옵션 바꿈" : ""} · 한 번에 수 분, 구독 플랜 사용량이 차감됩니다
      </p>
      {error && <p className="error">{error}</p>}
      {options && (
        <div id="quick-run-options" className="quick-run-options">
          <label>
            분석 기준일
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
          </label>
          {profiles.length > 0 && (
            <label>
              투자 성향
              <select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
              <span className="muted small">트레이더 · 리스크 토론 · 최종 판단에 지침으로 들어갑니다</span>
            </label>
          )}
          <fieldset>
            <legend>매매 시간축</legend>
            <div className="chips" role="radiogroup" aria-label="매매 시간축">
              {horizons.map((h) => (
                <label key={h.id} className={`chip ${horizon === h.id ? "chip-on" : ""}`} title={`보유 ${h.holding}`}>
                  <input type="radio" name="horizon" value={h.id} checked={horizon === h.id} onChange={() => setHorizon(h.id)} />
                  {h.label}
                  {h.id === "scalp" && <span className="muted small">&nbsp;(참고)</span>}
                </label>
              ))}
            </div>
            <span className="muted small">
              {horizon === "scalp"
                ? "참고용 — 분석에 수 분이 걸리고 지표는 일봉이라, 계획이 도착했을 때 가격이 진입가 근처일 때만 씁니다(15분 유효)."
                : `보유 ${horizonInfo?.holding ?? ""} · 지지 · 저항과 손절 · 목표 폭이 이 시간축의 봉으로 정해집니다`}
            </span>
          </fieldset>
          <fieldset>
            <legend>애널리스트</legend>
            <div className="chips">
              {ANALYST_CHOICES.map((c) => (
                <label key={c.key} className={`chip ${analysts.includes(c.key) ? "chip-on" : ""}`}>
                  <input type="checkbox" checked={analysts.includes(c.key)} onChange={() => toggle(c.key)} />
                  {c.label}
                </label>
              ))}
            </div>
            {analysts.length === 0 && <span className="error small">한 명 이상 고르세요.</span>}
          </fieldset>
          <label>
            보고서 언어
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="Korean">한국어</option>
              <option value="English">English</option>
            </select>
          </label>
          <label className="inline">
            <input type="checkbox" checked={usePortfolio} onChange={(e) => setUsePortfolio(e.target.checked)} />
            내 보유 종목을 반영해서 판단
          </label>
        </div>
      )}
    </form>
  );
}
