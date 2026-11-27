/**
 * README · docs/FEATURES.md 의 기능 GIF 녹화기 (#1027).
 *
 *   cd docs/diagrams && npm install && npx playwright install chromium
 *   node record-features.mjs [장면 …]   # 장면을 빼면 전부. 계정은 미리 설정한 DESK_CHECK_USER · DESK_CHECK_PASS 환경 변수
 *   python -X utf8 frames-to-gif.py                                        # frames/ → ../images/features/*.gif
 *
 * 운영(trading.8llow8llowme.com)을 실제로 조작하며 장면마다 프레임을 찍는다. 화면 아래 자막이 지금 하는 일을,
 * 주황 테두리가 설명하는 자리를 알려 준다. 프레임과 표시 시간은 frames/<장면>/ 에 남고 GIF 는 frames-to-gif.py 가 묶는다.
 *
 * 계좌 값은 GIF 에 싣지 않는다(docs/security.md #775). 녹화 계정이 소유자여도 브라우저에서
 * - 계좌 경로(/api/portfolio · /api/review · /api/holding-watch)를 비소유 계정과 같은 403 으로 막고,
 * - 분석 목록에서 계좌를 읽은 분석(options.use_portfolio)을 빼고,
 * - 모의 계좌(/api/paper/summary · equity · trades)를 막고 대시보드 모의 투자 카드를 숨기고,
 * - 오늘 알림에서 보유 종목 알림(holding_alert)을 빼고, 시장 스트림은 alert 채널 없이 구독하고,
 * - 목록에서 뺀 분석을 id 로 직접 여는 경로(/api/runs/{id} · ?run=)는 404 로 막고,
 * - 머리글의 로그인 아이디를 `demo` 로 바꾼다.
 * 응답 모양이 예상과 다르면 통과시키지 않고 막는다. 가장 안전한 길은 비소유 계정으로 녹화하는 것이다.
 * 계정 값은 환경 변수로만 받고 어디에도 쓰지 않는다.
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRAMES = path.join(HERE, 'frames');
const BASE = process.env.DESK_BASE_URL || 'https://trading.8llow8llowme.com';
// 계좌 값이 나가는 경로(docs/security.md #775 · tests/test_account_egress.py EXITS)
const ACCOUNT = [/\/api\/portfolio/, /\/api\/review/, /\/api\/holding-watch/];
// 소유자의 모의 계좌는 계좌를 읽은 분석까지 계산한다 — 다른 계정용 계산을 받을 길이 없어 통째로 막고 카드도 숨긴다
const PAPER = /\/api\/paper\/(summary|equity|trades)/;
const ACCENT = '#F97316';

const OVERLAY_CSS = `
  #rec-caption { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%); z-index: 2147483647;
    max-width: 86%; padding: 12px 22px; border-radius: 12px; background: rgba(15, 23, 42, .88); color: #fff;
    font: 600 20px/1.45 Pretendard, 'Malgun Gothic', sans-serif; text-align: center; box-shadow: 0 6px 24px rgba(0,0,0,.25);
    pointer-events: none; }
  #rec-caption small { display: block; font-weight: 500; font-size: 15px; opacity: .78; margin-top: 2px; }
  #rec-caption:empty { display: none; }
  #rec-caption.top { bottom: auto; top: 76px; }
  @media (max-width: 600px) { #rec-caption { font-size: 14px; padding: 8px 14px; bottom: 64px; max-width: 72%; } #rec-caption small { font-size: 12px; } }
  .rec-mark { outline: 3px solid ${ACCENT} !important; outline-offset: 3px !important; border-radius: 6px; }
  .rec-tap { position: fixed; z-index: 2147483646; width: 34px; height: 34px; margin: -17px 0 0 -17px; border-radius: 50%;
    background: rgba(249, 115, 22, .35); border: 2px solid ${ACCENT}; pointer-events: none; }
  /* 녹화에 방해되는 떠 있는 요소 */
  .tour-prompt { display: none !important; }
  section[data-section="paper"] { display: none !important; }
