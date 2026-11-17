import { useState } from "react";
import { DEFAULT_MARKETS, loadMarkets, type MarketSymbol, saveMarkets, TICKER } from "../markets";
import { sameTicker } from "../ticker";

/** 설정 — 대시보드 "시장" 패널 종목(#75). 이 브라우저에만 저장한다. */
export default function MarketListEditor() {
  const [list, setList] = useState<MarketSymbol[]>(loadMarkets);
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const commit = (next: MarketSymbol[]) => {
    setList(next);
    saveMarkets(next);
  };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= list.length) return;
    const next = [...list];
    [next[i], next[j]] = [next[j], next[i]];
    commit(next);
  };
  const add = (e: React.FormEvent) => {
    e.preventDefault();
    const t = ticker.trim().toUpperCase();
    if (!TICKER.test(t)) return setError("티커는 영문 · 숫자 · . - ^ = 로 20자 이내입니다 (예: NQ=F, ^KS11, BTC-USD).");
    if (list.some((m) => sameTicker(m.ticker, t))) return setError("이미 목록에 있습니다.");
    commit([...list, { ticker: t, name: name.trim() || t }]);
    setTicker("");
    setName("");
    setError(null);
  };

  return (
    <section className="card settings-card">
      <h2>시장 패널 종목</h2>
      <p className="muted small">대시보드 위 "시장" 패널에 띄울 지수 · 선물 · 환율 · 코인입니다. 이 브라우저에만 저장됩니다.</p>
      <ol className="market-list">
        {list.map((m, i) => (
          <li key={m.ticker}>
            <span>
              <strong>{m.name}</strong> <span className="muted small">{m.ticker}</span>
            </span>
            <span className="market-list-actions">
              <button type="button" className="link" disabled={i === 0} aria-label={`${m.name} 위로`} onClick={() => move(i, -1)}>
                ↑
              </button>
              <button type="button" className="link" disabled={i === list.length - 1} aria-label={`${m.name} 아래로`}
                onClick={() => move(i, 1)}>
                ↓
              </button>
              <button type="button" className="link" aria-label={`${m.name} 빼기`} onClick={() => commit(list.filter((x) => x !== m))}>
                빼기
              </button>
            </span>
          </li>
        ))}
      </ol>
      <form className="market-add" onSubmit={add}>
        <input placeholder="티커 (예: CL=F)" value={ticker} onChange={(e) => setTicker(e.target.value)} autoCapitalize="characters"
          spellCheck={false} aria-label="티커" />
        <input placeholder="이름 (예: WTI 원유)" value={name} onChange={(e) => setName(e.target.value)} aria-label="이름" />
        <button type="submit" disabled={!ticker.trim()}>추가</button>
      </form>
      {error && <p className="error small" role="alert">{error}</p>}
      <button type="button" className="link" onClick={() => { saveMarkets(null); setList(DEFAULT_MARKETS); setError(null); }}>
        기본 목록으로 되돌리기
      </button>
    </section>
  );
}
