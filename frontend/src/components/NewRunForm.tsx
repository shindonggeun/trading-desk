import { useEffect, useState } from "react";
import { ANALYST_CHOICES } from "../agents";
import { api } from "../api";
import { HORIZON } from "../korean";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

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
  const [error, setError] = useState<string | null>(null);

  const toggle = (key: string) =>
    setAnalysts((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));

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

  return (
    <form className="card new-run" onSubmit={submit}>
      <h2>새 분석</h2>
      <label>
        종목
        <InstrumentSearch value={instrument} onChange={setInstrument} />
      </label>
      <label>
        분석 기준일
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} required />
      </label>
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
      </fieldset>
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
            : `보유 ${horizons.find((h) => h.id === horizon)?.holding ?? ""} · 지지 · 저항과 손절 · 목표 폭이 이 시간축의 봉으로 정해집니다`}
        </span>
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
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy || !instrument.ticker.trim() || analysts.length === 0}>
        {busy ? "등록 중…" : "분석 시작"}
      </button>
      <p className="muted small">한 번 분석에 수 분이 걸리고, 구독 플랜 사용량이 차감됩니다.</p>
    </form>
  );
}
