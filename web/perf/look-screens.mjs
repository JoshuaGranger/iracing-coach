// Screenshots of every page in every look (src/looks/looks.ts), for comparing them side by side: Sessions, Setups, a setup detail,
// Tuning, Live and the component kit from web/mock; Race, Runs, Sections, Traces and Results Summary from the committed g2 fixture
// through the real app. Writes `<look>-<page>.<png|jpg>` to `--out`. Fails on a console error. Not a gate.
//
//   node perf/look-screens.mjs [--out <dir>] [--looks current,pitwall,...] [--pages sessions,race,...] [--size 1536x864] [--jpeg 80]
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { buildHarness, buildShell, outRoot, serveShell, shellUrl, webRoot } from "./harness-lib.mjs";

const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback);
const out = path.resolve(option("out", path.join(outRoot, "look-screens")));
const [width, height] = option("size", "1536x864").split("x").map(Number);
const jpeg = option("jpeg", null);
const DRIVER = 1507810;
const LOOKS = ["current", "pitwall", "console", "glass", "programme", "hud"];

const PAGES = {
  sessions: { source: "mock", hash: `/d/${DRIVER}/sessions`, ready: "li.session-row" },
  race: { source: "shell", view: "race", ready: ".ri-dot" },
  runs: { source: "shell", view: "runs", ready: ".ri-dot" },
  sections: { source: "shell", view: "sections", ready: ".ri-sl-tabs" },
  traces: { source: "shell", view: "traces", ready: "[data-analysis-trace-path]", cursor: 0.38 },
  results: { source: "shell", view: "results", ready: ".ri-workspace" },
  setups: { source: "mock", hash: `/d/${DRIVER}/setups`, ready: "main h1" },
  "setup-detail": { source: "mock", hash: `/d/${DRIVER}/setups/1`, ready: "main h1" },
  tuning: { source: "mock", hash: `/d/${DRIVER}/tuning`, ready: "main h1" },
  live: { source: "mock", hash: `/d/${DRIVER}/live`, ready: "[data-metric=position]" },
  kit: { source: "mock", hash: "/__kit", ready: "main h1" },
};
const looks = option("looks", LOOKS.join(",")).split(",");
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

/** The look goes in the page URL's query, before the hash: `/?look=glass#/d/1/sessions`. */
const withLook = (url, look) => url.replace(/\/#/, `/?look=${look}#`);

await mkdir(out, { recursive: true });
const distDir = await buildShell();
const mock = await startMock(distDir);
const { fixture } = await buildHarness("g2");
const shell = await serveShell(distDir, fixture);
const browser = await chromium.launch();
const errors = [];
try {
  for (const look of looks) {
    if (!LOOKS.includes(look)) throw new Error(`unknown look ${look}`);
    for (const name of wanted) {
      const page = PAGES[name];
      if (!page) throw new Error(`unknown page ${name}`);
      const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
      const tab = await context.newPage();
      tab.on("pageerror", (error) => errors.push(`${look} ${name}: ${error}`));
      tab.on("console", (message) => {
        if (message.type() === "error") errors.push(`${look} ${name}: ${message.text()}`);
      });
      const url = page.source === "mock" ? `${mock.origin}/#${page.hash}` : shellUrl(shell.origin, page.view);
      await tab.goto(withLook(url, look));
      await tab.waitForSelector(page.ready, { timeout: 30_000 });
      if (look !== "current") await tab.waitForSelector(`html[data-look="${look}"]`, { state: "attached", timeout: 10_000 });
      await tab.evaluate(() => document.fonts.ready);
      await tab.waitForTimeout(700);
      if (page.cursor) {
        const surface = await tab.locator(".ri-chart-surface").boundingBox();
        if (surface) await tab.mouse.move(surface.x + surface.width * page.cursor, surface.y + surface.height * 0.4);
        await tab.waitForTimeout(250);
      }
      const file = path.join(out, `${look}-${name}.${jpeg ? "jpg" : "png"}`);
      await tab.screenshot(jpeg ? { path: file, type: "jpeg", quality: Number(jpeg) } : { path: file });
      console.log(file);
      await context.close();
    }
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
