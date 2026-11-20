import { price } from "./format";
import type { ChartReadFrame, ChartReadLevel, ChartReadRef, LinesReport } from "./types";

/**
 * #632 · #672 '지금 이 차트 읽기' — GET /api/chart-read(#631)의 한 시간봉을 초보자용 설명으로.
 * 가까운 위 선(막힐 수 있는 곳) · 아래 선(받쳐 줄 수 있는 곳)에 ①②③… 번호를 붙이고, 차트의 같은 자리에 같은 번호를 그린다.
 * 규칙으로 읽은 지금 모습이지 예측 · 추천이 아니다.
 */

export const MARKS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"];
export const READ_NOTE = "차트 선과 숫자로 읽은 지금 모습이에요 — 예측도, 매수 · 매도 추천도 아니에요.";

export type ReadMark = { n: number; side: "above" | "below"; level: ChartReadLevel };

/** 위 선 먼저(가까운 순), 이어서 아래 선 — 번호는 0부터(MARKS[n]) */
export function marksOf(f: ChartReadFrame | null): ReadMark[] {
  if (!f?.levels) return [];
  const above = f.levels.above.slice(0, 3).map((level) => ({ side: "above" as const, level }));
  const below = f.levels.below.slice(0, 3).map((level) => ({ side: "below" as const, level }));
  return [...above, ...below].map((m, n) => ({ ...m, n }));
}

const pct = (d: number) => (Math.abs(d) < 0.05 ? "0%" : `${d > 0 ? "+" : ""}${Math.abs(d) >= 10 ? d.toFixed(0) : d.toFixed(1)}%`);
export const distText = (l: ChartReadLevel) => pct(l.distance_pct);

// 선이 지금가 위(above)인지 아래(below)인지로 말이 바뀐다 — 채널을 벗어난 뒤엔 위 끝이 받침, 아래 끝이 천장이 된다
const EDGE_WHY: Record<string, (side: "above" | "below") => string> = {
  upper: (side) => (side === "above" ? "채널 위 끝 — 오르면 막히기 쉬운 자리예요"
    : "채널 위 끝을 넘어 올라섰어요 — 다시 내려오면 받쳐 주기 쉬워요"),
  lower: (side) => (side === "below" ? "채널 아래 끝 — 내리면 받쳐 주기 쉬운 자리예요"
    : "채널 아래 끝을 깨고 내려섰어요 — 다시 올라와도 막히기 쉬워요"),
  mid: (side) => `채널 가운데 — ${side === "above" ? "넘으면 위 끝을 보러 가기 쉬워요" : "깨면 아래 끝을 보러 가기 쉬워요"}`,
  outer_upper: (side) => (side === "above" ? "채널 폭 한 칸 위 — 채널을 위로 벗어난 뒤의 다음 끝이에요"
    : "채널 폭 한 칸 위 선 — 넘어선 뒤라 다시 내려오면 받쳐 줄 수 있어요"),
  outer_lower: (side) => (side === "below" ? "채널 폭 한 칸 아래 — 채널을 아래로 벗어난 뒤의 다음 끝이에요"
    : "채널 폭 한 칸 아래 선 — 깨진 뒤라 다시 올라와도 막히기 쉬워요"),
};

/** 그 선이 왜 중요한지 한 마디 — 가장 가까운 선(refs[0]) 기준, 여러 선이 겹치면 그렇다고 */
export function whyOf(level: ChartReadLevel, side: "above" | "below"): string {
  const r: ChartReadRef | undefined = level.refs?.[0];
  let text: string;
  if (!r) text = side === "above" ? "오르면 막힐 수 있는 자리예요" : "내리면 받쳐 줄 수 있는 자리예요";
  else if (r.kind === "channel") text = (EDGE_WHY[r.edge ?? ""] ?? EDGE_WHY.mid)(side);
  else if (r.kind === "trendline" && r.broken)
    text = side === "above" ? "방금 깨진 선 — 다시 올라와도 여기서 막히기 쉬워요" : "방금 깨진 선 — 다시 내려와도 여기서 받쳐 주기 쉬워요";
  else if (r.kind === "trendline")
    text = r.side === "support" ? "이어진 저점을 이은 선 — 깨지지 않는 동안 받쳐 줘요" : "이어진 고점을 이은 선 — 넘기 전까지 막히기 쉬워요";
  else if (r.kind === "pattern")
    text = `좁혀지는 모양(삼각수렴 · 쐐기)의 ${r.edge === "upper" ? "윗선" : "아랫선"} — ${side === "above"
      ? "위에서 막을 수 있고, 넘으면 위로 움직임이 커지기 쉬워요" : "아래에서 받칠 수 있고, 깨면 아래로 움직임이 커지기 쉬워요"}`;
  else if (r.kind === "swing" && r.side === "high")
    text = side === "above" ? "아직 넘지 못한 최근 고점 — 팔려는 사람이 많았던 자리예요" : "막 넘어선 최근 고점 — 다시 내려오면 받쳐 주기 쉬워요";
  else if (r.kind === "swing")
    text = side === "below" ? "아직 깨지지 않은 최근 저점 — 사려는 사람이 많았던 자리예요" : "막 내려선 최근 저점 — 다시 올라와도 막히기 쉬워요";
  else if (r.kind === "fib") text = `최근 움직임을 ${r.ratio ?? ""} 만큼 되돌린 자리 — 쉬어 가기 쉬운 곳이에요`;
  else text = "비어 있던 가격(갭) — 채우러 가기 쉬운 자리예요";
  const more = (level.refs?.length ?? 0) - 1;
  return more > 0 ? `${text} · 선 ${more + 1}개가 겹쳐 더 단단해요` : text;
}

