import { useEffect, useId, useRef, useState } from "react";
import {
  api, ApiError, type LineWatch, type LineWatchEvent, type LineWatchPatch,
  type LineWatchRule, type LineWatchSide, type LineWatchSupport, type LineWatchChannels,
} from "../api";
import {
  chartFallbackLabel, kstHhmm, signalDateTime, signalKey, signalTime, splitText,
  LINE_EVENT_KO, LINE_EVENT_NOTE, LINE_TF_KO, SHORT_TFS,
} from "../lineSignals";
import { TICKER } from "../markets";
import { pushState, type PushState } from "../push";
import { sameTicker } from "../ticker";
import type { Favorite } from "../types";
import { loadExtraKinds } from "./FlowAlertToast";
import InstrumentSearch, { type Picked } from "./InstrumentSearch";
import LineSignalLink from "./LineSignalLink";
import { channelStatus, ruleProblem, rulesFromCommon } from "./lineWatchSettingsModel";
import "./LineWatchSettings.css";

const EXAMPLES = [
  { ticker: "NQ=F", name: "나스닥100 선물" }, { ticker: "^KS11", name: "코스피" },
  { ticker: "005930.KS", name: "삼성전자" }, { ticker: "000660.KS", name: "SK하이닉스" },
  { ticker: "SOXL", name: "SOXL" }, { ticker: "KORU", name: "KORU" },
];
const COMMON_FRAMES = ["5m", "15m", "60m", "120m", "240m", "1d"];
const SIDES: Record<LineWatchSide, string> = { both: "지지·저항 모두", support: "지지만", resistance: "저항만" };
const MARKET: Record<string, string> = { CME: "CME", KRX: "KRX", US: "미국 정규장" };
const SUPPORT_REASON: Record<string, string> = {
  unsupported_market: "이 종목의 거래 세션을 확인하는 원천이 없어요.",
  unsupported_source: "이 시간봉을 확인할 시세 원천이 없어요.",
  unsupported_timeframe: "이 시장은 해당 시간봉을 제공하지 않아요.",
};
const WEB_STATUS: Record<PushState, string> = {
  unsupported: "브라우저에서 지원 안 됨", "ios-browser": "홈 화면 앱 설치 필요", "server-off": "서버 설정 필요",
  denied: "이 기기에서 차단됨", off: "이 기기 구독 안 됨", on: "이 기기 구독됨",
};
const DEFAULT_FRAMES = ["1d", "60m"];
const RECENT_DATE = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric" });
type Save = (patch: LineWatchPatch, done?: string) => Promise<boolean>;
type SupportMatch = { ticker: string; support: LineWatchSupport };
type SupportLookup = { ticker: string; failed: boolean };
type Notice = { text: string; error: boolean };
const symbolOf = (raw: string) => raw.trim().toUpperCase();
const termOf = (tf: string) => LINE_TF_KO[tf] ?? tf;
const eventOf = (event: LineWatchEvent) => event === "break" ? "종가 돌파·이탈" : LINE_EVENT_KO[event];

function findSupport(allowed: LineWatch["allowed"]["support"], ticker: string): SupportMatch | undefined {
  if (!allowed) return undefined;
  const matched = allowed[ticker] ? ticker : Object.keys(allowed).find(target => sameTicker(target, ticker));
  return matched ? { ticker: matched, support: allowed[matched] } : undefined;
}

function TickerSearch({ label, onChoose }: { label: string; onChoose: (ticker: string) => boolean }) {
  const id = useId();
  const [picked, setPicked] = useState<Picked>({ ticker: "" });
  return (
    <form className="line-watch-add" onSubmit={event => {
      event.preventDefault();
      if (picked.ticker && onChoose(picked.ticker)) setPicked({ ticker: "" });
    }}>
      <label className="sr-only" htmlFor={id}>{label}</label>
      <InstrumentSearch inputId={id} value={picked} onChange={setPicked} placeholder="종목 이름 · 티커 (예: 삼성전자)" />
      <button type="submit" className="secondary" disabled={!picked.ticker}>{label}</button>
    </form>
  );
}

function ShortFrameNote({ frames }: { frames: string[] }) {
  const short = frames.filter(tf => SHORT_TFS.has(tf));
  return short.length > 0 ? (
    <p className="small line-watch-warn">
      {short.map(termOf).join(" · ")}처럼 짧은 봉은 잡음이 많아 선에 닿거나 벗어나는 일이 잦아요. 참고용으로 보세요.
    </p>
  ) : null;
}

