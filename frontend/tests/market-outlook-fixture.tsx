import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import "pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css";
import "../src/styles.css";
import MarketOutlook from "../src/components/MarketOutlook";
import type { MarketOutlookEvidence, MarketOutlookResponse } from "../src/marketOutlook";
import type { MacroBrief as MacroBriefResponse } from "../src/types";

const asOf = "2026-11-23T03:18:00+00:00";
const observed = "2026-11-20T21:00:00+00:00";
const fetched = "2026-11-21T01:10:00+00:00";
const available = { status: "available", reason: null } as const;

function evidence(overrides: Pick<MarketOutlookEvidence, "id" | "unit"> & Partial<MarketOutlookEvidence>): MarketOutlookEvidence {
  return {
    ...available, direction: null, details: {}, source: "합성 가격 원천", observed_at: observed,
    fetched_at: fetched, age_days: 3, value: null, ...overrides,
  };
}

const disabled = {
  status: "disabled", reason: "source_unconfigured", observed_at: null, fetched_at: null, age_days: null, source: null,
} as const;

export const reboundOutlook: MarketOutlookResponse = {
  market: "us", as_of: asOf,
  views: [
    {
      id: "growth-2027", market: "us", expert: "윤서진", organization: "예시 리서치", source: "미국 주식 전망 보고서",
      source_url: "https://example.test/outlook/report", date_source_url: null,
      spoken_on: null, published_on: "2026-11-19", stance: "bullish", horizon: "2027년 말까지",
      summary: "기업 이익이 주가 상승을 뒷받침할 전망입니다.", basis: "기업 이익 증가를 긍정 요인으로 봅니다.",
      change: "직전 전망보다 상승률 기대를 낮췄습니다.", positions: [], status: "available", age_days: 4,
    },
    {
      id: "caution-2026", market: "us", expert: "박도현", organization: "예시 패밀리 오피스", source: "예시 포럼 인터뷰",
      source_url: "https://example.test/outlook/interview", date_source_url: "https://example.test/outlook/schedule",
      spoken_on: "2026-11-17", published_on: null, stance: "cautious", horizon: "향후 약 12개월",
      summary: "증시 방향의 불확실성이 커 관망합니다.", basis: "장기 금리와 이익 전망의 불확실성을 짚었습니다.",
      change: null, positions: [{ asset: "구리", side: "short", status: "unverified", as_of: null }],
      status: "available", age_days: 6,
    },
  ],
  filings: [{
    fund_id: "duquesne", name: "Duquesne Family Office", cik: "0001536411", quarter: "2026Q2",
    period_of_report: "2026-06-30", filed_at: "2026-08-14", form: "13F-HR", accession: "fixture-2026Q2",
    source_url: "https://example.test/outlook/sec", publication_lag_days: 45, holdings_status: "available",
    previous_quarter: "2026Q1",
    holdings: [{
      ticker: "DEMO", name: "Example Company", cusip: "000000001", title: "COM", put_call: null, share_type: "SH",
      shares: 100, value: 1_500_000, weight: 100, change: "increase", change_shares: 10, prev_shares: 90, change_pct: 11.1,
    }, {
      ticker: "DEMO", name: "Example Company", cusip: "000000001", title: "COM", put_call: "PUT", share_type: "SH",
      shares: 30, value: 450_000, weight: null, change: "new", change_shares: 30, prev_shares: 0, change_pct: null,
    }],
  }],
  regime: {
    state: "rebound_candidate", supporting: ["index_rebound", "volatility"], opposing: ["index_trend", "index_drawdown"],
    next_checks: ["5거래일 반등이 이어지는지 확인", "20일·50일 구조와 시장 폭·신용을 별도로 확인"],
    evidence: [
      evidence({ id: "index_trend", unit: "%", value: -3.23, direction: "negative", details: { close: 6000, sma20: 6100, sma50: 6200 } }),
      evidence({ id: "index_drawdown", unit: "%", value: -7.69, direction: "negative", details: { peak: 6500 } }),
      evidence({ id: "index_rebound", unit: "%", value: 2.4, direction: "positive" }),
      evidence({ id: "volatility", unit: "index_points", value: 20, direction: "positive", details: { change5: -2.5 } }),
      evidence({ id: "breadth", unit: "%", ...disabled }),
      evidence({ id: "credit_spread", unit: "bps", ...disabled }),
    ],
  },
  sources: {
    views: available,
    filings: { status: "stale", reason: "check_overdue", source: "SEC EDGAR", checked_at: "2026-11-12T03:00:00+00:00" },
  },
};

