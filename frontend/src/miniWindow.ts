/**
 * #582 회사에서 조용히 띄워 두는 작은 창. Chrome · Edge 는 Document Picture-in-Picture(다른 앱 위에 늘 떠 있는 창),
 * 그 밖의 브라우저는 작은 팝업 창(#/mini). PiP 창에는 iframe 을 쓰지 않는다 — 웹 서버가 X-Frame-Options DENY 라
 * 같은 출처라도 막힌다. 대신 그 창 문서에 스타일을 옮기고 React 루트를 따로 만든다(창을 연 탭이 살아 있는 동안).
 */

export type MiniPrefs = { quiet: boolean; size: "s" | "m" | "l" };
const KEY = "desk.mini.v1";
const STRIP = { width: 640, height: 76 };

export function loadMiniPrefs(): MiniPrefs {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return { quiet: raw.quiet === true, size: raw.size === "s" || raw.size === "l" ? raw.size : "m" };
  } catch {
    return { quiet: false, size: "m" };
  }
}

export function saveMiniPrefs(p: MiniPrefs) {
  try {
    localStorage.setItem(KEY, JSON.stringify(p));
  } catch {
    /* 기억만 못 한다 — 이번 창에서는 그대로 */
  }
}

type PipApi = { requestWindow(opts: { width: number; height: number }): Promise<Window>; window: Window | null };
const pipApi = (): PipApi | undefined => (window as unknown as { documentPictureInPicture?: PipApi }).documentPictureInPicture;

/** 이 브라우저가 늘 위에 뜨는 작은 창(Document PiP)을 쓸 수 있는가 */
export const canPip = () => typeof pipApi()?.requestWindow === "function";

const miniUrl = () => `${location.origin}${location.pathname}#/mini`;

/** 버튼을 누른 그 동작 안에서 부른다(창 열기는 사용자 동작이 있어야 한다). "pip" · "popup" · "blocked" */
export async function openMini(): Promise<"pip" | "popup" | "blocked"> {
  const api = pipApi();
  if (api && typeof api.requestWindow === "function") {
    if (api.window) { api.window.focus(); return "pip"; }   // 이미 떠 있으면 그 창
    try {
      const win = await api.requestWindow(STRIP);
      copyStyles(win.document);
      win.document.title = "Trading Desk · 작은 창";
      const host = win.document.createElement("div");
      host.id = "mini-root";
      win.document.body.append(host);
      // 화면 코드는 창을 연 뒤에 받는다(창 열기에 사용자 동작이 필요해 먼저 연다)
      const [{ createRoot }, { default: MiniWindow }, { createElement }] = await Promise.all([
        import("react-dom/client"), import("./pages/MiniWindow"), import("react"),
      ]);
      const root = createRoot(host);
      root.render(createElement(MiniWindow, { pip: true }));
      win.addEventListener("pagehide", () => root.unmount(), { once: true });
      return "pip";
    } catch {
      /* PiP 를 열지 못했다 — 팝업으로 */
    }
  }
  const w = window.open(miniUrl(), "desk-mini", `popup,width=${STRIP.width},height=${STRIP.height + 44}`);
  return w ? "popup" : "blocked";
}

/** 지금 문서의 스타일(개발: <style>, 운영: <link rel=stylesheet>)과 글꼴을 작은 창 문서로 옮긴다 */
function copyStyles(doc: Document) {
  for (const node of document.head.querySelectorAll('style, link[rel="stylesheet"]')) {
    doc.head.append(node.cloneNode(true));
  }
  const meta = doc.createElement("meta");
  meta.name = "color-scheme";
  meta.content = "dark light";
  doc.head.append(meta);
  doc.body.style.margin = "0";
  doc.body.style.background = getComputedStyle(document.body).backgroundColor;
}
