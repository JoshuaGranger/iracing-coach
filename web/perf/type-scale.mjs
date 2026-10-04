// Type-scale audit: computed font sizes and control heights of the real app, page by page and viewport.
//
//   pnpm -C web build && node perf/type-scale.mjs [--dist dist] [--viewports 1920x1080,1536x864] [--tag before]
//        [--pages sessions,race,runs,sections,traces,setups,live,setup,baseline,tuning] [--check] [--shots <dir>]
//
// For every page it records the smallest and median computed size of body text, secondary text, table/rail/readout numbers
// and tab/button labels, the shortest control, the smallest text on the page and any canvas font the cursor assigns. With
// `--check` it fails (exit code 1) when a group is under the owner's minimums (2026-10-03: body 16 px, secondary 14 px,
// numbers 15 px, tab and button labels 15 px, controls 36 px tall on desktop and 44 px on a phone), or the page logs an error.
// Sessions, Setups and Live come from web/mock; the investigation views from the committed 82-lap g2 fixture, served through the
// real app (the mock carries no race data). Screenshots and <tag>-report.json go to `--shots` (default <repo>/target/W8/type-scale).
// Not part of the G2 gate.
import { chromium } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { buildHarness, serveShell, shellUrl } from "./harness-lib.mjs";

const perfDir = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(perfDir, "..");
const repoRoot = path.resolve(webRoot, "..");

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] !== undefined ? args[index + 1] : fallback;
};
const DIST = option("dist", "dist");
const VIEWPORTS = option("viewports", "1920x1080,1536x864").split(",").map((text) => {
  const [width, height] = text.split("x").map(Number);
  return { name: text, width, height };
});
const TAG = option("tag", "run");
const SHOTS = path.resolve(option("shots", path.join(repoRoot, "target", "W8", "type-scale")));
const CHECK = args.includes("--check");
const DRIVER = 1507810;

// Sessions, Setups and Live come from web/mock. The investigation views come from the committed 82-lap g2 fixture served
// through the real app (perf/harness-lib.mjs), because the mock carries no race data.
const PAGES = {
  sessions: { source: "mock", hash: `/d/${DRIVER}/sessions`, ready: "li.session-row" },
  race: { source: "shell", view: "race", ready: ".ri-dot" },
  runs: { source: "shell", view: "runs", ready: ".ri-dot" },
  sections: { source: "shell", view: "sections", ready: "[data-ri-chart]" },
  traces: { source: "shell", view: "traces", ready: "[data-analysis-trace-path]" },
  setups: { source: "mock", hash: `/d/${DRIVER}/setups`, ready: ".s-car a.s-name" },
  live: { source: "mock", hash: `/d/${DRIVER}/live`, ready: "[data-metric=position]" },
  setup: { source: "mock", hash: `/d/${DRIVER}/setups/1`, ready: "table tbody tr, .value-group" },
  baseline: { source: "mock", hash: `/d/${DRIVER}/setups/baseline`, ready: "main h1" },
  tuning: { source: "mock", hash: `/d/${DRIVER}/tuning/an-103`, ready: ".track-map, .map-empty" },
};
const WANTED = option("pages", Object.keys(PAGES).join(",")).split(",");

/** Selector groups. A group's text size is read from the elements that own text; a control's height is its box. */
const GROUPS = {
  body: { min: 16, selector: "body, .row-title, .note-body, .page p, .empty p, .notice, .ri-empty strong, .ri-comparison-title, .ri-card p, .ri-action p, .ri-channel-label, .ri-heading strong, .ri-panel-label strong, dt" },
  secondary: {
    min: 14,
    selector: [
      ".row-meta, .sub, .event-meta, .chip, .eyebrow, .count, .tile-label, .tile-secondary, .tile-unit, label > span:first-child, .field-error, .inline-error, .note-meta",
      ".ri-muted, .ri-fine, .ri-heading span, .ri-comparison-laps, .ri-legend, .ri-run-legend, .ri-key small, .ri-timing, .ri-foot, .ri-rail-foot, .ri-toolbox-foot, .ri-run-head, .ri-section-head, .ri-section-range, .ri-map-legend",
      ".ri-axis, .ri-axis-title, .ri-bracket-label, .ri-event-label, .ri-y-axis span, .ri-x-axis span, .ri-zone, .ri-row-unit, .ri-chip, .ri-rail-cols span, .ri-count-chip, .ri-unavailable, .ri-channel-label small, .ri-tabs, .crumbs, .driver-name",
    ].join(", "),
  },
  numbers: { min: 15, selector: "td, .ri-tm, .ri-dl, .ri-fu, .ri-n, .ri-kv strong, .ri-run-range, .ri-inc-chip, .ri-pit-cell, .ri-readings output, .ri-readout, .ri-cursor-readout, .ri-timing b, .ri-median, .ri-comparison strong, .tile-primary, dd, .stepper output, .ri-section-id, .turn-text, .map-handle text, .ri-map-label text" },
  controls: {
    min: 15,
    height: true,
    selector: "button, .btn, .nav-link, select, input:not([type=checkbox]):not([type=radio]):not([type=range]), .ri-tab, .ri-button, .ri-pill-button, .ri-pill-toggle, .ri-icon-pill, .ri-pqr-seg, .ri-text-button, .ri-toggle, .ri-lap-row, .ri-section, .ri-comparison, label.check, .ri-filters label, .crumbs a",
  },
};

