/**
 * #491 화면 안내 투어(driver.js). 투어 셋: 대시보드 · 분석 차트 · 매매 팁/기관 포트폴리오.
 * - 대상은 data-tour 속성(없으면 기존 선택자)으로 찾고, 없거나 안 보이는 단계는 건너뛴다(접힌 구역 · #487 배치 편집).
 * - driver.js 는 투어를 시작할 때만 불러온다(첫 화면 번들에 넣지 않는다).
 * - 본 투어 · "다시 보지 않기"는 이 브라우저에 기억(localStorage, try/catch).
 */
import { markSeen, onRoute, type TourId, type TourResult } from "./tourState";

interface Step {
  /** 앞에서부터 처음 보이는 요소를 쓴다 */
  targets: string[];
  title: string;
  /** "무엇인가" 한 줄 + "초보자는 이렇게" 한 줄 */
  what: string;
  how: string;
}
interface Segment {
  /** 이 묶음을 보이기 전에 할 일(예: 차트 탭으로) — 끝나면 true */
  before?: () => Promise<boolean>;
  steps: Step[];
}

const sec = (id: string) => [`[data-tour="dash-${id}"]`, `[aria-labelledby="dash-${id}-title"]`];

const TOURS: Record<TourId, Segment[]> = {
  dashboard: [{
    steps: [
      { targets: ['[data-tour="dash-search"]', ".dash-search"], title: "분석 시작", what: "종목 이름이나 티커(종목 코드)를 넣고 분석을 시작하는 곳입니다.", how: "처음엔 아는 종목 하나를 넣어 결과 화면을 둘러보세요. 한 번에 몇 분 걸립니다." },
      { targets: ['[data-tour="today"]', ".today-strip"], title: "오늘 볼 것", what: "즐겨찾기 가운데 지금 손댈 만한 종목(구역 근처 · 실적 임박 · 기관 매매)을 모읍니다.", how: "매일 아침 여기부터 보고, 눌러서 차트를 엽니다." },
      { targets: [...sec("market")], title: "시장 · 지수 · 선물", what: "주요 지수 · 선물 · 코인의 등락과 미니 차트입니다. 누르면 지지(받침) · 저항(막힘) 가격이 보입니다.", how: "빨강은 오름, 파랑은 내림. 전체 분위기를 먼저 보고 종목을 고르세요." },
      { targets: ['[data-tour="fear"]', ".fear-strip"], title: "공포지수", what: "VIX · CNN 공포탐욕 등으로 시장이 겁먹었는지(공포) 들떴는지(탐욕) 봅니다.", how: "극단적 공포일 때 서두르지 말고, 극단적 탐욕일 때 추격 매수를 조심하세요." },
      { targets: ['[data-tour="trend"]', ".trend-strip"], title: "지수 지금 추세", what: "코스피 · 나스닥100 등이 대세 상승장인지 하락장인지 지금 상태를 설명합니다.", how: "예측이 아니라 지금 흐름입니다. 흐름과 반대 방향 매매는 비중을 줄이세요." },
      { targets: [...sec("weekly")], title: "주간 지도", what: "한 주 단위로 본 큰 흐름과 자리입니다.", how: "일봉이 흔들려도 주간 방향이 같은지 확인하는 데 씁니다." },
      { targets: [...sec("flow")], title: "시장 수급", what: "외국인 · 기관 · 개인이 얼마를 사고팔았는지(수급)와 오늘 장중 흐름입니다.", how: "외국인 현물 · 선물이 같은 방향이면 힘이 실린 것으로 봅니다." },
      { targets: [...sec("favorites")], title: "즐겨찾기 시세", what: "즐겨찾기 종목의 가격과 롱(오름 베팅) · 숏(내림 베팅) 비율 막대입니다.", how: "막대가 한쪽으로 쏠리면 반대로 튈 위험도 있다는 뜻으로 읽으세요." },
      { targets: ['[data-tour="dash-layout"]', ".dash-edit-bar"], title: "배치 편집", what: "구역을 끌어 옮기고 크기를 바꿀 수 있습니다.", how: "자주 보는 구역을 위로 올려 두세요. '기본 배치로'로 언제든 되돌립니다." },
    ],
  }],
  analysis: [
    {
      before: () => openRunTab("summary"),
      steps: [
        { targets: ['[data-tour="easy-tab"]', "#run-tab-easy"], title: "쉽게 보기", what: "분석 결과를 질문 · 소제목 · 단계 카드 · 체크리스트로 풀어 쓴 초보자 해설 탭입니다.", how: "처음엔 이 탭부터 읽고, 숫자 근거는 차트 탭에서 확인하세요." },
        { targets: ['[data-tour="plan-action"]', ".pa-action"], title: "지금 할 일", what: "분석 계획을 한 문장으로 줄인 '지금 할 일'입니다.", how: "관망이면 조건 가격(롱 조건 · 숏 조건)이 올 때까지 기다리면 됩니다." },
      ],
    },
    {
      before: () => openRunTab("chart"),
      steps: [
        { targets: ['[data-tour="price-chart"]', ".price-chart"], title: "진입 · 손절 · 목표 선", what: "선 이름에 시나리오(롱 · 숏 · 관망)와 근거(지지 · 저항 구역 · 피보나치 · 갭)가 붙습니다.", how: "진입은 들어갈 값, 손절은 틀렸다고 보고 나올 값, 목표는 이익을 챙길 값입니다. 선을 누르면 근거가 펼쳐집니다." },
        { targets: ['[data-tour="explain"]', ".explain-panel"], title: "차트 해설", what: "지금 가격이 어디쯤이고 위 · 아래로 가면 무엇을 할지 풀어 쓴 설명입니다.", how: "처음엔 '지금 할 일'과 '지금 가격 위치'만 읽어도 충분합니다." },
        { targets: ['[data-tour="chart-menu"]', ".chart-menu"], title: "표시 고르기 · 안 메운 갭", what: "여기서 차트에 그릴 것(지지 · 저항, 추세선, 안 메운 갭 등)을 켜고 끕니다. 갭은 봉이 건너뛴 가격 구간으로, 옅은 상자로 보입니다.", how: "처음엔 '타점 보기' 묶음 그대로 두세요. 갭은 나중에 메워지는 일이 많아 목표 · 눌림 후보로만 참고합니다." },
      ],
    },
  ],
  tips: [{
    steps: [
      { targets: ['[data-tour="trend-card"]', ".trend-card"], title: "대세 국면", what: "지수마다 지금 대세 상승장 · 하락장 · 박스장인지와 근거입니다.", how: "국면별 할 일 · 하지 말 것을 먼저 읽으세요." },
      { targets: ['[data-tour="tip-regime"]', ".tip-regime"], title: "금리 · 달러 국면", what: "기준금리 방향 · 10년물 금리 · 달러 흐름입니다.", how: "금리 인상기엔 원자재 · 금광주처럼 약했던 자산을 조심하세요." },
      { targets: ['[data-tour="tip-list"]', ".tip-list"], title: "국면별 매매 팁", what: "과거 기록으로 확인한 국면별 주의점입니다.", how: "'근거 약함' 표시가 있으면 참고만 하세요." },
    ],
  }],
  funds: [{
    steps: [
      { targets: ['[data-tour="funds-lag"]', ".funds-lag"], title: "기관 포트폴리오", what: "유명 기관이 분기마다 내는 13F 보유 공시입니다.", how: "분기 끝 뒤 45일 안에 내서 최대 4~5개월 늦은 값입니다 — 지금 그대로 들고 있다고 보지 마세요." },
      { targets: ['[data-tour="fund-cards"]', ".fund-cards"], title: "기관 카드", what: "기관별 최근 공시 · 신규 매수 상위입니다.", how: "눌러서 보유 표와 전 분기 대비 변화(신규 · 증가 · 감소 · 전량 매도)를 봅니다." },
    ],
  }],
};


