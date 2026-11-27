/**
 * README 시스템 아키텍처 구성도 생성기 (#1027).
 *
 *   cd docs/diagrams && npm install && npm run build
 *
 * docs/images/architecture.png 를 2배 해상도로 다시 만든다. 앱과 무관한 문서 도구라 의존성을 이 디렉터리에 둔다.
 * 구성이 바뀌면(역할 · 저장소 · 외부 원천 · 배포) 아래 값을 고치고 다시 만든다 — 정본은 docs/deploy.md · desk/roles.py.
 */
import * as si from 'simple-icons';
import { Resvg } from '@resvg/resvg-js';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const OUT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../images');
fs.mkdirSync(OUT, { recursive: true });

const FONT = 'Malgun Gothic, Segoe UI, sans-serif';
const C = {
  bg: '#FFFFFF',
  panel: '#F1F5F9',
  panelBorder: '#CBD5E1',
  panelTitle: '#475569',
  card: '#FFFFFF',
  cardBorder: '#E2E8F0',
  title: '#0F172A',
  sub: '#64748B',
  arrow: '#94A3B8',
  arrowText: '#475569',
};

// simple-icons 에 없는 것 (24×24 path)
const CUSTOM = {
  spark: (c) =>
    `<path d="M12 2l2.2 6.3L20.5 10.5l-6.3 2.2L12 19l-2.2-6.3L3.5 10.5l6.3-2.2z M19 15l.9 2.1 2.1.9-2.1.9L19 21l-.9-2.1-2.1-.9 2.1-.9z" fill="${c}"/>`,
  bank: (c) =>
    `<path d="M12 2 2 7v2h20V7L12 2zM4 11v7h3v-7H4zm6.5 0v7h3v-7h-3zM17 11v7h3v-7h-3zM2 20v2h20v-2H2z" fill="${c}"/>`,
  candles: (c) =>
    `<path d="M6 3h2v3h1v9H8v6H6v-6H5V6h1V3zm9 2h2v5h1v7h-1v4h-2v-4h-1v-7h1V5z" fill="${c}"/>`,
  news: (c) =>
    `<path d="M4 4h13v14a2 2 0 0 0 2 2H6a2 2 0 0 1-2-2V4zm2 3v3h9V7H6zm0 5v1.6h9V12H6zm0 3.4V17h6v-1.6H6zM18.5 8H21v10a1.5 1.5 0 0 1-3 0V8.5z" fill="${c}"/>`,
  bell: (c) =>
    `<path d="M12 22a2.5 2.5 0 0 0 2.5-2.5h-5A2.5 2.5 0 0 0 12 22zm7-6V11a7 7 0 0 0-5.5-6.8V3a1.5 1.5 0 0 0-3 0v1.2A7 7 0 0 0 5 11v5l-2 2v1h18v-1l-2-2z" fill="${c}"/>`,
  disk: (c) =>
    `<path d="M12 3C7 3 4 4.6 4 6.5v11C4 19.4 7 21 12 21s8-1.6 8-3.5v-11C20 4.6 17 3 12 3zm0 2c4 0 6 1.1 6 1.5S16 8 12 8 6 6.9 6 6.5 8 5 12 5zm-6 4.2C7.5 9.8 9.6 10 12 10s4.5-.2 6-.8V12c0 .4-2 1.5-6 1.5S6 12.4 6 12V9.2zm0 5.5c1.5.6 3.6.8 6 .8s4.5-.2 6-.8v2.8c0 .4-2 1.5-6 1.5s-6-1.1-6-1.5v-2.8z" fill="${c}"/>`,
  flag: (c) =>
    `<path d="M5 2h2v20H5V2zm3 1h11l-2.5 4.5L19 12H8V3z" fill="${c}"/>`,
};

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function icon(name, x, y, size, override) {
  const s = size / 24;
  const open = `<g transform="translate(${x},${y}) scale(${s})">`;
  if (CUSTOM[name]) return `${open}${CUSTOM[name](override || '#475569')}</g>`;
  const ic = si['si' + name];
  if (!ic) return `<circle cx="${x + size / 2}" cy="${y + size / 2}" r="${size / 2}" fill="#CBD5E1"/>`;
  return `${open}<path d="${ic.path}" fill="${override || '#' + ic.hex}"/></g>`;
}

function person(x, y, size, color = '#334155') {
  const s = size / 24;
  const d = 'M12 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm0 2c-4.4 0-8 2.7-8 6v2h16v-2c0-3.3-3.6-6-8-6z';
  return `<g transform="translate(${x},${y}) scale(${s})"><path d="${d}" fill="${color}"/></g>`;
}

function textW(s, size) {
  let w = 0;
  for (const ch of String(s)) w += /[가-힣]/.test(ch) ? size : size * 0.56;
  return w;
}

