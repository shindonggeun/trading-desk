import type { ReportDetail, ReportList } from "./types";

// #496 개발 서버 전용 예시 — #495 API 가 없을 때(404)만 쓴다. 실제 리포트가 아니다(화면에 "예시" 표시). 운영 빌드는 쓰지 않는다.

const A: ReportDetail = {
  id: "rp_example_1",
  created_at: "2026-11-17T07:31:02Z",
  as_of: "2026-11-14",
  title: "코스피, 전저점까지 깨지면 진짜 하락장일까?",
  summary: "1주 -6.2% 로 전저점까지 1.8% — 깨지면 어디까지, 버티면 어디서 다시 보는지 단계로 정리했어요.",
  subject: { symbol: "^KS11", name: "코스피", kind: "index" },
  source: "ai",
  why: { text: "지난 1주 -6.2% 로 후보 중 가장 크게 움직였고 전저점까지 1.8% 남았어요.",
    candidates: [{ symbol: "^KS11", name: "코스피", score: 3.4, reasons: ["1주 -6.2%", "전저점까지 1.8%"] }, { symbol: "NVDA", name: "엔비디아", score: 2.1, reasons: ["거래량 2.3배"] }] },
  hook: "전저점까지 깨지면 진짜 하락장 아니야? 이번 주 코스피를 같이 짚어 봐요.",
  sections: [
    { title: "지금 어디쯤인가요", body: "코스피는 지난 1주 6.2% 내려 전저점 2,450 바로 위에 있어요.\n\n이 자리는 지난 두 번 모두 반등이 나온 받침이었어요.",
      highlight: "전저점 2,450 을 종가로 지키는지가 이번 주 핵심이에요.", term: { name: "전저점", explain: "최근에 가장 낮았던 가격. 여기서 다시 받치는지 보는 기준이 돼요." },
      chart: { kind: "levels", levels: [2450, 2580], labels: ["전저점", "저항"], marks: [2494] }, image: null },
    { title: "깨지면 어디까지 볼까요", body: "2,450 이 무너지면 다음 받침은 2,380~2,410 구역이에요.",
      highlight: "", term: { name: "지지 구역", explain: "여러 번 멈추거나 되돌아선 가격대. 한 선이 아니라 폭이 있는 띠로 봐요." },
      chart: { kind: "zone", levels: [2380, 2410], labels: ["구역 아래", "구역 위"], marks: [2494] }, image: null },
    { title: "버티면 무엇을 확인하나요", body: "2,450 위에서 버티고 2,580 을 다시 넘으면 하락 흐름이 멈춘 것으로 봐요.",
      highlight: "", term: { name: "되돌림", explain: "크게 움직인 뒤 일부를 되돌아가는 움직임." }, chart: { kind: "none", levels: [], marks: [] }, image: null },
  ],
  similar: [{ date: "2025-04-07", what: "1주 -8.1% · 전저점 이탈", after: "20거래일 뒤 +6.3%" }],
  stages: [
    { label: "1단계", low: 2440, high: 2460, why: "전저점 근처 — 종가로 지키는지 확인" },
    { label: "2단계", low: 2380, high: 2410, why: "다음 지지 구역" },
    { label: "최대", low: 2320, high: null, why: "여기도 깨지면 계획을 다시 세워요" },
  ],
  checklist: [{ q: "종가로 2,450 위인가요?", a: "위면 1단계 유지, 아래면 2단계로" }, { q: "외국인 선물이 같이 파나요?", a: "같이 팔면 서두르지 않아요" }],
  closing: "예측이 아니라 조건과 대응이에요. 가격이 조건에 닿을 때만 움직여요.",
  version: 1,
};

export const FAKE_REPORTS: ReportDetail[] = [
  A,
  { ...A, id: "rp_example_0", created_at: "2026-11-14T07:30:00Z", title: "엔비디아, 거래량이 늘며 저항을 넘을까?", subject: { symbol: "NVDA", name: "엔비디아", kind: "favorite" },
    summary: "거래량 2.3배 속 저항 바로 아래 — 넘으면 · 막히면 할 일.", hook: "거래량이 이렇게 늘면 이번엔 넘을까요?" },
];

export function fakeList(): ReportList {
  return { items: FAKE_REPORTS.map(({ id, created_at, title, summary, subject, source }) => ({ id, created_at, title, summary, subject, source })),
    generating: false, next_due: "2026-11-20T07:30:00Z", next_before: null };
}