function CommonConfig({ watch, favorites, save, warn }: {
  watch: LineWatch; favorites: Favorite[]; save: Save; warn: (text: string) => void;
}) {
  const full = watch.tickers.length >= watch.allowed.max_tickers;
  const available = watch.allowed.timeframes.filter(tf => COMMON_FRAMES.includes(tf) || watch.timeframes.includes(tf));
  const options = favorites.filter(favorite => !watch.tickers.some(ticker => sameTicker(ticker, favorite.ticker))).slice(0, 12);

  const add = (raw: string) => {
    const ticker = symbolOf(raw);
    if (!TICKER.test(ticker)) {
      warn("티커는 영문·숫자와 . - ^ = 를 쓸 수 있어요.");
      return false;
    }
    if (watch.tickers.some(symbol => sameTicker(symbol, ticker))) {
      warn(ticker + "은(는) 이미 있어요.");
      return false;
    }
    if (full) return false;
    void save({ tickers: [...watch.tickers, ticker] });
    return true;
  };
  return (
    <div className="line-watch-common">
      <h3>공통 설정</h3>
      <p className="muted small">아래 시간봉과 사건을 모든 공통 감시 종목에 적용해요.</p>
      <h4>감시 종목 <span className="muted small">{watch.tickers.length}/{watch.allowed.max_tickers}</span></h4>
      {watch.tickers.length > 0 ? (
        <ul className="chips line-watch-tickers" aria-label="공통 감시 종목">
          {watch.tickers.map(ticker => (
            <li className="chip chip-on" key={ticker}>{ticker}
              <button className="chip-x" type="button" aria-label={ticker + " 빼기"}
                onClick={() => void save({ tickers: watch.tickers.filter(symbol => symbol !== ticker) })}>×</button>
            </li>
          ))}
        </ul>
      ) : <p className="muted small">공통 감시 종목이 없어요.</p>}
      {!full && <>
        <TickerSearch label="공통 감시 종목 추가" onChoose={add} />
        {options.length > 0 && (
          <div className="chips" role="group" aria-label="즐겨찾기에서 더하기">
            {options.map(option => (
              <button type="button" className="chip" key={option.ticker} onClick={() => add(option.ticker)}>
                + {option.name ?? option.ticker}
              </button>
            ))}
          </div>
        )}
      </>}
      <h4>시간봉</h4>
      <div className="chips" role="group" aria-label="공통 감시 시간봉">
        {available.map(tf => {
          const on = watch.timeframes.includes(tf);
          return (
            <button key={tf} type="button" className={"chip" + (on ? " chip-on" : "")} aria-pressed={on}
              onClick={() => void save({ timeframes: on ? watch.timeframes.filter(value => value !== tf) : [...watch.timeframes, tf] })}>
              {termOf(tf)}
            </button>
          );
        })}
      </div>
      <ShortFrameNote frames={watch.timeframes} />
      <h4>알림 유형</h4>
      <div className="chips" role="group" aria-label="공통 알림 유형">
        {watch.allowed.events.map(event => {
          const on = !!watch.events[event];
          return (
            <button key={event} type="button" className={"chip" + (on ? " chip-on" : "")} aria-pressed={on}
              title={LINE_EVENT_NOTE[event]} onClick={() => void save({ events: { [event]: !on } })}>{eventOf(event)}</button>
          );
        })}
      </div>
    </div>
  );
}