export const incompleteOutlook: MarketOutlookResponse = {
  ...reboundOutlook,
  views: [
    { ...reboundOutlook.views[0], status: "stale", age_days: 99, source_url: "data:text/plain,not-a-source" },
    reboundOutlook.views[1],
  ],
  filings: [],
  sources: { ...reboundOutlook.sources, filings: { status: "error", reason: "refresh_failed" } },
  regime: {
    state: "undetermined", supporting: ["index_rebound"], opposing: [], next_checks: ["최신 마감 지수와 낙폭을 확인"],
    evidence: [
      evidence({ id: "index_trend", unit: "%", status: "missing", reason: "insufficient_history" }),
      evidence({
        id: "index_drawdown", unit: "%", status: "stale", reason: "latest_session_missing", value: -7.69, direction: "negative",
      }),
      evidence({ id: "index_rebound", unit: "%", value: 2.4, direction: "positive" }),
      evidence({ ...disabled, id: "volatility", unit: "index_points", status: "error", reason: "timeout" }),
      evidence({ id: "breadth", unit: "%", ...disabled }),
    ],
  },
};

function assert(ok: unknown, message: string): asserts ok {
  if (!ok) throw new Error(message);
}

function named(host: ParentNode, tag: string, title: string): HTMLElement {
  const element = [...host.querySelectorAll<HTMLElement>(`${tag}[aria-labelledby]`)].find((candidate) => {
    const label = candidate.getAttribute("aria-labelledby");
    return label && document.getElementById(label)?.textContent === title;
  });
  assert(element, `${title} 영역이 없습니다.`);
  return element;
}

function contains(host: Node, expected: string) {
  assert(host.textContent?.includes(expected), `“${expected}” 문구가 없습니다.`);
}

function fact(host: ParentNode, term: string): string | null | undefined {
  return [...host.querySelectorAll("dt")].find((node) => node.textContent === term)?.nextElementSibling?.textContent;
}

function assertViewLayout(first: HTMLElement, second: HTMLElement, sameRow: boolean) {
  const firstBox = first.getBoundingClientRect();
  const secondBox = second.getBoundingClientRect();
  if (sameRow) assert(firstBox.top === secondBox.top, "두 관점을 나란히 배치해야 합니다.");
  else assert(secondBox.top >= firstBox.bottom, "두 관점을 세로로 배치해야 합니다.");
}

