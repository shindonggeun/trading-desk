import assert from "node:assert/strict";
import { test } from "node:test";
import { hasScenarios, historyText, scenarioAt, scenarioExtraPrices, scenarioPlan, scenarioRegime } from "./directionScenarios.ts";

const absent = { status: "unavailable", n: 0, win_rate: null, start: null, end: null, win_rule: null };
const long = {
  status: "wait_for_conditions", regime: "uptrend", entry_price: 102, entry_zone: [100, 102], stop_loss: 95,
  targets: [{ role: "t1", price: 110, rr: 1.1 }, { role: "t2", price: 120, rr: 2.6 }], history: absent,
};
const short = { ...long, regime: "range", entry_price: 140, entry_zone: [140, 145], stop_loss: 150,
  targets: [{ role: "t1", price: 130, rr: 1 }] };
const plan = {
  bias: "flat", horizon: "swing", entry_price: 200, stop_loss: 190, price_target: 230, long_trigger: 220,
  short_trigger: 180, underlying: { ticker: "BASE" },
  horizons: { scalp: { scenarios: { long } }, swing: { scenarios: { short } }, intraday: {} },
};

test("추세 롱과 박스권 숏이 전체 관망과 독립적으로 가격선을 선택한다", () => {
  const first = scenarioAt(plan, "scalp", "long");
  const second = scenarioAt(plan, "swing", "short");
  assert.equal(scenarioRegime(first.regime), "상승 추세");
  assert.equal(scenarioRegime(second.regime), "박스권");
  const lines = (horizon, side, scenario) => {
    const shown = scenarioPlan(plan, horizon, side, scenario);
    assert.equal(shown.horizon, horizon);
    assert.equal(shown.bias, side);
    assert.equal(shown.underlying, undefined);
    assert.equal(shown.long_trigger, null);
    assert.equal(shown.short_trigger, null);
    return [shown.entry_price, shown.stop_loss, shown.price_target];
  };
  assert.deepEqual(lines("scalp", "long", first), [102, 95, 110]);
  assert.deepEqual(lines("swing", "short", second), [140, 150, 130]);
  assert.deepEqual(scenarioExtraPrices(first).map((line) => line.price), [100, 120]);
  assert.equal(plan.entry_price, 200);
});

test("없거나 이전 버전인 시나리오는 기존 화면으로 돌아가고 진입 근거 없을 때 선을 그리지 않는다", () => {
  assert.equal(scenarioAt(plan, "intraday", "long"), null);
  assert.equal(scenarioAt(plan, "scalp", "short"), null);
  assert.equal(hasScenarios(plan.horizons.intraday), false);
  const missing = { ...long, status: "no_setup" };
  assert.equal(scenarioPlan(plan, "scalp", "long", missing).entry_price, null);
  assert.equal(scenarioPlan(plan, "scalp", "long", missing).stop_loss, null);
  assert.equal(scenarioPlan(plan, "scalp", "long", missing).price_target, null);
  assert.deepEqual(scenarioExtraPrices(missing), []);
});

test("과거 승률은 표본 충분·부족·없음 상태를 구분한다", () => {
  assert.equal(historyText({ ...absent, status: "available", n: 30, win_rate: 0.5667 }), "과거 승률 56.7% · 30건");
  assert.match(historyText({ ...absent, status: "available", n: 30, win_rate: 0 }), /0.0%/);
  assert.equal(historyText({ ...absent, status: "insufficient", n: 12 }), "과거 승률 표본 부족 · 12건");
  assert.equal(historyText(absent), "과거 승률 집계 없음");
});