/** 머리 한 줄 — "지금 6.34 — 하락 흐름, 하락 채널 하단 근처. 상승추세선 이탈(방금)." (위 · 아래 선 문장은 아래 목록이 맡는다) */
export function headline(f: ChartReadFrame): string {
  const s = (f.summary ?? "").replace(/^[^—]*—\s*/, "");
  const parts = s.split(/(?<=\.)\s+/).filter((x) => x && !/^(위|아래) /.test(x));
  return `지금 ${f.price != null ? price(f.price) : "—"} — ${parts.join(" ")}`;
}

/** 조심할 때 / 볼 때 — 바로 아래 선을 마감으로 깨면 다음 아래 선까지, 바로 위 선을 넘으면 짧은 반등 시도 */
export function watchOf(f: ChartReadFrame, tfKo: string): { caution: string | null; look: string | null } {
  const [b0, b1] = f.levels?.below ?? [];
  const [a0, a1] = f.levels?.above ?? [];
  const caution = b0
    ? `${price(b0.price)} 아래로 ${tfKo}이 마감하면 ${b1 ? `${price(b1.price)}(${distText(b1)})까지 열려요` : "다음 받침이 멀어요"} — 조심`
    : null;
  const look = a0
    ? `${price(a0.price)} 위로 ${tfKo}이 마감하면 짧은 반등 시도${a1 ? ` — 다음은 ${price(a1.price)}(${distText(a1)})` : ""}`
    : null;
  return { caution, look };
}

/** 설명에 나온 선의 종류(초보 모드에서 남길 레이어) */
export function refKinds(marks: ReadMark[]): Set<ChartReadRef["kind"]> {
  return new Set(marks.flatMap((m) => (m.level.refs ?? []).map((r) => r.kind)));
}

/** 그 번호의 선 id(채널 · 짧은 추세선) — 차트에서 굵게 */
export function refIds(level: ChartReadLevel): Set<string> {
  return new Set((level.refs ?? []).map((r) => r.id).filter((x): x is string => !!x));
}

/**
 * 초보 모드: 설명에 나온 채널 · 짧은 추세선만. id 가 /api/lines 와 어긋나면(봉 경계에서 두 응답이 다를 때)
 * 채널은 가장 좋은 것 하나, 추세선은 끝값이 그 번호 가격 0.5% 안인 것만.
 */
export function beginnerLines(lines: LinesReport, marks: ReadMark[]): LinesReport {
  const refs = marks.flatMap((m) => (m.level.refs ?? []).map((r) => ({ r, price: m.level.price })));
  const ids = new Set(refs.map((x) => x.r.id).filter(Boolean));
  const wantChannel = refs.some((x) => x.r.kind === "channel");
  let channels = lines.channels.filter((c) => ids.has(c.id));
  if (!channels.length && wantChannel) channels = lines.channels.slice(0, 1);
  const trendPrices = refs.filter((x) => x.r.kind === "trendline").map((x) => x.price);
  const trendlines = lines.trendlines.filter((t) => ids.has(t.id) || trendPrices.some((p) => Math.abs(t.end.price / p - 1) <= 0.005));
  return { ...lines, channels, trendlines };
}

// 칸 켜기 · 초보 모드는 이 브라우저에 기억한다
const KEY = "desk.chartRead.v1";
export type ReadPrefs = { on: boolean; beginner: boolean };
export function loadReadPrefs(): ReadPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<ReadPrefs> | null;
    return { on: raw?.on ?? true, beginner: raw?.beginner ?? true };
  } catch {
    return { on: true, beginner: true };
  }
}
export function saveReadPrefs(p: ReadPrefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 막힌 저장소 — 이번 화면에서만 */
  }
}