function panel({ x, y, w, h, title, accent = C.panelBorder, fill = C.panel }) {
  return `
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="16" fill="${fill}" stroke="${accent}" stroke-width="1.5"/>
  ${title ? `<text x="${x + 22}" y="${y + 30}" font-family="${FONT}" font-size="15" font-weight="700" fill="${C.panelTitle}" letter-spacing="0.4">${esc(title)}</text>` : ''}`;
}

function pill(x, y, label, color = '#EFF6FF', textColor = '#1D4ED8') {
  const w = textW(label, 11) + 20;
  return `<rect x="${x}" y="${y}" width="${w}" height="21" rx="10.5" fill="${color}"/>
  <text x="${x + w / 2}" y="${y + 15}" text-anchor="middle" font-family="${FONT}" font-size="11" font-weight="600" fill="${textColor}">${esc(label)}</text>`;
}

function card({ x, y, w, h, ic, icColor, title, sub, badge, iconSize = 28 }) {
  const iy = y + (h - iconSize) / 2;
  const tx = x + 18 + iconSize + 14;
  const ty = sub ? y + h / 2 - 2 : y + h / 2 + 6;
  return `
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${C.card}" stroke="${C.cardBorder}" stroke-width="1.5"/>
  ${ic ? icon(ic, x + 18, iy, iconSize, icColor) : person(x + 18, iy, iconSize)}
  <text x="${tx}" y="${ty}" font-family="${FONT}" font-size="15" font-weight="700" fill="${C.title}">${esc(title)}</text>
  ${sub ? `<text x="${tx}" y="${ty + 19}" font-family="${FONT}" font-size="12" fill="${C.sub}">${esc(sub)}</text>` : ''}
  ${badge ? pill(x + w - 14 - textW(badge, 11) - 20, y + 12, badge) : ''}`;
}

/** 백엔드 역할 카드: 제목 · 배지 · 할 일 목록 */
function roleCard({ x, y, w, h, ic, title, badge, color, lines }) {
  let s = `
  <rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${C.card}" stroke="${color}" stroke-width="1.8"/>
  <rect x="${x}" y="${y}" width="${w}" height="6" rx="3" fill="${color}"/>
  ${icon(ic, x + 18, y + 20, 26)}
  <text x="${x + 56}" y="${y + 40}" font-family="${FONT}" font-size="17" font-weight="700" fill="${C.title}">${esc(title)}</text>
  ${pill(x + w - 14 - textW(badge, 11) - 20, y + 22, badge, '#F1F5F9', '#334155')}`;
  lines.forEach((l, i) => {
    const ly = y + 72 + i * 23;
    s += `<circle cx="${x + 24}" cy="${ly - 4}" r="2.6" fill="${color}"/>
    <text x="${x + 36}" y="${ly}" font-family="${FONT}" font-size="12.5" fill="#334155">${esc(l)}</text>`;
  });
  return s;
}

