import assert from "node:assert/strict";
import { test } from "node:test";
import { queueReason } from "./runQueue.ts";

const queued = (ahead, running, analyst) => ({ status: "queued", queue: { ahead, running, analyst } });

test("대기 순번과 실행 중 건수를 각각 표시하고 갱신된 숫자를 반영한다", () => {
  assert.equal(queueReason(queued(2, 1, "up"), []).text, "앞에 2건 · 실행 중 1건");
  assert.equal(queueReason(queued(1, 2, "up"), []).text, "앞에 1건 · 실행 중 2건");
});

test("분석 서비스가 대기열을 읽지 않을 때 응답 없음 안내를 표시한다", () => {
  const reason = queueReason(queued(0, 0, "down"), []);
  assert.equal(reason.tone, "warn");
  assert.match(reason.text, /분석 서비스 응답 없음/);
});

test("대기열 정보가 없으면 기존 대기 안내와 SSE의 기다리는 대상을 사용한다", () => {
  assert.match(queueReason({ status: "queued" }, []).text, /대기열에 등록됨/);
  const events = [{ type: "status", content: "queued", data: { waiting_for: "analyst" } }];
  assert.match(queueReason({ status: "queued" }, events).text, /분석 서비스가 가져가길 기다리는 중/);
  const reason = queueReason(queued(1, 1, null), []);
  assert.equal(reason.tone, "wait");
  assert.equal(reason.text, "앞에 1건 · 실행 중 1건");
  assert.match(reason.title, /차례|끝나면/);
});

test("분석이 실행되면 대기 안내를 숨긴다", () => {
  const run = { ...queued(0, 1, "up"), status: "running" };
  assert.equal(queueReason(run, []), null);
});
