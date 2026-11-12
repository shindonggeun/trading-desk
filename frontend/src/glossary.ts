/**
 * 쉬운 용어 사전(#24) — 에이전트 대화에 자주 나오는 말을 한 줄 뜻과 "숫자를 어떻게 읽나"로 푼다.
 * `match` 는 대화 본문에서 이 용어를 찾는 말들(영어 약어는 앞뒤가 영문자가 아닐 때만).
 * 투자 조언이 아니라 읽기 도우미다.
 */

export type GlossaryCategory = "지표" | "판단" | "상품" | "거시" | "재무";

export interface GlossaryEntry {
  id: string;
  term: string;
  category: GlossaryCategory;
  /** 한 줄 뜻 */
  desc: string;
  /** 숫자 · 신호를 어떻게 읽나 */
  read?: string;
  match: string[];
}

export const GLOSSARY: GlossaryEntry[] = [
  // 지표
  { id: "rsi", term: "RSI(상대강도지수)", category: "지표", match: ["RSI", "상대강도지수"],
    desc: "최근 오른 폭과 내린 폭을 비교해 0~100 으로 나타낸 값입니다.",
    read: "70 이상이면 과열(많이 올랐다), 30 이하면 과매도(많이 내렸다)로 봅니다. 강한 추세에서는 오래 머물 수 있습니다." },
  { id: "macd", term: "MACD", category: "지표", match: ["MACD"],
    desc: "짧은 이동평균과 긴 이동평균의 차이로 추세의 힘과 방향을 봅니다.",
    read: "MACD 선이 시그널 선을 위로 뚫으면 상승 신호, 아래로 뚫으면 하락 신호로 봅니다. 0 위면 상승 추세 쪽입니다." },
  { id: "ma", term: "이동평균(SMA · EMA)", category: "지표", match: ["SMA", "EMA", "이동평균", "moving average", "50일선", "200일선", "20일선"],
    desc: "최근 N일 종가의 평균을 이은 선입니다. SMA 는 단순 평균, EMA 는 최근 값에 더 무게를 둡니다.",
    read: "가격이 선 위면 그 기간 기준 상승 흐름, 아래면 하락 흐름입니다. 50일 · 200일선은 지지 · 저항으로도 쓰입니다." },
  { id: "cross", term: "골든크로스 · 데드크로스", category: "지표", match: ["골든크로스", "골든 크로스", "데드크로스", "데드 크로스", "golden cross", "death cross"],
    desc: "짧은 이동평균이 긴 이동평균을 위로 뚫으면 골든크로스, 아래로 뚫으면 데드크로스입니다.",
    read: "골든크로스는 상승 전환, 데드크로스는 하락 전환 신호로 읽지만 뒤늦게 나오는 신호입니다." },
  { id: "bollinger", term: "볼린저 밴드", category: "지표", match: ["볼린저", "Bollinger"],
    desc: "20일 이동평균 위아래로 표준편차 2배만큼 띠를 그린 것입니다.",
    read: "윗선에 닿으면 단기 과열, 아랫선에 닿으면 단기 과매도로 봅니다. 띠가 좁아지면 큰 움직임이 다가온다는 신호로 봅니다." },
  { id: "atr", term: "ATR(평균 진폭)", category: "지표", match: ["ATR"],
    desc: "하루에 보통 얼마나 오르내리는지(변동 폭)의 평균입니다. 보통 14일로 계산합니다.",
    read: "손절 · 목표 폭을 정하는 자로 씁니다. 예: ATR 이 5달러면 손절을 진입가에서 1.5~2 ATR(7.5~10달러) 아래에 둡니다." },
  { id: "vwap", term: "VWAP(거래량 가중 평균가)", category: "지표", match: ["VWAP"],
    desc: "그날 거래된 가격을 거래량으로 가중 평균한 값입니다. 기관이 많이 보는 기준가입니다.",
    read: "가격이 VWAP 위면 그날 평균 매수가보다 위라는 뜻으로, 매수 우위로 봅니다." },
  { id: "volume", term: "거래량", category: "지표", match: ["거래량", "volume"],
    desc: "일정 기간 동안 사고판 주식 수입니다.",
    read: "거래량이 늘며 오르면 상승에 힘이 있다는 뜻, 거래량 없이 오르면 힘이 약하다고 봅니다." },
  { id: "sr", term: "지지 · 저항", category: "지표", match: ["지지선", "저항선", "지지", "저항", "support", "resistance"],
    desc: "가격이 자주 멈추거나 되돌아간 가격대입니다. 아래는 지지(사려는 사람이 많음), 위는 저항(팔려는 사람이 많음).",
    read: "지지를 뚫고 내려가면 그 가격이 이번엔 저항이 되기 쉽습니다." },
  { id: "short-interest", term: "공매도 잔고", category: "지표", match: ["공매도 잔고", "공매도", "short interest"],
    desc: "빌려서 판 뒤 아직 되사지 않은 주식 수입니다. 유통 주식 대비 % 로 봅니다(미국은 FINRA 격주 보고).",
    read: "비율이 높으면 하락에 건 돈이 많다는 뜻이고, 가격이 오르면 되사기(숏 커버)로 급등할 수 있습니다." },
  { id: "week52", term: "52주 최고 · 최저", category: "지표", match: ["52주 최고", "52주 최저", "52주"],
    desc: "최근 1년(약 250거래일) 종가 가운데 가장 높은 값과 낮은 값입니다.",
    read: "지금가가 52주 최고에 가까우면 신고가 근처, 최저에 가까우면 1년 바닥 근처입니다." },
  { id: "breakout", term: "돌파 · 이탈", category: "지표", match: ["돌파 매매", "상향 돌파", "하향 이탈", "지지선 이탈", "저항선 돌파", "breakout", "breakdown"],
    desc: "저항 위로 올라서는 것이 돌파, 지지 아래로 내려가는 것이 이탈입니다.",
    read: "거래량이 함께 늘어야 믿을 만한 돌파로 봅니다. 곧바로 되돌아오면 가짜 돌파입니다." },
  { id: "volatility", term: "변동성", category: "지표", match: ["변동성", "volatility"],
    desc: "가격이 얼마나 크게 출렁이는지입니다.",
    read: "변동성이 크면 같은 금액이라도 손절 폭을 넓히고 비중을 줄입니다." },
  { id: "momentum", term: "모멘텀", category: "지표", match: ["모멘텀", "momentum"],
    desc: "가격이 한 방향으로 움직이는 힘(속도)입니다.",
    read: "모멘텀이 강하면 추세가 이어지기 쉽고, 꺾이면 추세 전환을 의심합니다." },

  // 판단
  { id: "overweight", term: "비중 확대 · 축소", category: "판단", match: ["비중 확대", "비중 축소", "Overweight", "Underweight"],
    desc: "포트폴리오에서 이 종목 몫을 늘리거나(확대) 줄이라는(축소) 뜻입니다.",
    read: "매수 · 매도보다 한 단계 약한 판단입니다. 한 번에 말고 나눠서 움직입니다." },
  { id: "rr", term: "손익비", category: "판단", match: ["손익비", "risk/reward", "risk-reward", "reward/risk", "R/R"],
    desc: "목표까지 벌 수 있는 폭 ÷ 손절까지 잃을 수 있는 폭입니다.",
    read: "손익비가 2 면 승률이 1/3 보다 높기만 해도 길게 보면 남습니다. 1 미만이면 들어갈 이유가 약합니다." },
  { id: "stop", term: "손절가", category: "판단", match: ["손절가", "손절", "stop loss", "stop-loss"],
    desc: "틀렸다고 인정하고 파는 가격입니다. 손실을 정해 둔 만큼으로 막습니다.",
    read: "진입 전에 정하고, 가격이 닿으면 기다리지 않고 지킵니다." },
  { id: "target", term: "목표가", category: "판단", match: ["목표가", "price target"],
    desc: "이익을 실현하려는 가격입니다. 뉴스의 \"목표주가\"는 애널리스트가 본 적정 가격이라 뜻이 조금 다릅니다.",
    read: "보통 다음 저항(롱) 또는 지지(숏) 바로 앞에 둡니다. 나눠서 팔기도 합니다." },
  { id: "entry", term: "진입가", category: "판단", match: ["진입가", "entry price"],
    desc: "사거나(롱) 파는(숏) 가격입니다.",
    read: "현재가 근처면 바로 진입, 멀면 그 가격에 닿을 때 들어가는 조건부 주문입니다." },
  { id: "long-short", term: "롱 · 숏", category: "판단", match: ["롱 포지션", "숏 포지션", "롱", "숏"],
    desc: "롱은 오를 쪽에, 숏은 내릴 쪽에 거는 것입니다.",
    read: "숏은 내려야 버는 구조라 손실 한도가 없을 수 있어 손절이 더 중요합니다." },
  { id: "position-sizing", term: "포지션 크기(비중)", category: "판단", match: ["포지션 크기", "position sizing", "포지션 규모"],
    desc: "이 거래에 얼마를 넣을지입니다.",
    read: "손절까지 잃을 금액이 전체 자산의 1~2% 를 넘지 않게 정하는 방식이 흔합니다." },

  // 상품
  { id: "leverage", term: "레버리지", category: "상품", match: ["레버리지", "leveraged"],
    desc: "빌린 돈이나 파생상품으로 움직임을 2배 · 3배로 키우는 것입니다.",
    read: "오를 때 크게 벌지만 내릴 때도 크게 잃습니다. 오래 들고 있으면 횡보만 해도 손실이 쌓일 수 있습니다." },
  { id: "inverse", term: "인버스 ETF", category: "상품", match: ["인버스", "inverse ETF"],
    desc: "기초 지수나 종목이 내리면 오르게 만든 ETF 입니다.",
    read: "하락에 거는 쉬운 방법이지만 하루 단위로 맞춰져 있어 오래 들면 어긋납니다." },
  { id: "short-selling", term: "공매도", category: "상품", match: ["공매도", "short selling", "short interest"],
    desc: "주식을 빌려 먼저 팔고 나중에 사서 갚는 것입니다. 내리면 법니다.",
    read: "공매도 잔고가 많으면 하락 베팅이 많다는 뜻이고, 급등 시 되사기(숏 스퀴즈)로 더 오르기도 합니다." },
  { id: "etf", term: "ETF", category: "상품", match: ["ETF"],
    desc: "지수나 바구니를 따라가도록 만든 펀드를 주식처럼 사고파는 상품입니다." },
  { id: "option", term: "옵션", category: "상품", match: ["콜옵션", "풋옵션", "옵션 거래", "옵션 시장", "put/call", "풋콜"],
    desc: "정해진 가격에 사거나(콜) 팔(풋) 권리입니다.",
    read: "풋 거래가 콜보다 많으면 하락을 대비하는 사람이 많다는 신호로 봅니다." },

  // 거시
  { id: "cpi", term: "CPI(소비자물가지수)", category: "거시", match: ["CPI", "소비자물가"],
    desc: "소비자가 사는 물건 · 서비스 가격의 변화입니다. 인플레이션 지표입니다.",
    read: "예상보다 높으면 금리 인상(또는 인하 지연) 걱정으로 주식, 특히 성장주에 부담입니다." },
  { id: "rate", term: "금리 · 기준금리", category: "거시", match: ["기준금리", "금리", "Fed", "FOMC", "연준"],
    desc: "돈을 빌리는 값입니다. 기준금리는 중앙은행(미국은 연준)이 정하고, 국채 · 회사채 금리는 시장에서 정해집니다.",
    read: "금리가 오르면 돈 빌리는 비용이 커지고 미래 이익의 현재 가치가 줄어 주식(특히 성장주)에 부담, 내리면 우호적으로 봅니다." },
  { id: "yield-curve", term: "장단기 금리차", category: "거시", match: ["장단기 금리차", "yield curve", "수익률 곡선"],
    desc: "10년물과 2년물 같은 긴 국채와 짧은 국채 금리의 차이입니다.",
    read: "짧은 금리가 더 높아지는 역전은 경기 침체의 앞선 신호로 봅니다." },
  { id: "gdp", term: "GDP", category: "거시", match: ["GDP", "국내총생산"],
    desc: "한 나라가 일정 기간 만든 모든 것의 가치, 경제 규모와 성장률입니다." },

  // 재무
  { id: "per", term: "PER(주가수익비율)", category: "재무", match: ["PER", "P/E", "주가수익비율"],
    desc: "주가 ÷ 주당순이익. 이익의 몇 배 가격에 거래되는지입니다.",
    read: "같은 업종보다 높으면 비싸게(또는 성장을 기대받으며) 거래된다는 뜻입니다." },
  { id: "eps", term: "EPS(주당순이익)", category: "재무", match: ["EPS", "주당순이익"],
    desc: "순이익을 주식 수로 나눈 값입니다.",
    read: "실적 발표에서 예상치(컨센서스)보다 높으면 서프라이즈, 낮으면 쇼크입니다." },
  { id: "guidance", term: "가이던스", category: "재무", match: ["가이던스", "guidance"],
    desc: "회사가 스스로 내놓는 다음 분기 · 연도 실적 전망입니다.",
    read: "실적이 좋아도 가이던스를 낮추면 주가가 내리기 쉽습니다." },
  { id: "margin", term: "마진(이익률)", category: "재무", match: ["마진", "이익률", "margin"],
    desc: "매출에서 이익이 차지하는 비율입니다(매출총이익률 · 영업이익률 등).",
    read: "마진이 늘면 같은 매출로 더 많이 번다는 뜻입니다." },
  { id: "implied-move", term: "내재 변동폭(옵션)", category: "재무", match: ["내재 변동폭", "implied move"],
    desc: "실적 발표 직후 만기 옵션의 등가격 콜 + 풋 가격을 주가로 나눈 값입니다. 시장이 예상하는 발표 뒤 움직임 크기입니다.",
    read: "±8% 면 발표 뒤 위든 아래든 8% 안팎 움직임을 옵션 가격이 이미 반영했다는 뜻입니다. 만기가 발표에서 멀면 실제보다 크게 나옵니다." },
  { id: "eps-surprise", term: "어닝 서프라이즈", category: "재무", match: ["서프라이즈", "어닝 쇼크"],
    desc: "실제 EPS 가 애널리스트 예상(컨센서스)보다 얼마나 높았나(낮았나)를 % 로 나타낸 것입니다.",
    read: "+면 예상보다 좋았고(서프라이즈), −면 나빴습니다(쇼크). 좋아도 이미 기대가 높았으면 주가가 내릴 수 있습니다." },
  { id: "consensus", term: "컨센서스", category: "재무", match: ["컨센서스", "consensus"],
    desc: "여러 애널리스트 예상치의 평균입니다. 실적 · 목표주가 · 투자의견에 씁니다.",
    read: "참여한 애널리스트 수가 적으면 평균이 잘 흔들립니다. 예상 범위(최저~최고)가 넓으면 불확실성이 큽니다." },
];


