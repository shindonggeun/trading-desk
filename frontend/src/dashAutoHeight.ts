import { useCallback, useEffect, useRef, useState } from "react";
import { rowsFor } from "./dashLayout";

/**
 * #519 격자 구역의 내용 높이를 재서 격자 줄 수로 — 구역 몸통 안 감싸개(.dash-body-inner)를 ResizeObserver 로 본다.
 * 감싸개는 상자 높이와 상관없이 제 내용 높이를 가지므로(구역은 overflow: hidden 이라 스크롤바로 폭이 바뀌지도 않는다)
 * 상자 높이를 바꿔도 다시 재는 값이 바뀌지 않는다 — 관측 ↔ 배치 사이에 흔들림이 없다.
 * 머리글(.dash-head)도 같이 본다 — 편집 모드의 손잡이 · 숨기기로 머리글이 두 줄이 되는 등 머리글 높이만 바뀌어도 다시 잰다
 * (구역 상자는 격자가 정한 높이라 머리글이 커져도 크기가 그대로여서 구역을 보면 놓친다).
 * 데이터가 늦게 와 길이가 바뀌면 짧게 모아(80ms) 다시 잰다.
 */
export function useAutoHeights() {
  const els = useRef(new Map<string, HTMLElement>());
  /** 구역마다 관측 중인 요소(감싸개 · 머리글) — 떼어 낼 때는 붙일 때 본 그대로 푼다 */
  const seen = useRef(new Map<string, HTMLElement[]>());
  const refs = useRef(new Map<string, (el: HTMLElement | null) => void>());
  const ro = useRef<ResizeObserver | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const [rows, setRows] = useState<Record<string, number>>({});

  const measure = useCallback(() => {
    const next: Record<string, number> = {};
    els.current.forEach((inner, id) => {
      const body = inner.parentElement;
      const section = body?.parentElement;
      if (!body || !section || !inner.isConnected) return;
      // #537 아직 그리지 않은 구역(자리표시)은 재지 않는다 — 저장한 높이를 자리표시 높이로 덮지 않게
      if (inner.querySelector(":scope > .when-visible-wait")) return;
      const cs = getComputedStyle(body);
      const pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
      // 구역 = 머리글 등(구역 높이 − 몸통 높이) + 몸통의 위아래 여백 + 내용
      const px = section.getBoundingClientRect().height - body.getBoundingClientRect().height + pad + inner.getBoundingClientRect().height;
      if (px > 0) next[id] = rowsFor(px);
    });
    setRows((cur) => (Object.keys(next).some((k) => cur[k] !== next[k]) ? { ...cur, ...next } : cur));
  }, []);
  const schedule = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(measure, 80);
  }, [measure]);

  useEffect(() => {
    const o = new ResizeObserver(schedule);
    ro.current = o;
    seen.current.forEach((list) => list.forEach((w) => o.observe(w)));
    return () => { o.disconnect(); ro.current = null; window.clearTimeout(timer.current); };
  }, [schedule]);

  /** 구역마다 같은 ref 함수(매 렌더 새로 붙였다 떼지 않게) */
  const register = useCallback((id: string) => {
    let fn = refs.current.get(id);
    if (!fn) {
      fn = (el: HTMLElement | null) => {
        const old = els.current.get(id);
        if (old && old !== el) {
          (seen.current.get(id) ?? []).forEach((w) => ro.current?.unobserve(w));
          els.current.delete(id); seen.current.delete(id);
        }
        if (el) {
          const list = [el, el.parentElement?.parentElement?.querySelector<HTMLElement>(":scope > .dash-head")].filter((w): w is HTMLElement => !!w);
          els.current.set(id, el); seen.current.set(id, list);
          list.forEach((w) => ro.current?.observe(w));
          schedule();
        }
      };
      refs.current.set(id, fn);
    }
    return fn;
  }, [schedule]);

  return { rows, register };
}
