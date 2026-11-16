/**
 * 차트 공통 표기(#434) — 십자선 시각 라벨과 오른쪽 축 눈금.
 */
import type { ISeriesApi, SeriesType, TickmarksPriceFormatterFn, Time } from "lightweight-charts";

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * 십자선 시각: 분봉 "11.16 13:05", 일봉 이상 "2026.11.16" — 라이브러리 기본("16 11월 '26 13:05") 대신.
 * 분봉 시각은 차트마다 KST 를 UTC 처럼 더해 넘기므로(KST_SEC) UTC 필드로 읽는다. 일봉은 "YYYY-MM-DD" 문자열 · BusinessDay.
 */
export function crosshairTime(t: Time): string {
  if (typeof t === "string") return t.replace(/-/g, ".");
  if (typeof t === "object") return `${t.year}.${pad(t.month)}.${pad(t.day)}`;
  const d = new Date(t * 1000);
  return `${pad(d.getUTCMonth() + 1)}.${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/**
 * 축 눈금 숫자가 라벨(마지막 값 · 축 라벨을 단 가격선) 뒤로 비쳐 "190,800 · 195,000", "80/100" 처럼 겹쳐 보이지 않게
 * 라벨에서 gapPx 안의 눈금은 비운다. 시리즈 priceFormat 의 tickmarksFormatter 로 쓴다 — 축을 그릴 때마다 지금 좌표로 계산하므로
 * 줌 · 이동 · 새 봉에도 맞는다. 라벨을 가진 시리즈는 그리기 전에 만들어지므로 getter 로 받는다.
 */
export function ticksClearOfLabels(
  labelled: () => ISeriesApi<SeriesType>[],
  format: (v: number) => string,
  gapPx = 18,
): TickmarksPriceFormatterFn {
  return (prices) => {
    const all = labelled();
    const ys: number[] = [];
    for (const s of all) {
      // 마지막 값 라벨 — 보이는 범위의 끝 값과 전체 끝 값 둘 다(어느 쪽을 그리든 비켜 가게)
      if (s.options().lastValueVisible) {
        for (const global of [false, true]) {
          const lv = s.lastValueData(global);
          const y = lv.noData ? null : s.priceToCoordinate(lv.price);
          if (y != null) ys.push(y);
        }
      }
      for (const line of s.priceLines()) {
        const o = line.options();
        const y = o.axisLabelVisible ? s.priceToCoordinate(o.price) : null;
        if (y != null) ys.push(y);
      }
    }
    const ref = all[0];
    return prices.map((p) => {
      const y = ref ? ref.priceToCoordinate(p) : null;
      return y != null && ys.some((ly) => Math.abs(ly - y) < gapPx) ? "" : format(p);
    });
  };
}