const BY_ID = new Map(GLOSSARY.map((e) => [e.id, e]));
export const glossaryEntry = (id: string) => BY_ID.get(id);

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\/]/g, "\$&");
const isAscii = (s: string) => /^[\x20-\x7e]+$/.test(s);

// 짧은 한국어 별칭이 다른 말 안에서 잡히지 않게 — 뒤에 오면 안 되는 말.
const NOT_AFTER: Record<string, string> = {
  지지: "부진|율|자|층|하|해|한|했",
  저항: "력|감",
  숏: "폼|츠",
  롱: "런|패딩",
  금리: "차",
};

// 긴 말부터 — "비중 확대" 가 "확대" 보다, "손절가" 가 "손절" 보다 먼저 잡히게.
const ALIASES: { text: string; id: string; ascii: boolean }[] = GLOSSARY.flatMap((e) =>
  e.match.map((text) => ({ text, id: e.id, ascii: isAscii(text) })),
).sort((a, b) => b.text.length - a.text.length);

const LOOKUP = new Map(ALIASES.map((a) => [a.text.toLowerCase(), a.id]));

function alias(a: { text: string; ascii: boolean }): string {
  const body = escape(a.text);
  const digitGuard = /^\d/.test(a.text) ? "(?<![0-9])" : ""; // "50일선" 이 "150일선" 안에서 잡히지 않게
  if (!a.ascii) return `${digitGuard}${body}${NOT_AFTER[a.text] ? `(?!${NOT_AFTER[a.text]})` : ""}`;
  // 4자 이하 약어(RSI · PER · Fed)는 대소문자를 지킨다 — "fed up" · "period" 가 잡히지 않게.
  const strict = a.text.length <= 4;
  // 대소문자 무시는 글자마다 — 인라인 (?i:) 는 오래된 브라우저 · 타입 검사에서 막힌다.
  const text = strict ? body : body.replace(/[A-Za-z]/g, (c) => `[${c.toLowerCase()}${c.toUpperCase()}]`);
  // 앞뒤가 영숫자가 아닐 때만, 복수형 s 는 허용(ETFs).
  return `(?<![A-Za-z0-9])${text}s?(?![A-Za-z0-9])`;
}

