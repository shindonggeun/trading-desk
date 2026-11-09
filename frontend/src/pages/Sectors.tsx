import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { boardBadge } from "../korean";
import { Spark } from "../components/FavoritesPanel";
import FavoriteStar from "../components/FavoriteStar";
import InstrumentSearch, { type Picked } from "../components/InstrumentSearch";
import PositioningView, { CreditSummary, positioningBadges } from "../components/PositioningView";
import { price, time } from "../format";
import type { BoardItem, CreditView, DeskConfig, Positioning, Run, Sector } from "../types";

const QUOTE_MS = 15_000;
const POSITIONING_MS = 15 * 60_000;
const US = /^[A-Z]{1,6}(?:[.-][A-Z])?$/;
const isKorean = (t: string) => /\.(KS|KQ)$/i.test(t);

type SortKey = "base" | "change" | "short" | "pc";
const SORTS: { id: SortKey; label: string }[] = [
  { id: "base", label: "기본" },
  { id: "change", label: "등락" },
  { id: "short", label: "공매도" },
  { id: "pc", label: "풋/콜" },
];

function sectorFromHash(): number | null {
  const m = window.location.hash.match(/^#\/sectors\/(\d+)/);
  return m ? Number(m[1]) : null;
}

function mean(values: (number | null | undefined)[]): number | null {
  const v = values.filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

/** 아래에서 올라오는 종목 시트: 포지션 요약 · 마지막 분석 · 분석 시작 · 차트 · 즐겨찾기 */
function TickerSheet({ ticker, name, item, p, onClose }: {
  ticker: string; name: string; item: BoardItem | undefined; p: Positioning | null | undefined; onClose: () => void;
}) {
  const [run, setRun] = useState<Run | null | undefined>(undefined);
  const [config, setConfig] = useState<DeskConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    api.runs(1, 100).then((page) => setRun(page.items.find((r) => r.ticker === ticker && r.status === "done" && r.plan) ?? null))
      .catch(() => setRun(null));
    api.config().then(setConfig, () => {});
  }, [ticker]);
  // 모달: 초점은 시트 안에서만 돌고, 닫으면 연 타일로 돌아간다. 뒤 페이지는 스크롤하지 않는다.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = [...panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), [tabindex='0']")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      opener?.focus();
    };
  }, [onClose]);

  async function analyze() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createRun({
        ticker, name, profile: config?.default_profile, horizon: config?.default_horizon ?? "swing",
        analysts: config?.analysts ?? ["market", "social", "news", "fundamentals"], use_portfolio: false,
        output_language: "Korean",
      });
      window.location.hash = `#/runs/${created.id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  const ch = item?.change_pct;
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={`${name} 요약`} tabIndex={-1} ref={panel}
        onClick={(e) => e.stopPropagation()}>
        <div className="sheet-grip" aria-hidden />
        <div className="sheet-head">
          <div>
            <h3>{name} <span className="muted small">{ticker}</span></h3>
            <div className="num">
              {price(item?.price ?? null)}{" "}
              <span className={`small ${ch == null ? "" : ch > 0 ? "up" : ch < 0 ? "down" : ""}`}>
                {ch != null ? `${ch > 0 ? "+" : ""}${ch.toFixed(2)}%` : ""}
              </span>
              {item?.state === "estimate" && <span className="qb-badge qb-estimate">추정</span>}
            </div>
          </div>
          <span className="sheet-head-tools">
            <FavoriteStar ticker={ticker} name={name} />
            <button type="button" className="secondary sheet-close" onClick={onClose} aria-label="닫기">×</button>
          </span>
        </div>
        <PositioningView p={p} />
        <div className="sheet-plan muted small">
          {run === undefined ? "마지막 분석 확인 중…" : run?.plan ? (
            <>마지막 분석 <a className="link" href={`#/runs/${run.id}`}>{run.trade_date}</a> · {run.plan.rating} · 진입 {price(run.plan.entry_price)}
              {" "}· 손절 {price(run.plan.stop_loss)} · 목표 {price(run.plan.price_target)}</>
          ) : "이 종목의 완료된 분석이 없습니다."}
        </div>
        {error && <p className="error small">{error}</p>}
        <div className="sheet-actions">
          <button type="button" disabled={busy || !config} onClick={analyze}>{busy ? "등록 중…" : "분석 시작"}</button>
          <a className="button secondary" href={`#/chart/${encodeURIComponent(ticker)}`}>차트</a>
          <a className="button secondary" href={`#/live?t=${encodeURIComponent(ticker)}`}>시세판</a>
        </div>
        <p className="muted small">분석은 수 분이 걸리고 구독 플랜 사용량이 차감됩니다.</p>
      </div>
    </div>
  );
}