export function runMarketOutlookRegression(host: HTMLElement, status: HTMLElement) {
  const root = createRoot(host);
  function render(data: MarketOutlookResponse | null, failedAt: string | null = null) {
    flushSync(() => root.render(<MarketOutlook data={data} failedAt={failedAt} />));
  }

  try {
    render(null);
    contains(host, "시장 전망을 불러오는 중");
    render(null, asOf);
    contains(host, "시장 전망을 불러오지 못했습니다.");

    render(reboundOutlook);
    const views = named(host, "section", "공개 시장 관점");
    const bullish = named(views, "article", "윤서진");
    const cautious = named(views, "article", "박도현");
    contains(bullish, "상승 전망");
    contains(bullish, "2027년 말까지");
    contains(bullish, "직전 전망보다 상승률 기대를 낮췄습니다.");
    contains(cautious, "신중 전망");
    contains(cautious, "향후 약 12개월");
    contains(cautious, "확인된 비교 자료 없음");
    contains(cautious, "장기 금리와 이익 전망의 불확실성");
    assert(fact(bullish, "공개일") === "2026-11-19" && fact(bullish, "발언일") === "미확인", "공개일과 발언일을 분리합니다.");
    assert(fact(cautious, "발언일") === "2026-11-17" && fact(cautious, "공개일") === "미확인", "미확인 공개일을 추정하지 않습니다.");
    assert(cautious.querySelector('a[href="https://example.test/outlook/schedule"]')?.textContent === "날짜 확인 출처", "발언일 출처 링크가 없습니다.");
    assert(bullish.querySelector('a[href="https://example.test/outlook/report"]')?.getAttribute("rel") === "noopener noreferrer", "출처 링크가 분리된 창에서 안전하게 열려야 합니다.");
    contains(cautious, "구리 숏 언급 · 보유 미확인 · 시점 미확인");

    const filings = named(host, "section", "기관 보유 공시 · 13F");
    contains(filings, "오래된 자료");
    contains(filings, "기준일에서 제출까지 45일");
    assert(fact(filings, "보유 기준일") === "2026-06-30" && fact(filings, "제출일") === "2026-08-14", "13F 기준일과 제출일을 분리합니다.");
    assert(filings.querySelector('a[href="https://example.test/outlook/sec"]'), "공시 원문 링크가 없습니다.");
    assert(!filings.textContent?.includes("구리"), "인터뷰 포지션이 공시로 표시됩니다.");
    contains(filings, "미보고 숏");
    const holdingList = filings.querySelector("details");
    assert(holdingList && !holdingList.open, "공시 보유 목록이 기본으로 펼쳐져 있습니다.");
    contains(holdingList, "공시 내역 · 직전 분기 비교");
    holdingList.querySelector("summary")?.click();
    assert(holdingList.open, "공시 보유 목록을 열 수 없습니다.");
    contains(holdingList, "2026년 1분기와 비교");
    contains(holdingList, "DEMO · 풋 옵션");
    contains(holdingList, "명목가");
    holdingList.open = false;

    const regime = named(host, "section", "지표로 본 장세");
    contains(regime, "단기 반등 후보");
    contains(named(regime, "section", "단기 반등"), "반등 후보");
    const trendStatus = named(regime, "section", "장기 추세 확인");
    contains(trendStatus, "회복 미확인");
    contains(trendStatus, "20·50일선 구조와 시장 폭·신용");
    contains(regime, "단기 반등 · 5거래일 수익률");
    contains(regime, "+2.4%");
    contains(regime, "중기 추세 · 20·50일 이동평균");
    contains(regime, "하락 구조");
    contains(regime, "판정 근거");
    contains(regime, "반대 근거");
    contains(regime, "다음 확인 조건");
    assert(!regime.textContent?.includes("추세 회복 관찰"), "단기 반등과 추세 회복이 함께 판정됐습니다.");
    const observedTime = regime.querySelector(`time[datetime="${observed}"]`);
    assert(observedTime?.textContent?.includes("06:00 KST") && regime.querySelector(`time[datetime="${fetched}"]`), "관측·조회 시각을 구분해 KST로 표시해야 합니다.");

    if (views.clientWidth >= 600) assertViewLayout(bullish, cautious, true);
    if (views.clientWidth <= 500 && !matchMedia("(min-width: 1100px)").matches) assertViewLayout(bullish, cautious, false);
    assert(host.scrollWidth <= host.clientWidth, "시장 전망 카드가 화면 너비를 넘습니다.");

    render(reboundOutlook, asOf);
    contains(host, "다시 읽기 실패 · 마지막 평가 결과를 표시합니다.");
    contains(host, "단기 반등 후보");

    render(incompleteOutlook);
    const incompleteRegime = named(host, "section", "지표로 본 장세");
    const incompleteViews = named(host, "section", "공개 시장 관점");
    contains(incompleteRegime, "판정 유보");
    contains(named(incompleteRegime, "section", "단기 반등"), "반등 신호 관측");
    contains(named(incompleteRegime, "section", "장기 추세 확인"), "판정 유보");
    contains(incompleteRegime, "자료 없음 · 연속 거래일 자료가 부족합니다");
    contains(incompleteRegime, "오래된 자료 · 최신 마감 자료가 없습니다");
    contains(incompleteRegime, "원천 미연결");
    contains(incompleteRegime, "원천 오류 · 원천 응답 시간이 초과됐습니다");
    const groups = [...incompleteRegime.querySelectorAll("h4")];
    const supporting = groups.find((heading) => heading.textContent === "개선 근거")?.nextElementSibling;
    const opposing = groups.find((heading) => heading.textContent === "악화 근거")?.nextElementSibling;
    assert(supporting?.textContent === "단기 반등 · 5거래일 수익률", "유효한 반등 지표가 판정 유보 상태에서 보이지 않습니다.");
    assert(opposing?.textContent === "확인된 근거 없음", "오래된 데이터가 판정 근거에 포함됐습니다.");
    contains(incompleteViews, "오래된 자료 · 날짜 기준 99일 경과");
    contains(incompleteViews, "기업 이익이 주가 상승을 뒷받침할 전망입니다.");
    assert(!named(incompleteViews, "article", "윤서진").querySelector("a"), "HTTP(S) 이외의 원천 URL을 링크로 표시했습니다.");
    assert(named(incompleteViews, "article", "박도현").querySelector("a"), "한 원천의 상태가 다른 관점의 표시를 막습니다.");
    contains(named(host, "section", "기관 보유 공시 · 13F"), "원천 오류 · SEC 최신 조회에 실패했습니다");

    render({
      ...reboundOutlook,
      views: [
        reboundOutlook.views[0],
        { ...reboundOutlook.views[1], stance: "bearish" },
        { ...reboundOutlook.views[1], id: "neutral-2026", expert: "김가람", stance: "neutral" },
      ],
      regime: {
        ...reboundOutlook.regime, state: "recovery_watch", supporting: ["index_trend", "breadth"], opposing: [],
        evidence: [
          evidence({ id: "index_trend", unit: "%", value: 4.23, direction: "positive" }),
          evidence({ id: "index_rebound", unit: "%", value: 0.8, direction: "neutral" }),
          evidence({ id: "breadth", unit: "%", value: 66, direction: "positive" }),
        ],
      },
    });
    const contrary = named(host, "section", "공개 시장 관점");
    contains(named(contrary, "article", "윤서진"), "상승 전망");
    contains(named(contrary, "article", "박도현"), "하락 전망");
    contains(named(contrary, "article", "김가람"), "중립 전망");
    const recovery = named(host, "section", "지표로 본 장세");
    contains(named(recovery, "section", "단기 반등"), "반등 기준 미달");
    contains(named(recovery, "section", "장기 추세 확인"), "추세 회복 관찰");

    render({
      ...reboundOutlook,
      filings: [{
        ...reboundOutlook.filings[0], quarter: "2026Q3", period_of_report: "2026-09-30", filed_at: "2026-11-16",
        publication_lag_days: 47, form: "13F-HR/A", accession: "fixture-2026Q3-A", holdings_status: "missing",
        previous_quarter: null, holdings: [],
      }],
    });
    const amendment = named(host, "section", "기관 보유 공시 · 13F");
    contains(amendment, "13F-HR/A");
    contains(amendment, "이 접수 건의 보유 내역 자료가 없습니다.");
    assert(!amendment.querySelector("details"), "직전 접수의 보유 목록이 새 수정 공시에 남았습니다.");

    const selected = new URLSearchParams(location.search).get("case");
    render(selected === "incomplete" ? incompleteOutlook : reboundOutlook);
    status.dataset.result = "passed";
    status.textContent = "시장 전망 회귀 검증 통과";
  } catch (error) {
    status.dataset.result = "failed";
    status.textContent = error instanceof Error ? error.message : String(error);
    throw error;
  }
}

