import { useEffect, useState } from "react";
import { api } from "../api";
import { time } from "../format";
import type { LongShortItem, LongShortSplit } from "../types";

const POLL_MS = 5 * 60_000;            // 바이낸스 통계가 5분마다 바뀐다(시세 15초와 따로)
const SHOW_KEY = "desk.longshort.show";
const SHOW_EVENT = "desk-longshort-show";

function readShow(): boolean {
  try {
    return localStorage.getItem(SHOW_KEY) !== "0";
  } catch {
    return true;
  }
}

/** 구역 머리의 "롱숏 표시" 토글 — 즐겨찾기 · 시세판이 같은 값을 쓰고 기억한다. */
export function useLongShortShown(): [boolean, () => void] {
  const [shown, setShown] = useState(readShow);
  useEffect(() => {
    const sync = () => setShown(readShow());
    window.addEventListener(SHOW_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(SHOW_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const flip = () => {
    try {
      localStorage.setItem(SHOW_KEY, shown ? "0" : "1");
    } catch {
      /* 저장 못 해도 이번 화면에서는 바뀐다 */
    }
    setShown(!shown);
    window.dispatchEvent(new Event(SHOW_EVENT));
  };
  return [shown, flip];
}

export function LongShortToggle({ shown, onFlip }: { shown: boolean; onFlip: () => void }) {
  return (
    <button type="button" className="link small" aria-pressed={shown} onClick={onFlip}>
      롱숏 {shown ? "숨기기" : "표시"}
    </button>
  );
}

/** 종목들의 롱/숏 비율을 5분마다 한 번에 묻는다. 꺼져 있으면 묻지 않는다. */
export function useLongShort(tickers: string[], enabled: boolean): Record<string, LongShortItem> {
  const [items, setItems] = useState<Record<string, LongShortItem>>({});
  const key = tickers.slice(0, 30).join(",");
  useEffect(() => {
    if (!key || !enabled) return;
    let alive = true;
    const load = () => {
      if (document.hidden) return;
      api.longShort(key.split(","))
        .then((r) => alive && setItems((cur) => ({ ...cur, ...Object.fromEntries(r.items.map((i) => [i.ticker, i])) })))
        .catch(() => { /* 실패하면 마지막 값을 그대로 둔다 */ });
    };
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [key, enabled]);
  return items;
}

/** #316 어느 계약의 비율인지 — 자기 계약 "SAMSUNG 선물", 프록시 · 레버리지 기초자산 계약이면 "SMH 선물 기준". */
export function contractLabel(it: LongShortItem): string {
  const name = (it.contract ?? "").replace(/USDT$/, "");
  return it.basis ? `${name} 선물 기준` : `${name} 선물`;
}

function splitText(s: LongShortSplit | null | undefined): string {
  return s ? `롱 ${s.long_pct.toFixed(1)} · 숏 ${s.short_pct.toFixed(1)}` : "—";
}

/**
 * 타일 아래 한 줄: "바이낸스 SAMSUNG 선물 숏NN [막대] 롱NN"(전체 계정 기준) — 어느 계약의 비율인지 늘 보인다(#316,
 * 좁은 화면은 "바이낸스" 만 뺀다). 올리거나 누르면 세 기준과 24시간 변화.
 * 계약이 없거나 값이 없으면 아무것도 그리지 않는다.
 */
export function LongShortBar({ it }: { it: LongShortItem | undefined }) {
  const [open, setOpen] = useState(false);
  const acc = it?.accounts;
  if (!it || !it.contract || !acc) return null;
  const change = it.change_24h?.long_pct;
  const which = contractLabel(it);
  const label = `바이낸스 ${which} 롱 ${acc.long_pct.toFixed(0)}% · 숏 ${acc.short_pct.toFixed(0)}%`;
  return (
    <div className="ls-wrap" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" className="ls-row small" aria-expanded={open} aria-label={`${label} — 자세히`}
        onClick={() => setOpen((v) => !v)}>
        {/* #316 계약 이름은 막대 위 한 줄 — 좁은 카드에서도 잘리지 않게 */}
        <span className="ls-label" title={`바이낸스 ${which}`}><span className="ls-src">바이낸스 </span>{which}</span>
        <span className="ls-short num">숏{acc.short_pct.toFixed(0)}</span>
        <span className="ls-bar" aria-hidden="true">
          <span className="ls-bar-short" style={{ width: `${acc.short_pct}%` }} />
          <span className="ls-bar-long" style={{ width: `${acc.long_pct}%` }} />
        </span>
        <span className="ls-long num">롱{acc.long_pct.toFixed(0)}</span>
      </button>
      {open && (
        <div className="ls-pop small" role="tooltip">
          <div><span className="muted">전체 계정</span> <span className="num">{splitText(acc)}</span></div>
          <div><span className="muted">상위 트레이더</span> <span className="num">{splitText(it.top_positions)}</span></div>
          <div>
            <span className="muted">테이커 체결 1시간</span>{" "}
            <span className="num">{it.taker?.buy_pct_1h != null ? `매수 ${it.taker.buy_pct_1h.toFixed(1)}%` : "—"}</span>
          </div>
          <div>
            <span className="muted">24시간 롱 변화</span>{" "}
            <span className={`num ${change == null ? "" : change > 0 ? "up" : change < 0 ? "down" : ""}`}>
              {change == null ? "—" : `${change > 0 ? "+" : ""}${change.toFixed(1)}%p`}
            </span>
          </div>
          <div className="muted">
            {it.contract} 선물 계약 기준{it.basis ? `(${it.basis}${it.multiple && it.multiple !== 1 ? ` × ${it.multiple}` : ""} 계약)` : ""}
            {acc.at && ` · ${time(acc.at)}`}
          </div>
        </div>
      )}
    </div>
  );
}
