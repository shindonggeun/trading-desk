import assert from "node:assert/strict";
import { test } from "node:test";
import { channelStatus, ruleProblem, rulesFromCommon } from "../src/components/lineWatchSettingsModel.ts";

test("공통 설정을 독립 규칙으로 깊게 복사한다", () => {
  const common = { tickers: ["NQ=F", "005930.KS"], timeframes: ["60m"], events: { touch: true, break: false } };
  const rules = rulesFromCommon(common);
  assert.deepEqual(rules[0], { ticker: "NQ=F", enabled: true, timeframes: ["60m"], events: { touch: true, break: false }, side: "both" });
  rules[0].timeframes.push("1w");
  rules[0].events.touch = false;
  assert.deepEqual(rules[1].timeframes, ["60m"]);
  assert.equal(common.events.touch, true);
});

test("지원하지 않는 시간봉은 꺼진 규칙에서도 제거해야 하며 비어 있는 비활성 규칙은 허용한다", () => {
  const rule = { ticker: "BTC-USD", side: "resistance", enabled: true, timeframes: [], events: {} };
  assert.match(ruleProblem(rule), /시간봉을 하나 이상/);
  assert.equal(ruleProblem({ ...rule, enabled: false }), null);
  const unsupported = { market: null, supported: [], unsupported: { "1w": "unsupported_market" } };
  assert.match(ruleProblem({ ...rule, enabled: false, timeframes: ["1w"] }, unsupported), /지원하지 않는/);
  assert.match(ruleProblem({ ...rule, timeframes: ["1w"] }), /확인하지 못했어요/);
  assert.equal(ruleProblem({ ...rule, timeframes: ["1w"] }, { ...unsupported, supported: ["1w"] }), null);
});

test("채널 미연결, 채널 비활성, 차트 선·참고 구독을 구분한다", () => {
  const telegram = { id: "telegram", configured: true, connected: true, enabled: true, kinds: {}, needs_reconnect: false };
  const info = { can_edit: true, kinds: ["line_signal", "tone_neutral"], channels: [telegram] };
  const withTelegram = patch => ({ ...info, channels: [{ ...telegram, ...patch }] });
  assert.equal(channelStatus({ ...info, can_edit: false }, "telegram"), "상태 확인 불가");
  assert.equal(channelStatus(withTelegram({ connected: false }), "telegram"), "연결 안 됨");
  assert.match(channelStatus(withTelegram({ needs_reconnect: true }), "telegram"), /다시 연결/);
  assert.match(channelStatus(withTelegram({ enabled: false }), "telegram"), /채널 꺼짐/);
  assert.match(channelStatus(withTelegram({ kinds: { line_signal: false } }), "telegram"), /차트 선 알림 꺼짐/);
  assert.match(channelStatus(withTelegram({ kinds: { tone_neutral: false } }), "telegram"), /참고 등급 꺼짐/);
  assert.match(channelStatus(info, "telegram"), /차트 선 알림 켜짐/);
});

test("보호 모드에서 편집 권한 없이도 승인 채널의 종류와 등급을 표시한다", () => {
  const telegram = { name: "telegram", configured: true, enabled: true, kinds: { line_signal: true, tone_neutral: true } };
  const info = { can_edit: false, kinds: ["line_signal", "tone_neutral"], channels: [telegram] };
  assert.equal(channelStatus(info, "telegram"), "승인된 수신처 · 차트 선 알림 켜짐");
  assert.equal(channelStatus({ ...info, channels: [{ ...telegram, configured: false }] }, "telegram"), "승인된 수신처 없음");
  assert.equal(channelStatus({ ...info, channels: [{ ...telegram, kinds: { line_signal: true } }] }, "telegram"),
    "승인된 수신처 · 참고 등급 꺼짐");
});