const free = () => new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.listen(0, "127.0.0.1", () => {
    const { port } = probe.address();
    probe.close(() => resolve(port));
  });
  probe.on("error", reject);
});

async function startMock() {
  const port = await free();
  const child = spawn(process.execPath, [path.join(webRoot, "mock", "server.mjs"), "--port", String(port), "--dist", DIST], { cwd: webRoot, stdio: ["ignore", "pipe", "pipe"] });
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

/** Runs in the page. Returns per-group sizes and heights, the smallest text on screen, and the document's fit. */
function measureInPage({ groups }) {
  const visible = (element) => {
    const box = element.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return false;
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" && style.opacity !== "0";
  };
  const owns = (element) => [...element.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim().length > 0);
  const describe = (element) => `${element.tagName.toLowerCase()}${element.className && typeof element.className === "string" ? "." + element.className.trim().split(/\s+/).slice(0, 2).join(".") : element.className?.baseVal ? "." + element.className.baseVal.trim().split(/\s+/).slice(0, 2).join(".") : ""}`;
  /** CSS pixels on screen: an SVG text is scaled by its view box. */
  const effectivePx = (element) => {
    const size = parseFloat(getComputedStyle(element).fontSize);
    if (element instanceof SVGTextElement) {
      const matrix = element.getScreenCTM();
      return matrix ? size * Math.hypot(matrix.a, matrix.b) : size;
    }
    return size;
  };
  const median = (values) => {
    if (values.length === 0) return null;
    const sorted = [...values].sort((a, b) => a - b);
    return sorted[sorted.length >> 1];
  };
  const result = { groups: {}, smallest: [], root: parseFloat(getComputedStyle(document.documentElement).fontSize), body: parseFloat(getComputedStyle(document.body).fontSize) };
  for (const [name, group] of Object.entries(groups)) {
    const sizes = [];
    const heights = [];
    const offenders = [];
    for (const element of document.querySelectorAll(group.selector)) {
      if (!visible(element)) continue;
      if (element !== document.body && !owns(element) && !group.height) continue;
      const px = effectivePx(element);
      if (element === document.body || owns(element)) {
        sizes.push(px);
        if (px < group.min - 0.01) offenders.push(`${describe(element)} ${px.toFixed(1)}px "${element.textContent.trim().slice(0, 24)}"`);
      }
      if (group.height) heights.push(element.getBoundingClientRect().height);
    }
    result.groups[name] = {
      count: sizes.length,
      minPx: sizes.length ? Math.min(...sizes) : null,
      medianPx: median(sizes),
      maxPx: sizes.length ? Math.max(...sizes) : null,
      minHeight: heights.length ? Math.min(...heights) : null,
      medianHeight: median(heights),
      offenders: [...new Set(offenders)].slice(0, 6),
    };
  }
  // The smallest text anywhere on the page, grouped by size.
  const bySize = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim();
    const element = node.parentElement;
    if (!text || !element || !visible(element) || element.closest("script, style, .visually-hidden, .ri-sr")) continue;
    const size = Math.round(effectivePx(element) * 10) / 10;
    const entry = bySize.get(size) ?? { px: size, count: 0, examples: [] };
    entry.count += 1;
    if (entry.examples.length < 3) entry.examples.push(`${describe(element)} "${text.slice(0, 22)}"`);
    bySize.set(size, entry);
  }
  result.smallest = [...bySize.values()].sort((a, b) => a.px - b.px).slice(0, 5);
  result.fit = { scrollHeight: document.documentElement.scrollHeight, innerHeight: window.innerHeight, scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth };
  return result;
}

