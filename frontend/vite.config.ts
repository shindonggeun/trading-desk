import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// #537 큰 라이브러리는 따로 묶는다: 앱 코드만 바뀐 배포에서도 이 파일들은 이름(해시)이 그대로라
// 브라우저 캐시(/assets/ 30일)를 다시 쓰고, 첫 방문에는 나란히 받는다.
const VENDOR: Record<string, string[]> = {
  react: ["react", "react-dom", "scheduler"],
  charts: ["lightweight-charts", "fancy-canvas"],
  grid: ["react-grid-layout", "react-draggable", "react-resizable"],
  markdown: ["react-markdown", "remark-gfm"],
  tour: ["driver.js"],
  qrcode: ["qrcode"],
};
const OWNER = new Map(Object.entries(VENDOR).flatMap(([chunk, pkgs]) => pkgs.map((p) => [p, chunk] as const)));
// react-markdown · remark-gfm 이 끌어오는 unified · micromark · mdast 계열 작은 패키지들
const MARKDOWN_DEPS = /^(unified|remark-|rehype-|micromark|mdast-|hast-|unist-|vfile|property-information|space-separated-tokens|comma-separated-tokens|decode-named-character-reference|character-entities|ccount|markdown-table|zwitch|longest-streak|bail|trough|devlop|is-plain-obj|trim-lines|html-url-attributes|estree-util-|style-to-object|inline-style-parser|extend)/;

function vendorChunk(id: string): string | undefined {
  const at = id.lastIndexOf("node_modules/");
  if (at < 0) return undefined;
  const parts = id.slice(at + "node_modules/".length).split("/");
  const pkg = parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
  return OWNER.get(pkg) ?? (MARKDOWN_DEPS.test(pkg) ? "markdown" : undefined);
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: (id) => vendorChunk(id.replace(/\\/g, "/")),
      },
    },
  },
  server: {
    port: 5173,
    // Keep the browser's Host: the API refuses writes whose Origin differs from Host.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