/** 섹터 편집: 이름 · 종목 넣고 빼기 · 순서 · 삭제 */
function SectorEditor({ sector, index, count, onSaved, onMove, onDeleted }: {
  sector: Sector; index: number; count: number; onSaved: (s: Sector) => void; onMove: (by: -1 | 1) => void; onDeleted: () => void;
}) {
  const [name, setName] = useState(sector.name);
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => setName(sector.name), [sector.name]);
  // 한 번에 하나: 종목 목록은 통째로 바뀌므로 앞 저장이 끝나기 전에 다음 목록을 만들지 않는다
  const save = (patch: Parameters<typeof api.updateSector>[1]) => {
    setSaving(true);
    return api.updateSector(sector.id, patch).then((s) => { setError(null); onSaved(s); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setSaving(false));
  };
  const add = () => {
    const t = pick.ticker.trim().toUpperCase();
    if (!t) return;
    if (!sector.tickers.some((x) => x.ticker === t)) save({ tickers: [...sector.tickers, { ticker: t, name: pick.name ?? null }] });
    setPick({ ticker: "" });
  };
  return (
    <section className="card sector-editor" aria-label={`${sector.name} 편집`}>
      <form className="se-row" onSubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== sector.name) save({ name: name.trim() }); }}>
        <label className="sr-only" htmlFor={`se-name-${sector.id}`}>섹터 이름</label>
        <input id={`se-name-${sector.id}`} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        <button type="submit" className="secondary" disabled={saving || !name.trim() || name.trim() === sector.name}>이름 저장</button>
        <span className="se-move" role="group" aria-label="섹터 순서">
          <button type="button" className="secondary" disabled={index === 0} aria-label="앞으로" onClick={() => onMove(-1)}>←</button>
          <button type="button" className="secondary" disabled={index === count - 1} aria-label="뒤로" onClick={() => onMove(1)}>→</button>
        </span>
      </form>
      <form className="se-row" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <InstrumentSearch value={pick} onChange={setPick} />
        <button type="submit" disabled={saving || !pick.ticker.trim() || sector.tickers.length >= 30}>종목 추가</button>
      </form>
      <ul className="se-tickers">
        {sector.tickers.map((t) => (
          <li key={t.ticker}>
            <span>{t.name ?? t.ticker} <span className="muted small">{t.ticker}</span></span>
            <button type="button" className="link" disabled={saving} aria-label={`${t.name ?? t.ticker} 빼기`}
              onClick={() => save({ tickers: sector.tickers.filter((x) => x.ticker !== t.ticker) })}>×</button>
          </li>
        ))}
      </ul>
      {error && <p className="error small">{error}</p>}
      <button type="button" className="link small se-danger"
        onClick={() => {
          if (!window.confirm(`"${sector.name}" 섹터를 지울까요? (섹터를 모두 지우면 기본 섹터가 다시 생깁니다)`)) return;
          api.deleteSector(sector.id).then(onDeleted, (e) => setError(e instanceof Error ? e.message : String(e)));
        }}>
        섹터 지우기
      </button>
    </section>
  );
}

/**
 * 섹터 탭(#157): 즐겨찾는 섹터(서버 저장 #156)를 칩으로 고르면 그 섹터 종목의 시세 · 장외 추정가 · 작은 차트와
 * 공매도 · 옵션 배지, 섹터 요약(평균 등락 · 오른 종목 · 풋/콜 · 공매도 비중)을 본다. 국내 섹터면 빚투 · 예탁금도 위에.
 * 휴대폰 한 손: 칩 가로 넘김 · 2열 타일 · 아래 시트.
 */
