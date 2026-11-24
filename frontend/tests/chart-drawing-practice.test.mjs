import assert from "node:assert/strict";
import test from "node:test";
import { findDrawingPractice } from "../src/components/chartDrawingPractice.ts";

const candles = Array.from({ length: 48 }, (_, i) => {
  const reference = 100 + i * 0.2 + [0, -7, -3, 2, 8, 3, 0, -2][i % 8] + (i >= 32 ? (i - 32) * -0.65 : 0);
  const stamp = new Date(Date.UTC(2026, 8, 1 + i)).toISOString();
  return { time: stamp.slice(0, 10), stamp, open: reference, close: reference + 1, high: reference + 2, low: reference - 1 };
});
const byTime = new Map(candles.map(candle => [candle.time, candle]));

test("상승선은 확정된 두 저점, 하락선은 확정된 두 고점에 순서대로 붙는다", () => {
  for (const [mode, side, direction] of [["rising", "low", 1], ["falling", "high", -1]]) {
    const practice = findDrawingPractice(candles, mode, false);
    assert.equal(practice.kind, "trend");
    const [{ anchor: a }, { anchor: b }] = practice.steps;
    assert.ok(a.time < b.time);
    assert.equal(a.price, byTime.get(a.time)[side]);
    assert.equal(b.price, byTime.get(b.time)[side]);
    assert.ok(direction * (b.price - a.price) > 0);
    assert.notEqual(b.time, candles.at(-1).time);
  }
});

test("수평선은 가격이 가까운 같은 방향 반응 둘을 고른다", () => {
  const practice = findDrawingPractice(candles, "horizontal", false);
  const [{ anchor }] = practice.steps;
  const other = practice.reviewAnchor;
  assert.notEqual(anchor.time, other.time);
  assert.ok(Math.abs(anchor.price - other.price) / Math.max(anchor.price, other.price) <= 0.009);
  assert.ok(["high", "low"].some(side => anchor.price === byTime.get(anchor.time)[side]
    && other.price === byTime.get(other.time)[side]));
});

test("채널은 실제 반대쪽 꼬리와 확인 문구를 사용한다", () => {
  for (const logarithmic of [false, true]) {
    const practice = findDrawingPractice(candles, "channel", logarithmic);
    const [first, second, opposite] = practice.steps.map(step => step.anchor);
    assert.equal(first.price, byTime.get(first.time).low);
    assert.equal(second.price, byTime.get(second.time).low);
    assert.equal(opposite.price, byTime.get(opposite.time).high);
    assert.match(practice.review, /종가 이탈/);
  }
  assert.equal(findDrawingPractice(candles.slice(0, 2), "channel", false), null);
});
