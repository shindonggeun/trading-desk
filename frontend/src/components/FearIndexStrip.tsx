import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { FearIndex, FearItem } from "../types";
import FearGauge from "./FearGauge";

// #463 시장 · 지수 · 선물 위 공포지수 4칸: VIX · CNN 공포탐욕 · VKOSPI · 암호화폐 공포탐욕(#462).
// #475 지수마다 반원 게이지(FearGauge): 공포 = 왼쪽 빨강 → 탐욕 = 오른쪽 초록, 어제 · 지난주 값과 그때 구간.

const REFRESH_MS = 10 * 60_000; // 원천이 하루 · 몇 분 단위라 10분이면 충분하다

// 개발 서버에서 #462 API 가 없거나(404) 가짜 모드(items 빈 배열)일 때만 — 운영 빌드에서는 쓰지 않는다.
const FAKE: FearItem[] = [
  { key: "vix", name: "VIX", value: 24.3, band: "fear", band_ko: "공포", prev_day: 21.8, prev_week: 18.2, source: "fake", as_of: null },
  { key: "cnn", name: "CNN 공포탐욕", value: 18, band: "extreme_fear", band_ko: "극단적 공포", prev_day: 22, prev_week: 41, source: "fake", as_of: null },
  { key: "vkospi", name: "VKOSPI", value: null, band: null, band_ko: null, prev_day: null, prev_week: null, source: "fake", as_of: null, error: "무료 출처 없음" },
  { key: "crypto", name: "암호화폐 공포탐욕", value: 62, band: "greed", band_ko: "탐욕", prev_day: 58, prev_week: 49, source: "fake", as_of: null },
];

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

  // 이미 받은 값이 있으면 갱신 한 번 실패해도 그 값을 두고 아래에 작게만 알린다(배포 중 502 등)
  if (!data) return failed ? <p className="muted small fear-failed">공포지수를 불러오지 못했습니다: {failed}</p> : null;
  if (data.items.length === 0) return data.error ? <p className="muted small fear-failed">공포지수: {data.error}</p> : null;
  return (
    <>
      <div className="fear-strip" role="group" aria-label="공포지수" data-tour="fear">
        {data.items.map((item) => <FearGauge key={item.key} item={item} />)}
      </div>
      {failed && <p className="muted small fear-failed">최근 갱신 실패 — 앞서 받은 값입니다({failed})</p>}
    </>
  );
}
