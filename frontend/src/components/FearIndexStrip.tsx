import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { FearBand, FearIndex, FearItem } from "../types";

// #463 시장 · 지수 · 선물 위 공포지수 4칸: VIX · CNN 공포탐욕 · VKOSPI · 암호화폐 공포탐욕(#462).
// 구간 배지는 서버 band 를 그대로 쓴다(VIX 방향은 서버가 맞춘다). 공포 = 파랑 · 탐욕 = 빨강(한국식 내림 · 오름 색).

const REFRESH_MS = 10 * 60_000; // 원천이 하루 · 몇 분 단위라 10분이면 충분하다
const BAND_KO: Record<FearBand, string> = {
  extreme_fear: "극단적 공포", fear: "공포", neutral: "중립", greed: "탐욕", extreme_greed: "극단적 탐욕",
};

function num(v: number): string {
  return v.toLocaleString("ko-KR", { maximumFractionDigits: Math.abs(v) < 100 ? 1 : 0 });
}
function delta(now: number, before: number | null): string | null {
  if (before == null) return null;
  const d = Math.round((now - before) * 10) / 10 || 0;
  return `${d > 0 ? "+" : d < 0 ? "−" : "±"}${num(Math.abs(d))}`;
}

// 개발 서버에서 #462 API 가 없거나(404) 가짜 모드(items 빈 배열)일 때만 — 운영 빌드에서는 쓰지 않는다.
const FAKE: FearItem[] = [
  { key: "vix", name: "VIX", value: 24.3, band: "fear", band_ko: "공포", prev_day: 21.8, prev_week: 18.2, source: "fake", as_of: null },
  { key: "cnn", name: "CNN 공포탐욕", value: 18, band: "extreme_fear", band_ko: "극단적 공포", prev_day: 22, prev_week: 41, source: "fake", as_of: null },
  { key: "vkospi", name: "VKOSPI", value: null, band: null, band_ko: null, prev_day: null, prev_week: null, source: "fake", as_of: null, error: "무료 출처 없음" },
  { key: "crypto", name: "암호화폐 공포탐욕", value: 62, band: "greed", band_ko: "탐욕", prev_day: 58, prev_week: 49, source: "fake", as_of: null },
];

function Cell({ item }: { item: FearItem }) {
  if (item.value == null) {
    const why = item.key === "vkospi" ? "출처 없음" : "불러오지 못함";
    return (
      <div className="fear-cell fear-empty" title={item.error ?? undefined}>
        <span className="fear-name">{item.name}</span>
        <span className="muted small">{why}</span>
      </div>
    );
  }
  const band = item.band;
  const day = delta(item.value, item.prev_day);
  const week = delta(item.value, item.prev_week);
  return (
    <div className={`fear-cell${band ? ` fear-${band}` : ""}`} title={item.as_of ? `${item.source} · ${item.as_of}` : item.source}>
      <span className="fear-name">{item.name}</span>
      <span className="fear-value">
        <strong>{num(item.value)}</strong>
        {band && <span className="fear-band">{item.band_ko ?? BAND_KO[band]}</span>}
      </span>
      <span className="muted small">
        {day ? `전일 ${day}` : "전일 —"}{week ? ` · 1주 ${week}` : ""}
      </span>
    </div>
  );
}

export default function FearIndexStrip() {
  const [data, setData] = useState<FearIndex | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.marketFear()
      .then((d) => {
        if (!alive) return;
        if (import.meta.env.DEV && d.items.length === 0) d = { items: FAKE };
        setData(d); setFailed(null);
      })
      .catch((e) => {
        if (!alive) return;
        // 404 = 공포지수 API(#462)가 아직 배포되지 않음: 운영에서는 칸을 그리지 않는다
        if (e instanceof ApiError && e.status === 404) { setData({ items: import.meta.env.DEV ? FAKE : [] }); return; }
        setFailed(e instanceof Error ? e.message : "불러오지 못했습니다");
      });
    load();
    const t = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  if (failed) return <p className="muted small fear-failed">공포지수를 불러오지 못했습니다: {failed}</p>;
  if (!data) return null;
  if (data.items.length === 0) return data.error ? <p className="muted small fear-failed">공포지수: {data.error}</p> : null;
  return (
    <div className="fear-strip" role="group" aria-label="공포지수">
      {data.items.map((item) => <Cell key={item.key} item={item} />)}
    </div>
  );
}