export default function Sectors() {
  const [sectors, setSectors] = useState<Sector[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<number | null>(sectorFromHash);
  const [items, setItems] = useState<Record<string, BoardItem>>({});
  const [pos, setPos] = useState<Record<string, Positioning | null>>({});
  const [credit, setCredit] = useState<CreditView | null | undefined>(undefined);
  const [sort, setSort] = useState<SortKey>("base");
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState("");
  const [sheet, setSheet] = useState<string | null>(null);
  const [updated, setUpdated] = useState<string | null>(null);

  // 칩 줄을 위 메뉴(휴대폰에선 두 줄, 고정) 바로 밑에 붙이려고 메뉴 높이를 CSS 로 넘긴다(대시보드와 같은 방식)
  useLayoutEffect(() => {
    const bar = document.querySelector<HTMLElement>(".topbar");
    if (!bar) return;
    const set = () => document.documentElement.style.setProperty("--topbar-h", `${Math.round(bar.getBoundingClientRect().height)}px`);
    set();
    const ro = new ResizeObserver(set);
    ro.observe(bar);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--topbar-h");
    };
  }, []);

  const load = () => api.sectors().then((s) => { setSectors(s); setError(null); })
    .catch((e) => { setError(e instanceof Error ? e.message : String(e)); setSectors((cur) => cur ?? []); });
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const on = () => setActiveId(sectorFromHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const sector = sectors?.find((s) => s.id === activeId) ?? sectors?.[0];
  const tickers = useMemo(() => sector?.tickers.map((t) => t.ticker) ?? [], [sector]);
  const key = tickers.join(",");

  // 시세: 15초마다(가려진 탭은 쉰다)
  useEffect(() => {
    if (!key) return;
    let alive = true;
    const tick = () => {
      if (document.hidden) return;
      api.board(key.split(",")).then((r) => {
        if (!alive) return;
        setItems((cur) => ({ ...cur, ...Object.fromEntries(r.items.map((it) => [it.ticker, it])) }));
        setUpdated(new Date().toISOString());
      }).catch(() => {});
    };
    tick();
    const t = window.setInterval(tick, QUOTE_MS);
    const onShow = () => { if (!document.hidden) tick(); };   // 가려진 동안 건너뛴 차례를 바로 채운다
    document.addEventListener("visibilitychange", onShow);
    return () => { alive = false; window.clearInterval(t); document.removeEventListener("visibilitychange", onShow); };
  }, [key]);

  // 공매도 · 옵션: 미국 종목만, 15분마다
  useEffect(() => {
    const us = key.split(",").filter((t) => US.test(t));
    if (!us.length) return;
    let alive = true;
    let missed = false;
    const tick = () => {
      if (document.hidden) {
        missed = true;
        return;
      }
      missed = false;
      api.positioningBatch(us).then((r) => {
        if (alive) setPos((cur) => ({ ...cur, ...Object.fromEntries(r.items.map((p) => [p.ticker, p])) }));
      }).catch(() => alive && setPos((cur) => ({ ...cur, ...Object.fromEntries(us.filter((t) => !(t in cur)).map((t) => [t, null])) })));
    };
    tick();
    const t = window.setInterval(tick, POSITIONING_MS);
    const onShow = () => { if (!document.hidden && missed) tick(); };
    document.addEventListener("visibilitychange", onShow);
    return () => { alive = false; window.clearInterval(t); document.removeEventListener("visibilitychange", onShow); };
  }, [key]);

  const korean = tickers.some(isKorean);
  useEffect(() => {
    if (korean && credit === undefined) api.credit(30).then(setCredit, () => setCredit(null));
  }, [korean, credit]);

  const rows = useMemo(() => {
    // undefined = 아직, null = 실패(시트가 "불러오지 못했습니다"로 말한다)
    const list = (sector?.tickers ?? []).map((t, i) => ({ ...t, i, it: items[t.ticker], p: t.ticker in pos ? pos[t.ticker] : undefined }));
    const by: Record<SortKey, (r: typeof list[number]) => number> = {
      base: (r) => r.i,
      change: (r) => -(r.it?.change_pct ?? -1e9),
      short: (r) => -(r.p?.short?.volume_ratio ?? -1e9),
      pc: (r) => -(r.p?.options?.put_call_oi ?? -1e9),
    };
    return [...list].sort((a, b) => by[sort](a) - by[sort](b) || a.i - b.i);
  }, [sector, items, pos, sort]);

  const changes = rows.map((r) => r.it?.change_pct);
  const avgChange = mean(changes);
  const up = changes.filter((c) => (c ?? 0) > 0).length;
  const avgPc = mean(rows.map((r) => r.p?.options?.put_call_oi));
  const avgShort = mean(rows.map((r) => r.p?.short?.volume_ratio));

  const pickSector = (id: number) => {
    setActiveId(id);
    window.history.replaceState(null, "", `#/sectors/${id}`);
  };
  const replace = (s: Sector) => setSectors((cur) => cur?.map((x) => (x.id === s.id ? s : x)) ?? cur);
  const move = (by: -1 | 1) => {
    if (!sectors || !sector) return;
    const ids = sectors.map((s) => s.id);
    const i = ids.indexOf(sector.id);
    const j = i + by;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    // 다른 탭에서 바뀌었으면(422) 다시 읽는다
    pickSector(sector.id);   // 옮긴 섹터를 계속 보고 있게
    api.reorderSectors(ids).then(setSectors, () => {
      setError("다른 곳에서 섹터가 바뀌어 다시 읽었습니다. 한 번 더 옮겨 주세요.");
      load();
    });
  };
  const create = () => {
    const name = creating.trim();
    if (!name) return;
    api.createSector(name).then((s) => { setSectors((cur) => [...(cur ?? []), s]); pickSector(s.id); setCreating(""); setError(null); setEditing(true); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  };

  const closeSheet = useCallback(() => setSheet(null), []);
  if (sectors === null) return <main className="sectors"><p className="empty-state">불러오는 중…</p></main>;
  const sheetRow = rows.find((r) => r.ticker === sheet);
  return (
    <main className="sectors">
      <div className="sector-chips">
        <span className="sector-chip-list" role="group" aria-label="섹터">
          {sectors.map((s) => (
            <button key={s.id} type="button" aria-pressed={s.id === sector?.id}
              className={`chip${s.id === sector?.id ? " chip-on" : ""}`} onClick={() => pickSector(s.id)}>{s.name}</button>
          ))}
        </span>
        <button type="button" className="chip sector-edit" aria-pressed={editing} onClick={() => setEditing((v) => !v)}>{editing ? "편집 끝" : "편집"}</button>
      </div>
      {error && <p className="error small">{error}</p>}
      {editing && (
        <form className="card se-new" onSubmit={(e) => { e.preventDefault(); create(); }}>
          <label className="sr-only" htmlFor="se-new-name">새 섹터 이름</label>
          <input id="se-new-name" placeholder="새 섹터 이름 (예: AI 전력)" value={creating} maxLength={40}
            onChange={(e) => setCreating(e.target.value)} />
          <button type="submit" disabled={!creating.trim() || sectors.length >= 20}>섹터 추가</button>
        </form>
      )}
      {!sector ? (
        <p className="empty-state">섹터가 없습니다. 편집에서 섹터를 추가하세요.</p>
      ) : (
        <>
          {editing && (
            <SectorEditor key={sector.id} sector={sector} index={sectors.indexOf(sector)} count={sectors.length} onSaved={replace} onMove={move}
              onDeleted={() => {
                // 이웃 섹터로(지운 섹터의 주소에 머물지 않게)
                const at = sectors.indexOf(sector);
                const next = sectors[at + 1] ?? sectors[at - 1];
                setSectors((cur) => cur?.filter((s) => s.id !== sector.id) ?? cur);
                if (next) pickSector(next.id);
                else { setActiveId(null); window.history.replaceState(null, "", "#/sectors"); }
                load();
              }} />
          )}
          <section className="card sector-summary" aria-label={`${sector.name} 요약`}>
            <div className="ss-item"><span className="muted small">평균 등락</span>
              <strong className={`num ${avgChange == null ? "" : avgChange > 0 ? "up" : avgChange < 0 ? "down" : ""}`}>
                {avgChange == null ? "—" : `${avgChange > 0 ? "+" : ""}${avgChange.toFixed(2)}%`}</strong></div>
            <div className="ss-item"><span className="muted small">오른 종목</span><strong className="num">{up}/{rows.length}</strong></div>
            <div className="ss-item"><span className="muted small">풋/콜 평균</span><strong className="num">{avgPc == null ? "—" : avgPc.toFixed(2)}</strong></div>
            <div className="ss-item"><span className="muted small">공매도 비중</span><strong className="num">{avgShort == null ? "—" : `${avgShort.toFixed(1)}%`}</strong></div>
          </section>
          {korean && (
            <section className="card" aria-label="빚투 · 예탁금"><CreditSummary c={credit} /></section>
          )}
          <div className="sector-sort chips" role="group" aria-label="정렬">
            {SORTS.map((s) => (
              <button key={s.id} type="button" className={`chip${sort === s.id ? " chip-on" : ""}`} aria-pressed={sort === s.id}
                onClick={() => setSort(s.id)}>{s.label}</button>
            ))}
            <span className="muted small">{updated && rows.some((r) => r.it) ? `${time(updated)} 갱신` : "불러오는 중"}</span>
          </div>
          {rows.length === 0 ? (
            <p className="empty-state">이 섹터에 종목이 없습니다. 편집에서 종목을 추가하세요.</p>
          ) : (
            <div className="qb-grid sector-grid">
              {rows.map((r) => {
                const ch = r.it?.change_pct;
                const tone = ch == null ? "" : ch > 0 ? "up" : ch < 0 ? "down" : "";
                const name = r.name ?? r.ticker;
                return (
                  <button key={r.ticker} type="button" className={`qb-tile sector-tile${r.it?.state === "estimate" ? " qb-est" : ""}`}
                    onClick={() => setSheet(r.ticker)} aria-haspopup="dialog">
                    <span className="qb-top">
                      <span className="qb-name">{name}</span>
                      <span className={`qb-badge qb-${r.it?.state ?? "wait"}`}>
                        {r.it ? boardBadge(r.it) : "…"}
                      </span>
                    </span>
                    <span className="qb-price num">
                      {price(r.it?.price ?? null)}
                      <span className={`small ${tone}`}>{ch != null ? ` ${ch > 0 ? "+" : ""}${ch.toFixed(2)}%` : ""}</span>
                    </span>
                    <Spark ticker={r.ticker} tone={tone} order={r.i} />
                    <span className="sector-badges">
                      {positioningBadges(r.p).map((b) => <span key={b.text} className={`sector-badge ${b.tone}`}>{b.text}</span>)}
                      {r.p?.short?.volume_ratio != null && <span className="muted small">공매도 {r.p.short.volume_ratio}%</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          <p className="muted small qb-note">공매도 · 옵션은 미국 상장 종목만(FINRA · Cboe, 15분 지연), 추정가는 바이낸스 무기한 선물 기준이며 실제 체결이 아닙니다.</p>
        </>
      )}
      {sheetRow && (
        <TickerSheet ticker={sheetRow.ticker} name={sheetRow.name ?? sheetRow.ticker} item={sheetRow.it}
          p={US.test(sheetRow.ticker) ? sheetRow.p : { ticker: sheetRow.ticker, supported: false, reason: "국내 · 지수 · 선물은 아직 공매도 · 옵션 요약을 지원하지 않습니다(미국 상장 종목만)", short: null, options: null, verdict: null, errors: null, sources: [], fetched_at: "" }}
          onClose={closeSheet} />
      )}
    </main>
  );
}
