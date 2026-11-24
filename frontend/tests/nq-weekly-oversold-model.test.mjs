import assert from "node:assert/strict";
import test from "node:test";
import { confirmedNqMarkers, nqChannelCopy, nqDraft, parseNqDraft } from "../src/nqWeeklyOversold.ts";

test("서버 규칙의 네 필드를 전체 저장 요청으로 복원한다", () => {
  const original = { enabled: false, rsi_limit: 0, price_conditions: ["bollinger", "channel", "fib"], min_hits: 2 };
  const draft = nqDraft(original);
  assert.deepEqual(parseNqDraft(draft), { settings: original });
  draft.enabled = true;
  draft.rsiLimit = "100";
  draft.priceConditions.reverse();
  draft.priceConditions.pop();
  assert.deepEqual(parseNqDraft(draft), {
    settings: { enabled: true, rsi_limit: 100, price_conditions: ["channel", "fib"], min_hits: 2 },
  });
  assert.deepEqual(original.price_conditions, ["bollinger", "channel", "fib"]);
});

test("빈 RSI·최소 개수, 범위 밖 RSI, 중복·빈 가격 조건, 소수·초과 개수를 막는다", () => {
  const draft = nqDraft({ enabled: false, rsi_limit: 30, price_conditions: ["bollinger"], min_hits: 1 });
  for (const input of ["", "  ", "-1", "101", "NaN", "Infinity"]) {
    assert.match(parseNqDraft({ ...draft, rsiLimit: input }).error, /RSI/);
  }
  for (const conditions of [[], ["bollinger", "bollinger"], ["not_applicable"]]) {
    assert.match(parseNqDraft({ ...draft, priceConditions: conditions }).error, /가격 조건/);
  }
  for (const input of ["", "0", "1.5", "2", "Infinity"]) {
    assert.match(parseNqDraft({ ...draft, minHits: input }).error, /최소 충족 개수/);
  }
});

test("판정 불가 상태에도 과거 확정 조건 충족 마커만 유지한다", () => {
  const start = "2026-11-09";
  const forming = "2026-11-23";
  const confirmedAt = "2026-11-15T23:00:00Z";
  const marker = { status: "matched", week: { start, confirmed_at: confirmedAt, fetched_at: "2026-11-22T23:30:00Z" } };
  const view = {
    current: { status: "unavailable", observed_at: "2026-11-24T14:10:00Z", forming_week: { start: forming } },
    markers: [
      marker,
      { ...marker, status: "not_matched" },
      { ...marker, week: { ...marker.week, start: forming } },
      { ...marker, week: { ...marker.week, fetched_at: "2026-11-14T23:30:00Z" } },
      { ...marker, week: { ...marker.week, fetched_at: "2026-11-25T23:30:00Z" } },
      { ...marker, week: { ...marker.week, fetched_at: null } },
    ],
  };
  assert.deepEqual(confirmedNqMarkers(view), [marker]);
  assert.deepEqual(confirmedNqMarkers(null), []);
});

test("한 채널만 연결된 경우와 재연결·알림 조건 비활성 상태를 구분한다", () => {
  assert.equal(nqChannelCopy({ channel: "telegram", status: "ready", needs_reconnect: false }), "수신 가능");
  assert.match(nqChannelCopy({ channel: "kakao", status: "unavailable", needs_reconnect: false }), /미확인/);
  assert.match(nqChannelCopy({ channel: "kakao", status: "disabled", needs_reconnect: false }), /꺼짐/);
  assert.match(nqChannelCopy({ channel: "kakao", status: "needs_reconnect", needs_reconnect: true }), /재연결 필요/);
  assert.match(nqChannelCopy({ channel: "kakao", status: "ready", needs_reconnect: true }), /재연결 예정/);
});
