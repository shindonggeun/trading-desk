import { useEffect, useState } from "react";

/**
 * #627 차트 색은 styles.css 의 토큰 하나에서 — 차트 라이브러리(lightweight-charts)는 CSS 변수를 직접 못 받아 그릴 때
 * getComputedStyle 로 읽는다. 국내 관례대로 오름 · 롱 = 빨강(--up), 내림 · 숏 = 파랑(--down)이고 그 값은 styles.css :root 한 곳에서 정한다.
 * 여기 FALLBACK 은 토큰을 못 읽을 때(스타일이 아직 없을 때)만 쓰는 다크 테마 값이다 — 색을 바꿀 때는 styles.css 만 고친다.
 */
const FALLBACK: Record<string, string> = {
  "--up": "#f2464b", "--down": "#3a86f8", "--long": "#f2464b", "--short": "#3a86f8", "--flat": "#8c939d",
  "--text": "#e8eaed", "--muted": "#8c939d", "--border": "#2a3038", "--accent": "#c9d1db",
  "--ok": "#3fb68b", "--warn": "#e5a23a", "--danger": "#ff6a3d",
  "--sr-support": "#2bb3a3", "--sr-resistance": "#b58cf0",
  "--flow-foreigner": "#a371f7", "--flow-institution": "#1fa39a", "--flow-individual": "#cf6f2a",
  "--ma5": "#ec4899", "--ma20": "#eab308", "--ma60": "#22c55e", "--ma120": "#a8a29e",
};

/** 토큰의 지금 값(테마에 따라 다르다). 모르는 토큰은 흐린 회색 */
export function css(name: string): string {
  const v = typeof document === "undefined" ? "" : getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || FALLBACK[name] || FALLBACK["--muted"];
}

/** 차트가 자주 쓰는 색 묶음 */
export function chartColors() {
  return {
    up: css("--up"), down: css("--down"), long: css("--long"), short: css("--short"),
    text: css("--text"), muted: css("--muted"), border: css("--border"), accent: css("--accent"),
    ok: css("--ok"), warn: css("--warn"), danger: css("--danger"),
  };
}

export type ColorScheme = "dark" | "light";
const schemeNow = (): ColorScheme => {
  if (typeof window === "undefined") return "dark";
  const forced = document.documentElement.dataset.theme;
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
};

/** 지금 테마 — OS 설정(prefers-color-scheme)이나 <html data-theme> 가 바뀌면 다시 그린다. 차트는 이 값을 key 로 다시 만든다 */
export function useColorScheme(): ColorScheme {
  const [scheme, setScheme] = useState<ColorScheme>(schemeNow);
  useEffect(() => {
    const update = () => setScheme(schemeNow());
    const mq = window.matchMedia?.("(prefers-color-scheme: light)");
    mq?.addEventListener?.("change", update);
    const mo = new MutationObserver(update);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => { mq?.removeEventListener?.("change", update); mo.disconnect(); };
  }, []);
  return scheme;
}
