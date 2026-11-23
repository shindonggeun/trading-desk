import assert from "node:assert/strict";
import { test } from "node:test";
import { reusable, SAME_FEED_MS } from "./lineFeedReuse.ts";

const last = { date: "2026-11-23", feed: '{"items":[1]}', at: 1_000, items: ["kept"] };

test("the same feed read again right away reuses the last line signals", () => {
  assert.deepEqual(reusable(last, "2026-11-23", '{"items":[1]}', 1_000 + SAME_FEED_MS - 1), ["kept"]);
});

test("a changed feed, a new day, a later reconcile or no earlier read reads again", () => {
  assert.equal(reusable(last, "2026-11-23", '{"items":[1,2]}', 2_000), null);        // a new alert
  assert.equal(reusable(last, "2026-11-24", '{"items":[1]}', 2_000), null);          // past midnight
  assert.equal(reusable(last, "2026-11-23", '{"items":[1]}', 1_000 + SAME_FEED_MS), null);   // the 5-minute reconcile
  assert.equal(reusable(null, "2026-11-23", '{"items":[1]}', 2_000), null);
});
