import { useEffect, useState } from "react";
import { api } from "../api";
import { price } from "../format";
import type { BottomZone } from "../types";
import { BottomStageBadge } from "./BottomZoneCard";

/** 늘 보이는 주요 지수 */
const INDICES: { ticker: string; name: string }[] = [
  { ticker: "^KS11", name: "코스피" },
  { ticker: "^NDX", name: "나스닥100" },
  { ticker: "^SOX", name: "필라델피아 반도체" },
];
const RANK: Record<string, number> = { none: 0, watch: 1, approach: 2, bottom: 3 };
const PARALLEL = 4;

type Row = { ticker: string; name: string; z: BottomZone | null };

/** 대시보드 "인생 타점" 구역(#241): 주요 지수는 늘, 즐겨찾기는 "접근" 이상인 것만 한 줄씩. */
export default function BottomZoneBrief() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      const favs = await api.favorites().catch(() => []);
      const wanted = [...INDICES, ...favs.filter((f) => !INDICES.some((i) => i.ticker === f.ticker))
        .map((f) => ({ ticker: f.ticker, name: f.name ?? f.ticker }))];
      const out: Row[] = [];
      for (let i = 0; i < wanted.length; i += PARALLEL) {
        if (!alive) return;
        const part = await Promise.all(wanted.slice(i, i + PARALLEL).map((w) =>
          api.bottomZone(w.ticker).then((z) => ({ ...w, z }), () => ({ ...w, z: null }))));
        out.push(...part);
        if (alive) setRows([...out]);
      }
      if (alive) {
        setRows(out);
        setDone(true);
      }
    })();
    return () => { alive = false; };
  }, []);
  if (rows === null) return <p className="muted small">불러오는 중…</p>;
  const index = new Set(INDICES.map((i) => i.ticker));
  const shown = rows.filter((r) => r.z?.supported && (index.has(r.ticker) || RANK[r.z.stage ?? "none"] >= RANK.approach));
  const favCount = rows.filter((r) => !index.has(r.ticker)).length;
  const favShown = shown.filter((r) => !index.has(r.ticker)).length;
  return (
    <div className="bz-brief">
      <ul className="bz-rows">
        {shown.map((r) => (
          <li key={r.ticker}>
            <a href={`#/chart/${encodeURIComponent(r.ticker)}?interval=1w`} className="bz-row">
              <span className="bz-name">{r.name}</span>
              <BottomStageBadge z={r.z as BottomZone} />
              <span className="num small">{r.z?.score}점</span>
              <span className="num small muted">−{r.z?.drawdown_pct}%</span>
              {r.z?.ladder && r.z.ladder.length > 0 && RANK[r.z.stage ?? "none"] >= RANK.approach && (
                <span className="small muted bz-row-ladder">
                  줍줍 {r.z.ladder.map((s) => price(s.price)).join(" → ")}
                </span>
              )}
            </a>
          </li>
        ))}
      </ul>
      {!done && <p className="muted small">즐겨찾기 종목을 살펴보는 중…</p>}
      {done && favCount > 0 && favShown === 0 && <p className="muted small">즐겨찾기 중 "접근" 이상인 종목은 지금 없습니다.</p>}
      <p className="muted small">주봉 · 월봉 기준 장기 바닥권 판단(규칙) — 예측이 아닙니다. 종목을 누르면 차트에서 분할 매수 선을 봅니다.</p>
    </div>
  );
}
