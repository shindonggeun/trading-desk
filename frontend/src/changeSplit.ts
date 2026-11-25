import { price } from "./format";
import type { BoardItem } from "./types";

/**
 * #1002 국장 정규장이 끝난 뒤 NXT · KRX 시간외 · 저녁 추정 타일: 큰 숫자는 합친 하루(전일 종가 대비), 아래 작은 줄은
 * "정규 -10.00% · NXT -0.20%". 기준 종가 · 날짜는 툴팁으로(#316). 장 전 NXT(정규장 전) · 필드 없음(예전 응답 ·
 * 미국 줄)이면 null — 지금 표시(change_pct 하나) 그대로.
 */
export type SplitView = { pct: number; line: string; title: string };

const OFF: Record<string, string> = { nxt: "NXT", "krx-overtime": "시간외", "binance-estimate": "추정" };
const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}%`;   // 타일의 큰 숫자와 같은 표기
const day = (iso: string) => iso.slice(5);

export function splitView(it: Pick<BoardItem, "change_split"> | undefined): SplitView | null {
  const s = it?.change_split;
  if (!s || !s.regular || !s.extended || !s.total) return null;
  const off = OFF[s.extended.source] ?? "장외";
  return {
    pct: s.total.pct,
    // 좁은 카드에서 줄이 바뀌면 " · " 에서만(부분 안은 붙여 둔다)
    line: `정규\u00a0${signed(s.regular.pct)} · ${off}\u00a0${signed(s.extended.pct)}`,
    title: [
      `하루 ${signed(s.total.pct)}: 전일 종가 ${price(s.total.base)}(${day(s.total.base_date)}) 대비`,
      `정규장 ${signed(s.regular.pct)}: 전일 종가 ${price(s.regular.base)}(${day(s.regular.base_date)}) → 오늘 KRX 종가`,
      `${off} ${signed(s.extended.pct)}: 오늘 KRX 종가 ${price(s.extended.base)}(${day(s.extended.base_date)}) 대비`,
    ].join("\n"),
  };
}

/** 타일의 큰 등락 숫자 — 나눈 하루가 있으면 합친 등락, 없으면 change_pct */
export function tileChange(it: Pick<BoardItem, "change_pct" | "change_split"> | undefined): number | null | undefined {
  return splitView(it)?.pct ?? it?.change_pct;
}
