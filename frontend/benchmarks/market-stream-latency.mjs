import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const quick = process.argv.includes("--quick");
const scale = quick ? 0.06 : 1;
const collectorDelayMs = 40;
const channels = ["quote", "flow"];
const phases = [0.1, 1.35, 2.7];
const offsetsMs = phases.map((phase) => Math.round(phase * 30_000 * scale));
const observers = new Map();
let trial;

function json(res, data, status = 200) {
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  res.end(JSON.stringify(data));
}

function publish(res, event, data) {
  const id = data.event_id ? `id: ${data.event_id}\n` : "";
  res.write(`${id}event: ${event}\ndata: ${JSON.stringify({ v: 1, ...data })}\n\n`);
}

function values(index, date) {
  return { quote: 500 + index, flow: 2600 + index, date };
}

function quote(value) {
  return { ticker: "AAPL", price: value.quote, source: "kis", fetched_at: value.at };
}

function flow(value) {
  return {
    market: "KOSPI", interval: "1d", session: "open", supported: true, source: "mock",
    index: { price: value.flow }, fetched_at: value.at,
    records: [{ date: value.date, provisional: true, in_progress: true, investors: {}, institution_breakdown: {} }],
  };
}

function publishValues(res, requested, value, sample) {
  for (const channel of channels) {
    if (!requested.includes(channel)) continue;
    publish(res, channel, {
      kind: channel,
      key: channel === "quote" ? "AAPL" : "KOSPI:1d",
      event_id: `${sample.index}-${channel}`,
      source: channel === "quote" ? "kis" : "toss_investors",
      source_at: new Date(sample.sourceAt).toISOString(),
      observed_at: value.at,
      payload: channel === "quote" ? quote(value) : flow(value),
    });
  }
}

function schedule(run) {
  offsetsMs.forEach((offset, i) => {
    setTimeout(() => {
      const sourceAt = Date.now();
      setTimeout(() => {
        if (trial !== run) return;
        const observedAt = Date.now();
        const sample = { index: i + 1, sourceAt, observedAt };
        run.value = { ...values(sample.index, run.value.date), at: new Date(observedAt).toISOString() };
        run.samples.push(sample);
        for (const [res, requested] of observers) publishValues(res, requested, run.value, sample);
      }, collectorDelayMs);
    }, offset);
  });
}

function handle(req, res, next) {
  const { pathname, searchParams } = new URL(req.url ?? "/", "http://localhost");
  if (pathname === "/") {
    res.setHeader("Content-Type", "text/html; charset=utf-8");
    res.end('<!doctype html><meta charset="utf-8"><title>시장 스트림 합성 측정</title><div id="root"></div><script type="module" src="/benchmarks/market-stream-latency.jsx"></script>');
    return;
  }
  if (pathname === "/benchmark/config") return json(res, { scale, offsetsMs, collectorDelayMs });
  if (pathname === "/benchmark/prepare" && req.method === "POST") {
    const mode = searchParams.get("mode");
    if (mode !== "poll" && mode !== "sse") return json(res, { error: "invalid mode" }, 400);
    for (const client of observers.keys()) client.end();
    observers.clear();
    const now = new Date().toISOString();
    trial = { mode, value: { ...values(0, now.slice(0, 10)), at: now }, samples: [], rest: { quote: 0, flow: 0 } };
    return json(res, { mode });
  }
  if (!trial) return next();
  if (pathname === "/benchmark/start" && req.method === "POST") {
    if (trial.started) return json(res, { error: "already started" }, 409);
    trial.started = true;
    schedule(trial);
    return json(res, { mode: trial.mode });
  }
  if (pathname === "/benchmark/results") return json(res, { samples: trial.samples, rest: trial.rest });
  if (pathname === "/api/stream/market") {
    if (trial.mode === "poll") return json(res, { detail: "SSE disabled for polling baseline" }, 503);
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
    const requested = searchParams.get("channels")?.split(",") ?? [];
    publishValues(res, requested, trial.value, trial.samples.at(-1) ?? { index: 0, sourceAt: Date.parse(trial.value.at) });
    publish(res, "ready", { channels: requested, bus: "local", degraded: [] });
    observers.set(res, requested);
    const heartbeat = setInterval(() => publish(res, "heartbeat", { t: new Date().toISOString(), degraded: [] }), 15_000);
    res.on("close", () => {
      observers.delete(res);
      clearInterval(heartbeat);
    });
    return;
  }
  if (pathname === "/api/estimates") {
    if (trial.started) trial.rest.quote++;
    return json(res, { items: [{ ...quote(trial.value), state: "live", session: "regular", quote_source: "kis" }] });
  }
  if (pathname === "/api/market/flow") {
    if (trial.started) trial.rest.flow++;
    return json(res, flow(trial.value));
  }
  if (pathname === "/api/alerts/today") return json(res, { date: trial.value.date, items: [] });
  if (pathname.startsWith("/api/")) return json(res, { detail: "outside synthetic benchmark" }, 404);
  next();
}

const server = await createServer({
  root: fileURLToPath(new URL("../", import.meta.url)),
  configFile: false,
  cacheDir: "node_modules/.vite-market-latency",
  plugins: [{ name: "synthetic-market-stream", configureServer(vite) { vite.middlewares.use(handle); } }],
  server: { host: "127.0.0.1", port: 43_173, strictPort: true, hmr: false },
});
await server.listen();
server.printUrls();
