import assert from "node:assert/strict";
import test from "node:test";
import {
  anchorCount, formatDrawingAnchors, MAX_DRAWINGS, measurePrice, readDrawings, validateAnchors, writeDrawings,
} from "../src/chartDrawingModel.ts";

const first = { time: "2026-11-19", stamp: "2026-11-19T14:30:00+00:00", price: 100 };
const second = { time: "2026-11-20", stamp: "2026-11-20T14:30:00+00:00", price: 125 };
const intraday = { time: 1_795_194_600, stamp: "2026-11-20T14:30:00Z", price: 120 };
const trend = { id: "test1", kind: "trend", anchors: [first, second] };
const horizontal = { id: "test2", kind: "horizontal", anchors: [first] };

function memoryStore() {
  const records = new Map();
  return {
    records,
    getItem: key => records.get(key) ?? null,
    setItem: (key, value) => { records.set(key, value); },
    removeItem: key => { records.delete(key); },
  };
}

test("가격 거리는 첫 앵커를 기준으로 변화의 부호와 크기를 반환한다", () => {
  assert.deepEqual(measurePrice(100, 125), { difference: 25, percent: 25 });
  assert.deepEqual(measurePrice(125, 100), { difference: -25, percent: -20 });
  assert.deepEqual(measurePrice(100, 100), { difference: 0, percent: 0 });
  for (const input of [[0, 1], [1, 0], [-1, 10], [10, Infinity], [NaN, 1], [Number.MIN_VALUE, 1]]) {
    assert.equal(measurePrice(...input), null);
  }
});

test("작도별 앵커 수와 가격·시각·평행 채널 기준점을 검증한다", () => {
  assert.deepEqual(["horizontal", "trend", "channel", "measure"].map(anchorCount), [1, 2, 3, 2]);
  assert.equal(validateAnchors("horizontal", [first]), true);
  assert.equal(validateAnchors("horizontal", [intraday]), true);
  assert.equal(validateAnchors("trend", [second, first]), true);
  assert.equal(validateAnchors("trend", [first, { ...first, price: 135 }]), false);
  assert.equal(validateAnchors("trend", [first, intraday]), false);
  assert.equal(validateAnchors("measure", [first, { ...first, price: 135 }]), true);
  assert.equal(validateAnchors("channel", [first, second, { ...second, price: 90 }]), true);
  assert.equal(validateAnchors("channel", [first, second, second]), false);
  assert.equal(validateAnchors("horizontal", [{ ...first, price: -1 }]), false);
  assert.equal(validateAnchors("horizontal", [{ ...first, time: "2026-02-31" }]), false);
  assert.equal(validateAnchors("horizontal", [{ ...first, stamp: "2026-02-31T10:00:00Z" }]), false);
  assert.equal(validateAnchors("trend", Array(2)), false);
});

test("상품과 시간봉별로 저장하고 새로 읽거나 마지막 선을 삭제할 수 있다", () => {
  const storage = memoryStore();
  assert.equal(writeDrawings("NQ=F", "1d", [trend], storage), true);
  assert.equal(writeDrawings("NQ=F", "1M", [horizontal], storage), true);
  assert.equal(writeDrawings("SOXX", "1d", [horizontal], storage), true);
  assert.deepEqual(readDrawings("nq=f", "1d", storage), [trend]);
  assert.deepEqual(readDrawings("NQ=F", "1M", storage), [horizontal]);
  assert.deepEqual(readDrawings("NQ=F", "1m", storage), []);
  assert.deepEqual(readDrawings("SOXX", "1d", storage), [horizontal]);
  assert.equal(writeDrawings("NQ=F", "1d", [], storage), true);
  assert.equal(storage.records.size, 2);
  assert.deepEqual(readDrawings("NQ=F", "1d", storage), []);
});

test("손상·과다 로컬 데이터와 저장 실패를 처리하며 유효한 선을 보존한다", () => {
  const storage = memoryStore();
  assert.equal(writeDrawings("SOXX", "1w", [trend], storage), true);
  assert.equal(writeDrawings("SOXX", "1w", [trend, trend], storage), false);
  assert.equal(writeDrawings("SOXX", "1w", Array(MAX_DRAWINGS + 1).fill(horizontal), storage), false);
  assert.equal(writeDrawings("SOXX", "1w", [trend], { setItem() { throw new Error("quota"); } }), false);
  assert.deepEqual(readDrawings("SOXX", "1w", storage), [trend]);

  const key = [...storage.records.keys()][0];
  storage.records.set(key, JSON.stringify([
    { ...trend, unknown: true, anchors: [{ ...first, unknown: true }, second] },
    trend,
    { ...horizontal, id: "\n" },
    { ...horizontal, kind: "__proto__" },
    {},
    horizontal,
  ]));
  assert.deepEqual(readDrawings("SOXX", "1w", storage), [trend, horizontal]);
  storage.records.set(key, "[");
  assert.deepEqual(readDrawings("SOXX", "1w", storage), []);
  storage.records.set(key, " ".repeat(100_000));
  assert.deepEqual(readDrawings("SOXX", "1w", storage), []);
  assert.deepEqual(readDrawings("SOXX", "1w", { getItem() { throw new Error("blocked"); } }), []);
});

test("앵커 복사에 상품·시간봉·선 종류와 원천 시각과 가격을 보존한다", () => {
  assert.equal(formatDrawingAnchors(trend, "NQ=F", "1d", "USD"), [
    "NQ=F · 1d · 추세선",
    "A 2026-11-19T14:30:00+00:00 · 100 USD",
    "B 2026-11-20T14:30:00+00:00 · 125 USD",
  ].join("\n"));
  assert.match(formatDrawingAnchors({ ...horizontal, anchors: [intraday] }, "NQ=F", "5m", "USD"),
    /2026-11-20T14:30:00Z · 120 USD/);
});