// lookbehind 를 못 읽는 오래된 브라우저(iOS 16.3 이하)에서는 밑줄만 끈다 — 앱 전체가 멈추지 않게.
const PATTERN: RegExp | null = (() => {
  try {
    return new RegExp(ALIASES.map(alias).join("|"), "g");
  } catch {
    return null;
  }
})();

export type Piece = string | { text: string; id: string };

function idFor(found: string): string | undefined {
  const key = found.toLowerCase();
  return LOOKUP.get(key) ?? (key.endsWith("s") ? LOOKUP.get(key.slice(0, -1)) : undefined);
}

/** 글을 용어와 나머지로 나눈다. `seen` 에 있는 용어는 다시 표시하지 않는다(호출한 블록 안에서 처음 한 번만 밑줄). */
export function splitTerms(text: string, seen?: Set<string>): Piece[] {
  if (!PATTERN) return [text];
  const out: Piece[] = [];
  let last = 0;
  for (const m of text.matchAll(PATTERN)) {
    const id = idFor(m[0]);
    if (!id || seen?.has(id)) continue;
    seen?.add(id);
    if (m.index! > last) out.push(text.slice(last, m.index));
    out.push({ text: m[0], id });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** 글에 나온 용어 id(처음 나온 순서). */
export function termsIn(text: string): string[] {
  if (!PATTERN) return [];
  const found = new Set<string>();
  for (const m of text.matchAll(PATTERN)) {
    const id = idFor(m[0]);
    if (id) found.add(id);
  }
  return [...found];
}
