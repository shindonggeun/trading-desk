import React from "react";
import { createRoot } from "react-dom/client";
import QuoteBoard from "../../src/components/QuoteBoard";
import MarketFlowPanel from "../../src/components/MarketFlowPanel";
import FuturesFlowView from "../../src/components/FuturesFlowView";
import { useTodayAlerts } from "../../src/alertsFeed";
import "../../src/styles.css";

const paths = {
  "/api/market/flow": "flow",
  "/api/estimates": "quote",
  "/api/market/futures": "futures",
  "/api/alerts/today": "alert",
};
const requests = { flow: 0, quote: 0, futures: 0, alert: 0 };
const responses = { ...requests };
const channels = Object.keys(requests);
const remote = ["flow", "futures", "alert"];
const fetchRequest = window.fetch.bind(window);
window.fetch = async (path, options) => {
  const key = paths[new URL(String(path), location.origin).pathname];
  if (key) requests[key]++;
  const response = await fetchRequest(path, options);
  if (key && response.ok) responses[key]++;
  return response;
};

// The product defaults are 15/30/60 seconds. Run the same usePoll code at 1/20 in this page.
const timeout = window.setTimeout.bind(window);
window.setTimeout = (callback, ms, ...args) =>
  timeout(callback, [15000, 30000, 60000].includes(ms) ? ms / 20 : ms, ...args);
const wait = ms => new Promise(resolve => timeout(resolve, ms));
const live = channel => Boolean(document.querySelector(
  `#test-${channel} .live-badge.on, #test-${channel} [data-live=true]`,
));
const currentText = selector => document.querySelector(selector)?.textContent ?? "";
const flowPrice = () => currentText("#test-flow .flow-index strong").replaceAll(",", "");
const futuresPrice = () => currentText("#test-futures .fut-price");
const quotePrice = () => currentText("#test-quote");
const alertRows = () => document.querySelectorAll("#test-alert li").length;
const errors = [];
window.addEventListener("error", event => errors.push(event.message));
window.addEventListener("unhandledrejection", event => errors.push(String(event.reason)));

function assert(condition, label) {
  if (!condition) throw Error(`${label} ${JSON.stringify({ requests, responses, live: channels.filter(live), errors })}`);
}

async function until(predicate, label, seconds = 12) {
  const deadline = performance.now() + seconds * 1000;
  while (performance.now() < deadline) {
    if (predicate()) return;
    await wait(25);
  }
  assert(false, label);
}

async function phase(name) {
  const response = await fetch(`/api/_integration/${name}`, { method: "POST" });
  assert(response.ok, `${name} returned ${response.status}`);
  return response.json();
}

const watch = [{ ticker: "AAPL" }];
localStorage.setItem("desk.board.weekend.open", "false");

function Alerts() {
  const alerts = useTodayAlerts();
  return <div data-live={alerts.live}>
    <ul>{alerts.data?.items.map((item, index) => <li key={index}>{item.text}</li>)}</ul>
  </div>;
}

function Panels() {
  return <main>
    <div id="test-quote"><QuoteBoard watch={watch} selected="AAPL" onPick={() => {}} /></div>
    <div id="test-flow"><MarketFlowPanel /></div>
    <div id="test-futures"><FuturesFlowView /></div>
    <div id="test-alert"><Alerts /></div>
  </main>;
}

async function verify() {
  const login = await fetch("/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(window.marketFixture),
  });
  assert(login.ok, "fixture login");
  const initial = await phase("seed");
  assert(initial.api.received_remote >= 2 && initial.collector.published_remote >= 2, "seed crossed Redis");
  createRoot(document.getElementById("root")).render(<Panels />);
  await until(() => channels.every(key => live(key) && responses[key] > 0) && alertRows() === 1, "initial panels");
  await phase("healthy");
  await until(() => flowPrice().includes("2688") && futuresPrice().includes("483") && quotePrice().includes("182"),
    "Redis and local updates reached the panels");
  await wait(400);
  const healthy = { ...requests };
  await wait(3200);
  assert(channels.every(key => requests[key] === healthy[key]), "polling while healthy");

  const beforeOutage = { ...requests };
  await phase("prepare-outage");
  document.body.dataset.stage = "stop-redis";
  await until(() => remote.every(key => !live(key) && responses[key] > beforeOutage[key])
    && flowPrice().includes("2699") && futuresPrice().includes("489"), "remote panels switch to REST values", 20);
  await phase("outage");
  await until(() => live("quote") && quotePrice().includes("193"), "new local quote arrives with Redis down");
  await wait(3200);
  const outage = Object.fromEntries(channels.map(key => [key, requests[key] - beforeOutage[key]]));
  assert(remote.every(key => outage[key] >= 2), "remote panels did not continue polling");
  assert(outage.quote === 0 && live("quote") && quotePrice().includes("193") && alertRows() === 1,
    "local quote or saved alert was lost during the outage");

  const beforeRecovery = { ...responses };
  document.body.dataset.stage = "start-redis";
  await until(() => channels.every(live), "all channels return to live", 20);
  await wait(400);
  const recoveryRest = Object.fromEntries(channels.map(key => [key, responses[key] - beforeRecovery[key]]));
  const recovered = { ...requests };
  const first = await phase("recovery-first");
  const firstMarkersVisible = () => flowPrice().includes("2711") && futuresPrice().includes("491")
    && currentText("#test-alert").includes("Redis first recovery marker");
  const firstVisible = first.batch.complete
    ? await until(firstMarkersVisible, "first recovery batch markers", 2).then(() => true, () => false)
    : firstMarkersVisible();
  const firstRecovery = { ...first.batch, visible: firstVisible };

  // Publish later values with their own event IDs, not a replay of the first recovery batch.
  const restored = await phase("recovered");
  assert(restored.batch.complete && restored.api.reconnects > 0,
    `live Redis subscription did not receive all new events: ${JSON.stringify({ firstRecovery, second: restored.batch })}`);
  // REST still serves 2699/489 and the single saved alert. Only SSE can show all three markers.
  const recoveredMarkersVisible = () => flowPrice().includes("2722") && futuresPrice().includes("492")
    && currentText("#test-alert").includes("Redis recovery marker");
  await until(recoveredMarkersVisible, "post-reconnect SSE markers");
  await wait(3200);
  assert(channels.every(key => requests[key] === recovered[key] && live(key)) && recoveredMarkersVisible(),
    "polling continued or SSE markers were lost after recovery");
  assert(errors.length === 0, "browser errors");
  return { ok: true, outage, localQuoteDuringOutage: 193, recoveryRest, receivedRemote: restored.api.received_remote,
    publishedRemote: restored.collector.published_remote, redisReconnects: restored.api.reconnects,
    recoveryBatches: { first: firstRecovery, second: restored.batch } };
}

verify().then(
  result => { document.body.dataset.result = JSON.stringify(result); },
  error => { document.body.dataset.result = JSON.stringify({ ok: false, error: error.stack }); },
);
