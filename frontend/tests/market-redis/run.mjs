// Run on Linux with local Docker, google-chrome, backend/.venv and frontend/node_modules.
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer as netServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { setTimeout as wait } from "node:timers/promises";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

const run = promisify(execFile);
const loopback = "127.0.0.1";
const root = fileURLToPath(new URL("../../../", import.meta.url));
const scratchRoot = resolve(tmpdir());
const scratch = await mkdtemp(join(scratchRoot, "desk-market-redis-"));
const container = `desk-market-test-${randomUUID()}`;
const account = { username: "fixture@example.test", password: randomUUID() };
const pagePath = "/__market_redis_fixture";
const docker = async (...args) => (await run("docker", ["--host", "unix:///var/run/docker.sock", ...args], {
  env: { PATH: process.env.PATH }, timeout: 45_000,
})).stdout.trim();
let backend, chrome, vite, socket;
let containerStarted = false;
let backendLog = "";
let chromeLog = "";

async function eventually(probe, label, timeoutMs = 12_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const result = await probe();
      if (result) return result;
    } catch (error) {
      lastError = error;
    }
    await wait(100);
  }
  throw Error(`Timed out: ${label}`, { cause: lastError });
}

function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = netServer().once("error", reject).listen(0, loopback, () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

function start(command, args, options) {
  const child = spawn(command, args, options);
  child.on("error", error => {
    backendLog += `${command}: ${error.message}\n`;
  });
  return child;
}

async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null || !child.pid) return;
  const exited = new Promise(resolve => child.once("exit", resolve));
  child.kill();
  await Promise.race([exited, wait(3000)]);
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
    await Promise.race([exited, wait(1500)]);
  }
}

