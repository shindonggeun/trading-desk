import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { krwShort, price } from "../format";
import type { FuturesDaily, FuturesIntraday, FuturesPoint } from "../types";

// 선물 수급 기록(#255, 데이터 #254): 간결 = 최근 20거래일 누적 순매수(계약) 선 + 일자별 표,
// 자세히 = 오늘 장중 스냅샷(15분 간격, 그날 누적) 선. 색 · 순서는 현물 추이와 같다(외국인 → 기관 → 개인).

type Key = "foreign" | "institution" | "individual";
const SERIES: { key: Key; label: string; color: string }[] = [
  { key: "foreign", label: "외국인", color: "var(--flow-foreigner)" },
  { key: "institution", label: "기관", color: "var(--flow-institution)" },
  { key: "individual", label: "개인", color: "var(--flow-individual)" },
];
type Mode = "brief" | "detail";
type Prefs = { mode: Mode; on: Record<Key, boolean>; table: boolean };
const PREFS = "desk.futuresHistory.v1";
const DAYS = 20;
// 장중 자세히 보기는 스냅샷 간격(서버 설정)마다, 최소 1분 · 장 밖에는 다시 부르지 않는다
const MIN_REFRESH_MS = 60_000;
const CLOSED_REFRESH_MS = 600_000;

function loadPrefs(): Prefs {
  const base: Prefs = { mode: "brief", on: { foreign: true, institution: true, individual: true }, table: false };
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS) ?? "{}");
    if (raw.mode === "brief" || raw.mode === "detail") base.mode = raw.mode;
    for (const s of SERIES) if (typeof raw.on?.[s.key] === "boolean") base.on[s.key] = raw.on[s.key];
    if (typeof raw.table === "boolean") base.table = raw.table;
  } catch {
    /* 저장소가 막혀 있으면 기본값 */
  }
  return base;
}
function savePrefs(p: Prefs) {
  try {
    localStorage.setItem(PREFS, JSON.stringify(p));
  } catch {
    /* 기억만 못 한다 */
  }
}

