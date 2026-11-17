import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { sampleSettings } from "../paperSample";
import type { PaperSettings as Settings, PaperSettingsPatch } from "../types";

/**
 * #528 설정의 "모의 투자" 칸 — 시작 자금 · 크기 정하는 법 · 동시 보유 · 수수료 · 슬리피지 · 숏 허용(#527).
 * 저장하면 모의 투자 화면이 새 설정으로 처음부터 다시 계산한다. 실제 주문은 없다.
 */

type Form = {
  start_capital: string; start_date: string; sizing: Settings["sizing"]; risk_pct: string; weight_pct: string;
  max_positions: string; fee_kr_pct: string; fee_us_pct: string; tax_kr_pct: string; slippage_pct: string; allow_short: boolean;
};

const commas = (digits: string) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

function toForm(s: Settings): Form {
  return {
    start_capital: commas(String(Math.round(s.start_capital))), start_date: s.start_date ?? "", sizing: s.sizing,
    risk_pct: String(s.risk_pct), weight_pct: String(s.weight_pct), max_positions: String(s.max_positions),
    fee_kr_pct: String(s.fee_kr_pct), fee_us_pct: String(s.fee_us_pct), tax_kr_pct: String(s.tax_kr_pct),
    slippage_pct: String(s.slippage_pct), allow_short: s.allow_short,
  };
}

function num(v: string): number {
  return Number(v.replace(/,/g, "").trim());
}

function toPatch(f: Form): PaperSettingsPatch | string {
  const capital = num(f.start_capital);
  if (!Number.isFinite(capital) || capital < 100_000 || capital > 100_000_000_000) return "시작 자금은 10만 원 ~ 1,000억 원 사이로 적어 주세요.";
  const fields = ["risk_pct", "weight_pct", "max_positions", "fee_kr_pct", "fee_us_pct", "tax_kr_pct", "slippage_pct"] as const;
  const out: PaperSettingsPatch = { start_capital: Math.round(capital), start_date: f.start_date || null, sizing: f.sizing, allow_short: f.allow_short };
  for (const k of fields) {
    const v = num(f[k]);
    if (!Number.isFinite(v) || v < 0) return "숫자 칸을 확인해 주세요.";
    out[k] = k === "max_positions" ? Math.round(v) : v;
  }
  return out;
}

export default function PaperSettings() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [sample, setSample] = useState(false);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    api.paperSettings().then((s) => { setSaved(s); setForm(toForm(s)); })
      .catch((e: unknown) => {
        if (import.meta.env.DEV && e instanceof ApiError && e.status === 404) {   // API(#527) 전 개발 모드
          const s = sampleSettings();
          setSaved(s); setForm(toForm(s)); setSample(true);
        } else {
          setMsg({ ok: false, text: `모의 투자 설정을 불러오지 못했습니다: ${e instanceof Error ? e.message : ""}` });
        }
      });
  }, []);
  // #/settings?paper 로 오면 이 칸으로
  useEffect(() => {
    if (form && window.location.hash.includes("paper")) ref.current?.scrollIntoView({ block: "start" });
  }, [form]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => setForm((f) => (f ? { ...f, [k]: v } : f));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!form) return;
    const patch = toPatch(form);
    if (typeof patch === "string") { setMsg({ ok: false, text: patch }); return; }
    setBusy(true);
    setMsg(null);
    try {
      const s = await api.savePaperSettings(patch);
      setSaved(s); setForm(toForm(s));
      setMsg({ ok: true, text: "저장했어요. 모의 투자 화면이 새 설정으로 처음부터 다시 계산해요." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "저장하지 못했습니다" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card settings-card" id="paper" ref={ref}>
      <h2>모의 투자 <span className="paper-badge">모의 · 실제 주문 없음</span></h2>
      <p className="muted">분석 계획대로 샀다면 어땠을지 원화 모의 계좌로 계산해요. 진짜 돈이나 주문은 움직이지 않아요.</p>
      {sample && <p className="paper-note small">개발용 예시 값이에요(모의 투자 API 준비 중). 저장은 되지 않아요.</p>}
      {form && (
        <form className="paper-form" onSubmit={save}>
          <label>시작 자금(원)
            <input inputMode="numeric" value={form.start_capital}
              onChange={(e) => set("start_capital", commas(e.target.value.replace(/[^\d]/g, "")))} />
          </label>
          <label>시작일 <span className="muted small">(비우면 채점된 분석 전부)</span>
            <input type="date" value={form.start_date} onChange={(e) => set("start_date", e.target.value)} />
          </label>
          <fieldset className="paper-sizing">
            <legend>한 번에 살 크기</legend>
            <label className="paper-radio"><input type="radio" name="sizing" checked={form.sizing === "risk"} onChange={() => set("sizing", "risk")} />
              손절 때 잃는 돈을 잔고의
              <input className="paper-small" inputMode="decimal" value={form.risk_pct} disabled={form.sizing !== "risk"}
                onChange={(e) => set("risk_pct", e.target.value)} aria-label="한 거래 위험 %" />% 로</label>
            <label className="paper-radio"><input type="radio" name="sizing" checked={form.sizing === "weight"} onChange={() => set("sizing", "weight")} />
              잔고의
              <input className="paper-small" inputMode="decimal" value={form.weight_pct} disabled={form.sizing !== "weight"}
                onChange={(e) => set("weight_pct", e.target.value)} aria-label="고정 비중 %" />% 만큼 사기</label>
          </fieldset>
          <label>동시에 들고 있을 최대 종목 수
            <input inputMode="numeric" value={form.max_positions} onChange={(e) => set("max_positions", e.target.value)} />
          </label>
          <div className="paper-costs">
            <label>국장 수수료 %<input inputMode="decimal" value={form.fee_kr_pct} onChange={(e) => set("fee_kr_pct", e.target.value)} /></label>
            <label>미장 수수료 %<input inputMode="decimal" value={form.fee_us_pct} onChange={(e) => set("fee_us_pct", e.target.value)} /></label>
            <label>국장 매도 세금 %<input inputMode="decimal" value={form.tax_kr_pct} onChange={(e) => set("tax_kr_pct", e.target.value)} /></label>
            <label>슬리피지 %<input inputMode="decimal" value={form.slippage_pct} onChange={(e) => set("slippage_pct", e.target.value)} /></label>
          </div>
          <label className="paper-check"><input type="checkbox" checked={form.allow_short} onChange={(e) => set("allow_short", e.target.checked)} />
            숏(하락에 거는) 계획도 모의로 따라가기</label>
          <div className="settings-actions">
            <button type="submit" disabled={busy || sample}>{busy ? "저장 중…" : "저장"}</button>
            {saved && <span className="muted small">설정 버전 {saved.version}</span>}
            <a className="small" href="#/paper">모의 투자 화면 보기</a>
          </div>
        </form>
      )}
      {msg && <p className={`small ${msg.ok ? "muted" : "down"}`} role="status">{msg.text}</p>}
    </section>
  );
}