function visible(el: Element): el is HTMLElement {
  const r = (el as HTMLElement).getBoundingClientRect?.();
  return !!r && r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
}
function find(targets: string[]): HTMLElement | null {
  for (const t of targets) for (const el of Array.from(document.querySelectorAll(t))) if (visible(el)) return el;
  return null;
}
const wait = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/** 분석 화면 탭 열기(이미 그 탭이면 그대로). 탭이 없으면(분석 화면이 아님) false */
async function openRunTab(tab: "summary" | "chart"): Promise<boolean> {
  const btn = document.getElementById(`run-tab-${tab}`) as HTMLButtonElement | null;
  if (!btn) return false;
  if (btn.getAttribute("aria-selected") !== "true") btn.click();
  for (let i = 0; i < 25; i++) { // 차트는 지연 로드 — 최대 5초 기다린다
    await wait(200);
    if (tab === "summary" || document.querySelector(".price-chart canvas")) break;
  }
  return true;
}

let running = false;


/** 투어 시작. "busy" = 이미 도는 중(아무것도 하지 않는다), "empty" = 이 화면에 대상이 하나도 없음 */
export async function startTour(id: TourId): Promise<TourResult> {
  if (running) return "busy";
  running = true;
  let shownAny = false;
  try {
    // 불러오기 실패(배포 뒤 예전 탭의 청크 404 등)도 finally 로 running 을 푼다
    const [{ driver }] = await Promise.all([import("driver.js"), import("driver.js/dist/driver.css")]);
    for (const seg of TOURS[id]) {
      if (seg.before && !(await seg.before())) continue;
      // 데이터가 늦게 오는 대상(기관 카드 등)을 위해 마지막 단계 대상이 보일 때까지 잠깐(최대 3초) 기다린다
      const last = seg.steps[seg.steps.length - 1];
      for (let i = 0; i < 10 && last && !find(last.targets); i++) await wait(300);
      const steps = seg.steps.map((s) => ({ s, el: find(s.targets) })).filter((x) => x.el);
      if (!steps.length) continue;
      shownAny = true;
      const done = await new Promise<boolean>((resolve) => {
        let completed = false;
        const d = driver({
          steps: steps.map(({ s, el }) => ({
            element: el!,
            popover: { title: s.title, description: `<p>${s.what}</p><p class="tour-how">${s.how}</p>` },
          })),
          showProgress: steps.length > 1,
          progressText: "{{current}} / {{total}}",
          nextBtnText: "다음",
          prevBtnText: "이전",
          doneBtnText: "끝",
          allowClose: true,
          allowKeyboardControl: true, // ← → 이동, Esc 닫기
          smoothScroll: true,
          stagePadding: 6,
          popoverClass: "desk-tour",
          onNextClick: () => {
            if (d.hasNextStep()) d.moveNext();
            else { completed = true; d.destroy(); }
          },
          onDestroyed: () => resolve(completed),
        });
        d.drive();
      });
      if (!done) break; // 건너뛰기 · Esc 로 닫으면 다음 묶음도 하지 않는다
    }
  } finally {
    running = false;
  }
  if (shownAny) markSeen(id);
  return shownAny ? "shown" : "empty";
}

/** 다른 화면에서 고른 투어: 그 화면으로 옮긴 뒤 대상이 나타나면 시작 */
export async function goAndStart(id: TourId): Promise<TourResult> {
  const route: Partial<Record<TourId, string>> = { dashboard: "#/", tips: "#/tips", funds: "#/funds" };
  const want = route[id];
  // 실제로 그 화면에 있는지로 판단한다(tourForRoute 는 모르는 화면을 대시보드로 보므로 쓰지 않는다)
  if (want && !onRoute(id, window.location.hash)) window.location.hash = want;
  for (let i = 0; i < 30; i++) {
    await wait(300);
    const first = TOURS[id][0]?.steps[0];
    if (!first || find(first.targets) || id === "analysis") break;
  }
  return startTour(id);
}
