import React from "react";
import { createRoot } from "react-dom/client";
import QuoteBoard from "../src/components/QuoteBoard";
import MarketFlowPanel from "../src/components/MarketFlowPanel";
import "../src/styles.css";

const rootElement = document.getElementById("root");
const mode = new URLSearchParams(location.search).get("mode");
const config = await request("/benchmark/config");
const rounds = ["poll", "sse", "sse", "poll"];
const observationLimit = config.offsetsMs.at(-1) + 35_000 * config.scale + 5000;
const targets = {
  quote: { selector: "#quote .qb-price", seed: 500 },
  flow: { selector: "#flow .flow-index strong", seed: 2600 },
};

async function request(path, method = "GET") {
  const response = await fetch(path, { method });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function until(condition, name, timeout = 8000) {
  const start = performance.now();
  while (!condition()) {
    if (performance.now() - start > timeout) throw new Error(name);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
}

function numberAt(selector) {
  return Number.parseFloat(document.querySelector(selector)?.textContent.replaceAll(",", ""));
}

function Panels() {
  return <main>
    <div id="quote"><QuoteBoard watch={[{ ticker: "AAPL" }]} selected="AAPL" onPick={() => {}} /></div>
    <div id="flow"><MarketFlowPanel /></div>
  </main>;
}

async function measure() {
  localStorage.setItem("desk.board.weekend.open", "false");
  localStorage.setItem("desk.longshort.show", "false");
  localStorage.setItem("desk.marketFlow.v1", JSON.stringify({ market: "KOSPI", interval: "1d" }));

  // Keep actual usePoll behavior. The quick mode only shortens the two polling intervals and source schedule.
  const setTimer = window.setTimeout.bind(window);
  if (config.scale !== 1) {
    window.setTimeout = (callback, ms, ...args) =>
      setTimer(callback, [15_000, 30_000].includes(ms) ? ms * config.scale : ms, ...args);
  }
  await request(`/benchmark/prepare?mode=${mode}`, "POST");
  const root = createRoot(rootElement);
  root.render(<Panels />);
  await until(() => Object.values(targets).every(({ selector, seed }) => numberAt(selector) === seed), "initial REST values");
  await until(() => Object.keys(targets).every((channel) =>
    Boolean(document.querySelector(`#${channel} .live-badge.on`)) === (mode === "sse")), "requested transport");
  const domTimes = new Map();
  const observer = new MutationObserver(() => {
    for (const [channel, { selector, seed }] of Object.entries(targets)) {
      const sample = numberAt(selector) - seed;
      const key = `${channel}:${sample}`;
      if (Number.isInteger(sample) && sample > 0 && sample <= config.offsetsMs.length && !domTimes.has(key)) {
        domTimes.set(key, Date.now());
      }
    }
  });
  observer.observe(rootElement, { childList: true, characterData: true, subtree: true });
  try {
    await request("/benchmark/start", "POST");
    await until(() => domTimes.size === config.offsetsMs.length * Object.keys(targets).length,
      `missing DOM observations: ${[...domTimes.keys()].join(", ")}`, observationLimit);
    const { samples, rest } = await request("/benchmark/results");
    const records = samples.flatMap(({ index, sourceAt, observedAt }) => Object.keys(targets).map((channel) => ({
      channel, index, sourceAt, observedAt, domAt: domTimes.get(`${channel}:${index}`),
    })));
    if (samples.length !== config.offsetsMs.length || records.some(({ sourceAt, observedAt, domAt }) =>
      !Number.isFinite(domAt) || domAt < observedAt || observedAt < sourceAt)) throw new Error("invalid sample times");
    if (Object.values(rest).some((count) => (count === 0) !== (mode === "sse"))) throw new Error("unexpected REST usage");
    return { mode, rest, records };
  } finally {
    observer.disconnect();
    root.unmount();
  }
}

function trial(kind, frame) {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => finish(new Error(`timeout: ${kind}`)), observationLimit + 20_000);
    function finish(error, data) {
      window.clearTimeout(timeout);
      window.removeEventListener("message", receive);
      if (error) reject(error);
      else resolve(data);
    }
    function receive(event) {
      if (event.origin !== location.origin || event.source !== frame.contentWindow || event.data?.mode !== kind) return;
      finish(event.data.error && new Error(event.data.error), event.data);
    }
    window.addEventListener("message", receive);
    frame.src = `/?mode=${kind}`;
  });
}

function percentile(values, fraction) {
  return [...values].sort((a, b) => a - b)[Math.ceil(values.length * fraction) - 1];
}

function summary(results) {
  return ["poll", "sse"].flatMap((kind) => Object.keys(targets).map((channel) => {
    const selected = results.filter((r) => r.mode === kind);
    const records = selected.flatMap((r) => r.records).filter((r) => r.channel === channel);
    const metrics = {
      sourceToObserved: records.map((r) => r.observedAt - r.sourceAt),
      observedToDom: records.map((r) => r.domAt - r.observedAt),
      sourceToDom: records.map((r) => r.domAt - r.sourceAt),
    };
    return {
      mode: kind, channel, n: records.length, rest: selected.reduce((n, r) => n + r.rest[channel], 0),
      latencyMs: Object.fromEntries(Object.entries(metrics).map(([name, values]) => [
        name, { p50: percentile(values, 0.5), p95: percentile(values, 0.95) },
      ])),
    };
  }));
}

if (mode === "poll" || mode === "sse") {
  measure().then((result) => parent.postMessage(result, location.origin))
    .catch((error) => parent.postMessage({ mode, error: error.message }, location.origin));
} else {
  rootElement.innerHTML = '<h1>시장 스트림 합성 측정</h1><button type="button">측정 시작</button><pre aria-live="polite"></pre><iframe title="측정 중인 시세·수급 패널" style="width:100%;height:650px;border:0"></iframe>';
  const button = rootElement.querySelector("button");
  const output = rootElement.querySelector("pre");
  const frame = rootElement.querySelector("iframe");
  button.addEventListener("click", async () => {
    button.disabled = true;
    const results = [];
    try {
      for (const kind of rounds) {
        output.textContent = `${results.length + 1}/${rounds.length} · ${kind}`;
        results.push(await trial(kind, frame));
      }
      output.textContent = JSON.stringify({ environment: "synthetic-loopback", ...config, summary: summary(results), results }, null, 2);
    } catch (error) {
      output.textContent = error.message;
    } finally {
      frame.src = "about:blank";
      button.disabled = false;
    }
  });
}
