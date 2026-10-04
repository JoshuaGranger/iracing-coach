// Screenshots of every page of the app at one size (default 1536x864), for the round's record: Sessions, Setups, a setup detail
// and Live from web/mock, Race, Runs, Sections and Traces from the committed g2 fixture through the real app (the mock carries no
// race data). Writes `<prefix>-<page>.png` to `--out`. Not a gate.
//
//   node perf/final-screens.mjs [--out <dir>] [--prefix final] [--size 1536x864] [--pages sessions,race,...]
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { buildHarness, buildShell, outRoot, serveShell, shellUrl, webRoot } from "./harness-lib.mjs";

const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback);
const out = path.resolve(option("out", path.join(outRoot, "final-screens")));
const prefix = option("prefix", "final");
const [width, height] = option("size", "1536x864").split("x").map(Number);
const DRIVER = 1507810;

const PAGES = {
  sessions: { source: "mock", hash: `/d/${DRIVER}/sessions`, ready: "li.session-row" },
  race: { source: "shell", view: "race", ready: ".ri-dot" },
  runs: { source: "shell", view: "runs", ready: ".ri-dot" },
  sections: { source: "shell", view: "sections", ready: "[data-ri-chart]" },
  traces: { source: "shell", view: "traces", ready: "[data-analysis-trace-path]", cursor: 0.38 },
  setups: { source: "mock", hash: `/d/${DRIVER}/setups`, ready: ".s-car a.s-name" },
  "setup-detail": { source: "mock", hash: `/d/${DRIVER}/setups/1`, ready: "main h1" },
  live: { source: "mock", hash: `/d/${DRIVER}/live`, ready: "[data-metric=position]" },
};
const wanted = option("pages", Object.keys(PAGES).join(",")).split(",");

const free = () =>
  new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
    probe.on("error", reject);
  });

async function startMock(distDir) {
  const port = await free();
  const child = spawn(process.execPath, [path.join(webRoot, "mock", "server.mjs"), "--port", String(port), "--dist", distDir], { cwd: webRoot, stdio: ["ignore", "pipe", "pipe"] });
  child.stderr.on("data", (chunk) => process.stderr.write(chunk));
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      if ((await fetch(`http://127.0.0.1:${port}/health`)).ok) return { child, origin: `http://127.0.0.1:${port}` };
    } catch {
      /* not listening yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  child.kill();
  throw new Error("the mock server did not start");
}

await mkdir(out, { recursive: true });
const distDir = await buildShell();
const mock = await startMock(distDir);
const { fixture } = await buildHarness("g2");
const shell = await serveShell(distDir, fixture);
const browser = await chromium.launch();
const errors = [];
try {
  for (const name of wanted) {
    const page = PAGES[name];
    if (!page) throw new Error(`unknown page ${name}`);
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
    const tab = await context.newPage();
    tab.on("pageerror", (error) => errors.push(`${name}: ${error}`));
    tab.on("console", (message) => {
      if (message.type() === "error") errors.push(`${name}: ${message.text()}`);
    });
    await tab.goto(page.source === "mock" ? `${mock.origin}/#${page.hash}` : shellUrl(shell.origin, page.view));
    await tab.waitForSelector(page.ready, { timeout: 30_000 });
    await tab.waitForTimeout(700);
    if (page.cursor) {
      const surface = await tab.locator(".ri-chart-surface").boundingBox();
      if (surface) await tab.mouse.move(surface.x + surface.width * page.cursor, surface.y + surface.height * 0.4);
      await tab.waitForTimeout(250);
    }
    const file = path.join(out, `${prefix}-${name}.png`);
    await tab.screenshot({ path: file });
    console.log(file);
    await context.close();
  }
} finally {
  await browser.close();
  shell.server.close();
  mock.child.kill();
}
if (errors.length) {
  console.log(`console errors:\n${errors.join("\n")}`);
  process.exitCode = 1;
}