const signed = (v: number | null | undefined) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${Math.round(v).toLocaleString("ko-KR")}`;
const contracts = (v: number | null | undefined) => (v == null ? "—" : `${signed(v)}계약`);
const kstTime = (iso: string) =>
  new Date(iso).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
const md = (date: string) => `${Number(date.slice(5, 7))}.${Number(date.slice(8, 10))}`;

type Row = { label: string; values: Record<Key, number>; nets: Record<Key, number | null>; p: FuturesPoint };

/** 선 그래프 — 0 기준선 · 끝 라벨 · 십자선 + 아래 읽기 줄(현물 추이와 같은 모양). */
function Lines({ rows, on, what, unitWord }: { rows: Row[]; on: Record<Key, boolean>; what: string; unitWord: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver(([e]) => setWidth(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => {
    if (hover === null) return;
    const close = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setHover(null);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [hover]);

  const shown = SERIES.filter((s) => on[s.key]);
  let body: React.ReactNode = null;
  if (rows.length < 2) body = <p className="muted small">선을 그릴 기록이 아직 부족합니다 — 기록은 서버가 쌓기 시작한 날부터 늘어납니다.</p>;
  else if (shown.length === 0) body = <p className="muted small">위에서 볼 주체를 하나 이상 켜세요.</p>;
  else if (width > 0) {
    const H = 150;
    const pad = { l: 22, r: 52, t: 10, b: 20 };
    const all = shown.flatMap((s) => rows.map((r) => r.values[s.key]));
    const lo = Math.min(0, ...all);
    const hi = Math.max(0, ...all);
    const span = hi - lo || 1;
    const x = (i: number) => pad.l + (i / (rows.length - 1)) * (width - pad.l - pad.r);
    const y = (v: number) => pad.t + (1 - (v - lo) / span) * (H - pad.t - pad.b);
    const at = Math.min(hover ?? rows.length - 1, rows.length - 1);
    const last = rows[rows.length - 1];
    const ends = shown.map((s) => ({ s, y: y(last.values[s.key]) })).sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 14) ends[i].y = ends[i - 1].y + 14;
    const floor = H - pad.b;
    if (ends.length && ends[ends.length - 1].y > floor) {
      ends[ends.length - 1].y = floor;
      for (let i = ends.length - 2; i >= 0; i--) if (ends[i + 1].y - ends[i].y < 14) ends[i].y = ends[i + 1].y - 14;
    }
    const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
      const rect = e.currentTarget.getBoundingClientRect();
      const i = Math.round(((e.clientX - rect.left - pad.l) / (width - pad.l - pad.r)) * (rows.length - 1));
      setHover(Math.min(rows.length - 1, Math.max(0, i)));
    };
    const cur = rows[at];
    body = (
      <>
        <svg width={width} height={H} role="img" onPointerMove={onMove} onPointerDown={onMove}
          onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
          aria-label={`${what}(${rows[0].label} ~ ${last.label}): ${shown.map((s) => `${s.label} ${contracts(last.values[s.key])}`).join(", ")}`}>
          <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} className="flow-axis" />
          <text x={pad.l - 6} y={y(0) + 4} className="flow-tick" textAnchor="end">0</text>
          {shown.map((s) => (
            <polyline key={s.key} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
              points={rows.map((r, i) => `${x(i)},${y(r.values[s.key])}`).join(" ")} />
          ))}
          {hover !== null && <line x1={x(at)} x2={x(at)} y1={pad.t} y2={H - pad.b} className="flow-cross" />}
          {shown.map((s) => (
            <circle key={s.key} cx={x(at)} cy={y(cur.values[s.key])} r={4} fill={s.color} className="flow-dot" />
          ))}
          {ends.map(({ s, y: ly }) => (
            <text key={s.key} x={width - pad.r + 6} y={ly + 4} className="flow-end">{s.label}</text>
          ))}
          <text x={pad.l} y={H - 4} className="flow-tick">{rows[0].label}</text>
          <text x={width - pad.r} y={H - 4} className="flow-tick" textAnchor="end">{last.label}</text>
          <rect x={0} y={0} width={width} height={H} fill="transparent" />
        </svg>
        <div className="flow-readout">
          <span className="muted small">{cur.label}{hover === null ? " 최신" : ""} 누적</span>
          {shown.map((s) => (
            <span key={s.key} className="flow-readout-item">
              <span className="flow-swatch" style={{ background: s.color }} />
              {s.label} <strong>{contracts(cur.values[s.key])}</strong>
              {cur.nets[s.key] != null && <span className="muted small">({unitWord} {signed(cur.nets[s.key])})</span>}
            </span>
          ))}
          <span className="muted small">
            선물 {cur.p.price != null ? price(cur.p.price) : "—"} · 미결제 {cur.p.open_interest != null ? cur.p.open_interest.toLocaleString("ko-KR") : "—"}
          </span>
        </div>
      </>
    );
  }
  return <div className="flow-trend" ref={box}>{body}</div>;
}

const net = (p: FuturesPoint, k: Key) => p.investors?.[k]?.net_contracts ?? null;

export default function FuturesHistoryView() {
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs);
  const update = (patch: Partial<Prefs>) => setPrefs((p) => {
    const next = { ...p, ...patch };
    savePrefs(next);
    return next;
  });
  const [daily, setDaily] = useState<FuturesDaily | null>(null);
  const [intraday, setIntraday] = useState<FuturesIntraday | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    setFailed(null);
    const fail = (e: unknown) => alive && setFailed(e instanceof Error ? e.message : "불러오지 못했습니다.");
    if (prefs.mode === "brief") {
      api.marketFuturesDaily(DAYS).then((d) => alive && setDaily(d), fail);
    } else {
      let open = false;          // 마지막으로 받은 장 상태 — 실패해도 이것으로 다음 예약을 정한다
      let every = 15;
      const next = () => {
        window.clearTimeout(timer);
        // 장중이면 다음 스냅샷 무렵, 장 밖에도 가끔(장 전에 열어 둔 화면이 개장 뒤 따라오게)
        timer = window.setTimeout(load, open ? Math.max(MIN_REFRESH_MS, every * 60_000) : CLOSED_REFRESH_MS);
      };
      const load = () => api.marketFuturesIntraday().then(async (today) => {
        open = today.market_open;
        every = today.interval_min || every;
        let d = today;
        // 오늘 기록이 아직 없으면(주말 · 휴장일 · 장 전 · 첫 스냅샷 전) 마지막 거래일의 장중을 보여 준다
        if (d.supported && d.points.length === 0) {
          const last = (await api.marketFuturesDaily(1).catch(() => null))?.days[0];
          if (last && last.date !== d.date) d = await api.marketFuturesIntraday(last.date).catch(() => d);
        }
        if (!alive) return;
        setIntraday(d);
        setFailed(null);
        next();
      }, (e) => {
        fail(e);
        if (alive) next();       // 일시 오류(배포 재시작 등) 뒤에도 갱신을 이어 간다
      });
      load();
    }
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [prefs.mode]);

  // 간결: 날마다 그날 순매수(마감 누적)를 오래된 날부터 더해 간다
  const dailyRows: Row[] = useMemo(() => {
    const sum: Record<Key, number> = { foreign: 0, institution: 0, individual: 0 };
    return [...(daily?.days ?? [])].reverse().map((p) => {
      const nets = {} as Record<Key, number | null>;
      for (const s of SERIES) {
        nets[s.key] = net(p, s.key);
        sum[s.key] += nets[s.key] ?? 0;
      }
      return { label: md(p.date), values: { ...sum }, nets, p };
    });
  }, [daily]);
  // 자세히: 스냅샷 값이 이미 그날 누적이다
  const intraRows: Row[] = useMemo(() => (intraday?.points ?? []).map((p) => {
    const values = {} as Record<Key, number>;
    const nets = {} as Record<Key, number | null>;
    for (const s of SERIES) {
      values[s.key] = net(p, s.key) ?? 0;
      nets[s.key] = null;
    }
    return { label: kstTime(p.at), values, nets, p };
  }), [intraday]);

  const view = prefs.mode === "brief" ? daily : intraday;
  return (
    <div className="futh">
      <div className="flow-trend-head">
        <div className="seg seg-small" role="group" aria-label="선물 수급 기록 보기">
          {(["brief", "detail"] as Mode[]).map((m) => (
            <button key={m} type="button" className={prefs.mode === m ? "on" : ""} aria-pressed={prefs.mode === m}
              onClick={() => update({ mode: m })}>{m === "brief" ? "간결(일자별)" : "자세히(장중)"}</button>
          ))}
        </div>
        <span className="flow-legend" role="group" aria-label="선에 보일 주체">
          {SERIES.map((s) => (
            <button key={s.key} type="button" className={`chip ${prefs.on[s.key] ? "chip-on" : ""}`} aria-pressed={prefs.on[s.key]}
              onClick={() => update({ on: { ...prefs.on, [s.key]: !prefs.on[s.key] } })}>
              <span className="flow-swatch" style={{ background: s.color }} /> {s.label}
            </button>
          ))}
        </span>
      </div>

      {failed && <p className="error small" role="alert">선물 수급 기록을 불러오지 못했습니다. {failed}</p>}
      {!view && !failed && <p className="muted small">불러오는 중…</p>}
      {view && !view.supported && <p className="muted small">한국투자증권 키가 없어 선물 수급 기록을 쌓지 않습니다.</p>}

      {view?.supported && prefs.mode === "brief" && (
        <>
          <p className="muted small futh-caption">최근 {dailyRows.length}거래일 누적 순매수(계약, 날마다 마지막 기록 · 오늘은 진행 중)</p>
          <Lines rows={dailyRows} on={prefs.on} what="선물 일자별 누적 순매수" unitWord="그날" />
          {dailyRows.length > 0 && (
            <button type="button" className="link small" aria-expanded={prefs.table} onClick={() => update({ table: !prefs.table })}>
              {prefs.table ? "표 닫기" : "표로 보기"}
            </button>
          )}
          {prefs.table && dailyRows.length > 0 && (
            <div className="table-wrap">
              <table className="flow-table">
                <thead>
                  <tr><th>날짜</th>{SERIES.map((s) => <th key={s.key} className="num">{s.label}</th>)}<th className="num">미결제</th></tr>
                </thead>
                <tbody>
                  {(daily?.days ?? []).map((p) => (
                    <tr key={p.date}>
                      <td>{p.date}</td>
                      {SERIES.map((s) => (
                        <td key={s.key} className="num" title={p.investors?.[s.key]?.net_value_mn != null
                          ? `금액 ${krwShort((p.investors[s.key].net_value_mn ?? 0) * 1_000_000)}` : undefined}>{signed(net(p, s.key))}</td>
                      ))}
                      <td className="num">{p.open_interest != null ? p.open_interest.toLocaleString("ko-KR") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {view?.supported && prefs.mode === "detail" && intraday && (
        <>
          <p className="muted small futh-caption">
            {intraday.date} 장중 누적 순매수(계약){intraday.interval_min > 0 ? ` · ${intraday.interval_min}분 간격` : ""}
            {intraday.market_open ? " · 장중 자동 갱신" : " · 장 마감"}
          </p>
          <Lines rows={intraRows} on={prefs.on} what="선물 장중 누적 순매수" unitWord="" />
        </>
      )}
    </div>
  );
}
