import type { ReactNode } from "react";
import { price } from "../format";
import type { Plan, TradeMap as Map, TradeScenario, TradeStep, VolumeProfile } from "../types";

type Side = "long" | "short";

// 숏이면 "지지" 자리가 진입 위의 저항이다.
const ROLE: Record<Side, Record<TradeStep["role"], string>> = {
  long: { target2: "2차 목표", target1: "1차 목표", entry: "진입", guard1: "1차 지지", guard2: "2차 지지", stop: "손절" },
  short: { target2: "2차 목표", target1: "1차 목표", entry: "진입", guard1: "1차 저항", guard2: "2차 저항", stop: "손절" },
};
const ENTRY_KO: Record<string, string> = { now: "지금", limit: "되돌림 지정가" };
const entryWord = (t: string, up: boolean) => (t === "stop" ? (up ? "돌파 확인 뒤" : "이탈 확인 뒤") : ENTRY_KO[t] ?? t);

function pctFrom(entry: number, p: number): string {
  const v = ((p - entry) / entry) * 100;
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** 근거 한 줄: 계획 값 · 지지 / 저항 강도 · 매물대 · ATR 계산. */
function why(s: TradeStep): string {
  const parts: string[] = [];
  if (s.sources.includes("plan")) parts.push("계획 값");
  if (s.strength != null) parts.push(`강도 ${s.strength}`);
  if (s.volume === "thick") parts.push("매물대 두꺼움");
  if (s.volume === "thin") parts.push("매물대 얇음");
  if (s.source === "atr") parts.push("레벨 없어 ATR 로 계산");
  if (s.basis && s.source !== "plan" && s.source !== "trigger") parts.push(s.basis);
  return parts.join(" · ");
}

/** 그 가격의 매물대 비중(가장 두꺼운 구간 = 1). */
function weight(vp: VolumeProfile | null | undefined, p: number): number | null {
  if (!vp?.bins.length || p < vp.low - vp.bin_width || p > vp.high + vp.bin_width) return null;
  const top = Math.max(...vp.bins.map((b) => b.share));
  const near = vp.bins.reduce((a, b) => (Math.abs(b.price - p) < Math.abs(a.price - p) ? b : a));
  return top ? near.share / top : null;
}

/** 단계별로 무엇을 할지 — 사람이 읽는 순서(진입 → 목표 → 되돌림 → 손절). */
function scenario(side: Side, steps: TradeStep[]): ReactNode[] {
  const at = (r: TradeStep["role"]) => steps.find((s) => s.role === r);
  const e = at("entry");
  const t1 = at("target1");
  const t2 = at("target2");
  const g1 = at("guard1");
  const g2 = at("guard2");
  const st = at("stop");
  const up = side === "long";
  const out: ReactNode[] = [];
  if (e) {
    out.push(
      <>
        <strong>{price(e.price)}</strong> {e.entry_type ? `${entryWord(e.entry_type, up)} ` : ""}
        {up ? "매수" : "매도(숏)"} 진입
      </>,
    );
  }
  if (t1) {
    const heavy = !!t1.heavy;
    out.push(
      <>
        <strong>{price(t1.price)}</strong>({why(t1) || "1차 목표"}) 도달 시 <strong>{t1.share}%</strong> 익절
        {heavy ? " — 벽이 두꺼워 여기서 많이 던다" : ""}
        {t1.then_stop != null && <>, 남은 물량 손절선은 진입가({price(t1.then_stop)})까지 {up ? "올림" : "내림"}</>}
      </>,
    );
  }
  if (t1 && t2) {
    out.push(
      <>
        {price(t1.price)} {up ? "돌파 · 종가 안착" : "이탈 · 종가 마감"} 시 <strong>{price(t2.price)}</strong>({why(t2) || "2차 목표"})까지 보유 → 나머지 익절
        {t2.gap_thin ? " (사이 매물대가 얇아 빨리 갈 수 있음)" : ""}
      </>,
    );
  }
  if (g1) {
    out.push(
      <>
        되돌리면 <strong>{price(g1.price)}</strong>({why(g1) || ROLE[side].guard1}) 확인 — 버티면 보유, 종가 {up ? "이탈" : "돌파"} 시 비중 축소
      </>,
    );
  }
  if (g2) {
    out.push(
      <>
        <strong>{price(g2.price)}</strong>({why(g2) || ROLE[side].guard2})까지 {up ? "밀리면" : "올라오면"} 절반 이상 정리
      </>,
    );
  }
  if (st) {
    out.push(
      <>
        <strong className="tm-stop-text">{price(st.price)}</strong> {up ? "이탈" : "돌파"} 시 전량 손절{st.source === "atr" ? "(레벨이 없어 ATR 로 계산)" : ""}
      </>,
    );
  }
  return out;
}

function Ladder({ side, steps, live, vp }: { side: Side; steps: TradeStep[]; live: number | null; vp?: VolumeProfile | null }) {
  const entry = steps.find((s) => s.role === "entry")?.price ?? null;
  // 현재가를 가격 순서 자리에 끼운다(높은 가격이 위)
  const rows: ({ kind: "step"; s: TradeStep } | { kind: "live"; p: number })[] = steps.map((s) => ({ kind: "step", s }));
  if (live != null) {
    const i = rows.findIndex((r) => r.kind === "step" && r.s.price < live);
    rows.splice(i < 0 ? rows.length : i, 0, { kind: "live", p: live });
  }
  return (
    <ol className="tm-ladder">
      {rows.map((r) =>
        r.kind === "live" ? (
          <li key="live" className="tm-row tm-live">
            <span className="tm-role">현재가</span>
            <strong className="tm-price num">{price(r.p)}</strong>
            <span className="tm-pct muted small">{entry != null ? pctFrom(entry, r.p) : ""}</span>
            <span className="tm-why muted small" />
          </li>
        ) : (
          <li key={r.s.role} className={`tm-row tm-${r.s.role}`}>
            <span className="tm-role">{ROLE[side][r.s.role]}</span>
            <strong className="tm-price num">{price(r.s.price)}</strong>
            <span className="tm-pct muted small">
              {entry != null && r.s.role !== "entry" ? pctFrom(entry, r.s.price) : ""}
              {r.s.rr != null && ` · 손익비 ${r.s.rr}`}
            </span>
            <span className="tm-why small">
              {(() => {
                const w = weight(vp, r.s.price);
                return w != null ? <span className="tm-vol" style={{ width: `${Math.max(4, Math.round(w * 100))}%` }} title="이 가격대 거래량(가장 두꺼운 구간 대비)" /> : null;
              })()}
              <span className="muted">{why(r.s)}</span>
            </span>
          </li>
        ),
      )}
    </ol>
  );
}

const KIND = { breakout: "돌파 확인 진입", support: "지지 근처 진입" } as const;
const KIND_SHORT = { breakout: "이탈 확인 진입", support: "저항 근처 진입" } as const;
const HOW = { now: "지금", limit: "지정가", stop: "도달 시" } as const;
/** #671 평균 R 은 부호와 함께, ±0.005 안쪽은 0 — "−0.00R" 을 쓰지 않는다 */
const rText = (v: number) => {
  const x = Math.abs(v) < 0.005 ? 0 : v;
  return `${x >= 0 ? "+" : ""}${x.toFixed(2)}R`;
};

/** #652 진입 시나리오 비교: 방향마다 돌파 확인과 지지(저항) 근처 진입을 한 줄씩 — 손익비 낮으면 흐리게. */
function Scenarios({ list, note }: { list: TradeScenario[]; note: string | null }) {
  return (
    <div className="tm-scenarios">
      <h4>진입 시나리오 비교</h4>
      <ul className="small">
        {list.map((s) => {
          const kind = s.side === "long" ? KIND[s.kind] : KIND_SHORT[s.kind];
          const rec = s.record;
          return (
            <li key={`${s.side}-${s.kind}`} className={s.low_rr ? "tm-sc-low" : undefined}>
              <b>{s.side === "long" ? "롱" : "숏"} · {kind}</b>{" "}
              {HOW[s.entry_type]} {price(s.entry)}
              {s.distance_pct != null && s.entry_type !== "now" && ` (지금보다 ${s.distance_pct > 0 ? "+" : ""}${s.distance_pct}%)`}
              {" "}· 손절 {price(s.stop)} (−{s.stop_pct}%, {s.stop_atr} ATR)
              {s.targets.map((t, i) => ` · ${i + 1}차 ${price(t.price)} 손익비 ${t.rr}`).join("")}
              {s.low_rr && " — 1차 손익비 낮음"}
              {s.basis && <span className="muted"> · 근거 {s.basis}{s.level != null ? ` ${price(s.level)}` : ""}</span>}
              {rec && rec.n > 0 && (
                <span className="muted"> · 이 레짐 과거 {rec.n_indep ?? rec.n}번(겹치지 않는 거래) 승률 {Math.round(rec.win_rate * 100)}% · 평균 {rText(rec.avg_r)}</span>
              )}
            </li>
          );
        })}
      </ul>
      {note && <p className="small">{note}</p>}
      <p className="muted small">진입 근거가 깨지는 곳에 손절을 둔 계획 계산입니다. 추천이나 예측이 아닙니다.</p>
    </div>
  );
}

/**
 * 요약 탭의 매매 지도(#107): 진입 → 1차 / 2차 목표 → 되돌림 때 볼 지지 → 손절을 가격 순서로 보이고,
 * 단계마다 할 일(일부 익절 · 손절 올리기 · 보유 연장 · 축소 · 전량 손절)을 문장으로. 숫자는 서버 계산값.
 */
export default function TradeMap({ plan, ticker }: { plan: Plan; ticker: string }) {
  const tm: Map | null | undefined = plan.trade_map;
  if (!tm?.maps.length) return null;
  const vp = plan.support_resistance?.volume_profile;
  const live = plan.live_price?.price ?? tm.reference_price ?? null;
  return (
    <section className="card trade-map" aria-label="매매 지도">
      <div className="tm-head">
        <h3>매매 지도</h3>
        {vp && (
          <span className="muted small">
            매물대 {vp.bars}봉 · 최다 거래 {price(vp.poc)} · 거래 70% 구간 {price(vp.value_area.low)}~{price(vp.value_area.high)}
          </span>
        )}
      </div>
      <div className={`tm-maps ${tm.maps.length > 1 ? "tm-maps-two" : ""}`}>
        {tm.maps.map((m) => (
          <div key={m.side} className={`tm-map tm-${m.side}`}>
            <h4>
              {m.side === "long" ? "롱" : "숏"} {m.origin === "trigger" ? (m.side === "long" ? "— 롱 조건 돌파 시" : "— 숏 조건 이탈 시") : "시나리오"}
            </h4>
            {m.ignored && (m.ignored.stop_loss != null || m.ignored.price_target != null) && (
              <p className="small tm-ignored">
                계획의 {[m.ignored.stop_loss != null && `손절가 ${price(m.ignored.stop_loss)}`, m.ignored.price_target != null && `목표가 ${price(m.ignored.price_target)}`]
                  .filter(Boolean).join(" · ")}는 진입가와 방향이 맞지 않아 빼고 차트 레벨로 채웠습니다.
              </p>
            )}
            <Ladder side={m.side} steps={m.steps} live={live} vp={vp} />
            <ol className="tm-steps small">
              {scenario(m.side, m.steps).map((line, i) => <li key={i}>{line}</li>)}
            </ol>
          </div>
        ))}
      </div>
      {!!tm.scenarios?.length && <Scenarios list={tm.scenarios} note={tm.regime_note ?? null} />}
      <p className="muted small">
        {ticker} 가격 기준 — 계획 값, 지지 · 저항 구역{tm.volume_profile ? ", 매물대" : ""}로 계산한 지도입니다(ATR {price(tm.atr)}).
        {!tm.levels && " 이 분석에는 지지 · 저항 기록이 없어 ATR 배수로 채운 칸이 있습니다."}
        {" "}가격 기록의 계산이지 예측이 아닙니다.
      </p>
    </section>
  );
}
