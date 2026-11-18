import { useState } from "react";
import { api } from "../api";
import { cleanMiniItems, MINI_DEFAULT, MINI_FUTURES, MINI_MAX, MINI_NAME_MAX, MINI_PRESETS, type MiniItem } from "../miniWindow";
import { TICKER } from "../markets";
import { sameTicker } from "../ticker";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";

/**
 * #616 작은 창 종목 고치기 — 보이는 이름(한글) 바꾸기 · 순서 · 빼기 · 더하기(자주 쓰는 것 · 검색 · 즐겨찾기).
 * 설정 화면은 종목 검색을 쓰고, 작은 창 안(⚙)은 검색 목록이 창 밖으로 뜨지 않게 티커 · 이름 칸을 쓴다.
 */
export default function MiniTickerEditor({ items, onChange, search = true }: {
  items: MiniItem[];
  onChange: (items: MiniItem[]) => void;
  search?: boolean;
}) {
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [ticker, setTicker] = useState("");
  const [name, setName] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});   // 고치는 중인 이름 — 칸을 떠날 때 저장
  const full = items.length >= MINI_MAX;

  const set = (next: MiniItem[]) => { setNote(null); onChange(cleanMiniItems(next)); };
  const add = (it: MiniItem) => {
    const t = it.ticker.trim().toUpperCase();
    if (!(t === MINI_FUTURES || TICKER.test(t))) return setNote("티커는 영문 · 숫자와 . - ^ = 만 돼요(예: NVDA, 005930.KS, ^KS11).");
    if (items.some((x) => sameTicker(x.ticker, t))) return setNote(`${it.name || t} 은(는) 이미 있어요.`);
    if (full) return setNote(`작은 창 종목은 ${MINI_MAX}개까지예요.`);
    set([...items, { ticker: t, name: it.name || t }]);
  };
  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    set(next);
  };
  const fromFavorites = async () => {
    try {
      const favs = await api.favorites();
      const fresh = favs.filter((f) => !items.some((x) => sameTicker(x.ticker, f.ticker)));
      const next = cleanMiniItems([...items, ...fresh.map((f) => ({ ticker: f.ticker, name: f.name ?? f.ticker }))]);
      onChange(next);
      setNote(fresh.length ? `즐겨찾기에서 ${next.length - items.length}개를 더했어요.` : "즐겨찾기 종목이 이미 다 있어요.");
    } catch {
      setNote("즐겨찾기를 불러오지 못했어요.");
    }
  };
  const presets = MINI_PRESETS.filter((p) => !items.some((x) => x.ticker === p.ticker));

  return (
    <div className="mini-editor">
      {items.length === 0 ? <p className="muted small">종목이 없어요. 아래에서 더해 주세요.</p> : (
        <ol className="mini-editor-list">
          {items.map((it, i) => (
            <li key={it.ticker}>
              <input aria-label={`${it.ticker} 보이는 이름`} value={draft[it.ticker] ?? it.name} maxLength={MINI_NAME_MAX}
                onChange={(e) => setDraft({ ...draft, [it.ticker]: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); }}
                onBlur={() => {
                  if (draft[it.ticker] === undefined) return;
                  set(items.map((x) => (x.ticker === it.ticker ? { ...x, name: draft[it.ticker] } : x)));
                  setDraft((d) => { const n = { ...d }; delete n[it.ticker]; return n; });
                }} />
              <span className="muted small mini-editor-ticker">{it.ticker === MINI_FUTURES ? "근월물" : it.ticker}</span>
              <button type="button" aria-label={`${it.name} 위로`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button type="button" aria-label={`${it.name} 아래로`} disabled={i === items.length - 1} onClick={() => move(i, 1)}>↓</button>
              <button type="button" aria-label={`${it.name} 빼기`} onClick={() => set(items.filter((x) => x.ticker !== it.ticker))}>×</button>
            </li>
          ))}
        </ol>
      )}
      {presets.length > 0 && !full && (
        <div className="mini-editor-presets" aria-label="자주 쓰는 종목 더하기">
          {presets.map((p) => (
            <button key={p.ticker} type="button" className="chip" onClick={() => add(p)}>+ {p.name}</button>
          ))}
        </div>
      )}
      {!full && (search ? (
        <form className="mini-editor-add" onSubmit={(e) => { e.preventDefault(); if (pick.ticker) { add({ ticker: pick.ticker, name: pick.name ?? "" }); setPick({ ticker: "" }); } }}>
          <InstrumentSearch value={pick} onChange={setPick} placeholder="종목 이름 · 티커로 더하기 (예: 삼성전자, SOXL)" />
          <button type="submit" disabled={!pick.ticker}>더하기</button>
        </form>
      ) : (
        <form className="mini-editor-add" onSubmit={(e) => { e.preventDefault(); if (ticker.trim()) { add({ ticker, name: name.trim() }); setTicker(""); setName(""); } }}>
          <input aria-label="티커" placeholder="티커 (예: NVDA)" value={ticker} onChange={(e) => setTicker(e.target.value)} />
          <input aria-label="보이는 이름" placeholder="이름 (비우면 티커)" value={name} maxLength={MINI_NAME_MAX} onChange={(e) => setName(e.target.value)} />
          <button type="submit" disabled={!ticker.trim()}>더하기</button>
        </form>
      ))}
      <div className="mini-editor-actions">
        <button type="button" className="link" onClick={() => void fromFavorites()} disabled={full}>즐겨찾기에서 가져오기</button>
        <button type="button" className="link" onClick={() => set(MINI_DEFAULT)}>기본 10종으로</button>
      </div>
      {note && <p className="muted small" role="status">{note}</p>}
    </div>
  );
}
