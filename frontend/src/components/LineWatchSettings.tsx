import { useEffect, useState } from "react";
import { api, ApiError, type LineWatch, type LineWatchEvent } from "../api";
import type { Favorite } from "../types";
import { chartHref, kstHhmm, signalTime, LINE_EVENT_KO, LINE_EVENT_NOTE, LINE_TF_KO, SHORT_TFS, signalKey, splitText } from "../lineSignals";
import { TICKER } from "../markets";
import { sameTicker } from "../ticker";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";

type Body = Partial<Pick<LineWatch, "enabled" | "tickers" | "timeframes" | "events">>;

/**
 * #614 설정 → 차트 선 알림(#613): 고른 종목 · 시간봉의 평행 채널 · 짧은 추세선에 닿거나 이탈하면 웹 푸시 · 텔레그램 · 카카오톡으로.
 * 바꿀 때마다 서버에 저장한다(다시 열어도 남는다). 감시는 켜기 전까지 꺼짐, 바꿀 수 있는 사람은 데스크 주인뿐.
 */
export default function LineWatchSettings() {
  const [w, setW] = useState<LineWatch | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [pick, setPick] = useState<Picked>({ ticker: "" });
  const [favs, setFavs] = useState<Favorite[]>([]);

  useEffect(() => {
    let alive = true;
    api.lineWatch().then((v) => alive && setW(v), (e) => alive && (e instanceof ApiError && e.status === 404 ? setMissing(true) : setNote("설정을 불러오지 못했어요.")));
    api.favorites().then((f) => alive && setFavs(f), () => {});
    return () => { alive = false; };
  }, []);

  if (missing) return null;   // 서버에 #613 이 아직 없다
  const save = async (body: Body, done?: string) => {
    if (!w) return;
    const before = w;
    // 누른 대로 먼저 보이고(저장은 뒤에서), 실패하면 되돌린다
    setW({ ...w, ...body, events: { ...w.events, ...(body.events ?? {}) } });
    setBusy(true);
    setNote(null);
    try {
      setW(await api.saveLineWatch(body));
      if (done) setNote(done);
    } catch (e) {
      setW(before);
      setNote(e instanceof ApiError && e.status === 422 ? "켜려면 종목과 시간봉을 하나 이상 골라 주세요." : "저장하지 못했어요. 잠시 뒤 다시 해 주세요.");
    } finally {
      setBusy(false);
    }
  };
  if (!w) {
    return (
      <section className="card settings-card">
        <h2>차트 선 알림</h2>
        <p className="muted small">{note ?? "불러오는 중…"}</p>
      </section>
    );
  }
  const locked = !w.can_edit || busy;
  const full = w.tickers.length >= w.allowed.max_tickers;
  const addTicker = (raw: string) => {
    const t = raw.trim().toUpperCase();
    if (!TICKER.test(t)) return setNote("티커는 영문 · 숫자와 . - ^ = 만 돼요(예: NQ=F, 005930.KS).");
    if (w.tickers.some((x) => sameTicker(x, t))) return setNote(`${t} 은(는) 이미 있어요.`);
    if (full) return setNote(`종목은 ${w.allowed.max_tickers}개까지예요.`);
    void save({ tickers: [...w.tickers, t] });
  };
  const toggleTf = (tf: string) => save({ timeframes: w.timeframes.includes(tf) ? w.timeframes.filter((x) => x !== tf) : [...w.timeframes, tf] });
  const toggleEvent = (ev: LineWatchEvent) => save({ events: { [ev]: !w.events[ev] } });
  const shortOn = w.allowed.timeframes.filter((tf) => w.timeframes.includes(tf) && SHORT_TFS.has(tf));
  const favLeft = favs.filter((f) => !w.tickers.some((t) => sameTicker(t, f.ticker))).slice(0, 12);

  return (
    <section className="card settings-card line-watch">
      <h2>차트 선 알림</h2>
      <p className="muted">
        고른 종목 · 시간봉의 평행 채널 · 짧은 추세선(차트의 "평행 채널" · "짧은 추세선")에 가격이 닿거나 이탈하면, 그 봉이 끝난 직후 알려 드려요.
        웹 푸시 · 텔레그램 · 카카오톡의 "차트 선" 종류가 켜져 있어야 받아요.
      </p>
      <label className="line-watch-on">
        <input type="checkbox" checked={w.enabled} disabled={locked} onChange={() => save({ enabled: !w.enabled }, w.enabled ? "선 알림을 껐어요." : "선 알림을 켰어요.")} />
        <strong>선 알림 받기</strong> <span className="muted small">{w.enabled ? "켜짐" : "꺼짐 — 켜기 전까지는 아무것도 보내지 않아요"}</span>
      </label>

      <h3>종목 <span className="muted small">{w.tickers.length}/{w.allowed.max_tickers}</span></h3>
      {w.tickers.length === 0 ? <p className="muted small">종목이 없어요. 아래에서 더해 주세요.</p> : (
        <ul className="chips line-watch-tickers" aria-label="감시 종목">
          {w.tickers.map((t) => (
            <li key={t} className="chip chip-on">
              {t}
              <button type="button" className="chip-x" aria-label={`${t} 빼기`} disabled={locked} onClick={() => save({ tickers: w.tickers.filter((x) => x !== t) })}>×</button>
            </li>
          ))}
        </ul>
      )}
      {!full && w.can_edit && (
        <>
          <form className="line-watch-add" onSubmit={(e) => { e.preventDefault(); if (pick.ticker) { addTicker(pick.ticker); setPick({ ticker: "" }); } }}>
            <InstrumentSearch value={pick} onChange={setPick} placeholder="종목 이름 · 티커로 더하기 (예: NQ=F, 삼성전자)" />
            <button type="submit" disabled={!pick.ticker || busy}>더하기</button>
          </form>
          {favLeft.length > 0 && (
            <div className="chips" aria-label="즐겨찾기에서 더하기">
              <span className="muted small">즐겨찾기에서</span>
              {favLeft.map((f) => (
                <button key={f.ticker} type="button" className="chip" disabled={busy} onClick={() => addTicker(f.ticker)}>+ {f.name ?? f.ticker}</button>
              ))}
            </div>
          )}
        </>
      )}

      <h3>시간봉</h3>
      <div className="chips" role="group" aria-label="감시 시간봉">
        {w.allowed.timeframes.map((tf) => {
          const on = w.timeframes.includes(tf);
          return (
            <button key={tf} type="button" className={`chip${on ? " chip-on" : ""}`} aria-pressed={on} disabled={locked} onClick={() => void toggleTf(tf)}>
              <span aria-hidden="true" className="push-mark">{on ? "✓" : "＋"}</span>{LINE_TF_KO[tf] ?? tf}
            </button>
          );
        })}
      </div>
      {shortOn.length > 0 && (
        <p className="small line-watch-warn">
          ⚠ {shortOn.map((tf) => LINE_TF_KO[tf] ?? tf).join(" · ")} 같은 짧은 봉은 잡음이 많아 선에 닿고 이탈하는 일이 잦고, 과거에도 아무 선보다 잘 맞지 않았어요. 눈금으로만 봐 주세요.
        </p>
      )}

      <h3>사건</h3>
      <div className="chips" role="group" aria-label="알릴 사건">
        {w.allowed.events.map((ev) => {
          const on = !!w.events[ev];
          return (
            <button key={ev} type="button" className={`chip${on ? " chip-on" : ""}`} aria-pressed={on} disabled={locked} title={LINE_EVENT_NOTE[ev]} onClick={() => void toggleEvent(ev)}>
              <span aria-hidden="true" className="push-mark">{on ? "✓" : "＋"}</span>{LINE_EVENT_KO[ev] ?? ev}
            </button>
          );
        })}
      </div>
      <p className="muted small">
        {w.allowed.events.map((ev) => `${LINE_EVENT_KO[ev]}: ${LINE_EVENT_NOTE[ev]}`).join(" · ")}.
        같은 선 · 사건은 하루 한 번, 같은 종목 · 시간봉은 {w.limits.cooldown_min}분(또는 {w.limits.cooldown_bars}봉) 쉬고, 하루 {w.limits.daily_max}건 · 종목당 {w.limits.per_ticker_max}건까지예요.
        매수 · 매도 추천이 아니에요.
      </p>
      {!w.can_edit && <p className="muted small">설정은 데스크 주인만 바꿀 수 있어요.</p>}
      {note && <p className="settings-notice" role="status">{note}</p>}

      {w.recent.length > 0 && (
        <>
          <h3>최근 보낸 선 알림</h3>
          <ol className="line-watch-recent">
            {w.recent.slice(0, 8).map((s) => {
              const { title, body } = splitText(s.text);
              return (
                <li key={signalKey(s)}>
                  <a href={chartHref(s)}>
                    <span className="muted small">{s.trade_date.slice(5)} {kstHhmm(signalTime(s))}</span> <strong>{title}</strong>
                  </a>
                  {body && <span className="muted small"> {body.split("\n")[0]}</span>}
                </li>
              );
            })}
          </ol>
        </>
      )}
    </section>
  );
}