export async function runMacroOutlookIntegration(host: HTMLElement, status: HTMLElement) {
  try {
    const { default: MacroBrief } = await import("../src/components/MacroBrief");
    const news: MacroBriefResponse = { ticker: null, themes: [], sources: [], refreshed_at: asOf, error: null };
    const newsPath = "/api/macro/brief";
    const outlookPath = "/api/macro/market-outlook?market=us";
    const failure = new URLSearchParams(location.search).get("fail");
    const failedPath = failure === "news" ? newsPath : failure === "outlook" ? outlookPath : null;
    const results = new Map<string, MacroBriefResponse | MarketOutlookResponse>([[newsPath, news], [outlookPath, reboundOutlook]]);
    const requests: { path: string; sameOrigin: boolean; method: string; credentials: RequestCredentials | undefined }[] = [];
    window.fetch = async (input, init) => {
      const request = input instanceof Request ? input : null;
      const url = new URL(request?.url ?? String(input), location.origin);
      const path = `${url.pathname}${url.search}`;
      requests.push({
        path, sameOrigin: url.origin === location.origin,
        method: init?.method ?? request?.method ?? "GET", credentials: init?.credentials ?? request?.credentials,
      });
      if (path === failedPath) return Response.json({ detail: "합성 조회 실패" }, { status: 500 });
      return results.has(path) ? Response.json(results.get(path)) : new Response(null, { status: 404 });
    };

    // The wide desktop dashboard can still assign the macro panel a single narrow column.
    host.style.maxWidth = "380px";
    const root = createRoot(host);
    root.render(<MacroBrief />);
    const outlookText = failure === "outlook" ? "시장 전망을 불러오지 못했습니다." : "단기 반등 후보";
    const newsText = failure === "news" ? "브리핑을 불러오지 못했습니다." : "모은 테마가 없습니다.";
    const complete = () => host.textContent?.includes(outlookText) && host.textContent?.includes(newsText);
    for (let attempt = 0; attempt < 100 && !complete(); attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    contains(host, outlookText);
    contains(host, newsText);
    assert(requests.length === 2 && requests.every(({ path }) => results.has(path)), "기존 브리핑과 새 전망을 따로 조회해야 합니다.");
    assert(requests.every(({ sameOrigin, method, credentials }) => sameOrigin && method === "GET" && credentials === "same-origin"), "기존 세션의 동일 출처 GET 요청을 사용해야 합니다.");
    if (failure !== "outlook") {
      const views = named(host, "section", "공개 시장 관점");
      assertViewLayout(named(views, "article", "윤서진"), named(views, "article", "박도현"), matchMedia("(min-width: 1100px)").matches);
    }
    assert(host.scrollWidth <= host.clientWidth, "대시보드의 시장 전망 카드가 배정 너비를 넘습니다.");
    status.dataset.result = "passed";
    status.textContent = "거시 브리핑과 시장 전망 통합 검증 통과";
  } catch (error) {
    status.dataset.result = "failed";
    status.textContent = error instanceof Error ? error.message : String(error);
    throw error;
  }
}