async function verify() {
  await writeFile(join(scratch, "account.json"), JSON.stringify(account), { mode: 0o600 });
  const redisPort = await unusedPort();
  const redisBinding = `${loopback}:${redisPort}`;
  await docker("run", "-d", "--name", container, "-p", `${redisBinding}:6379`,
    "redis:7-alpine", "--save", "", "--appendonly", "no");
  containerStarted = true;
  if (await docker("port", container, "6379/tcp") !== redisBinding) {
    throw Error("Redis did not bind to the expected loopback port");
  }
  await eventually(async () => await docker("exec", container, "redis-cli", "PING") === "PONG", "Redis");

  const backPort = await unusedPort();
  const backendDir = join(root, "backend");
  // Pass only the OS search path and fixture options. In particular, no application or CI credentials.
  const env = { PATH: process.env.PATH, PYTHONPATH: backendDir, DESK_FAKE_RUNS: "true" };
  backend = start(join(backendDir, ".venv", "bin", "python"),
    ["-B", fileURLToPath(new URL("./backend.py", import.meta.url)), scratch, String(backPort), redisPort],
    { cwd: scratch, env, stdio: ["ignore", "ignore", "pipe"] });
  backend.stderr.on("data", chunk => { backendLog = (backendLog + chunk).slice(-6000); });
  await eventually(async () => (await fetch(`http://${loopback}:${backPort}/api/health`,
    { signal: AbortSignal.timeout(1000) })).ok, "fixture API");

  // Let the first alert REST request finish before the real SSE ready event can trigger a catch-up request.
  let startStream;
  const streamStarted = new Promise(resolve => { startStream = resolve; });
  vite = await createServer({
    root: join(root, "frontend"),
    configFile: false,
    cacheDir: join(scratch, "vite"),
    server: { host: loopback, port: await unusedPort(), strictPort: true, hmr: false,
      proxy: { "/api": { target: `http://${loopback}:${backPort}`, changeOrigin: false } } },
    plugins: [{ name: "market-redis-fixture", configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.method === "POST" && request.url === `${pagePath}/start-stream`) {
          startStream();
          response.writeHead(204).end();
          return;
        }
        if (request.url?.startsWith("/api/stream/market?")) {
          void streamStarted.then(() => {
            if (!response.destroyed) next();
          });
          return;
        }
        if (request.url !== pagePath) return next();
        response.setHeader("Content-Type", "text/html; charset=utf-8");
        response.end(`<!doctype html><html lang="en"><meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <title>Redis market stream fixture</title><div id="root"></div>
          <script>window.marketFixture=${JSON.stringify(account)}</script>
          <script type="module" src="/tests/market-redis/scenario.jsx"></script></html>`);
      });
    } }],
  });
  await vite.listen();
  // #690 flaky: on a busy runner Chrome once wrote no DevToolsActivePort within 12 s. Give it 30 s, and if it
  // still has not, stop it and start once more on a fresh profile.
  const launchChrome = async attempt => {
    const profile = join(scratch, `chrome-${attempt}`);
    chrome = start("google-chrome", ["--headless=new", "--disable-dev-shm-usage", "--disable-background-networking",
      "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
      "--no-first-run", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
      `--user-data-dir=${profile}`, "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1", "about:blank"],
      { stdio: ["ignore", "ignore", "pipe"] });
    chrome.stderr.on("data", chunk => { chromeLog = (chromeLog + chunk).slice(-4000); });
    return eventually(async () => (await readFile(join(profile, "DevToolsActivePort"), "utf8"))
      .split(/\r?\n/)[0], `Chrome DevTools (attempt ${attempt})`, 30_000);
  };
  let debugPort;
  try {
    debugPort = await launchChrome(1);
  } catch (error) {
    console.error(`${error.message}; starting Chrome once more`);
    await stop(chrome);
    debugPort = await launchChrome(2);
  }
  const pageDebuggerUrl = await eventually(async () => {
    const response = await fetch(`http://${loopback}:${debugPort}/json/list`, { signal: AbortSignal.timeout(2500) });
    if (!response.ok) throw Error(`Chrome target listing returned ${response.status}`);
    const targets = await response.json();
    return targets.find(target => target.type === "page" && target.webSocketDebuggerUrl)?.webSocketDebuggerUrl;
  }, "Chrome page target", 20_000);
  socket = new WebSocket(pageDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  const outstanding = new Map();
  const streams = new Set();
  const ready = [];
  const degraded = [];
  const errors = [];
  let id = 0;
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    const params = message.params;
    if (message.id) {
      outstanding.get(message.id)?.(message);
      outstanding.delete(message.id);
    } else if (message.method === "Network.requestWillBeSent" && params.request.url.includes("/api/stream/market?")) {
      streams.add(params.requestId);
    } else if (message.method === "Network.eventSourceMessageReceived" && streams.has(params.requestId)) {
      if (params.eventName === "ready") ready.push(JSON.parse(params.data));
      if (params.eventName === "degraded") degraded.push(JSON.parse(params.data));
    } else if (message.method === "Runtime.exceptionThrown") {
      errors.push(params.exceptionDetails.exception?.description ?? params.exceptionDetails.text);
    }
  });
  async function command(method, params = {}) {
    const commandId = ++id;
    try {
      return await Promise.race([new Promise((resolve, reject) => {
        outstanding.set(commandId, reply => reply.error ? reject(Error(`${method}: ${reply.error.message}`)) : resolve(reply.result));
        socket.send(JSON.stringify({ id: commandId, method, params }));
      }), wait(4000).then(() => { throw Error(`Chrome command timed out: ${method}`); })]);
    } finally {
      outstanding.delete(commandId);
    }
  }
  await command("Runtime.enable");
  await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 960, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: `http://${loopback}:${vite.httpServer.address().port}${pagePath}` });

  let stopped = false;
  let restarted = false;
  let result;
  const deadline = Date.now() + 65_000;
  while (!result && Date.now() < deadline) {
    const response = await command("Runtime.evaluate", {
      expression: "({stage:document.body?.dataset.stage,result:document.body?.dataset.result})", returnByValue: true,
    });
    const page = response.result.value;
    if (page?.result) result = JSON.parse(page.result);
    if (page?.stage === "stop-redis" && !stopped) {
      await docker("stop", "--time", "2", container);
      stopped = true;
    }
    if (page?.stage === "start-redis" && stopped && !restarted) {
      await docker("start", container);
      if (await docker("port", container, "6379/tcp") !== redisBinding) {
        throw Error("Redis loopback binding changed on restart");
      }
      await eventually(async () => await docker("exec", container, "redis-cli", "PING") === "PONG", "restarted Redis");
      restarted = true;
    }
    await wait(75);
  }
  const down = degraded.findIndex(frame => frame.reason === "bus_down"
    && [...frame.degraded].sort().join(",") === "alert,flow,futures");
  console.log(JSON.stringify({ result, stopped, restarted, sseConnections: streams.size, ready, degraded, errors }));
  if (!result?.ok) throw Error(result?.error ?? "Market Redis browser scenario did not finish");
  if (!result.staleAlertRest?.markerRetained) throw Error("Delayed REST regression check did not run");
  if (!stopped || !restarted || streams.size !== 1 || errors.length || ready.length !== 1
      || ready[0].bus !== "redis" || down < 0 || !degraded.slice(down + 1).some(frame => !frame.degraded.length)) {
    throw Error("Redis outage did not drive channel-specific REST fallback and SSE recovery");
  }
}

try {
  await verify();
} catch (error) {
  console.error(error);
  if (backendLog) console.error(backendLog);
  if (chromeLog) console.error(chromeLog);
  process.exitCode = 1;
} finally {
  const cleanup = await Promise.allSettled([
    Promise.resolve().then(() => { socket?.close(); }),
    stop(chrome),
    Promise.resolve().then(() => vite?.close()),
    stop(backend),
    docker("rm", "-f", container).catch(error => { if (containerStarted) throw error; }),
  ]);
  for (const outcome of cleanup) {
    if (outcome.status === "rejected") {
      console.error("Fixture cleanup:", outcome.reason);
      process.exitCode = 1;
    }
  }
  if (dirname(scratch) === scratchRoot) await rm(scratch, { recursive: true, force: true });
}
