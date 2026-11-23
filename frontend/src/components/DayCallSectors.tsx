import { useEffect, useState } from "react";
import { api } from "../api";
import { rate } from "../dayCall";
import type { DayCallMarket } from "../types";
import "./dayCallSectors.css";

type Sector = {
  id: string;
  name: string;
  ticker: string;
  prob: number;
  reasons: string[];
  excess_pct: number | null;
};

type HitRates = { top3_hit: number | null; bottom3_hit: number | null };

export type DayCallSectorsResponse =
  | { ready: false; loading?: boolean; note: string }
  | {
      ready: true;
      date: string;
      target: string;
      top: Sector[];
      bottom: Sector[];
      record: { days: number; baseline?: HitRates } & Partial<HitRates>;
      result: { top_hits: number; bottom_hits: number } | null;
    };

function Picks({ sectors, weak = false }: { sectors: Sector[]; weak?: boolean }) {
  return (
    <div className="daycall-sector-group">
      <h5 className={weak ? "down" : "up"}>
        <span aria-hidden="true">{weak ? "▼ " : "▲ "}</span>{weak ? "약할 섹터" : "강할 섹터"}
      </h5>
      <ol>
        {sectors.map((sector) => (
          <li key={sector.id}>
            <div className="daycall-sector-row">
              <strong>{sector.name} <span className="muted">{sector.ticker}</span></strong>
              <span>{rate(sector.prob)}</span>
            </div>
            {sector.reasons[0] && <p className="muted">{sector.reasons[0]}</p>}
            {sector.excess_pct != null && (
              <p className="muted">시장 대비 {sector.excess_pct > 0 ? "+" : ""}{sector.excess_pct.toFixed(2)}%</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** #705 카드가 같이 받아 둔 섹터: "wait" 받는 중(아직 묻지 않는다) · 응답(그대로 쓴다) · "failed"(묶음에서 실패 —
 *  다시 묻지 않고 다시 불러오기만) · null(직접 묻는다: 받는 중이었거나 다른 시장 탭) */
export type SectorsPreset = DayCallSectorsResponse | "failed" | "wait" | null;

export default function DayCallSectors({ market, preset = null }: { market: DayCallMarket; preset?: SectorsPreset }) {
  const [fetched, setData] = useState<DayCallSectorsResponse | "failed">();
  const [attempt, setAttempt] = useState(0);
  // 받아 둔 값은 첫 그리기부터 그대로(불러오는 중 한 프레임 없이) — 다시 불러오기를 누르면 직접 묻는다
  const fromCard = attempt === 0 && preset !== null && preset !== "wait";
  const data = fromCard ? preset : fetched;
  useEffect(() => {
    if (preset === "wait" || (attempt === 0 && preset !== null)) return;
    let active = true;
    api.dayCallSectors(market)
      .then((response) => { if (active) setData(response); })
      .catch(() => { if (active) setData("failed"); });
    return () => { active = false; };
  }, [market, attempt, preset]);

  return (
    <section className="daycall-sectors small" aria-labelledby="daycall-sector-title">
      <h4 id="daycall-sector-title">섹터 전망</h4>
      {data && data !== "failed" && data.ready ? (
        <>
          <p className="muted">
            <time dateTime={data.date}>{data.date}</time> {data.date === data.target ? "세션" : "직전 세션 판정"}
            {" · "}표시 확률은 시장 대비 강할 추정 확률
          </p>
          <div className="daycall-sector-grid">
            <Picks sectors={data.top} />
            <Picks sectors={data.bottom} weak />
          </div>
          <p>
            {data.record.days > 0
              ? <>지난 {data.record.days}거래일 검증 · 강 {rate(data.record.top3_hit, 1)} · 약 {rate(data.record.bottom3_hit, 1)}</>
              : <span className="muted">지난 적중 기록이 아직 없어요.</span>}
          </p>
          {data.record.days > 0 && data.record.baseline && (
            <p className="muted">
              전날 순위 기준선 · 강 {rate(data.record.baseline.top3_hit, 1)} · 약 {rate(data.record.baseline.bottom3_hit, 1)}
            </p>
          )}
          {data.result && <p>세션 적중 · 강 {data.result.top_hits}/3 · 약 {data.result.bottom_hits}/3</p>}
        </>
      ) : (
        <div className="daycall-sector-status">
          <p className="muted" role="status">
            {data === undefined ? "섹터 전망을 불러오는 중…"
              : data === "failed" ? "섹터 전망을 불러오지 못했어요." : data.note}
          </p>
          {data !== undefined && (
            <button type="button" className="btn ghost" onClick={() => { setData(undefined); setAttempt(attempt + 1); }}>
              다시 불러오기
            </button>
          )}
        </div>
      )}
    </section>
  );
}