function RuleFields({ watch, rule, support, onChange }: {
  watch: LineWatch; rule: LineWatchRule; support: LineWatchSupport | undefined;
  onChange: (next: LineWatchRule) => void;
}) {
  return (
    <div className="line-watch-fields">
      <label className="line-watch-on">
        <input type="checkbox" checked={rule.enabled} onChange={event => onChange({ ...rule, enabled: event.target.checked })} />
        이 종목 감시
      </label>
      <label className="line-watch-select">
        지지·저항
        <select value={rule.side} onChange={event => onChange({ ...rule, side: event.target.value as LineWatchSide })}>
          {(watch.allowed.sides ?? []).map(side => <option key={side} value={side}>{SIDES[side]}</option>)}
        </select>
      </label>
      <fieldset>
        <legend>감시 시간봉</legend>
        <div className="line-watch-checks">
          {watch.allowed.timeframes.map(tf => {
            const chosen = rule.timeframes.includes(tf);
            const supported = !!support?.supported.includes(tf);
            const reason = support?.unsupported[tf];
            return (
              <label key={tf}>
                <span><input type="checkbox" checked={chosen} disabled={!chosen && !supported} onChange={() => onChange({
                  ...rule, timeframes: chosen ? rule.timeframes.filter(value => value !== tf) : [...rule.timeframes, tf],
                })} /> {termOf(tf)}</span>
                {!supported && support && (
                  <small className="muted">{reason ? SUPPORT_REASON[reason] ?? "이 조합은 현재 지원하지 않아요." : "지원 안 됨"}</small>
                )}
              </label>
            );
          })}
        </div>
      </fieldset>
      <ShortFrameNote frames={rule.timeframes} />
      <fieldset>
        <legend>알림 유형</legend>
        <div className="line-watch-checks">
          {watch.allowed.events.map(event => {
            const available = event !== "long" || rule.timeframes.includes("1d");
            return (
              <label key={event} title={LINE_EVENT_NOTE[event]}>
                <span><input type="checkbox" disabled={!available} checked={available && rule.events[event] !== false}
                  onChange={() => onChange({ ...rule, events: { ...rule.events, [event]: rule.events[event] === false } })} /> {eventOf(event)}</span>
                {!available && <small className="muted">일봉을 선택할 때 쓸 수 있어요.</small>}
              </label>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

function DeliveryStatus({ signal }: { signal?: AbortSignal }) {
  const [status, setStatus] = useState<{ channels: LineWatchChannels | null; web: PushState | null }>();
  const [revision, setRevision] = useState(0);
  const refresh = () => { setStatus(undefined); setRevision(value => value + 1); };
  useEffect(() => {
    let alive = true;
    void Promise.all([
      api.lineWatchChannels(signal).catch(() => null), pushState(signal).catch(() => null),
    ]).then(([channels, web]) => { if (alive && !signal?.aborted) setStatus({ channels, web }); });
    return () => { alive = false; };
  }, [revision, signal]);

  const channels = status?.channels ?? null;
  const web = status?.web ?? null;
  const { line_signal, tone_neutral } = loadExtraKinds();
  const webText = web !== "on" ? web ? WEB_STATUS[web] : "상태 확인 불가"
    : !line_signal ? "이 기기 구독됨 · 차트 선 선택 꺼짐"
    : !tone_neutral ? "이 기기 구독됨 · 참고 등급 꺼짐" : "이 기기 구독됨 · 차트 선 선택 켜짐";
  return (
    <section className="line-watch-delivery">
      <div className="line-watch-actions">
        <h3>수신 채널 상태</h3>
        <button type="button" className="secondary" onClick={refresh} disabled={!status}>상태 갱신</button>
      </div>
      <dl>
        <dt>텔레그램</dt><dd>{status ? channelStatus(channels, "telegram") : "확인 중…"}</dd>
        <dt>웹 푸시</dt><dd>{status ? webText : "확인 중…"}</dd>
      </dl>
      <p className="muted small">
        {channels?.channels.some(item => "name" in item)
          ? "텔레그램은 서버에서 승인한 수신 정책을 표시해요. 이 기기의 웹 푸시는 위의 알림 설정에서 바꿀 수 있어요."
          : "수신 채널과 이 기기의 차트 선·참고 등급 선택은 위의 알림 설정에서 바꿀 수 있어요."}
      </p>
    </section>
  );
}

function RecentSignals({ recent }: { recent: LineWatch["recent"] }) {
  if (!recent.length) return <p className="muted small">아직 보낸 차트 선 알림이 없어요.</p>;
  return (
    <section>
      <h3>최근 보낸 선 알림</h3>
      <ol className="line-watch-recent">
        {recent.slice(0, 8).map(signal => {
          const { title, body } = splitText(signal.text);
          const fallback = chartFallbackLabel(signal.timeframe);
          const stamp = signalDateTime(signal);
          const shownDate = stamp && Number.isFinite(Date.parse(stamp))
            ? RECENT_DATE.format(new Date(stamp))
            : signal.trade_date.slice(5);
          return (
            <li key={signalKey(signal)}>
              <LineSignalLink signal={signal}>
                <time className="muted small" dateTime={stamp}>
                  {shownDate} {kstHhmm(signalTime(signal))} {signal.recorded_at ? "기록" : "봉 기준"}
                </time>{" "}<strong>{title}</strong>
              </LineSignalLink>
              {fallback && <span className="muted small line-watch-fallback">{fallback}</span>}
              {body && <span className="muted small"> {body.split("\n")[0]}</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export default function LineWatchSettings() {
  const [watch, setWatch] = useState<LineWatch | null>(null);
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);
  const [missing, setMissing] = useState(false);
  const [reload, setReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Notice | null>(null);
  const [draft, setDraft] = useState<LineWatchRule[] | null>(null);
  const [selected, setSelected] = useState("");
  const [lookup, setLookup] = useState<SupportLookup | null>(null);
  const [lookupRetry, setLookupRetry] = useState(0);
  const [supportCache, setSupportCache] = useState<Partial<Record<string, SupportMatch>>>({});
  const lifetime = useRef<AbortController | null>(null);
  const activeLookup = useRef<AbortController | null>(null);
  const writer = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    const { signal } = controller;
    void api.lineWatch(undefined, signal).then(result => {
      if (signal.aborted) return;
      setWatch(result);
      if (result.can_edit) void api.favorites(signal).then(
        list => { if (!signal.aborted) setFavorites(list); }, () => {},
      );
    }, (error: unknown) => {
      if (signal.aborted) return;
      if (error instanceof ApiError && error.status === 404) setMissing(true);
      else setLoadFailed(true);
    });
    return () => { controller.abort(); };
  }, [reload]);

  const canEdit = watch?.can_edit ?? false;
  const personalSupported = watch?.rules !== undefined && !!watch?.allowed.support && !!watch.allowed.sides?.length;
  const rules = draft ?? watch?.rules;
  const personal = personalSupported && Array.isArray(rules);
  const currentRules = Array.isArray(rules) ? rules : [];
  const choice = selected || currentRules[0]?.ticker || watch?.tickers[0] || EXAMPLES[0].ticker;
  const currentMatch = supportCache[choice] ?? findSupport(watch?.allowed.support, choice);
  const known = !!currentMatch;

  useEffect(() => {
    if (!canEdit || !personal || known) return;
    const controller = new AbortController();
    activeLookup.current = controller;
    const { signal } = controller;
    setLookup({ ticker: choice, failed: false });
    void api.lineWatch(choice, signal).then(result => {
      if (signal.aborted) return;
      if (!result.can_edit) {
        setWatch(result); setDraft(null); setSupportCache({}); setFavorites([]); setSelected(""); setNote(null);
        return;
      }
      const found = findSupport(result.allowed.support, choice);
      if (found) setSupportCache(cache => ({ ...cache, [choice]: found }));
      else setLookup({ ticker: choice, failed: true });
    }, () => { if (!signal.aborted) setLookup({ ticker: choice, failed: true }); });
    return () => {
      controller.abort();
      if (activeLookup.current === controller) activeLookup.current = null;
    };
  }, [canEdit, personal, choice, known, lookupRetry]);

  const warn = (text: string) => setNote({ text, error: true });
  const save: Save = async (patch, done) => {
    const signal = lifetime.current?.signal;
    if (!watch?.can_edit || !signal || signal.aborted || writer.current) return false;
    writer.current = true;
    setBusy(true);
    setNote(null);
    try {
      if (Array.isArray(patch.rules)) {
        for (const rule of patch.rules) {
          let matching = supportCache[rule.ticker] ?? findSupport(watch.allowed.support, rule.ticker);
          if (!matching && rule.timeframes.length > 0) {
            const response = await api.lineWatch(rule.ticker, signal);
            if (signal.aborted) return false;
            if (!response.can_edit) {
              setWatch(response); setDraft(null); setSupportCache({}); setFavorites([]); setSelected("");
              return false;
            }
            matching = findSupport(response.allowed.support, rule.ticker);
            if (matching) {
              const found = matching;
              setSupportCache(cache => ({ ...cache, [rule.ticker]: found }));
            }
          }
          const problem = ruleProblem(rule, matching?.support);
          if (problem) {
            setSelected(rule.ticker);
            warn(rule.ticker + " · " + problem);
            return false;
          }
        }
      }
      const updated = await api.saveLineWatch(patch, signal);
      if (signal.aborted) return false;
      setWatch(updated);
      if (patch.rules !== undefined || !updated.can_edit) setDraft(null);
      if (!updated.can_edit) { setSupportCache({}); setFavorites([]); setSelected(""); }
      if (done && updated.can_edit) setNote({ text: done, error: false });
      return updated.can_edit;
    } catch (error) {
      if (signal.aborted) return false;
      if (error instanceof ApiError && error.status === 403) {
        // 권한 변경 뒤 도착한 응답에서 이전 설정과 읽기 결과를 계속 보여 주지 않는다.
        setWatch({ ...watch, can_edit: false, enabled: false, tickers: [], timeframes: [], events: {}, rules: [], recent: [] });
        setDraft(null); setSupportCache({}); setFavorites([]); setSelected("");
      } else if (error instanceof ApiError && error.status === 422) {
        warn("사용할 종목과 시간봉 또는 지원 범위를 확인해 주세요.");
      } else warn("저장하지 못했어요. 잠시 뒤 다시 해 주세요.");
      return false;
    } finally {
      writer.current = false;
      if (!signal.aborted) setBusy(false);
    }
  };

  if (missing) return null;
  if (!watch || !watch.can_edit) return (
    <section className="card settings-card line-watch">
      <h2>차트 선 알림</h2>
      {watch ? <p className="muted">설정은 데스크 주인만 볼 수 있어요.</p> : loadFailed ? (
        <div className="line-watch-actions">
          <p className="error small" role="alert">선 알림 설정을 불러오지 못했어요.</p>
          <button type="button" className="secondary" onClick={() => { setLoadFailed(false); setReload(value => value + 1); }}>다시 읽기</button>
        </div>
      ) : <p className="muted small" role="status">불러오는 중…</p>}
    </section>
  );

  const support = currentMatch?.support;
  const currentRule = currentRules.find(rule => sameTicker(rule.ticker, choice));
  const full = currentRules.length >= watch.allowed.max_tickers;
  const extra = [...favorites, ...currentRules, { ticker: choice }].filter((row, index, list) =>
    !EXAMPLES.some(example => sameTicker(example.ticker, row.ticker))
    && list.findIndex(candidate => sameTicker(candidate.ticker, row.ticker)) === index);
  const shownChoice = [...EXAMPLES, ...extra].find(option => sameTicker(option.ticker, choice))?.ticker ?? choice;

  const select = (raw: string) => {
    const ticker = symbolOf(raw);
    if (!TICKER.test(ticker)) {
      warn("티커는 영문·숫자와 . - ^ = 를 쓸 수 있어요.");
      return false;
    }
    if (choice !== ticker) activeLookup.current?.abort();
    setSelected(ticker);
    setNote(null);
    return true;
  };
  const addRule = () => {
    if (full || !support?.supported.length || !currentMatch) return;
    const tf = DEFAULT_FRAMES.find(item => support.supported.includes(item)) ?? support.supported[0];
    setDraft([...currentRules, { ticker: currentMatch.ticker, enabled: true, timeframes: [tf],
      events: { touch: true, break: false, retest: false, long: false }, side: "both" }]);
    setNote(null);
  };

  return (
    <section className="card settings-card line-watch">
      <h2>차트 선 알림</h2>
      <p className="muted">판정한 봉에서 평행 채널이나 추세선에 닿거나, 종가로 선을 넘으면 알려 드려요. 차트 선과 참고 등급을 켠 수신 채널로 받아볼 수 있어요.</p>
      <fieldset className="line-watch-form" disabled={busy}>
        <label className="line-watch-on">
          <input type="checkbox" checked={watch.enabled} onChange={() => void save({ enabled: !watch.enabled }, watch.enabled ? "선 알림을 껐어요." : "선 알림을 켰어요.")} />
          <strong>선 알림 받기</strong><span className="muted small">{watch.enabled ? "켜짐" : "꺼짐"}</span>
        </label>
        {personal ? (
          <div className="line-watch-personal">
            <div className="line-watch-actions">
              <h3>종목별 규칙 <span className="muted small">{currentRules.length}/{watch.allowed.max_tickers}</span></h3>
              <button type="button" className="secondary" onClick={() => {
                if (watch.rules === null) { setDraft(null); setNote(null); }
                else void save({ rules: null }, "보존한 공통 설정으로 돌아왔어요.");
              }}>{watch.rules === null ? "전환 취소" : "공통 설정으로 돌아가기"}</button>
            </div>
            {currentRules.length > 0 && (
              <div className="chips" role="group" aria-label="설정한 종목">
                {currentRules.map(rule => (
                  <button type="button" className={"chip" + (sameTicker(rule.ticker, choice) ? " chip-on" : "")}
                    key={rule.ticker} aria-pressed={sameTicker(rule.ticker, choice)} onClick={() => select(rule.ticker)}>
                    {rule.ticker} · {rule.enabled ? "켜짐" : "꺼짐"}
                  </button>
                ))}
              </div>
            )}
            <div className="line-watch-picker">
              <label className="line-watch-select">
                종목별 알림 종목
                <select value={shownChoice} onChange={event => select(event.target.value)}>
                  <optgroup label="주요 종목">
                    {EXAMPLES.map(row => <option value={row.ticker} key={row.ticker}>{row.name === row.ticker ? row.ticker : row.name + " · " + row.ticker}</option>)}
                  </optgroup>
                  {extra.length > 0 && <optgroup label="즐겨찾기·직접 선택">
                    {extra.map(row => <option value={row.ticker} key={row.ticker}>{"name" in row && typeof row.name === "string" && row.name ? row.name + " · " : ""}{row.ticker}</option>)}
                  </optgroup>}
                </select>
              </label>
              <TickerSearch label="종목 검색으로 선택" onChoose={select} />
            </div>
            {support ? (
              <p className="muted small" role="status">{choice} · {support.market
                ? (MARKET[support.market.toUpperCase()] ?? support.market) + " · 시간봉 " + support.supported.length + "개 지원"
                : SUPPORT_REASON[support.unsupported[watch.allowed.timeframes[0]]] ?? "지원하는 원천이 없어요."}</p>
            ) : lookup?.ticker === choice && lookup.failed ? (
              <div className="line-watch-actions">
                <p className="error small" role="alert">{choice} · 지원 정보를 불러오지 못했어요.</p>
                <button type="button" className="secondary" onClick={() => setLookupRetry(value => value + 1)}>지원 다시 읽기</button>
              </div>
            ) : <p className="muted small" role="status">{choice} · 지원 정보 확인 중…</p>}
            {currentRule ? <>
              <RuleFields watch={watch} rule={currentRule} support={support} onChange={next => {
                setDraft(currentRules.map(rule => rule === currentRule ? next : rule)); setNote(null);
              }} />
              <button type="button" className="secondary line-watch-remove" onClick={() => {
                setDraft(currentRules.filter(rule => rule !== currentRule)); setNote(null);
              }}>이 종목 규칙 빼기</button>
            </> : (
              <button type="button" className="secondary line-watch-remove" disabled={full || !support?.supported.length} onClick={addRule}>
                이 종목 규칙 추가
              </button>
            )}
            <div className="line-watch-actions">
              <button type="button" disabled={draft === null} onClick={() => void save({ rules: currentRules }, "종목별 규칙을 저장했어요.")}>
                종목별 규칙 저장
              </button>
              {draft !== null && <span className="muted small">저장할 변경이 있어요.</span>}
            </div>
          </div>
        ) : <>
          <CommonConfig watch={watch} favorites={favorites} save={save} warn={warn} />
          {personalSupported && <button className="secondary line-watch-remove" type="button" onClick={() => {
            const next = rulesFromCommon(watch);
            setSelected(next[0]?.ticker ?? EXAMPLES[0].ticker); setDraft(next); setNote(null);
          }}>종목별 규칙으로 전환</button>}
        </>}
      </fieldset>
      {busy && <p className="muted small" role="status">저장 중…</p>}
      {note && <p className={note.error ? "error small" : "settings-notice"} role={note.error ? "alert" : "status"}>{note.text}</p>}
      <div className="line-watch-examples muted small">
        <p><strong>닿음 예시</strong> 고가가 저항 근처까지 닿았지만 종가 돌파는 확인되지 않았어요.</p>
        <p><strong>종가 돌파 예시</strong> 확정 봉의 종가가 저항 위의 돌파 기준을 넘었어요.</p>
        <p>같은 선·사건은 하루 한 번, 같은 종목·시간봉은 {watch.limits.cooldown_min}분(또는 {watch.limits.cooldown_bars}봉) 쉬어요.
          하루 {watch.limits.daily_max}건 · 종목당 {watch.limits.per_ticker_max}건까지예요.</p>
      </div>
      <DeliveryStatus signal={lifetime.current?.signal} />
      <RecentSignals recent={watch.recent} />
    </section>
  );
}
