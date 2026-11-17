import type { FundBrief, FundChanges, FundDetail, FundHolding } from "./types";

// #484 개발 서버 전용 예시 데이터 — #483 API 가 없을 때(404 · 503)만 쓴다. 실제 공시 숫자가 아니다(화면에 "예시" 표시).
// 운영 빌드에서는 import.meta.env.DEV 분기로 쓰이지 않는다.

const h = (ticker: string | null, name: string, shares: number, value: number, change: FundHolding["change"], change_shares: number, extra: Partial<FundHolding> = {}): FundHolding => ({
  ticker, name, cusip: `EX${(ticker ?? name).replace(/[^A-Z0-9]/gi, "").slice(0, 6).toUpperCase().padEnd(6, "0")}0`, title: "COM", put_call: null,
  shares, value, weight: 0, change, change_shares, prev_shares: change === "new" ? null : shares - change_shares,
  change_pct: change === "new" || shares - change_shares === 0 ? null : Math.round((change_shares / (shares - change_shares)) * 10000) / 100, ...extra,
});

const pershingRows: FundHolding[] = [
  h("UBER", "Uber Technologies", 30_100_000, 2_348_000_000, "new", 30_100_000),
  h("BN", "Brookfield Corp", 42_000_000, 2_210_000_000, "increase", 3_500_000),
  h("GOOG", "Alphabet Inc", 9_100_000, 2_050_000_000, "unchanged", 0, { title: "CL C" }),
  h("HHH", "Howard Hughes Holdings", 18_800_000, 1_640_000_000, "unchanged", 0),
  h("CMG", "Chipotle Mexican Grill", 26_000_000, 1_420_000_000, "decrease", -4_200_000),
  h("QSR", "Restaurant Brands Intl", 21_000_000, 1_390_000_000, "decrease", -1_100_000),
  h("HLT", "Hilton Worldwide", 4_900_000, 1_270_000_000, "unchanged", 0),
  h("GOOGL", "Alphabet Inc", 5_400_000, 1_210_000_000, "increase", 600_000, { title: "CL A" }),
  h("AMZN", "Amazon.com", 5_100_000, 1_150_000_000, "new", 5_100_000),
  h("CP", "Canadian Pacific Kansas City", 12_000_000, 960_000_000, "unchanged", 0),
  h("FNMA", "Fannie Mae", 98_000_000, 880_000_000, "unchanged", 0),
  h("FMCC", "Freddie Mac", 60_000_000, 610_000_000, "unchanged", 0),
  h("SEG", "Seaport Entertainment Group", 5_200_000, 160_000_000, "increase", 400_000),
  h("NKE", "Nike Inc", 1_800_000, 120_000_000, "new", 1_800_000),
  h("HTZ", "Hertz Global", 9_000_000, 41_000_000, "decrease", -3_000_000),
  h(null, "Example Holdings Ltd", 1_000_000, 21_000_000, "new", 1_000_000),
  h("NFLX", "Netflix Inc", 0, 0, "sold", -1_900_000),
];
const total = pershingRows.reduce((s, r) => s + r.value, 0);
for (const r of pershingRows) r.weight = Math.round((r.value / total) * 10000) / 100;

export const FAKE_FUNDS: FundBrief[] = [
  { id: "pershing", name: "퍼싱스퀘어", manager: "빌 애크먼", cik: "0000000000",
    latest: { quarter: "2026Q3", period_of_report: "2026-09-30", filed_at: "2026-11-16", form: "13F-NT", count: 16, total_value: total,
      summary: "빌 애크먼 퍼싱스퀘어 3분기 포트폴리오 — 우버 23억 4,800만 달러 신규 매수",
      top_new: [{ ticker: "UBER", name: "Uber Technologies", value: 2_348_000_000 }, { ticker: "AMZN", name: "Amazon.com", value: 1_150_000_000 }, { ticker: "NKE", name: "Nike Inc", value: 120_000_000 }] },
    next_due: "2027-02-14", error: null },
  { id: "berkshire", name: "버크셔 해서웨이", manager: "워런 버핏", cik: "0000000000",
    latest: { quarter: "2026Q3", period_of_report: "2026-09-30", filed_at: "2026-11-14", form: "13F-HR", count: null, total_value: null, summary: null, top_new: [] },
    next_due: "2027-02-14", error: null },
  { id: "bridgewater", name: "브리지워터", manager: "레이 달리오", cik: "0000000000", latest: null, next_due: "2027-02-14", error: "예시: 아직 받지 못했습니다" },
];

export function fakeFund(id: string, quarter?: string): FundDetail | null {
  if (id !== "pershing") return null;
  return {
    id, name: "퍼싱스퀘어", manager: "빌 애크먼", quarter: quarter ?? "2026Q3", period_of_report: "2026-09-30", filed_at: "2026-11-16",
    form: "13F-NT", prev_quarter: "2026Q2", quarters: ["2026Q3", "2026Q2", "2026Q1"], reported_by: { name: "예시 운용사 LP", cik: "0000000000" },
    total_value: total, count: 16, holdings: pershingRows,
    summary: "빌 애크먼 퍼싱스퀘어 3분기 포트폴리오 — 우버 23억 4,800만 달러 신규 매수",
    bullets: [
      "신규 최대: 우버(UBER) 23억 4,800만 달러 — 비중 13.4%",
      "최대 증가: 브룩필드(BN) 350만 주 늘림",
      "축소: 치폴레(CMG) 420만 주 줄임",
      "전량 매도: 넷플릭스(NFLX)",
    ],
  };
}

export const FAKE_CHANGES: FundChanges = {
  items: {
    UBER: [{ fund: "pershing", name: "퍼싱스퀘어", quarter: "2026Q3", change: "new", change_shares: 30_100_000, shares: 30_100_000, weight: 8.4 }],
    AMZN: [{ fund: "pershing", name: "퍼싱스퀘어", quarter: "2026Q3", change: "new", change_shares: 5_100_000, shares: 5_100_000, weight: 4.1 }],
    NVDA: [{ fund: "berkshire", name: "버크셔 해서웨이", quarter: "2026Q3", change: "decrease", change_shares: -1_000_000, shares: 2_000_000, weight: 0.2 }],
  },
};