function arrow(x1, y1, x2, y2, { dashed = false, label, labelSide = 'right' } = {}) {
  const d = dashed ? ' stroke-dasharray="6 5"' : '';
  let lbl = '';
  if (label) {
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const w = textW(label, 12) + 16;
    const lx = labelSide === 'right' ? mx + 10 : labelSide === 'center' ? mx - w / 2 : mx - w - 10;
    const ly = labelSide === 'center' ? my - 26 : my - 11;
    lbl = `<rect x="${lx}" y="${ly}" width="${w}" height="22" rx="6" fill="#FFFFFF" opacity="0.95"/>
    <text x="${lx + w / 2}" y="${ly + 15}" text-anchor="middle" font-family="${FONT}" font-size="12" fill="${C.arrowText}">${esc(label)}</text>`;
  }
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${C.arrow}" stroke-width="2.2"${d} marker-end="url(#ah)"/>${lbl}`;
}

const DEFS = `<defs>
  <marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
    <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.arrow}"/>
  </marker>
</defs>`;

function svgDoc(w, h, body) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  ${DEFS}
  <rect width="${w}" height="${h}" fill="${C.bg}"/>
  ${body}
</svg>`;
}

function heading(x, y, main, sub) {
  return `<text x="${x}" y="${y}" font-family="${FONT}" font-size="24" font-weight="700" fill="${C.title}">${esc(main)}</text>
  <text x="${x}" y="${y + 24}" font-family="${FONT}" font-size="13" fill="${C.sub}">${esc(sub)}</text>`;
}

const W = 1240;
const PX = 30;
const PW = W - PX * 2;
const IX = PX + 24;
const IW = PW - 48;

/* ---------------------------------------------------------------- */
function architecture() {
  let s = '';
  s += heading(
    PX,
    46,
    'Trading Desk · System Architecture',
    '로그인 뒤 한 화면에서 AI 분석 토론 · 시세 · 수급을 본다 — 백엔드는 한 이미지를 api · collector · analyst 세 역할로 나눠 돌린다',
  );

  // 사용자
  const userY = 92;
  s += card({ x: W / 2 - 150, y: userY, w: 300, h: 56, title: '사용자 브라우저 · 모바일', sub: 'PWA · 웹 푸시' });
  s += arrow(W / 2, userY + 56, W / 2, userY + 92, { label: 'HTTPS' });

  // 인그레스
  const edgeY = 190;
  const edgeH = 112;
  s += panel({ x: PX, y: edgeY, w: PW, h: edgeH, title: 'PUBLIC EDGE · 홈랩 storage' });
  s += card({
    x: IX,
    y: edgeY + 42,
    w: 560,
    h: 56,
    ic: 'Nginx',
    title: 'Nginx 인그레스 + Certbot + fail2ban',
    sub: 'TLS 종단 · 로그인 시도 제한 · SSE 긴 read timeout',
  });
  s += pill(IX + 600, edgeY + 50, 'trading.8llow8llowme.com', '#DBEAFE');
  s += pill(IX + 600, edgeY + 78, '사설 IP 로만 web 에 전달', '#F1F5F9', '#334155');
  s += arrow(W / 2, edgeY + edgeH, W / 2, edgeY + edgeH + 34);

  // 프론트
  const feY = 340;
  const feH = 128;
  s += panel({ x: PX, y: feY, w: PW, h: feH, title: 'FRONTEND · web 컨테이너', accent: '#93C5FD', fill: '#F0F7FF' });
  s += card({
    x: IX,
    y: feY + 42,
    w: 560,
    h: 68,
    ic: 'React',
    title: 'React 18 SPA · nginx',
    sub: '해시 라우팅 · 화면별 지연 로드 · /api 는 backend 로 프록시',
  });
  const chips = ['TypeScript · Vite', 'lightweight-charts', 'react-grid-layout', 'react-markdown', 'driver.js 화면 안내', 'Pretendard'];
  chips.forEach((c, i) => {
    s += pill(IX + 600 + (i % 2) * 230, feY + 48 + Math.floor(i / 2) * 26, c, '#FFFFFF', '#334155');
  });
  s += arrow(W / 2, feY + feH, W / 2, feY + feH + 34, { label: '/api · SSE · 세션 쿠키' });

  // 백엔드
  const beY = 502;
  const beH = 330;
  s += panel({
    x: PX,
    y: beY,
    w: PW,
    h: beH,
    title: 'BACKEND · Python 3.12 · FastAPI · 한 이미지를 DESK_ROLE 로 세 프로세스',
    accent: '#86EFAC',
    fill: '#F2FBF5',
  });
  const rW = (IW - 2 * 22) / 3;
  const rY = beY + 46;
  const rH = 176;
  const roles = [
    {
      ic: 'Fastapi',
      title: 'api',
      badge: '화면',
      color: '#22C55E',
      lines: ['HTTP · SSE · 로그인 · 세션 쿠키', 'AI 채팅(데스크 자료를 도구로)', '한투 실시간 시세 WebSocket', '대시보드 미리 읽기 · 시장 스트림'],
    },
    {
      ic: 'candles',
      title: 'collector',
      badge: '수집 · 알림',
      color: '#0EA5E9',
      lines: ['화면이 원하는 시세만 수집(수요 기반)', '선물 · 수급 · 공매도 · 빚투 · 13F', '나스닥 선물 · 추세선 · 섹터 급등락 알림', '뉴스 · 경제 일정 · KRX 일별 이력'],
    },
    {
      ic: 'Langchain',
      title: 'analyst',
      badge: '모델',
      color: '#A855F7',
      lines: ['TradingAgents 분석(LangGraph 멀티 에이전트)', '애널리스트 → 강세/약세 → 트레이더 → 리스크 → PM', '아침 시황 · 정기 리포트 · 오늘 방향', '분석 계획 · 방향 판정 채점'],
    },
  ];
  roles.forEach((r, i) => {
    s += roleCard({ x: IX + i * (rW + 22), y: rY, w: rW, h: rH, ...r });
  });
  // 역할 사이 공유 경로
  const busY = rY + rH + 22;
  const busH = 70;
  s += `<rect x="${IX}" y="${busY}" width="${IW}" height="${busH}" rx="12" fill="#FFFFFF" stroke="#BBF7D0" stroke-width="1.5" stroke-dasharray="6 4"/>`;
  s += `<text x="${IX + 18}" y="${busY + 26}" font-family="${FONT}" font-size="13" font-weight="700" fill="${C.title}">프로세스 사이는 메모리를 나누지 않는다</text>`;
  const bus = [
    '분석 대기열 run_queue (DB)',
    '분석 이벤트 pg_notify → SSE',
    'Redis 공유 캐시 · pub/sub',
    '브로커 토큰은 한 곳에서 발급(lease)',
  ];
  let bx = IX + 18;
  bus.forEach((b) => {
    s += pill(bx, busY + 38, b, '#DCFCE7', '#166534');
    bx += textW(b, 11) + 20 + 12;
  });
  s += arrow(W / 2, beY + beH, W / 2, beY + beH + 34);

  // 데이터 · 외부
  const dY = 868;
  const dH = 300;
  const dW = 380;
  s += panel({ x: PX, y: dY, w: dW, h: dH, title: 'DATA' });
  const data = [
    ['Postgresql', 'PostgreSQL · 홈랩', '분석 · 이벤트 · 계정 · 알림 · Alembic'],
    ['Redis', 'Redis · 홈랩', '공유 캐시 · 시장 스트림 · 수요'],
    ['disk', '볼륨 tradingagents_home', 'ChatGPT 로그인 · 리포트 · 스냅숏'],
  ];
  data.forEach((d, i) => {
    s += card({ x: PX + 20, y: dY + 46 + i * 80, w: dW - 40, h: 66, ic: d[0], icColor: d[0] === 'disk' ? '#64748B' : undefined, title: d[1], sub: d[2] });
  });

  const eX = PX + dW + 20;
  const eW = PW - dW - 20;
  s += panel({ x: eX, y: dY, w: eW, h: dH, title: 'EXTERNAL · 원천과 알림' });
  const ext = [
    ['spark', 'ChatGPT', '구독 플랜 LLM', '#10A37F'],
    ['bank', '토스증권 Open API', '종목 · 시세 · 계좌 읽기', '#3182F6'],
    ['bank', '한국투자증권 KIS', '국장 · 선물 · 실시간', '#E2231A'],
    ['candles', 'Yahoo Finance', '봉 · 지수 · 해외', '#6001D2'],
    ['Binance', 'Binance', '무기한 선물 추정 · 펀딩', null],
    ['flag', 'KRX OPEN API', '거래일 확정 이력', '#0B3B8C'],
    ['Naver', '네이버 증권', 'NXT 애프터마켓', null],
    ['news', 'GDELT · 구글 뉴스', '뉴스 흐름 · 이슈', '#EA4335'],
    ['news', 'SEC · 금투협 · 연준', '13F · 빚투 · 일정', '#475569'],
    ['Telegram', '텔레그램', '소유자 알림', null],
    ['bell', '웹 푸시(VAPID)', '기기 알림', '#F97316'],
    ['candles', 'CNN · Cboe · FINRA', '공포탐욕 · 옵션 · 공매도', '#475569'],
  ];
  const cols = 3;
  const gx = 14;
  const cW = (eW - 40 - gx * (cols - 1)) / cols;
  ext.forEach((e, i) => {
    s += card({
      x: eX + 20 + (i % cols) * (cW + gx),
      y: dY + 46 + Math.floor(i / cols) * 61,
      w: cW,
      h: 52,
      ic: e[0],
      icColor: e[3] || undefined,
      title: e[1],
      sub: e[2],
      iconSize: 24,
    });
  });

  // CI/CD
  const cY = dY + dH + 36;
  const cH = 118;
  s += panel({ x: PX, y: cY, w: PW, h: cH, title: 'CI / CD · develop 이 배포본', accent: '#FDE68A', fill: '#FFFBEB' });
  const steps = [
    ['Github', 'GitHub PR', 'Rebase and merge'],
    ['Githubactions', 'Actions CI', 'ruff · pytest · build'],
    ['Jenkins', 'Jenkins', '바뀐 역할만 배포'],
    ['Vault', 'Vault', '운영 env 주입'],
    ['Docker', 'deploy-compose', '대기 · 교체 · 되돌림'],
  ];
  const gap = 34;
  const sW = (IW - gap * (steps.length - 1)) / steps.length;
  steps.forEach((st, i) => {
    const x = IX + i * (sW + gap);
    s += card({ x, y: cY + 44, w: sW, h: 56, ic: st[0], title: st[1], sub: st[2], iconSize: 24 });
    if (i < steps.length - 1) s += arrow(x + sW + 2, cY + 72, x + sW + gap - 2, cY + 72);
  });

  return svgDoc(W, cY + cH + 30, s);
}

function write(name, svg) {
  const r = new Resvg(svg, {
    fitTo: { mode: 'zoom', value: 2 },
    font: { loadSystemFonts: true, defaultFontFamily: 'Segoe UI' },
  });
  fs.writeFileSync(`${OUT}/${name}.png`, r.render().asPng());
  console.log(`${name}: ok`);
}

write('architecture', architecture());