const mock = await startMock();
const needShell = WANTED.some((name) => PAGES[name]?.source === "shell");
let shell = null;
if (needShell) {
  const { fixture } = await buildHarness("g2");
  shell = await serveShell(path.resolve(webRoot, DIST), fixture);
}
await mkdir(SHOTS, { recursive: true });
const browser = await chromium.launch();
const report = { tag: TAG, dist: DIST, results: [] };
let failures = 0;
try {
  for (const viewport of VIEWPORTS) {
    for (const name of WANTED) {
      const spec = PAGES[name];
      if (!spec) throw new Error(`unknown page ${name}`);
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: 1 });
      await context.addInitScript(() => {
        const fonts = (window.__canvasFonts = new Set());
        const descriptor = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, "font");
        Object.defineProperty(CanvasRenderingContext2D.prototype, "font", {
          configurable: true,
          get() { return descriptor.get.call(this); },
          set(value) { fonts.add(String(value)); descriptor.set.call(this, value); },
        });
      });
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (error) => errors.push(String(error)));
      // A console line for a failed load carries no URL; name the response instead.
      page.on("console", (message) => { if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) errors.push(message.text()); });
      // A 404 is an answer (nothing stored yet for a draft or an annotation); other failed loads are errors.
      page.on("response", (response) => { if (response.status() >= 400 && response.status() !== 404) errors.push(`${response.status()} ${new URL(response.url()).pathname}`); });
      await page.goto(spec.source === "shell" ? shellUrl(shell.origin, spec.view) : `${mock.origin}/#${spec.hash}`);
      await page.waitForSelector(spec.ready, { timeout: 30_000 });
      if (name === "live") await page.waitForFunction(() => /P\d/.test(document.querySelector("[data-metric=position]")?.textContent ?? ""), null, { timeout: 15_000 }).catch(() => undefined);
      await page.waitForTimeout(500);
      if (name === "traces") {
        const surface = page.locator(".ri-chart-surface");
        const box = await surface.boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.5);
          await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
          await page.waitForTimeout(300);
        }
      }
      if (name === "sections") {
        const chart = page.locator("[data-ri-chart]").first();
        const box = await chart.boundingBox();
        if (box) {
          await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
          await page.waitForTimeout(200);
        }
      }
      const measured = await page.evaluate(measureInPage, { groups: GROUPS });
      measured.canvasFonts = await page.evaluate(() => [...(window.__canvasFonts ?? [])]);
      measured.errors = errors;
      const file = `${TAG}-${viewport.name}-${name}.png`;
      await page.screenshot({ path: path.join(SHOTS, file) });
      const phone = viewport.width < 700;
      const verdicts = [];
      for (const [group, spec2] of Object.entries(GROUPS)) {
        const data = measured.groups[group];
        if (data.count > 0 && data.minPx < spec2.min - 0.01) verdicts.push(`${group} text ${data.minPx.toFixed(1)}px < ${spec2.min}px (${data.offenders.join("; ")})`);
        const minimumHeight = phone ? 44 : 36;
        if (spec2.height && data.minHeight !== null && data.minHeight < minimumHeight - 0.5) verdicts.push(`control height ${data.minHeight.toFixed(1)}px < ${minimumHeight}px`);
      }
      for (const font of measured.canvasFonts) {
        const size = parseFloat(font);
        if (size < 15 - 0.01) verdicts.push(`canvas font ${font}`);
      }
      if (measured.fit.scrollWidth > measured.fit.innerWidth + 1) verdicts.push(`horizontal scroll ${measured.fit.scrollWidth} > ${measured.fit.innerWidth}`);
      if (errors.length) verdicts.push(`console errors: ${errors.join(" | ").slice(0, 160)}`);
      measured.verdicts = verdicts;
      report.results.push({ viewport: viewport.name, page: name, shot: file, ...measured });
      const row = (group) => {
        const data = measured.groups[group];
        return data.count === 0 && data.minHeight === null ? "-" : `${data.minPx === null ? "-" : data.minPx.toFixed(1)}/${data.medianPx === null ? "-" : data.medianPx.toFixed(1)}${data.minHeight !== null ? ` h${data.minHeight.toFixed(0)}` : ""}`;
      };
      console.log(`${viewport.name} ${name.padEnd(8)} root ${measured.root}px  body ${row("body")}  secondary ${row("secondary")}  numbers ${row("numbers")}  controls ${row("controls")}  smallest ${measured.smallest[0] ? measured.smallest[0].px : "-"}px  fit ${measured.fit.scrollHeight}/${measured.fit.innerHeight}${measured.canvasFonts.length ? `  canvas ${measured.canvasFonts.join(",")}` : ""}`);
      if (CHECK && verdicts.length) {
        failures += 1;
        for (const verdict of verdicts) console.log(`  FAIL ${verdict}`);
      }
      await context.close();
    }
  }
} finally {
  await browser.close();
  mock.child.kill();
  shell?.server.close();
}
await writeFile(path.join(SHOTS, `${TAG}-report.json`), JSON.stringify(report, null, 2));
console.log(`report: ${path.join(SHOTS, `${TAG}-report.json`)}`);
if (CHECK) {
  console.log(failures === 0 ? "type scale ok" : `${failures} pages under the type-scale minimums`);
  process.exitCode = failures === 0 ? 0 : 1;
}