`;

export async function openDesk({ width = 1440, height = 900, mobile = false } = {}) {
  const user = process.env.DESK_CHECK_USER;
  const pass = process.env.DESK_CHECK_PASS;
  if (!user || !pass) throw new Error('DESK_CHECK_USER / DESK_CHECK_PASS 가 필요하다');
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    baseURL: BASE,
    viewport: { width, height },
    deviceScaleFactor: mobile ? 2 : 1,
    isMobile: mobile,
    hasTouch: mobile,
    serviceWorkers: 'block',
    locale: 'ko-KR',
    timezoneId: 'Asia/Seoul',
    colorScheme: 'light',
  });
  await ctx.addInitScript(() => {
    try {
      const all = JSON.stringify(['dashboard', 'analysis', 'tips', 'funds']);
      localStorage.setItem('desk.tour.never', all);
    } catch { /* 안내 띠는 CSS 로도 숨긴다 */ }
  });
  const hidden = new Set(); // 목록에서 뺀 계좌 분석 id
  const deny = (route) => route.fulfill({ status: 403, json: { detail: "only the desk's owner sees the account" } });
  await ctx.route('**/api/**', async (route) => {
    const req = route.request();
    const url = req.url();
    if (ACCOUNT.some((re) => re.test(url)) || PAPER.test(url)) return deny(route);
    const runRef = url.match(/\/api\/runs\/([\w-]+)/) || url.match(/[?&]run=([\w-]+)/);
    if (runRef && hidden.has(runRef[1])) return route.fulfill({ status: 404, json: { detail: 'run not found' } });
    if (/\/api\/stream\/market/.test(url)) {
      const u = new URL(url);
      const ch = (u.searchParams.get('channels') || '').split(',').filter((c) => c && c !== 'alert');
      if (!ch.length) return route.abort(); // 빈 channels 는 서버가 기본 채널(alert 포함)로 읽는다
      u.searchParams.set('channels', ch.join(','));
      return route.continue({ url: u.toString() });
    }
    if (/\/api\/alerts\/today/.test(url)) {
      const res = await route.fetch();
      const j = await res.json().catch(() => null);
      if (!j || !Array.isArray(j.items)) return route.fulfill({ status: 502, json: { detail: 'unexpected alerts shape' } });
      return route.fulfill({ response: res, json: { ...j, items: j.items.filter((a) => a.kind !== 'holding_alert') } });
    }
    if (/\/api\/auth\/me(\?|$)/.test(url)) {
      const res = await route.fetch();
      const j = await res.json().catch(() => null);
      return route.fulfill({ response: res, json: j ? { ...j, username: 'demo' } : j });
    }
    if (/\/api\/runs(\?|$)/.test(url) && req.method() === 'GET') {
      const res = await route.fetch();
      let j = await res.json().catch(() => null);
      const drop = (a) => a.filter((r) => {
        const acct = !r.options || r.options.use_portfolio !== false;
        if (acct && r.id) hidden.add(r.id);
        return !acct;
      });
      if (Array.isArray(j)) j = drop(j);
      else if (j && Array.isArray(j.items)) j.items = drop(j.items);
      else return route.fulfill({ status: 502, json: { detail: 'unexpected runs shape' } });
      return route.fulfill({ response: res, json: j });
    }
    return route.continue();
  });
  const res = await ctx.request.post('/api/auth/login', { headers: { Origin: BASE }, data: { username: user, password: pass } });
  if (res.status() !== 204) throw new Error(`로그인 실패 ${res.status()}`);
  const page = await ctx.newPage();
  return { browser, ctx, page };
}

export class Recorder {
  constructor(page, name) {
    this.page = page;
    this.name = name;
    this.dir = path.join(FRAMES, name);
    fs.rmSync(this.dir, { recursive: true, force: true });
    fs.mkdirSync(this.dir, { recursive: true });
    this.frames = [];
  }

  async go(hash, wait = 2500) {
    await this.page.goto('/' + hash, { waitUntil: 'domcontentloaded' });
    await this.ready(wait);
  }

  async ready(wait = 1500) {
    await this.page.evaluate((css) => {
      if (!document.getElementById('rec-style')) {
        const s = document.createElement('style');
        s.id = 'rec-style';
        s.textContent = css;
        document.head.appendChild(s);
      }
      if (!document.getElementById('rec-caption')) {
        const c = document.createElement('div');
        c.id = 'rec-caption';
        document.body.appendChild(c);
      }
    }, OVERLAY_CSS);
    await this.page.waitForTimeout(wait);
  }

  /** 화면 아래(또는 pos='top' 이면 위) 자막 */
  async caption(text, sub = '', pos = 'bottom') {
    await this.page.evaluate(([t, s, p]) => {
      const c = document.getElementById('rec-caption');
      if (!c) return;
      c.className = p;
      c.textContent = t;
      if (s) {
        const sm = document.createElement('small');
        sm.textContent = s;
        c.appendChild(sm);
      }
    }, [text, sub, pos]);
  }

  async mark(locator) {
    await this.unmark();
    const el = typeof locator === 'string' ? this.page.locator(locator).first() : locator;
    await el.scrollIntoViewIfNeeded().catch(() => {});
    await el.evaluate((n) => n.classList.add('rec-mark')).catch(() => {});
  }

  /** 글자가 text 로 시작하는 가장 작은 요소를 찾아 그 카드(가까운 조상)에 테두리를 두른다 */
  async markText(text, up = 'section, .card, article, li, [role="tabpanel"]') {
    await this.unmark();
    const found = await this.page.evaluate(([t, sel]) => {
      const all = [...document.querySelectorAll('body *')].filter((n) => {
        if (n.closest('#rec-caption')) return false;
        const s = (n.textContent || '').replace(/^[▾▸\s]+/, '').trim();
        return s.startsWith(t) && n.getBoundingClientRect().height > 0;
      });
      if (!all.length) return false;
      all.sort((a, b) => a.textContent.length - b.textContent.length);
      const head = all[0];
      const box = head.closest(sel) || head;
      box.scrollIntoView({ block: 'start', behavior: 'instant' });
      window.scrollBy(0, -70);
      box.classList.add('rec-mark');
      return true;
    }, [text, up]);
    if (!found) console.warn(`  (${this.name}) "${text}" 를 못 찾았다`);
    await this.page.waitForTimeout(500);
    return found;
  }

  async top() {
    await this.page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await this.page.waitForTimeout(300);
  }

  async unmark() {
    await this.page.evaluate(() => {
      document.querySelectorAll('.rec-mark').forEach((n) => n.classList.remove('rec-mark'));
      document.querySelectorAll('.rec-tap').forEach((n) => n.remove());
    });
  }

  /** 누를 자리를 주황 원으로 보여 준 뒤 누른다 */
  async tap(locator, { hold = 700, after = 1500 } = {}) {
    const el = typeof locator === 'string' ? this.page.locator(locator).first() : locator;
    await el.scrollIntoViewIfNeeded().catch(() => {});
    const box = await el.boundingBox();
    if (box) {
      await this.page.evaluate(([x, y]) => {
        const d = document.createElement('div');
        d.className = 'rec-tap';
        d.style.left = `${x}px`;
        d.style.top = `${y}px`;
        document.body.appendChild(d);
      }, [box.x + box.width / 2, box.y + box.height / 2]);
      await this.shot(hold);
    }
    await el.click();
    await this.page.waitForTimeout(after);
    await this.page.evaluate(() => document.querySelectorAll('.rec-tap').forEach((n) => n.remove()));
  }

  async type(locator, text, { perChar = 140 } = {}) {
    const el = typeof locator === 'string' ? this.page.locator(locator).first() : locator;
    await el.click();
    for (const ch of text) {
      await el.pressSequentially(ch);
      await this.shot(perChar);
    }
  }

  /** 지금 화면을 ms 동안 보여 줄 프레임 하나 */
  async shot(ms = 1500) {
    const file = path.join(this.dir, `${String(this.frames.length).padStart(4, '0')}.png`);
    await this.page.screenshot({ path: file });
    this.frames.push({ file: path.basename(file), ms });
  }

  /** 살아 움직이는 화면(시세 갱신 · 스트림)을 n 장 */
  async live(n, every = 1000) {
    for (let i = 0; i < n; i += 1) {
      await this.shot(every);
      await this.page.waitForTimeout(every);
    }
  }

  /** 문서를 dy 만큼 부드럽게 내린다 */
  async scroll(dy, { steps = 6, each = 260, target } = {}) {
    for (let i = 0; i < steps; i += 1) {
      await this.page.evaluate(([d, sel]) => {
        const el = sel ? document.querySelector(sel) : null;
        (el || window).scrollBy({ top: d, behavior: 'instant' });
      }, [dy / steps, target || null]);
      await this.page.waitForTimeout(120);
      await this.shot(each);
    }
  }

  save() {
    fs.writeFileSync(path.join(this.dir, 'frames.json'), JSON.stringify({ name: this.name, frames: this.frames }, null, 1));
    console.log(`${this.name}: ${this.frames.length} frames`);
  }
}

/* ------------------------------------------------------------------ 장면 */

// 분석 대상: 계좌를 읽지 않은 완료 분석(가장 최근 것). 바꾸려면 DESK_DEMO_RUN
async function demoRun(page) {
  const pick = process.env.DESK_DEMO_RUN;
  if (pick) {
    const r = await page.evaluate(async (id) => (await fetch(`/api/runs/${id}`)).json(), pick);
    if (!r || !r.options || r.options.use_portfolio !== false) throw new Error('DESK_DEMO_RUN 이 계좌를 읽지 않은 분석이 아니다');
    return pick;
  }
  const runs = await page.evaluate(async () => (await fetch('/api/runs?page=1&size=100')).json());
  const list = Array.isArray(runs) ? runs : runs.items;
  const run = list.find((r) => r.status === 'done');
  if (!run) throw new Error('계좌 없이 끝난 분석이 없다');
  return run.id;
}

const tab = (page, name) => page.getByRole('tab', { name, exact: true });
const btn = (page, name) => page.getByRole('button', { name, exact: true });

const SCENES = {
  async dashboard(rec, page) {
    await rec.go('#/', 6000);
    await rec.caption('대시보드 — 하루를 여는 첫 화면', '분석 시작 · 최근 알림 · 아침 시황 · 오늘 방향을 한 화면에');
    await rec.shot(2600);
    const input = page.getByPlaceholder(/분석할 종목/);
    await rec.mark(input);
    await rec.caption('종목 이름 · 티커로 바로 분석', '한 번에 스캘핑 · 단타 · 짧은 스윙 · 스윙 네 기간을 판단합니다');
    await rec.type(input, '엔비디아');
    await page.waitForTimeout(1200);
    await rec.shot(2200);
    await input.fill('');
    await page.keyboard.press('Escape');
    await rec.markText('최근 알림');
    await rec.caption('최근 알림 — 수급 급변 · 섹터 급등 · 선물 이상 징후', '웹 푸시 · 텔레그램으로도 받습니다');
    await rec.shot(2600);
    await rec.markText('아침 시황');
    await rec.caption('아침 시황 — 매일 아침 데스크가 쓰는 시장 정리');
    await rec.shot(2400);
    await rec.markText('오늘 방향');
    await rec.caption('오늘 방향 — 국장 · 미장의 롱 / 숏 확률과 근거', '판정은 매일 채점해 적중률로 남깁니다');
    await rec.shot(2800);
    await rec.unmark();
    await rec.caption('시장 · 선물 · 주간 지도 · 거시 브리핑 · 섹터 이슈 · 시장 수급', '카드는 접고 순서를 바꿀 수 있습니다');
    await rec.scroll(2400, { steps: 8, each: 420 });
    await rec.scroll(1000, { steps: 4, each: 420 });
    await rec.shot(1600);
    await rec.top();
    await rec.caption('AI 채팅 — 데스크 자료를 도구로 읽어 답합니다');
    await rec.tap(page.getByRole('button', { name: /AI 채팅 열기/ }), { after: 1500 });
    await rec.shot(2600);
  },

  async analysis(rec, page) {
    await rec.go('#/', 3000);
    const id = await demoRun(page);
    await rec.go('#/history', 4000);
    await rec.caption('분석 기록 — 지난 분석의 등급 · 진입 · 손절 · 목표가');
    await rec.shot(2600);
    await rec.go(`#/runs/${id}`, 5000);
    await rec.caption('분석 결과 — 네 기간을 한눈에', '스캘핑 · 단타 · 짧은 스윙 · 스윙마다 판단 · 진입 조건');
    await rec.shot(2800);
    await rec.caption('기간별 롱 · 숏 조건 — 진입 구간 · 손절 · 목표 1 · 2와 손익비');
    await rec.scroll(700, { steps: 4, each: 500 });
    await rec.shot(1800);
    await rec.top();
    await rec.caption('토론 — 애널리스트 → 강세 / 약세 → 트레이더 → 리스크 → PM', '에이전트 발언이 채팅처럼 쌓이고, 새로고침해도 DB 에서 다시 재생됩니다');
    await rec.tap(tab(page, '토론'), { after: 2500 });
    await rec.shot(2600);
    await rec.scroll(1800, { steps: 8, each: 450 });
    await rec.top();
    await rec.caption('차트 — 진입 · 손절 · 목표선을 봉 위에', '', 'top');
    await rec.tap(tab(page, '차트'), { after: 4000 });
    await rec.shot(3000);
    await rec.caption('근거 — 결론을 만든 지지 · 저항과 지표', '', 'top');
    await rec.tap(tab(page, '근거'), { after: 2500 });
    await rec.shot(2600);
    await rec.caption('쉽게 보기 — 같은 결론을 초보자 말로');
    await rec.tap(tab(page, '쉽게 보기'), { after: 2500 });
    await rec.shot(2800);
  },

  async live(rec, page) {
    await rec.go('#/live', 6000);
    await rec.caption('실시간 시세 — 한투 실시간 · 토스 · 바이낸스 추정', '장 밖에서는 바이낸스 무기한 선물로 추정가를 냅니다');
    await rec.live(6, 1300);
    await rec.markText('바이낸스 QQQ', '.card, div');
    await rec.caption('바이낸스 선물 롱 · 숏 비율을 종목 옆에');
    await rec.shot(2400);
    await rec.unmark();
    await rec.go('#/market-watchboard', 6000);
    await rec.caption('시장 모니터 — 관심군을 한 표로 보고 일봉 차트로 바로');
    await rec.shot(2600);
    await rec.scroll(700, { steps: 4, each: 500 });
  },

  async futures(rec, page) {
    await rec.go('#/futures', 6000);
    await rec.caption('코스피200 선물 — 주간 · 야간 세션 시세와 분봉', '15초마다 갱신 · 점선은 전일 정산가', 'top');
    await rec.live(4, 1500);
    await rec.caption('야간 세션(18:00 ~ 다음 날 06:00)', '', 'top');
    await rec.tap(page.getByRole('button', { name: /야간/ }).first(), { after: 3000 });
    await rec.shot(2600);
    await rec.caption('1분 · 5분 · 15분 봉', '', 'top');
    await rec.tap(btn(page, '15분'), { after: 2500 });
    await rec.shot(2400);
  },

  async charts(rec, page) {
    await rec.go('#/charts', 4000);
    await rec.caption('차트 — 종목을 고르면 분석 없이 바로', '지지 · 저항 · 추세선 · 매물대 · RSI · 스토캐스틱');
    await rec.shot(2200);
    await rec.tap(btn(page, '엔비디아'), { after: 6000 });
    await rec.caption('엔비디아 일봉 — 지지 · 저항 · 추세선 · 매물대와 보조 지표', '', 'top');
    await rec.shot(3200);
    await rec.scroll(600, { steps: 4, each: 600 });
    await rec.shot(2000);
  },

  async flow(rec, page) {
    await rec.go('#/?flow=intraday&view=flow', 7000);
    await rec.markText('시장 수급');
    await rec.caption('시장 수급 — 외국인 · 기관 · 개인 장중 흐름', '공매도 · 바이낸스 펀딩비 · 미장 옵션 탭', 'top');
    await rec.live(3, 1400);
    await rec.unmark();
    await rec.go('#/flow-board/kr', 6000);
    await rec.markText('관심종목 22거래일');
    await rec.caption('국장 수급 현황판 — 관심 종목의 외국인 참여율 · 매수 우위 · 지분 22거래일', '', 'top');
    await rec.shot(2800);
    await rec.scroll(500, { steps: 3, each: 500 });
    await rec.shot(1600);
  },

  async sectors(rec, page) {
    await rec.go('#/sectors', 6000);
    await rec.caption('섹터 — 업종별 등락과 구성 종목', '공매도 · 풋/콜 · 빚투 · 섹터 급등락 주범까지');
    await rec.shot(2600);
    await rec.tap(btn(page, '반도체(미국)'), { after: 4000 });
    await rec.shot(2400);
    await rec.go('#/weekly', 6000);
    await rec.caption('주간 지도 — 지수 · 섹터의 주봉 위치와 다음 주 볼 자리');
    await rec.shot(2600);
    await rec.scroll(900, { steps: 5, each: 450 });
  },

  async research(rec, page) {
    await rec.go('#/reports', 5000);
    await rec.caption('리포트 — 2 ~ 3일마다 데스크가 쓰는 초보자용 분석 글');
    await rec.shot(2400);
    await rec.tap(page.locator('a[href^="#/reports/"]').first(), { after: 4000 });
    await rec.shot(2600);
    await rec.scroll(900, { steps: 5, each: 450 });
    await rec.go('#/backtest', 6000);
    await rec.caption('백테스트 — 계획 규칙의 과거 체결률 · 기대 R', '규칙을 바꾸면 로컬에서 스냅샷을 다시 만들어 커밋');
    await rec.shot(2800);
    await rec.go('#/day-call', 6000);
    await rec.caption('오늘 방향 기록 — 매일의 롱 / 숏 판정과 적중');
    await rec.shot(2800);
  },

  mobile: {
    viewport: { width: 390, height: 844, mobile: true },
    async run(rec, page) {
      await rec.go('#/', 6000);
      await rec.caption('모바일 — 같은 화면을 한 손으로');
      await rec.shot(2200);
      await rec.scroll(1400, { steps: 6, each: 420 });
      await rec.go('#/live', 6000);
      await rec.caption('실시간 시세');
      await rec.shot(2400);
      const id = await demoRun(page);
      await rec.go(`#/runs/${id}`, 5000);
      await rec.caption('분석 결과');
      await rec.shot(2400);
      await rec.scroll(900, { steps: 4, each: 450 });
      await rec.go('#/futures', 6000);
      await rec.caption('코스피200 선물');
      await rec.shot(2400);
    },
  },
};

const want = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENES);
for (const name of want) {
  const scene = SCENES[name];
  if (!scene) throw new Error(`모르는 장면 ${name}`);
  const run = typeof scene === 'function' ? scene : scene.run;
  const { browser, page } = await openDesk(scene.viewport || { width: 1440, height: 900 });
  let rec;
  try {
    rec = new Recorder(page, name);
    await run(rec, page);
    rec.save();
  } catch (e) {
    rec?.save?.();
    console.error(`${name} 실패: ${e.message}`);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

