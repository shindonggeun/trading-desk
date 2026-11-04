import { useState } from "react";
import { ANALYST_CHOICES } from "../agents";
import { api } from "../api";
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
  const [busy, setBusy] = useState(false);
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
