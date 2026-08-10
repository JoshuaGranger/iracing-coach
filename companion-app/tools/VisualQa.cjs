const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const baseUrl = process.argv[2] || "http://127.0.0.1:5284/";
const outputDirectory = path.resolve(
  process.argv[3] || path.join(__dirname, "..", "artifacts", "qa", "v0.15.0"),
);
const reportPath = path.join(outputDirectory, "visual-qa-report.json");
const suiteMode = process.argv[4] || "all";
const viewports = [
  { width: 1280, height: 720 },
  { width: 1920, height: 1080 },
];

const report = {
  schemaVersion: 1,
  suite: "iRacing Coach v0.15.0 visual QA",
  baseUrl,
  startedAt: new Date().toISOString(),
  completedAt: null,
  status: "running",
  environment: {},
  summary: {},
  checks: [],
  defects: [],
  consoleErrors: [],
  networkErrors: [],
  webSockets: [],
  screenshots: [],
};

let browser;
let screenshotSequence = 0;

process.once("exit", () => {
  if (report.status !== "running") return;
  report.status = "interrupted";
  report.completedAt = new Date().toISOString();
  try { writeReport(); } catch { }
});

function ensureDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true });
}

function writeReport() {
  ensureDirectory(outputDirectory);
  const passed = report.checks.filter((check) => check.status === "pass").length;
  const failed = report.checks.filter((check) => check.status === "fail").length;
  const warnings = report.checks.filter((check) => check.status === "warning").length;
  report.summary = {
    total: report.checks.length,
    passed,
    failed,
    warnings,
    defects: report.defects.length,
    screenshots: report.screenshots.length,
  };
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
}

function round(value) {
  return Number.isFinite(value) ? Math.round(value * 100) / 100 : value;
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function addCheck(run, name, status, details = {}) {
  const check = {
    viewport: run.viewportName,
    page: run.pageName,
    name,
    status,
    ...details,
  };
  report.checks.push(check);
  if (status === "fail") {
    report.defects.push({
      id: `VQA-${String(report.defects.length + 1).padStart(3, "0")}`,
      severity: details.severity || "medium",
      viewport: run.viewportName,
      page: run.pageName,
      check: name,
      selector: details.selector || null,
      message: details.message || name,
      measurements: details.measurements || null,
    });
  }
  writeReport();
  return check;
}

async function attempt(run, name, operation, options = {}) {
  try {
    const value = await operation();
    if (!options.manual) addCheck(run, name, "pass", options.details || {});
    return value;
  } catch (error) {
    addCheck(run, name, options.warning ? "warning" : "fail", {
      severity: options.severity || "high",
      selector: options.selector,
      message: error.message,
    });
    return undefined;
  }
}

async function capture(page, run, name) {
  const viewportDirectory = path.join(outputDirectory, run.viewportName);
  ensureDirectory(viewportDirectory);
  screenshotSequence += 1;
  const filename = `${String(screenshotSequence).padStart(3, "0")}-${slug(name)}.png`;
  const absolutePath = path.join(viewportDirectory, filename);
  await page.screenshot({ path: absolutePath, animations: "disabled" });
  const relativePath = path.relative(outputDirectory, absolutePath).replaceAll("\\", "/");
  report.screenshots.push({ viewport: run.viewportName, page: run.pageName, name, path: relativePath });
  writeReport();
  return absolutePath;
}

async function captureFailureState(page, run, name) {
  const diagnosticsDirectory = path.join(outputDirectory, "diagnostics");
  ensureDirectory(diagnosticsDirectory);
  const prefix = `${run.viewportName}-${slug(name)}`;
  const state = await page.evaluate(() => ({
    capturedAt: new Date().toISOString(),
    title: document.title,
    url: location.href,
    readyState: document.readyState,
    blazor: Boolean(window.Blazor),
    appShell: Boolean(document.querySelector(".app-shell")),
    appShellClass: document.querySelector(".app-shell")?.className || null,
    reconnectOpen: Boolean(document.querySelector("#components-reconnect-modal[open]")),
    bodyText: document.body.innerText.slice(0, 20_000),
    bodyHtml: document.body.innerHTML.slice(0, 30_000),
    buttons: [...document.querySelectorAll("button")].slice(0, 150).map((button) => ({
      text: button.innerText.trim(),
      ariaLabel: button.getAttribute("aria-label"),
      visible: Boolean(button.offsetWidth || button.offsetHeight || button.getClientRects().length),
    })),
  })).catch((error) => ({ evaluationError: error.message }));
  fs.writeFileSync(path.join(diagnosticsDirectory, `${prefix}.json`), JSON.stringify(state, null, 2));
  try {
    const screenshotPath = path.join(diagnosticsDirectory, `${prefix}.png`);
    await page.screenshot({ path: screenshotPath, animations: "disabled", timeout: 8_000 });
    report.screenshots.push({
      viewport: run.viewportName,
      page: run.pageName,
      name: `${name} failure state`,
      path: path.relative(outputDirectory, screenshotPath).replaceAll("\\", "/"),
    });
  } catch (error) {
    addCheck(run, `${name} failure screenshot captured`, "warning", {
      severity: "low",
      message: `Screenshot capture also failed: ${error.message}`,
    });
  }
  writeReport();
  return state;
}

async function elementMetrics(page, selector) {
  return page.locator(selector).first().evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const viewport = { width: innerWidth, height: innerHeight };
    let clipLeft = 0;
    let clipTop = 0;
    let clipRight = viewport.width;
    let clipBottom = viewport.height;
    let parent = element.parentElement;
    while (parent) {
      const parentStyle = getComputedStyle(parent);
      const overflowX = parentStyle.overflowX;
      const overflowY = parentStyle.overflowY;
      if (overflowX !== "visible" || overflowY !== "visible") {
        const parentRect = parent.getBoundingClientRect();
        if (overflowX !== "visible") {
          clipLeft = Math.max(clipLeft, parentRect.left);
          clipRight = Math.min(clipRight, parentRect.right);
        }
        if (overflowY !== "visible") {
          clipTop = Math.max(clipTop, parentRect.top);
          clipBottom = Math.min(clipBottom, parentRect.bottom);
        }
      }
      parent = parent.parentElement;
    }
    const visibleWidth = Math.max(0, Math.min(rect.right, clipRight) - Math.max(rect.left, clipLeft));
    const visibleHeight = Math.max(0, Math.min(rect.bottom, clipBottom) - Math.max(rect.top, clipTop));
    return {
      rect: {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        right: rect.right,
        bottom: rect.bottom,
      },
      clientWidth: element.clientWidth,
      clientHeight: element.clientHeight,
      scrollWidth: element.scrollWidth,
      scrollHeight: element.scrollHeight,
      overflowX: style.overflowX,
      overflowY: style.overflowY,
      display: style.display,
      visibility: style.visibility,
      visibleWidth,
      visibleHeight,
      clippedX: Math.max(0, rect.width - visibleWidth),
      clippedY: Math.max(0, rect.height - visibleHeight),
      inViewport: rect.right >= 0 && rect.bottom >= 0 && rect.left <= viewport.width && rect.top <= viewport.height,
    };
  });
}

async function auditLayout(page, run, selectors, fixedScreen = false) {
  const root = await page.evaluate(() => {
    const documentElement = document.documentElement;
    const body = document.body;
    const workspace = document.querySelector("#main-content");
    return {
      viewport: { width: innerWidth, height: innerHeight },
      document: {
        clientWidth: documentElement.clientWidth,
        clientHeight: documentElement.clientHeight,
        scrollWidth: documentElement.scrollWidth,
        scrollHeight: documentElement.scrollHeight,
      },
      body: {
        clientWidth: body.clientWidth,
        clientHeight: body.clientHeight,
        scrollWidth: body.scrollWidth,
        scrollHeight: body.scrollHeight,
      },
      workspace: workspace ? {
        clientWidth: workspace.clientWidth,
        clientHeight: workspace.clientHeight,
        scrollWidth: workspace.scrollWidth,
        scrollHeight: workspace.scrollHeight,
        scrollTop: workspace.scrollTop,
      } : null,
    };
  });
  const horizontalOverflow = Math.max(
    root.document.scrollWidth - root.document.clientWidth,
    root.body.scrollWidth - root.body.clientWidth,
  );
  addCheck(run, "No page-level horizontal overflow", horizontalOverflow <= 1 ? "pass" : "fail", {
    severity: "high",
    selector: "html, body",
    message: `Page exceeds the viewport by ${round(horizontalOverflow)} px horizontally.`,
    measurements: root,
  });
  if (fixedScreen) {
    const workspaceVerticalOverflow = root.workspace
      ? root.workspace.scrollHeight - root.workspace.clientHeight
      : Number.POSITIVE_INFINITY;
    addCheck(run, "Fixed-screen workspace fits viewport height", workspaceVerticalOverflow <= 1 ? "pass" : "fail", {
      severity: "high",
      selector: "#main-content",
      message: `Fixed workspace exceeds its viewport by ${round(workspaceVerticalOverflow)} px vertically.`,
      measurements: root.workspace,
    });
  }
  for (const selector of selectors) {
    const count = await page.locator(selector).count();
    if (count === 0) {
      addCheck(run, `Key element exists: ${selector}`, "fail", {
        severity: "high",
        selector,
        message: "Required key element was not rendered.",
      });
      continue;
    }
    const metrics = await elementMetrics(page, selector);
    const clipped = metrics.clippedX > 1 || metrics.clippedY > 1 || !metrics.inViewport;
    addCheck(run, `Key element is not clipped: ${selector}`, clipped ? "fail" : "pass", {
      severity: "medium",
      selector,
      message: `Element clipping is ${round(metrics.clippedX)} px horizontal and ${round(metrics.clippedY)} px vertical.`,
      measurements: metrics,
    });
  }
  return root;
}

async function rect(page, selector) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`${selector} does not have a visible bounding box.`);
  return Object.fromEntries(Object.entries(box).map(([key, value]) => [key, round(value)]));
}

function rectDrift(first, second) {
  return Math.max(
    Math.abs(first.x - second.x),
    Math.abs(first.y - second.y),
    Math.abs(first.width - second.width),
    Math.abs(first.height - second.height),
  );
}

async function checkHoverStability(page, run, selector, label) {
  const locator = page.locator(selector).first();
  await locator.waitFor({ state: "visible" });
  const before = await rect(page, selector);
  await locator.hover();
  await page.waitForTimeout(120);
  const after = await rect(page, selector);
  const drift = rectDrift(before, after);
  addCheck(run, `${label} does not move on hover`, drift <= 0.5 ? "pass" : "fail", {
    severity: "medium",
    selector,
    message: `${label} moved ${round(drift)} px on hover.`,
    measurements: { before, after, maximumDrift: round(drift) },
  });
  await page.mouse.move(1, 1);
}

async function clickAndCheckFocusRelease(page, run, locator, name) {
  await locator.click();
  await page.waitForTimeout(60);
  const focus = await page.evaluate(() => {
    const active = document.activeElement;
    return {
      tag: active?.tagName || null,
      id: active?.id || null,
      className: typeof active?.className === "string" ? active.className : null,
      ariaLabel: active?.getAttribute?.("aria-label") || null,
      text: active?.textContent?.trim().slice(0, 80) || null,
    };
  });
  const released = !focus.tag || focus.tag === "BODY" || focus.tag === "HTML" || focus.id === "main-content";
  addCheck(run, `${name} releases pointer focus`, released ? "pass" : "fail", {
    severity: "medium",
    selector: await locator.evaluate((element) => {
      if (element.id) return `#${element.id}`;
      if (element.getAttribute("aria-label")) return `[aria-label="${element.getAttribute("aria-label")}"]`;
      return element.tagName.toLowerCase();
    }).catch(() => null),
    message: `${name} left focus on ${focus.tag || "an unknown element"}.`,
    measurements: { activeElement: focus },
  });
  return focus;
}

async function openIowa(page, run) {
  const homeNavigation = page.getByRole("button", { name: "Home", exact: true });
  let homeReady = false;
  let homeError = null;
  for (let attemptIndex = 1; attemptIndex <= 3 && !homeReady; attemptIndex += 1) {
    try {
      if (attemptIndex === 1) await page.waitForTimeout(600);
      await homeNavigation.click({ timeout: 5_000 });
      await page.locator(".home-heading").waitFor({ state: "visible", timeout: 5_000 });
      homeReady = true;
    } catch (error) {
      homeError = error.message;
      if (attemptIndex < 3) {
        await page.reload({ waitUntil: "domcontentloaded", timeout: 20_000 });
        await page.waitForFunction(() => Boolean(window.Blazor) && Boolean(document.querySelector(".app-shell")), null, { timeout: 30_000 });
        await page.getByRole("button", { name: "Settings", exact: true }).waitFor({ state: "visible", timeout: 30_000 });
        await page.waitForTimeout(800);
      }
    }
  }
  if (!homeReady) throw new Error(`Home interaction gate failed after 3 attempts: ${homeError}`);
  run.pageName = "Race Analysis list";
  const navigation = page.getByRole("button", { name: "Race Analysis", exact: true });
  await clickAndCheckFocusRelease(page, run, navigation, "Race Analysis navigation");
  const telemetryTab = page.getByRole("tab", { name: "Telemetry", exact: true });
  await page.waitForFunction(() => Boolean(
    document.querySelector("[data-analysis-track-map]")
      || [...document.querySelectorAll("h1, h2")].find((heading) => heading.textContent?.trim() === "Race events"),
  ), null, { timeout: 30_000 });
  if (!(await page.locator("[data-analysis-track-map]").isVisible().catch(() => false))) {
    await page.getByRole("heading", { name: "Race events", exact: true }).waitFor({ state: "visible", timeout: 30_000 });
    await capture(page, run, "race-analysis-list");
    await auditLayout(page, run, [".race-list-heading", ".race-list-toolbar"]);
    const iowaRow = page.locator(".race-event-row").filter({ hasText: "Iowa" }).first();
    await iowaRow.waitFor({ state: "visible", timeout: 30_000 });
    await clickAndCheckFocusRelease(page, run, iowaRow, "Iowa race row");
  }
  await telemetryTab.waitFor({ state: "visible", timeout: 60_000 });
  await page.locator("[data-analysis-track-map]").waitFor({ state: "visible", timeout: 60_000 });
  await page.waitForTimeout(350);
  run.pageName = "Iowa Race Analysis telemetry";
  const title = await page.locator(".race-analysis-title-copy h1").innerText();
  addCheck(run, "Real Iowa race opened", /Iowa Speedway/i.test(title) ? "pass" : "fail", {
    severity: "high",
    selector: ".race-analysis-title-copy h1",
    message: `Expected Iowa Speedway, rendered ${JSON.stringify(title)}.`,
    measurements: { renderedTitle: title },
  });
}

async function exerciseContextToggles(page, run) {
  const track = page.locator('[data-context-toggle="track"]');
  const laps = page.locator('[data-context-toggle="laps"]');
  const failures = [];
  let focusFailures = 0;
  for (const [name, locator] of [["track", track], ["laps", laps]]) {
    for (let cycle = 1; cycle <= 10; cycle += 1) {
      await locator.click();
      await page.waitForFunction(
        ([selector, expected]) => document.querySelector(selector)?.getAttribute("aria-pressed") === expected,
        [`[data-context-toggle="${name}"]`, "false"],
        { timeout: 3_000 },
      );
      await page.waitForTimeout(550);
      const collapsed = await locator.getAttribute("aria-pressed");
      const collapsedFocus = await page.evaluate(() => document.activeElement?.tagName || null);
      if (collapsed !== "false") failures.push({ name, cycle, phase: "collapse", ariaPressed: collapsed });
      if (collapsedFocus && !["BODY", "HTML", "MAIN"].includes(collapsedFocus)) focusFailures += 1;
      if (cycle === 1) await capture(page, run, `${name}-context-collapsed`);
      await locator.click();
      await page.waitForFunction(
        ([selector, expected]) => document.querySelector(selector)?.getAttribute("aria-pressed") === expected,
        [`[data-context-toggle="${name}"]`, "true"],
        { timeout: 3_000 },
      );
      await page.waitForTimeout(550);
      const expanded = await locator.getAttribute("aria-pressed");
      const expandedFocus = await page.evaluate(() => document.activeElement?.tagName || null);
      if (expanded !== "true") failures.push({ name, cycle, phase: "expand", ariaPressed: expanded });
      if (expandedFocus && !["BODY", "HTML", "MAIN"].includes(expandedFocus)) focusFailures += 1;
    }
  }
  addCheck(run, "Track and Laps toggles survive 10 cycles each", failures.length === 0 ? "pass" : "fail", {
    severity: "high",
    selector: '[data-context-toggle="track"], [data-context-toggle="laps"]',
    message: failures.length === 0 ? "Both toggles returned to expanded state after every cycle." : `${failures.length} toggle transitions did not reach the expected state.`,
    measurements: { cyclesPerToggle: 10, failures },
  });
  addCheck(run, "Context toggle clicks release focus", focusFailures === 0 ? "pass" : "fail", {
    severity: "medium",
    selector: '[data-context-toggle="track"], [data-context-toggle="laps"]',
    message: `${focusFailures} of 40 pointer transitions retained control focus.`,
    measurements: { transitions: 40, focusFailures },
  });
}

async function exerciseSplitter(page, run) {
  await page.waitForTimeout(650);
  const splitter = page.locator("[data-analysis-context-splitter]");
  const initial = Number(await splitter.getAttribute("aria-valuenow"));
  const box = await splitter.boundingBox();
  const containerBox = await page.locator(".telemetry-context-column").boundingBox();
  if (!box) throw new Error("Context splitter has no bounding box.");
  if (!containerBox) throw new Error("Context column has no bounding box.");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(
    box.x + box.width / 2,
    containerBox.y + containerBox.height * 0.6,
    { steps: 12 },
  );
  await page.mouse.up();
  await page.waitForFunction(
    (expected) => Number(document.querySelector("[data-analysis-context-splitter]")?.getAttribute("aria-valuenow")) !== expected,
    initial,
    { timeout: 3_000 },
  ).catch(() => {});
  await page.waitForTimeout(250);
  const dragged = Number(await splitter.getAttribute("aria-valuenow"));
  await splitter.press("Home");
  const home = Number(await splitter.getAttribute("aria-valuenow"));
  await splitter.press("End");
  const end = Number(await splitter.getAttribute("aria-valuenow"));
  await splitter.press("Home");
  for (let index = 0; index < 5; index += 1) await splitter.press("ArrowDown");
  const restored = Number(await splitter.getAttribute("aria-valuenow"));
  const trackRect = await rect(page, ".track-panel");
  const lapsRect = await rect(page, ".lap-rail");
  const passed = dragged !== initial && home === 33 && end === 67 && restored === 43;
  addCheck(run, "Context splitter supports pointer and keyboard bounds", passed ? "pass" : "fail", {
    severity: "high",
    selector: "[data-analysis-context-splitter]",
    message: `Splitter values: initial ${initial}, dragged ${dragged}, Home ${home}, End ${end}, restored ${restored}.`,
    measurements: { initial, dragged, home, end, restored, trackRect, lapsRect },
  });
}

async function exerciseMap(page, run) {
  const select = page.locator("select[data-map-type]");
  const map = page.locator("[data-analysis-track-map]");
  const options = await select.locator("option").evaluateAll((nodes) => nodes.map((node) => ({
    value: node.value,
    label: node.textContent.trim(),
  })));
  const modes = [];
  let focusFailures = 0;
  for (const option of options) {
    await select.click();
    await select.selectOption(option.value);
    let transitionError = null;
    try {
      await waitForCondition(page, `Track map ${option.label} renders`, (value) => {
        const mapElement = document.querySelector("[data-analysis-track-map]");
        if (document.querySelector("select[data-map-type]")?.value !== value) return false;
        if (mapElement?.getAttribute("data-map-type") !== value) return false;
        if (value === "traces") return true;
        return mapElement.querySelectorAll(`[data-map-layer="${value}"]`).length > 0
          && document.querySelector("[data-map-legend]")?.getAttribute("data-mode") === value;
      }, option.value, 5_000);
    } catch (error) {
      transitionError = error.message;
    }
    const state = await page.evaluate(({ value, error }) => {
      const mapElement = document.querySelector("[data-analysis-track-map]");
      const selectElement = document.querySelector("select[data-map-type]");
      return {
        requested: value,
        transitionError: error,
        selected: selectElement?.value,
        rendered: mapElement?.getAttribute("data-map-type"),
        layers: mapElement?.querySelectorAll(`[data-map-layer="${value}"]`).length || 0,
        legend: document.querySelector("[data-map-legend]")?.getAttribute("data-mode") || null,
        activeElement: document.activeElement?.tagName || null,
      };
    }, { value: option.value, error: transitionError });
    modes.push(state);
    if (!state.activeElement || !["BODY", "HTML", "MAIN"].includes(state.activeElement)) focusFailures += 1;
    if (["speed", "throttle", "brake", "stress"].includes(option.value)) {
      await capture(page, run, `track-map-${option.value}`);
    }
  }
  await select.selectOption("traces");
  const modesPassed = modes.every((mode) => !mode.transitionError && mode.selected === mode.requested && mode.rendered === mode.requested && (mode.requested === "traces" || mode.layers > 0) && (mode.requested === "traces" || mode.legend === mode.requested));
  addCheck(run, "All track map modes render", modesPassed ? "pass" : "fail", {
    severity: "high",
    selector: "select[data-map-type]",
    message: modesPassed ? `${modes.length} map modes rendered their expected layer or legend.` : "One or more map modes did not render its expected layer or legend.",
    measurements: { options, modes },
  });
  addCheck(run, "Map mode selector releases pointer focus", focusFailures === 0 ? "pass" : "fail", {
    severity: "medium",
    selector: "select[data-map-type]",
    message: `${focusFailures} of ${options.length} mode changes retained select focus.`,
    measurements: { focusFailures, changes: options.length },
  });

  const before = await map.evaluate((element) => ({ viewBox: element.getAttribute("viewBox"), zoom: Number(element.dataset.mapZoom) }));
  const mapBox = await map.boundingBox();
  if (!mapBox) throw new Error("Track map has no bounding box.");
  await page.mouse.move(mapBox.x + mapBox.width / 2, mapBox.y + mapBox.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(100);
  const zoomed = await map.evaluate((element) => ({ viewBox: element.getAttribute("viewBox"), zoom: Number(element.dataset.mapZoom) }));
  const fit = page.getByRole("button", { name: "Fit track", exact: true });
  await clickAndCheckFocusRelease(page, run, fit, "Fit track");
  const fitted = await map.evaluate((element) => ({ viewBox: element.getAttribute("viewBox"), zoom: Number(element.dataset.mapZoom) }));
  const passed = zoomed.zoom > before.zoom + 0.01 && Math.abs(fitted.zoom - 1) <= 0.001 && fitted.viewBox === before.viewBox;
  addCheck(run, "Wheel zoom changes the map and Fit restores it", passed ? "pass" : "fail", {
    severity: "high",
    selector: "[data-analysis-track-map], [aria-label=\"Fit track\"]",
    message: `Map zoom changed ${before.zoom} -> ${zoomed.zoom} -> ${fitted.zoom}.`,
    measurements: { before, zoomed, fitted },
  });
  await capture(page, run, "track-map-modes-zoom-fit");
}

async function exerciseLapSelection(page, run) {
  const studioSelector = "[data-analysis-trace-studio]";
  const footerSelector = ".lap-rail-footer";
  const selectedStudio = await rect(page, studioSelector);
  const selectedFooter = await rect(page, footerSelector);
  await clickAndCheckFocusRelease(page, run, page.getByRole("button", { name: "Clear", exact: true }).first(), "Clear lap selection");
  await page.getByText("No laps selected", { exact: true }).waitFor({ state: "visible" });
  const emptyStudio = await rect(page, studioSelector);
  const emptyFooter = await rect(page, footerSelector);
  await capture(page, run, "no-laps-selected");
  await clickAndCheckFocusRelease(page, run, page.getByRole("button", { name: "Best three", exact: true }), "Best three lap selection");
  await page.locator('[data-analysis-trace-path]').first().waitFor({ state: "attached" });
  const restoredStudio = await rect(page, studioSelector);
  const restoredFooter = await rect(page, footerSelector);
  const studioDrift = Math.max(rectDrift(selectedStudio, emptyStudio), rectDrift(selectedStudio, restoredStudio));
  const footerDrift = Math.max(rectDrift(selectedFooter, emptyFooter), rectDrift(selectedFooter, restoredFooter));
  const passed = studioDrift <= 1 && footerDrift <= 1;
  addCheck(run, "Lap footer and trace studio keep stable height with no selection", passed ? "pass" : "fail", {
    severity: "medium",
    selector: `${studioSelector}, ${footerSelector}`,
    message: `Maximum trace-studio drift is ${round(studioDrift)} px; footer drift is ${round(footerDrift)} px.`,
    measurements: { selectedStudio, emptyStudio, restoredStudio, selectedFooter, emptyFooter, restoredFooter, studioDrift, footerDrift },
  });
}

async function exerciseSpotlight(page, run) {
  const trigger = page.getByRole("button", { name: "Spotlight lap", exact: true });
  const listbox = page.getByRole("listbox", { name: "Spotlight lap choices" });
  const pointerSamples = [];
  for (let cycle = 1; cycle <= 10; cycle += 1) {
    await trigger.click();
    await page.waitForTimeout(180);
    const openState = {
      cycle,
      phase: "open",
      ariaExpanded: await trigger.getAttribute("aria-expanded"),
      listboxVisible: await listbox.isVisible().catch(() => false),
      activeElement: await page.evaluate(() => ({
        tag: document.activeElement?.tagName || null,
        ariaLabel: document.activeElement?.getAttribute?.("aria-label") || null,
      })),
    };
    pointerSamples.push(openState);
    if (openState.ariaExpanded !== "true" || !openState.listboxVisible) break;
    if (cycle < 10) {
      await trigger.click();
      await page.waitForTimeout(180);
      pointerSamples.push({
        cycle,
        phase: "close",
        ariaExpanded: await trigger.getAttribute("aria-expanded"),
        listboxVisible: await listbox.isVisible().catch(() => false),
        activeElement: await page.evaluate(() => ({
          tag: document.activeElement?.tagName || null,
          ariaLabel: document.activeElement?.getAttribute?.("aria-label") || null,
        })),
      });
    }
  }
  const pointerOpenSamples = pointerSamples.filter((sample) => sample.phase === "open");
  const pointerCloseSamples = pointerSamples.filter((sample) => sample.phase === "close");
  const pointerPassed = pointerOpenSamples.length === 10
    && pointerOpenSamples.every((sample) => sample.ariaExpanded === "true" && sample.listboxVisible && ["BODY", "HTML"].includes(sample.activeElement.tag))
    && pointerCloseSamples.length === 9
    && pointerCloseSamples.every((sample) => sample.ariaExpanded === "false" && !sample.listboxVisible && ["BODY", "HTML"].includes(sample.activeElement.tag));
  const pointerState = pointerSamples.at(-1);
  addCheck(run, "Pointer click keeps the Spotlight choices open across 10 cycles", pointerPassed ? "pass" : "fail", {
    severity: "high",
    selector: '[aria-label="Spotlight lap"], [role="listbox"][aria-label="Spotlight lap choices"]',
    message: `${pointerOpenSamples.filter((sample) => sample.ariaExpanded === "true" && sample.listboxVisible).length} of 10 pointer opens remained visible; ${pointerCloseSamples.filter((sample) => sample.ariaExpanded === "false" && !sample.listboxVisible).length} of 9 closes completed; final focus ${pointerState.activeElement.tag || "none"}.`,
    measurements: { cycles: 10, pointerSamples },
  });
  if (!pointerState.listboxVisible) {
    await trigger.focus();
    await trigger.press("Enter");
  }
  await listbox.waitFor({ state: "visible" });
  const option = listbox.getByRole("option").first();
  const optionText = (await option.innerText()).trim();
  const selectedLap = Number.parseInt((await option.locator(".spotlight-lap b").innerText()).trim(), 10);
  await option.click();
  await page.locator(".spotlight-active-chip").waitFor({ state: "visible", timeout: 3_000 });
  await page.waitForFunction((lap) => [...document.querySelectorAll("[data-analysis-trace-path]")]
    .some((path) => Number.parseInt(path.getAttribute("data-lap"), 10) === lap), selectedLap, { timeout: 3_000 });
  const state = await page.evaluate((lap) => ({
    activeChip: document.querySelector(".spotlight-active-chip")?.textContent?.trim() || null,
    selectedLap: lap,
    selectedLapPaths: [...document.querySelectorAll("[data-analysis-trace-path]")]
      .filter((path) => Number.parseInt(path.getAttribute("data-lap"), 10) === lap).length,
    spotlightPaths: [...document.querySelectorAll("[data-analysis-trace-path]")]
      .filter((path) => path.getAttribute("data-spotlight")?.toLowerCase() === "true").length,
    spotlightAttributeValues: [...new Set([...document.querySelectorAll("[data-analysis-trace-path]")]
      .map((path) => path.getAttribute("data-spotlight")))],
    menuOpen: Boolean(document.querySelector(".spotlight-menu")),
  }), selectedLap);
  await capture(page, run, "spotlight-lap-active");
  const clear = page.locator(".spotlight-clear");
  await clear.click();
  await page.locator(".spotlight-active-chip").waitFor({ state: "hidden", timeout: 3_000 });
  const cleared = await page.locator(".spotlight-active-chip").count() === 0;
  const passed = state.activeChip?.includes(`Lap ${selectedLap}`)
    && state.selectedLapPaths > 0
    && state.spotlightPaths > 0
    && cleared;
  addCheck(run, "Spotlight selects, renders, and clears a lap", passed ? "pass" : "fail", {
    severity: "high",
    selector: ".trace-spotlight-control, [data-analysis-trace-path]",
    message: `Selected ${JSON.stringify(optionText)}; selected-lap paths ${state.selectedLapPaths}; explicit spotlight paths ${state.spotlightPaths}; cleared ${cleared}.`,
    measurements: { optionText, state, cleared },
  });
}

async function exerciseCustomizeAndLayouts(page, run) {
  const trigger = page.getByRole("button", { name: "Customize trace charts", exact: true });
  const baseline = await rect(page, ".telemetry-context-column");
  const widths = [];
  const focusSamples = [];
  const captureFocus = (cycle, phase) => page.evaluate(({ cycleValue, phaseValue }) => {
    const active = document.activeElement;
    return {
      cycle: cycleValue,
      phase: phaseValue,
      tag: active?.tagName || null,
      id: active?.id || null,
      className: typeof active?.className === "string" ? active.className : null,
      ariaLabel: active?.getAttribute?.("aria-label") || null,
      text: active?.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) || null,
      insideToolbox: Boolean(active?.closest?.("#analysis-trace-toolbox")),
    };
  }, { cycleValue: cycle, phaseValue: phase });
  for (let cycle = 1; cycle <= 10; cycle += 1) {
    await trigger.click();
    await page.locator("#analysis-trace-toolbox.open").waitFor({ state: "visible" });
    await page.waitForTimeout(220);
    const openRect = await rect(page, ".telemetry-context-column");
    widths.push({ cycle, phase: "open", width: openRect.width, x: openRect.x });
    if (cycle === 1) await capture(page, run, "customize-traces-open");
    focusSamples.push(await captureFocus(cycle, "open"));
    const close = page.getByRole("button", { name: "Hide trace toolbox", exact: true });
    await close.click();
    await page.locator("#analysis-trace-toolbox.open").waitFor({ state: "hidden" });
    await page.waitForTimeout(220);
    const closedRect = await rect(page, ".telemetry-context-column");
    widths.push({ cycle, phase: "closed", width: closedRect.width, x: closedRect.x });
    focusSamples.push(await captureFocus(cycle, "close"));
  }
  const maximumDrift = Math.max(...widths.map((value) => Math.max(Math.abs(value.width - baseline.width), Math.abs(value.x - baseline.x))));
  addCheck(run, "Customize survives 10 open/close cycles without moving the context column", maximumDrift <= 0.5 ? "pass" : "fail", {
    severity: "high",
    selector: ".telemetry-context-column, #analysis-trace-toolbox",
    message: `Context-column maximum drift across 10 cycles is ${round(maximumDrift)} px.`,
    measurements: { baseline, maximumDrift: round(maximumDrift), samples: widths },
  });
  const focusFailures = focusSamples.filter((sample) => sample.tag && !["BODY", "HTML", "MAIN"].includes(sample.tag));
  addCheck(run, "Customize open/close controls release focus", focusFailures.length === 0 ? "pass" : "fail", {
    severity: "medium",
    selector: '[aria-label="Customize trace charts"], [aria-label="Hide trace toolbox"]',
    message: `${focusFailures.length} of 20 Customize transitions retained focus; failures occurred in ${[...new Set(focusFailures.map((sample) => sample.phase))].join(", ")} phase(s).`,
    measurements: { transitions: 20, focusFailures, samples: focusSamples },
  });

  await trigger.click();
  const layout = page.locator("[data-analysis-layout-select]");
  await layout.waitFor({ state: "visible" });
  const readLayoutState = () => page.evaluate(() => {
    const select = document.querySelector("[data-analysis-layout-select]");
    return {
      value: select?.value || null,
      optionCount: select?.options.length || 0,
      selectedLabel: select?.selectedOptions[0]?.textContent?.trim() || null,
      heading: document.querySelector(".toolbox-section-heading strong")?.textContent?.trim() || null,
      rows: document.querySelectorAll("[data-analysis-render-row]").length,
    };
  });
  const waitForLayoutState = (expected, label) => waitForCondition(page, label, (state) => {
    const select = document.querySelector("[data-analysis-layout-select]");
    const heading = document.querySelector(".toolbox-section-heading strong")?.textContent?.trim();
    const selectedLabel = select?.selectedOptions[0]?.textContent?.trim();
    return Boolean(select)
      && (state.value === null || select.value === state.value)
      && (state.notValue === null || select.value !== state.notValue)
      && (state.optionCount === null || select.options.length === state.optionCount)
      && selectedLabel === heading;
  }, expected, 5_000);
  const original = await layout.inputValue();
  const options = await layout.locator("option").evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, label: node.textContent.trim() })));
  const states = [];
  for (const option of options) {
    await layout.selectOption(option.value);
    let settleError = null;
    try {
      await waitForLayoutState({ value: option.value, notValue: null, optionCount: options.length }, `Trace layout ${option.label} settles`);
    } catch (error) {
      settleError = error.message;
    }
    const settled = await readLayoutState();
    states.push({
      ...option,
      selected: settled.value,
      heading: settled.heading,
      selectedLabel: settled.selectedLabel,
      rows: settled.rows,
      settleError,
    });
  }
  await layout.selectOption(original);
  let restoreError = null;
  try {
    await waitForLayoutState({ value: original, notValue: null, optionCount: options.length }, "Original trace layout settles before New");
  } catch (error) {
    restoreError = error.message;
  }
  const newButton = page.locator("[data-analysis-layout-new]");
  await newButton.click();
  let createError = null;
  try {
    await waitForLayoutState({ value: null, notValue: original, optionCount: options.length + 1 }, "New trace layout becomes active");
  } catch (error) {
    createError = error.message;
  }
  const createdState = await readLayoutState();
  const createdValue = createdState.value;
  const deleteButton = page.locator("[data-analysis-layout-delete]");
  const deleteEnabled = await deleteButton.isEnabled();
  let deleteError = null;
  if (deleteEnabled) await deleteButton.click();
  if (deleteEnabled) {
    try {
      await waitForLayoutState({ value: null, notValue: createdValue, optionCount: options.length }, "Deleted trace layout leaves the library");
    } catch (error) {
      deleteError = error.message;
    }
  }
  const afterDeleteState = await readLayoutState();
  const afterDelete = afterDeleteState.value;
  const passed = options.length > 0
    && states.every((state) => state.selected === state.value && state.selectedLabel === state.heading && state.rows >= 1 && state.rows <= 10 && !state.settleError)
    && createdValue !== original
    && createdState.optionCount === options.length + 1
    && createdState.selectedLabel === createdState.heading
    && deleteEnabled
    && afterDeleteState.optionCount === options.length
    && afterDelete !== createdValue
    && afterDeleteState.selectedLabel === afterDeleteState.heading
    && !restoreError
    && !createError
    && !deleteError;
  addCheck(run, "Trace layouts switch and an ephemeral layout can be created/deleted", passed ? "pass" : "fail", {
    severity: "high",
    selector: "[data-analysis-layout-select], [data-analysis-layout-new], [data-analysis-layout-delete]",
    message: `Exercised ${options.length} saved layouts; created ${createdValue}; delete enabled ${deleteEnabled}; active after delete ${afterDelete}.`,
    measurements: { original, options, states, restoreError, createdState, createError, deleteEnabled, afterDeleteState, deleteError },
  });
  await page.getByRole("button", { name: "Hide trace toolbox", exact: true }).click();
}

async function exerciseFullscreen(page, run) {
  const enter = page.getByRole("button", { name: "View traces full screen", exact: true });
  await enter.click();
  await page.locator(".trace-panel-expanded").waitFor({ state: "visible" });
  let focusError = null;
  try {
    await waitForCondition(page, "Trace full-screen receives focus", () => document.activeElement?.matches?.("[data-analysis-trace-studio]"));
  } catch (error) {
    focusError = error.message;
  }
  const expansionFocus = await page.evaluate(() => ({
    tag: document.activeElement?.tagName || null,
    className: typeof document.activeElement?.className === "string" ? document.activeElement.className : null,
    isTraceStudio: document.activeElement?.matches?.("[data-analysis-trace-studio]") || false,
    tabIndex: document.activeElement?.tabIndex ?? null,
  }));
  addCheck(run, "Trace full-screen receives invisible keyboard focus after pointer expansion", expansionFocus.isTraceStudio ? "pass" : "fail", {
    severity: "high",
    selector: "[data-analysis-trace-studio]",
    message: `Active element after expansion is ${expansionFocus.tag || "none"}.${expansionFocus.className || ""}; trace studio focused=${expansionFocus.isTraceStudio}.`,
    measurements: { expansionFocus, focusError },
  });
  const expanded = await rect(page, ".trace-panel-expanded");
  const viewport = page.viewportSize();
  await capture(page, run, "trace-fullscreen");
  await page.keyboard.press("Escape");
  let escapeError = null;
  try {
    await page.locator(".trace-panel-expanded").waitFor({ state: "hidden", timeout: 3_000 });
  } catch (error) {
    escapeError = error.message;
  }
  const escapeState = await page.evaluate(() => ({
    expanded: Boolean(document.querySelector(".trace-panel-expanded")),
    activeTag: document.activeElement?.tagName || null,
    activeAriaLabel: document.activeElement?.getAttribute?.("aria-label") || null,
  }));
  if (escapeState.expanded) {
    await page.getByRole("button", { name: "Exit full-screen traces", exact: true }).click({ force: true });
    await page.locator(".trace-panel-expanded").waitFor({ state: "hidden", timeout: 3_000 });
  }
  const passed = !escapeState.expanded
    && expanded.x <= 1
    && expanded.y <= 1
    && Math.abs(expanded.width - viewport.width) <= 2
    && Math.abs(expanded.height - viewport.height) <= 2;
  addCheck(run, "Trace full-screen fills the viewport and Escape exits", passed ? "pass" : "fail", {
    severity: "high",
    selector: ".trace-panel-expanded",
    message: `Expanded rect ${expanded.width}x${expanded.height} at ${expanded.x},${expanded.y}; viewport ${viewport.width}x${viewport.height}; Escape left expanded=${escapeState.expanded}.`,
    measurements: { expanded, viewport, escapeState, escapeError },
  });

  await enter.click();
  await page.locator(".trace-panel-expanded").waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Exit full-screen traces", exact: true }).click();
  await page.locator(".trace-panel-expanded").waitFor({ state: "hidden", timeout: 3_000 });
  await page.waitForTimeout(120);
  const pointerExitFocus = await page.evaluate(() => ({
    tag: document.activeElement?.tagName || null,
    id: document.activeElement?.id || null,
    className: typeof document.activeElement?.className === "string" ? document.activeElement.className : null,
    ariaLabel: document.activeElement?.getAttribute?.("aria-label") || null,
  }));
  const pointerExitReleased = !pointerExitFocus.tag
    || ["BODY", "HTML"].includes(pointerExitFocus.tag)
    || pointerExitFocus.id === "main-content";
  addCheck(run, "Pointer exit from trace full-screen releases focus", pointerExitReleased ? "pass" : "fail", {
    severity: "medium",
    selector: '[aria-label="Exit full-screen traces"]',
    message: `Pointer exit left focus on ${pointerExitFocus.tag || "none"}${pointerExitFocus.ariaLabel ? ` (${pointerExitFocus.ariaLabel})` : ""}.`,
    measurements: { pointerExitFocus },
  });
}

async function exerciseTechnical(page, run) {
  run.pageName = "Iowa Technical data";
  await clickAndCheckFocusRelease(page, run, page.getByRole("tab", { name: "Technical data", exact: true }), "Technical data tab");
  await page.locator("[data-technical-overview]").waitFor({ state: "visible" });
  const cards = page.locator("[data-technical-card]");
  const cardCount = await cards.count();
  addCheck(run, "Technical overview renders four cards", cardCount === 4 ? "pass" : "fail", {
    severity: "high",
    selector: "[data-technical-card]",
    message: `Technical overview rendered ${cardCount} cards; expected 4.`,
    measurements: { cardCount },
  });
  await auditLayout(page, run, [".race-analysis-toolbar", "[data-technical-overview]", "[data-technical-card=\"pit\"]", "[data-technical-card=\"tires\"]", "[data-technical-card=\"fuel\"]", "[data-technical-card=\"racecraft\"]"], true);
  await capture(page, run, "technical-overview-four-cards");
  const investigations = [];
  for (const id of ["pit", "tires", "fuel", "racecraft"]) {
    const selector = `[data-technical-card="${id}"]`;
    await checkHoverStability(page, run, selector, `${id} technical card`);
    await clickAndCheckFocusRelease(page, run, page.locator(selector), `${id} technical card`);
    const investigationSelector = `[data-technical-investigation="${id}"]`;
    await page.locator(investigationSelector).waitFor({ state: "visible" });
    const metrics = await elementMetrics(page, investigationSelector);
    investigations.push({ id, metrics });
    await capture(page, run, `technical-${id}-investigation`);
    await clickAndCheckFocusRelease(page, run, page.locator("[data-technical-back]"), `${id} technical Back`);
    await page.locator("[data-technical-overview]").waitFor({ state: "visible" });
  }
  const clipped = investigations.filter((item) => item.metrics.clippedX > 1 || item.metrics.clippedY > 1 || !item.metrics.inViewport);
  addCheck(run, "All four Technical investigations open without clipping", clipped.length === 0 ? "pass" : "fail", {
    severity: "high",
    selector: "[data-technical-investigation]",
    message: clipped.length === 0 ? "All four Technical investigations rendered within the fixed workspace." : `${clipped.length} Technical investigations were clipped.`,
    measurements: { investigations },
  });
}

async function exerciseReplay(page, run) {
  run.pageName = "Iowa Race replay";
  await clickAndCheckFocusRelease(page, run, page.getByRole("tab", { name: "Race replay", exact: true }), "Race replay tab");
  await page.locator("[data-race-replay]").waitFor({ state: "visible" });
  const unavailable = page.locator("[data-replay-unavailable]").first();
  const isUnavailable = await unavailable.isVisible().catch(() => false);
  const text = isUnavailable ? (await unavailable.innerText()).trim() : null;
  addCheck(run, "Iowa replay unavailable state is explicit", isUnavailable && /Race replay is unavailable/i.test(text) ? "pass" : "fail", {
    severity: "high",
    selector: "[data-replay-unavailable]",
    message: isUnavailable ? text : "Expected the explicit replay-unavailable state, but it was not shown.",
    measurements: { isUnavailable, text },
  });
  await auditLayout(page, run, [".race-analysis-toolbar", "[data-race-replay]", "[data-replay-unavailable]"], true);
  await capture(page, run, "race-replay-unavailable");
}

async function navigateTo(page, run, label, pageName, rootSelector) {
  run.pageName = pageName;
  await clickAndCheckFocusRelease(
    page,
    run,
    page.getByRole("button", { name: label, exact: true }),
    `${label} navigation`,
  );
  await page.locator(rootSelector).waitFor({ state: "visible", timeout: 8_000 });
  await page.locator("#main-content").press("Home");
  await page.mouse.move(1, 1);
  await page.waitForTimeout(300);
}

async function attemptPage(page, run, name, operation, timeoutMs) {
  let timeoutHandle;
  const operationPromise = Promise.resolve().then(operation);
  try {
    await Promise.race([
      operationPromise,
      new Promise((_, reject) => {
        timeoutHandle = setTimeout(() => reject(new Error(`${name} exceeded its ${timeoutMs} ms hard deadline.`)), timeoutMs);
      }),
    ]);
    addCheck(run, name, "pass");
  } catch (error) {
    addCheck(run, name, "fail", { severity: "critical", message: error.message });
    if (/hard deadline/.test(error.message)) {
      await page.reload({ waitUntil: "domcontentloaded", timeout: 20_000 }).catch(() => {});
      await page.getByRole("button", { name: "Settings", exact: true }).waitFor({ state: "visible", timeout: 20_000 }).catch(() => {});
    }
  } finally {
    clearTimeout(timeoutHandle);
  }
}

async function waitForCondition(page, label, predicate, argument = null, timeout = 8_000) {
  try {
    await page.waitForFunction(predicate, argument, { timeout });
  } catch (error) {
    throw new Error(`${label}: ${error.message}`);
  }
}

async function settleCssTransition(page, selector) {
  const timing = await page.locator(selector).first().evaluate((element) => {
    const style = getComputedStyle(element);
    const parse = (value) => value.split(",").map((part) => {
      const text = part.trim();
      if (text.endsWith("ms")) return Number.parseFloat(text);
      if (text.endsWith("s")) return Number.parseFloat(text) * 1000;
      return 0;
    });
    const durations = parse(style.transitionDuration);
    const delays = parse(style.transitionDelay);
    const count = Math.max(durations.length, delays.length);
    let maximumMs = 0;
    for (let index = 0; index < count; index += 1) {
      maximumMs = Math.max(maximumMs, (durations[index % durations.length] || 0) + (delays[index % delays.length] || 0));
    }
    return { transitionDuration: style.transitionDuration, transitionDelay: style.transitionDelay, maximumMs };
  });
  await page.waitForTimeout(Math.ceil(timing.maximumMs) + 40);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  return timing;
}

async function auditVisibleControls(page, run) {
  const result = await page.evaluate(() => {
    const selector = "button, select, input, textarea, summary, [role='button'], [role='tab']";
    const candidates = [...document.querySelectorAll(selector)];
    const offenders = [];
    const clippedText = [];
    const describe = (element) => {
      if (element.id) return `#${element.id}`;
      const aria = element.getAttribute("aria-label");
      if (aria) return `${element.tagName.toLowerCase()}[aria-label=${JSON.stringify(aria)}]`;
      const className = typeof element.className === "string"
        ? element.className.trim().split(/\s+/).filter(Boolean).slice(0, 3).join(".")
        : "";
      return `${element.tagName.toLowerCase()}${className ? `.${className}` : ""}`;
    };
    for (const element of candidates) {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0 || rect.width < 1 || rect.height < 1) continue;
      const centerInViewport = rect.left + rect.width / 2 >= 0
        && rect.left + rect.width / 2 <= innerWidth
        && rect.top + rect.height / 2 >= 0
        && rect.top + rect.height / 2 <= innerHeight;
      if (!centerInViewport) continue;
      const overflow = {
        left: Math.max(0, -rect.left),
        top: Math.max(0, -rect.top),
        right: Math.max(0, rect.right - innerWidth),
        bottom: Math.max(0, rect.bottom - innerHeight),
      };
      if (Math.max(...Object.values(overflow)) > 1) {
        offenders.push({ selector: describe(element), rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, overflow });
      }
      if (element.scrollWidth - element.clientWidth > 2 && !["auto", "scroll"].includes(style.overflowX)) {
        clippedText.push({ selector: describe(element), clientWidth: element.clientWidth, scrollWidth: element.scrollWidth, overflowX: style.overflowX, text: element.textContent.trim().replace(/\s+/g, " ").slice(0, 100) });
      }
    }
    return { viewport: { width: innerWidth, height: innerHeight }, offenders, clippedText };
  });
  addCheck(run, "Visible controls stay inside the viewport", result.offenders.length === 0 ? "pass" : "fail", {
    severity: "high",
    selector: result.offenders.map((item) => item.selector).join(", ") || null,
    message: result.offenders.length === 0 ? "All visible controls are contained by the viewport." : `${result.offenders.length} visible controls extend outside the viewport.`,
    measurements: result,
  });
  return result;
}

async function exerciseHome(page, run) {
  await navigateTo(page, run, "Home", "Home", ".page-heading");
  await auditLayout(page, run, [".home-heading", ".home-live-strip"]);
  await auditVisibleControls(page, run);
  const analysisCard = page.locator(".workflow-card.analysis");
  await analysisCard.waitFor({ state: "visible", timeout: 8_000 }).catch(() => {});
  if (await analysisCard.isVisible().catch(() => false)) {
    await checkHoverStability(page, run, ".workflow-card.analysis", "Home Review a race card");
  }
  await capture(page, run, "home-dashboard");
}

async function exercisePlanning(page, run) {
  await navigateTo(page, run, "Race Planning", "Race Planning", ".planning-page");
  await auditLayout(page, run, [".planning-heading", ".planning-setup-card"]);
  await auditVisibleControls(page, run);
  const reference = page.locator(".planning-reference-field select");
  const references = await reference.locator("option").evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, label: node.textContent.trim() })));
  if (references.some((option) => option.value)) {
    const chosen = references.find((option) => option.value);
    if (!(await reference.inputValue())) {
      await reference.click();
      await reference.selectOption(chosen.value);
      await page.waitForFunction((value) => document.querySelector(".planning-reference-field select")?.value === value, chosen.value, { timeout: 3_000 });
    }
  }
  await capture(page, run, "planning-race-details");
  const build = page.getByRole("button", { name: "Build plan", exact: true });
  const enabled = await build.isEnabled();
  if (enabled) {
    await clickAndCheckFocusRelease(page, run, build, "Build plan");
    await page.waitForFunction(() => Boolean(document.querySelector(".planning-results")), null, { timeout: 30_000 }).catch(() => {});
  }
  const generated = await page.locator(".planning-results").isVisible().catch(() => false);
  addCheck(run, "Planning builds a briefing from a real recorded reference", enabled && generated ? "pass" : "fail", {
    severity: "high",
    selector: ".planning-reference-field select, button",
    message: `Build plan enabled=${enabled}; planning results visible=${generated}.`,
    measurements: { references, selectedReference: await reference.inputValue().catch(() => null), enabled, generated },
  });
  if (generated) {
    await auditVisibleControls(page, run);
    await capture(page, run, "planning-generated-briefing");
    const disclosure = page.locator(".plan-briefing details.settings-disclosure").first();
    if (await disclosure.count()) {
      const summary = disclosure.locator("summary");
      await summary.click();
      await page.waitForFunction(() => document.querySelector(".plan-briefing details.settings-disclosure")?.open === true, null, { timeout: 3_000 });
      addCheck(run, "Planning calculation disclosure opens", "pass", { selector: ".plan-briefing details.settings-disclosure" });
      await summary.click();
    }
  }
}

async function exerciseStartingTune(page, run) {
  await navigateTo(page, run, "Setups", "Starting Tune", ".starting-tune-page");
  await auditLayout(page, run, [".starting-tune-heading", ".starting-tune", ".progress-rail"]);
  await auditVisibleControls(page, run);
  const progressCount = await page.locator(".progress-rail button").count();
  addCheck(run, "Starting Tune renders all four workflow steps", progressCount === 4 ? "pass" : "fail", {
    severity: "high",
    selector: ".progress-rail button",
    message: `Rendered ${progressCount} workflow steps; expected 4.`,
    measurements: { progressCount },
  });
  const qualifying = page.getByRole("button", { name: "Qualifying", exact: true });
  const race = page.getByRole("button", { name: "Race", exact: true });
  if (await qualifying.isVisible().catch(() => false)) {
    await qualifying.click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Starting Tune session"] button[aria-pressed="true"]')?.textContent?.trim() === "Qualifying", null, { timeout: 3_000 });
    await race.click();
    await page.waitForFunction(() => document.querySelector('[aria-label="Starting Tune session"] button[aria-pressed="true"]')?.textContent?.trim() === "Race", null, { timeout: 3_000 });
    addCheck(run, "Starting Tune session toggles Race and Qualifying", "pass", { selector: '[aria-label="Starting Tune session"]' });
  }
  await capture(page, run, "starting-tune-event");
  const find = page.getByRole("button", { name: /Find starting point|Finding source/ });
  const enabled = await find.isEnabled().catch(() => false);
  let advanced = false;
  let resolved = false;
  let began = false;
  const initialMessage = await page.locator(".starting-tune-message").textContent().catch(() => null);
  if (enabled) {
    await find.click();
    began = await waitForCondition(page, "Starting Tune source search begins", (initial) => {
      const heading = document.querySelector("#starting-tune-heading")?.textContent?.trim();
      const button = document.querySelector(".starting-tune-primary-action button")?.textContent || "";
      const message = document.querySelector(".starting-tune-message")?.textContent?.trim() || "";
      return heading === "Confirm the source" || /Finding source/i.test(button) || (message && message !== String(initial || "").trim());
    }, initialMessage, 8_000).then(() => true).catch(() => false);
    await waitForCondition(page, "Starting Tune source search resolves", (initial) => {
      const heading = document.querySelector("#starting-tune-heading")?.textContent?.trim();
      const button = document.querySelector(".starting-tune-primary-action button")?.textContent || "";
      const message = document.querySelector(".starting-tune-message")?.textContent?.trim() || "";
      if (heading === "Confirm the source") return true;
      return !/Finding source/i.test(button) && !/Reviewing your local setups/i.test(message) && Boolean(message) && message !== String(initial || "").trim();
    }, initialMessage, 60_000).catch(() => {});
    advanced = await page.getByRole("heading", { name: "Confirm the source", exact: true }).isVisible().catch(() => false);
    const finalButton = await page.locator(".starting-tune-primary-action button").textContent().catch(() => null);
    const finalMessage = await page.locator(".starting-tune-message").textContent().catch(() => null);
    resolved = advanced || (began && !/Finding source/i.test(finalButton || "") && !/Reviewing your local setups/i.test(finalMessage || "") && Boolean(finalMessage));
  }
  const sourceMessage = await page.locator(".starting-tune-message").textContent().catch(() => null);
  addCheck(run, "Starting Tune resolves the local source search", enabled && resolved ? "pass" : "fail", {
    severity: "high",
    selector: ".starting-tune-primary-action button, #starting-tune-heading",
    message: advanced ? "A read-only setup source advanced to confirmation." : `Source search resolved without a usable source: ${sourceMessage || "no message"}`,
    measurements: { enabled, began, resolved, advanced, initialMessage, sourceMessage },
  });
  if (advanced) {
    await capture(page, run, "starting-tune-source");
    await page.getByRole("button", { name: "Review checks", exact: true }).click();
    await page.getByRole("heading", { name: "Verify the baseline", exact: true }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Baseline run", exact: true }).click();
    await page.getByRole("heading", { name: "Record the baseline", exact: true }).waitFor({ state: "visible" });
    await capture(page, run, "starting-tune-baseline-run");
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Verify the baseline", exact: true }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Back", exact: true }).click();
    await page.getByRole("heading", { name: "Confirm the source", exact: true }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Change event", exact: true }).click();
    await page.getByRole("heading", { name: "Choose the event", exact: true }).waitFor({ state: "visible" });
    addCheck(run, "Starting Tune steps advance and return to Event", "pass", { selector: ".progress-rail" });
  }
}

async function exerciseProgressiveTuning(page, run) {
  await navigateTo(page, run, "Progressive Tuning", "Progressive Tuning", ".tuning-workbench-page");
  await page.waitForFunction(() => {
    if (document.querySelector(".tuning-workbench")) return true;
    const heading = document.querySelector(".tuning-empty-page h2")?.textContent?.trim();
    return Boolean(heading && heading !== "Loading race data");
  }, null, { timeout: 60_000 }).catch(() => {});
  const ready = await page.locator(".tuning-workbench").isVisible().catch(() => false);
  const emptyText = ready ? null : await page.locator(".tuning-empty-page").innerText().catch(() => null);
  addCheck(run, "Progressive Tuning loads an analyzed open-setup race", ready ? "pass" : "fail", {
    severity: "high",
    selector: ".tuning-workbench, .tuning-empty-page",
    message: ready ? "The tuning workbench loaded." : `Tuning workbench unavailable: ${emptyText || "unknown state"}`,
    measurements: { ready, emptyText },
  });
  if (!ready) {
    await capture(page, run, "progressive-tuning-unavailable");
    return;
  }
  await auditLayout(page, run, [".tuning-session-bar", ".tuning-map-column", ".tuning-toolbox"]);
  await auditVisibleControls(page, run);
  const phaseTabs = page.locator('[aria-label="Run phase for all turns"] [role="tab"]');
  const phaseCount = await phaseTabs.count();
  const phaseStates = [];
  for (let index = 0; index < phaseCount; index += 1) {
    const tab = phaseTabs.nth(index);
    const label = (await tab.innerText()).trim();
    await tab.click();
    await page.waitForFunction((requested) => [...document.querySelectorAll('[aria-label="Run phase for all turns"] [role="tab"]')].some((item) => item.getAttribute("aria-selected") === "true" && item.textContent.trim().startsWith(requested)), label, { timeout: 3_000 });
    phaseStates.push({ label, selected: await tab.getAttribute("aria-selected") });
  }
  addCheck(run, "Progressive Tuning switches Early, Mid, and Late phases", phaseCount === 3 && phaseStates.every((item) => item.selected === "true") ? "pass" : "fail", {
    severity: "high",
    selector: '[aria-label="Run phase for all turns"]',
    message: `Exercised ${phaseCount} run phase tabs.`,
    measurements: { phaseCount, phaseStates },
  });
  const goal = page.locator(".tuning-goal-picker select");
  const goalOptions = await goal.locator("option").evaluateAll((nodes) => nodes.map((node) => node.value));
  const originalGoal = await goal.inputValue();
  for (const option of goalOptions) {
    await goal.selectOption(option);
    await page.waitForFunction((value) => document.querySelector(".tuning-goal-picker select")?.value === value, option, { timeout: 3_000 });
  }
  await goal.selectOption(originalGoal);
  addCheck(run, "Progressive Tuning exercises every goal option", goalOptions.length === 4 ? "pass" : "fail", {
    severity: "medium",
    selector: ".tuning-goal-picker select",
    message: `Exercised ${goalOptions.length} tuning goals.`,
    measurements: { goalOptions, originalGoal },
  });
  const firstTurn = page.locator('[aria-label="Selectable corners and load zones"] [role="listitem"]').first();
  if (await firstTurn.isVisible().catch(() => false)) {
    await firstTurn.click();
    await page.locator(".tuning-feedback-popover.open").waitFor({ state: "visible" });
    await capture(page, run, "progressive-tuning-corner-feedback");
    await page.getByRole("button", { name: "Close turn feedback", exact: true }).click();
    await page.locator(".tuning-feedback-popover.open").waitFor({ state: "hidden" });
    addCheck(run, "Progressive Tuning corner feedback opens and closes", "pass", { selector: ".tuning-feedback-popover" });
  }
  await capture(page, run, "progressive-tuning-workbench");
}

async function exerciseLive(page, run) {
  await navigateTo(page, run, "Live telemetry", "Live telemetry", "[data-live-layout-studio]");
  await auditLayout(page, run, [".live-heading", "[data-live-layout-studio]", "[data-live-grid-viewport]"]);
  await auditVisibleControls(page, run);
  const layout = page.getByLabel("Dashboard layout", { exact: true });
  const original = await layout.inputValue();
  const options = await layout.locator("option").evaluateAll((nodes) => nodes.map((node) => ({ value: node.value, label: node.textContent.trim() })));
  const states = [];
  for (const option of options) {
    await layout.selectOption(option.value);
    await page.waitForFunction((value) => document.querySelector('select[aria-label="Dashboard layout"]')?.value === value, option.value, { timeout: 3_000 });
    states.push({ ...option, grid: await page.locator("[data-live-grid]").getAttribute("aria-label") });
  }
  await layout.selectOption(original);
  addCheck(run, "Live dashboard layouts switch and render their grid", options.length > 0 && states.every((state) => state.grid) ? "pass" : "fail", {
    severity: "high",
    selector: 'select[aria-label="Dashboard layout"], [data-live-grid]',
    message: `Exercised ${options.length} live dashboard layouts.`,
    measurements: { original, options, states },
  });
  const baseline = await rect(page, "[data-live-grid-viewport]");
  const samples = [];
  const transitionTimings = [];
  for (let cycle = 1; cycle <= 5; cycle += 1) {
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    await page.locator("[data-live-layout-studio].editing").waitFor({ state: "visible" });
    transitionTimings.push({ cycle, phase: "open", ...(await settleCssTransition(page, ".page-frame")) });
    samples.push({ cycle, phase: "open", rect: await rect(page, "[data-live-grid-viewport]") });
    if (cycle === 1) await capture(page, run, "live-customize-open");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await page.locator("[data-live-layout-studio].viewing").waitFor({ state: "visible" });
    transitionTimings.push({ cycle, phase: "closed", ...(await settleCssTransition(page, ".page-frame")) });
    samples.push({ cycle, phase: "closed", rect: await rect(page, "[data-live-grid-viewport]") });
  }
  const openSamples = samples.filter((sample) => sample.phase === "open");
  const closedSamples = samples.filter((sample) => sample.phase === "closed");
  const closedDrift = Math.max(...closedSamples.map((sample) => rectDrift(baseline, sample.rect)));
  const openReference = openSamples[0].rect;
  const openSpread = Math.max(...openSamples.map((sample) => rectDrift(openReference, sample.rect)));
  const requiredOpenShrink = baseline.width - openReference.width;
  const customizeStable = closedDrift <= 1 && openSpread <= 1 && requiredOpenShrink > 1;
  addCheck(run, "Live Customize survives five cycles and restores the grid", customizeStable ? "pass" : "fail", {
    severity: "medium",
    selector: "[data-live-grid-viewport]",
    message: `Required open shrink ${round(requiredOpenShrink)} px; closed-to-baseline drift ${round(closedDrift)} px; open-state spread ${round(openSpread)} px.`,
    measurements: { baseline, requiredOpenShrink: round(requiredOpenShrink), closedDrift: round(closedDrift), openSpread: round(openSpread), transitionTimings, samples },
  });
  const traces = page.getByRole("button", { name: "Driving traces", exact: true });
  const readDisclosureState = () => page.evaluate(() => ({
    ariaExpanded: document.querySelector(".live-driving-toggle")?.getAttribute("aria-expanded"),
    bodyAriaHidden: document.querySelector("#live-driving-traces")?.getAttribute("aria-hidden"),
    disclosureClass: document.querySelector(".live-driving-disclosure")?.className,
    visualCount: document.querySelectorAll("#live-driving-traces canvas, #live-driving-traces svg").length,
    activeElement: {
      tag: document.activeElement?.tagName || null,
      className: typeof document.activeElement?.className === "string" ? document.activeElement.className : null,
    },
  }));
  const initialState = await readDisclosureState();
  await traces.click();
  let openError = null;
  try {
    await waitForCondition(page, "Live Driving traces opens", () => document.querySelector(".live-driving-disclosure")?.classList.contains("open"));
  } catch (error) {
    openError = error.message;
  }
  const openState = await readDisclosureState();
  const opened = openState.disclosureClass?.split(/\s+/).includes("open") === true;
  if (opened) await capture(page, run, "live-driving-traces-open");
  let closeError = null;
  if (opened) {
    await traces.click();
    try {
      await waitForCondition(page, "Live Driving traces closes", () => !document.querySelector(".live-driving-disclosure")?.classList.contains("open"));
    } catch (error) {
      closeError = error.message;
    }
  }
  const closeState = await readDisclosureState();
  const closed = closeState.disclosureClass?.split(/\s+/).includes("open") !== true;
  addCheck(run, "Live driving traces disclosure opens and closes", opened && closed ? "pass" : "fail", {
    severity: "high",
    selector: ".live-driving-toggle, #live-driving-traces",
    message: `Opened=${opened}, closed=${closed}${openError ? `; open wait: ${openError}` : ""}${closeError ? `; close wait: ${closeError}` : ""}.`,
    measurements: { initialState, openState, closeState, openError, closeError },
  });
  const attrEquals = (value, expected) => typeof value === "string" && value.toLowerCase() === expected;
  const disclosureAriaValid = attrEquals(initialState.ariaExpanded, "false")
    && attrEquals(openState.ariaExpanded, "true")
    && attrEquals(closeState.ariaExpanded, "false")
    && attrEquals(initialState.bodyAriaHidden, "true")
    && attrEquals(openState.bodyAriaHidden, "false")
    && attrEquals(closeState.bodyAriaHidden, "true");
  addCheck(run, "Live driving traces exposes explicit ARIA boolean states", disclosureAriaValid ? "pass" : "fail", {
    severity: "medium",
    selector: ".live-driving-toggle, #live-driving-traces",
    message: disclosureAriaValid
      ? "aria-expanded and aria-hidden expose explicit true/false values in every phase."
      : "One or more ARIA state attributes were absent or did not expose an explicit true/false value.",
    measurements: { initialState, openState, closeState },
  });
  await capture(page, run, "live-dashboard");
}

async function exerciseSettings(page, run) {
  await navigateTo(page, run, "Settings", "Settings", ".settings-hub-grid");
  await auditLayout(page, run, [".settings-heading", ".settings-hub-grid"]);
  await auditVisibleControls(page, run);
  const themeButtons = page.locator('[aria-label="Theme color"] .theme-color-swatch');
  const themeCount = await themeButtons.count();
  const initialTheme = await page.evaluate(() => ({
    namedTheme: document.querySelector('[aria-label="Theme color"] .theme-color-swatch.active')?.getAttribute("aria-label") || null,
    customActive: document.querySelector("label.theme-color-custom")?.classList.contains("active") || false,
    customValue: document.querySelector('input[aria-label="Choose custom theme color"]')?.value || null,
    appClass: document.querySelector(".app-shell")?.className || null,
    accent: getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--accent").trim(),
  }));
  const activeTheme = initialTheme.namedTheme;
  const themeStates = [];
  for (let index = 0; index < themeCount; index += 1) {
    const button = themeButtons.nth(index);
    const label = await button.getAttribute("aria-label");
    await button.click();
    let activationError = null;
    try {
      await waitForCondition(page, `Theme ${label} activates`, (name) => [...document.querySelectorAll('[aria-label="Theme color"] .theme-color-swatch')]
        .find((item) => item.getAttribute("aria-label") === name)?.classList.contains("active"), label);
    } catch (error) {
      activationError = error.message;
    }
    themeStates.push(await page.evaluate(({ name, error }) => ({
      name,
      activationError: error,
      ariaPressed: [...document.querySelectorAll('[aria-label="Theme color"] .theme-color-swatch')]
        .find((item) => item.getAttribute("aria-label") === name)?.getAttribute("aria-pressed"),
      appClass: document.querySelector(".app-shell")?.className,
      accent: getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--accent").trim(),
      activeLabel: document.querySelector('[aria-label="Theme color"] .theme-color-swatch.active')?.getAttribute("aria-label") || null,
    }), { name: label, error: activationError }));
  }
  const custom = page.getByLabel("Choose custom theme color", { exact: true });
  const customTestValue = initialTheme.customValue?.toLowerCase() === "#3388cc" ? "#cc8833" : "#3388cc";
  const dispatchCustomColor = (value) => custom.evaluate((element, nextValue) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    setter?.call(element, nextValue);
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: nextValue }));
  }, value);
  await dispatchCustomColor(customTestValue);
  await waitForCondition(page, "Custom theme activates", (value) => {
    const input = document.querySelector('input[aria-label="Choose custom theme color"]');
    return document.querySelector("label.theme-color-custom")?.classList.contains("active")
      && input?.value.toLowerCase() === value;
  }, customTestValue);
  const customState = await page.evaluate((testValue) => ({
    testValue,
    inputValue: document.querySelector('input[aria-label="Choose custom theme color"]')?.value || null,
    appClass: document.querySelector(".app-shell")?.className,
    accent: getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--accent").trim(),
    customLabelActive: document.querySelector("label.theme-color-custom")?.classList.contains("active") || false,
    shellRect: (() => {
      const bounds = document.querySelector(".app-shell")?.getBoundingClientRect();
      return bounds ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } : null;
    })(),
    viewport: { width: innerWidth, height: innerHeight },
  }), customTestValue);
  await capture(page, run, "settings-custom-theme");
  let restorationError = null;
  if (activeTheme) {
    await page.locator(`[aria-label="Theme color"] .theme-color-swatch[aria-label="${activeTheme}"]`).click();
    try {
      await waitForCondition(page, `Theme ${activeTheme} restores`, (name) => [...document.querySelectorAll('[aria-label="Theme color"] .theme-color-swatch')]
        .find((item) => item.getAttribute("aria-label") === name)?.classList.contains("active"), activeTheme);
    } catch (error) {
      restorationError = error.message;
    }
  } else if (initialTheme.customActive && initialTheme.customValue) {
    await dispatchCustomColor(initialTheme.customValue);
    try {
      await waitForCondition(page, "Original custom theme restores", (state) => {
        const input = document.querySelector('input[aria-label="Choose custom theme color"]');
        const shell = document.querySelector(".app-shell");
        return document.querySelector("label.theme-color-custom")?.classList.contains("active")
          && input?.value.toLowerCase() === state.value.toLowerCase()
          && getComputedStyle(shell).getPropertyValue("--accent").trim() !== state.testAccent;
      }, { value: initialTheme.customValue, testAccent: customState.accent });
    } catch (error) {
      restorationError = error.message;
    }
  }
  const restorationState = await page.evaluate(() => ({
    namedTheme: document.querySelector('[aria-label="Theme color"] .theme-color-swatch.active')?.getAttribute("aria-label") || null,
    customActive: document.querySelector("label.theme-color-custom")?.classList.contains("active") || false,
    customValue: document.querySelector('input[aria-label="Choose custom theme color"]')?.value || null,
    appClass: document.querySelector(".app-shell")?.className || null,
    accent: getComputedStyle(document.querySelector(".app-shell")).getPropertyValue("--accent").trim(),
  }));
  const themeAriaState = await themeButtons.evaluateAll((buttons) => buttons.map((button) => ({
    label: button.getAttribute("aria-label"),
    ariaPressed: button.getAttribute("aria-pressed"),
    active: button.classList.contains("active"),
  })));
  const themesPassed = themeCount === 9
    && themeStates.every((state) => state.accent && state.activeLabel === state.name && !state.activationError)
    && Boolean(customState.accent)
    && customState.customLabelActive
    && Math.abs(customState.shellRect.width - customState.viewport.width) <= 1
    && !restorationError;
  addCheck(run, "Settings applies all named themes and a custom theme", themesPassed ? "pass" : "fail", {
    severity: "high",
    selector: '[aria-label="Theme color"]',
    message: `Exercised ${themeCount} named themes and one distinct custom color; restored ${activeTheme || "the original custom theme"}.`,
    measurements: { initialTheme, activeTheme, themeCount, themeStates, customState, restorationState, restorationError, themeAriaState },
  });
  const customShellStable = Math.abs(customState.shellRect.width - customState.viewport.width) <= 1
    && Math.abs(customState.shellRect.height - customState.viewport.height) <= 1;
  addCheck(run, "Custom theme preserves full application-shell geometry", customShellStable ? "pass" : "fail", {
    severity: "high",
    selector: ".app-shell.theme-color-custom, label.theme-color-custom",
    message: `Custom-theme shell is ${round(customState.shellRect.width)}x${round(customState.shellRect.height)}; viewport is ${customState.viewport.width}x${customState.viewport.height}.`,
    measurements: { customState },
  });
  const expectedActiveNamedThemes = activeTheme ? 1 : 0;
  const themeAriaValid = themeAriaState.every((state) => ["true", "false"].includes(state.ariaPressed?.toLowerCase()))
    && themeAriaState.filter((state) => state.ariaPressed?.toLowerCase() === "true").length === expectedActiveNamedThemes
    && (!activeTheme || themeAriaState.find((state) => state.active)?.ariaPressed?.toLowerCase() === "true");
  addCheck(run, "Theme swatches expose explicit aria-pressed states", themeAriaValid ? "pass" : "fail", {
    severity: "medium",
    selector: '[aria-label="Theme color"] .theme-color-swatch',
    message: themeAriaValid
      ? `Every named theme swatch exposes true or false; expected active named count ${expectedActiveNamedThemes}.`
      : "One or more swatches omit aria-pressed or expose an empty value instead of true/false.",
    measurements: { initialTheme, activeTheme, restorationState, themeAriaState },
  });
  await capture(page, run, "settings-themes");

  const folderDetails = page.locator("details.settings-disclosure").first();
  const folderSummary = folderDetails.locator("summary");
  if (!(await folderDetails.evaluate((element) => element.open))) await folderSummary.click();
  await page.waitForFunction(() => document.querySelector("details.settings-disclosure")?.open === true, null, { timeout: 3_000 });
  const folderInputs = await folderDetails.locator("input").count();
  await folderSummary.click();
  await page.waitForFunction(() => document.querySelector("details.settings-disclosure")?.open === false, null, { timeout: 3_000 });

  const connections = page.locator(".settings-accordion-toggle").filter({ hasText: "Connections" }).first();
  if (await connections.getAttribute("aria-expanded") !== "false") await connections.click();
  await connections.click();
  await page.waitForFunction(() => [...document.querySelectorAll(".settings-accordion-toggle")].find((item) => item.textContent.includes("Connections"))?.getAttribute("aria-expanded") === "true", null, { timeout: 3_000 });
  await connections.click();
  await page.waitForFunction(() => [...document.querySelectorAll(".settings-accordion-toggle")].find((item) => item.textContent.includes("Connections"))?.getAttribute("aria-expanded") === "false", null, { timeout: 3_000 });

  const troubleshooting = page.locator(".diagnostics-toggle");
  if (await troubleshooting.getAttribute("aria-expanded") !== "false") await troubleshooting.click();
  await troubleshooting.click();
  await page.waitForFunction(() => document.querySelector(".diagnostics-toggle")?.getAttribute("aria-expanded") === "true", null, { timeout: 3_000 });
  await troubleshooting.click();
  await page.waitForFunction(() => document.querySelector(".diagnostics-toggle")?.getAttribute("aria-expanded") === "false", null, { timeout: 3_000 });
  addCheck(run, "Settings disclosures open and close", folderInputs === 3 ? "pass" : "fail", {
    severity: "medium",
    selector: "details.settings-disclosure, .settings-accordion-toggle",
    message: `Folder disclosure rendered ${folderInputs} fields; Connections and Troubleshooting opened and closed.`,
    measurements: { folderInputs },
  });

  const guide = page.getByRole("button", { name: "Guide me", exact: true });
  await guide.click();
  const dialog = page.getByRole("dialog", { name: "Prepare the Coach folder" });
  await dialog.waitFor({ state: "visible" });
  const dialogMetrics = await elementMetrics(page, ".settings-guide");
  const guideFits = dialogMetrics.clippedX <= 1 && dialogMetrics.clippedY <= 1 && dialogMetrics.inViewport;
  addCheck(run, "Backup guide fits the viewport", guideFits ? "pass" : "fail", {
    severity: "high",
    selector: ".settings-guide",
    message: `Backup guide clipping is ${round(dialogMetrics.clippedX)} px horizontal and ${round(dialogMetrics.clippedY)} px vertical.`,
    measurements: dialogMetrics,
  });
  await capture(page, run, "settings-backup-guide");
  await dialog.locator("button.button.secondary").filter({ hasText: /^Close$/ }).click();
  await dialog.waitFor({ state: "hidden" });

  const reducedMotion = page.locator("label.toggle-row").filter({ hasText: "Reduce motion" }).locator('input[type="checkbox"]');
  const initiallyReduced = await reducedMotion.isChecked();
  await reducedMotion.click();
  await page.waitForFunction((expected) => document.querySelector(".app-shell")?.classList.contains("reduced-motion") === expected, !initiallyReduced, { timeout: 3_000 });
  const toggledState = await page.evaluate(() => ({
    reducedClass: document.querySelector(".app-shell")?.classList.contains("reduced-motion"),
    transitionDuration: getComputedStyle(document.querySelector(".settings-hub-card")).transitionDuration,
    animationDuration: getComputedStyle(document.querySelector(".settings-hub-card")).animationDuration,
    activeTag: document.activeElement?.tagName || null,
  }));
  await reducedMotion.click();
  await page.waitForFunction((expected) => document.querySelector(".app-shell")?.classList.contains("reduced-motion") === expected, initiallyReduced, { timeout: 3_000 });
  addCheck(run, "Reduced motion toggles the application mode and restores", toggledState.reducedClass === !initiallyReduced ? "pass" : "fail", {
    severity: "high",
    selector: ".toggle-row input[type=checkbox]",
    message: `Reduced motion changed from ${initiallyReduced} to ${toggledState.reducedClass} and was restored.`,
    measurements: { initiallyReduced, toggledState },
  });
  const workspace = page.locator("#main-content");
  await workspace.press("End");
  await page.waitForTimeout(200);
  await auditVisibleControls(page, run);
  await capture(page, run, "settings-bottom-disclosures");
  await workspace.press("Home");
}

async function runMajorPagesSuite(page, run) {
  let pages = [
    ["Home page", exerciseHome, 30_000],
    ["Planning page", exercisePlanning, 60_000],
    ["Starting Tune page", exerciseStartingTune, 75_000],
    ["Progressive Tuning page", exerciseProgressiveTuning, 75_000],
    ["Live telemetry page", exerciseLive, 60_000],
    ["Settings page", exerciseSettings, 75_000],
  ];
  if (suiteMode === "deltas") {
    pages = pages.filter(([name]) => ["Home page", "Live telemetry page", "Settings page"].includes(name));
  }
  for (const [name, exercise, timeoutMs] of pages) {
    await attemptPage(page, run, name, () => exercise(page, run), timeoutMs);
  }
}

async function runRaceAnalysisSuite(page, run) {
  await openIowa(page, run);
  await auditLayout(page, run, [
    ".analysis-workspace-page",
    ".race-analysis-toolbar",
    ".telemetry-workstation-grid",
    ".telemetry-context-column",
    ".track-panel",
    ".lap-rail",
    ".lap-rail-footer",
    "[data-analysis-trace-studio]",
  ], true);
  await capture(page, run, "iowa-telemetry-baseline");
  for (const [selector, label] of [
    ['[data-context-toggle="track"]', "Track position toggle"],
    ['[data-context-toggle="laps"]', "Laps and runs toggle"],
    ['[aria-label="Fit track"]', "Fit track"],
    ['[aria-label="Customize trace charts"]', "Customize"],
    ['[aria-label="View traces full screen"]', "Trace full-screen"],
  ]) {
    await checkHoverStability(page, run, selector, label);
  }
  await exerciseContextToggles(page, run);
  await exerciseSplitter(page, run);
  await exerciseMap(page, run);
  await exerciseLapSelection(page, run);
  await exerciseSpotlight(page, run);
  await exerciseCustomizeAndLayouts(page, run);
  await exerciseFullscreen(page, run);
  await exerciseTechnical(page, run);
  await exerciseReplay(page, run);
}

async function runViewport(executablePath, viewport) {
  const viewportName = `${viewport.width}x${viewport.height}`;
  const run = { viewportName, viewport, pageName: "Startup" };
  const context = await browser.newContext({ viewport, reducedMotion: "no-preference" });
  const page = await context.newPage();
  page.setDefaultTimeout(8_000);
  page.setDefaultNavigationTimeout(20_000);
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    report.consoleErrors.push({ viewport: viewportName, text: message.text(), location: message.location() });
    writeReport();
  });
  page.on("pageerror", (error) => {
    report.consoleErrors.push({ viewport: viewportName, text: error.message, pageError: true });
    writeReport();
  });
  page.on("response", (response) => {
    if (response.status() < 400) return;
    report.networkErrors.push({ viewport: viewportName, status: response.status(), method: response.request().method(), url: response.url() });
    writeReport();
  });
  page.on("requestfailed", (request) => {
    report.networkErrors.push({ viewport: viewportName, method: request.method(), url: request.url(), failure: request.failure()?.errorText || "request failed" });
    writeReport();
  });
  page.on("websocket", (socket) => {
    const entry = { viewport: viewportName, url: socket.url(), openedAt: new Date().toISOString(), closedAt: null, errors: [] };
    report.webSockets.push(entry);
    socket.on("socketerror", (error) => {
      entry.errors.push(String(error));
      writeReport();
    });
    socket.on("close", () => {
      entry.closedAt = new Date().toISOString();
      writeReport();
    });
    writeReport();
  });
  try {
    const startupReady = await attempt(run, "Preview reaches interactive UI", async () => {
      await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 20_000 });
      await page.waitForFunction(() => Boolean(window.Blazor) && Boolean(document.querySelector(".app-shell")), null, { timeout: 30_000 });
      await page.getByRole("button", { name: "Settings", exact: true }).waitFor({ state: "visible", timeout: 30_000 });
      return true;
    }, { severity: "critical" });
    if (!startupReady) {
      await captureFailureState(page, run, "startup");
      return;
    }
    const shellStartup = await page.evaluate(() => {
      const shell = document.querySelector(".app-shell");
      const workspace = document.querySelector("#main-content");
      const shellBounds = shell?.getBoundingClientRect();
      const shellStyle = shell ? getComputedStyle(shell) : null;
      return {
        innerWidth,
        innerHeight,
        devicePixelRatio,
        visualViewport: window.visualViewport ? {
          width: window.visualViewport.width,
          height: window.visualViewport.height,
          scale: window.visualViewport.scale,
          offsetLeft: window.visualViewport.offsetLeft,
          offsetTop: window.visualViewport.offsetTop,
        } : null,
        shellClass: shell?.className || null,
        shellInlineStyle: shell?.getAttribute("style") || null,
        shellRect: shellBounds ? { x: shellBounds.x, y: shellBounds.y, width: shellBounds.width, height: shellBounds.height } : null,
        shellComputed: shellStyle ? { width: shellStyle.width, height: shellStyle.height, transform: shellStyle.transform, zoom: shellStyle.zoom, gridTemplateColumns: shellStyle.gridTemplateColumns } : null,
        workspaceClientWidth: workspace?.clientWidth || null,
        bodyClientWidth: document.body.clientWidth,
      };
    });
    const shellFillsViewport = shellStartup.shellRect
      && Math.abs(shellStartup.shellRect.width - shellStartup.innerWidth) <= 1
      && Math.abs(shellStartup.shellRect.height - shellStartup.innerHeight) <= 1
      && shellStartup.visualViewport?.scale === 1;
    addCheck(run, "Application shell starts at the requested viewport geometry", shellFillsViewport ? "pass" : "fail", {
      severity: "critical",
      selector: ".app-shell, #main-content",
      message: shellFillsViewport
        ? `Shell matches ${shellStartup.innerWidth}x${shellStartup.innerHeight} at DPR ${shellStartup.devicePixelRatio}.`
        : `Shell ${round(shellStartup.shellRect?.width)}x${round(shellStartup.shellRect?.height)} does not match ${shellStartup.innerWidth}x${shellStartup.innerHeight}.`,
      measurements: shellStartup,
    });
    if (!shellFillsViewport) {
      await captureFailureState(page, run, "startup-shell-geometry");
      return;
    }
    if (suiteMode === "all" || suiteMode === "race") {
      await attempt(run, "Iowa Race Analysis interaction matrix", () => runRaceAnalysisSuite(page, run), { severity: "critical" });
    }
    if (suiteMode === "all" || suiteMode === "pages" || suiteMode === "deltas") {
      await runMajorPagesSuite(page, run);
    }
  } finally {
    await context.close().catch(() => {});
    writeReport();
  }
}

async function main() {
  ensureDirectory(outputDirectory);
  writeReport();
  const executablePath = [
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  ].find(fs.existsSync);
  if (!executablePath) throw new Error("Microsoft Edge was not found.");
  report.environment = {
    executablePath,
    node: process.execPath,
    platform: process.platform,
    suiteMode,
    viewports,
  };
  browser = await chromium.launch({
    executablePath,
    headless: true,
    args: ["--disable-gpu-sandbox", "--disable-features=Translate"],
  });
  try {
    for (const viewport of viewports) {
      await runViewport(executablePath, viewport);
    }
    const relevantNetworkErrors = report.networkErrors.filter((item) => !/favicon\.ico(?:$|\?)/i.test(item.url));
    const faviconErrors = report.networkErrors.filter((item) => /favicon\.ico(?:$|\?)/i.test(item.url));
    const suiteRun = { viewportName: "all", pageName: "Application shell" };
    addCheck(suiteRun, "No unexpected HTTP or request failures", relevantNetworkErrors.length === 0 ? "pass" : "fail", {
      severity: "high",
      message: `${relevantNetworkErrors.length} unexpected network failures were recorded.`,
      measurements: { errors: relevantNetworkErrors },
    });
    if (faviconErrors.length > 0) {
      addCheck(suiteRun, "Application favicon resolves", "fail", {
        severity: "low",
        selector: "head link[rel~='icon']",
        message: `${faviconErrors.length} request(s) for /favicon.ico returned HTTP ${faviconErrors[0].status}; the Preview head does not declare a favicon.`,
        measurements: { errors: faviconErrors },
      });
    }
    addCheck(suiteRun, "No browser console or page errors", report.consoleErrors.length === 0 ? "pass" : "fail", {
      severity: "high",
      message: `${report.consoleErrors.length} console/page errors were recorded.`,
      measurements: { errors: report.consoleErrors },
    });
    report.status = report.defects.length === 0 ? "pass" : "fail";
  } finally {
    await browser.close().catch(() => {});
    report.completedAt = new Date().toISOString();
    writeReport();
  }
  process.stdout.write(`${JSON.stringify({ status: report.status, summary: report.summary, reportPath }, null, 2)}\n`);
}

main().catch(async (error) => {
  report.status = "error";
  report.completedAt = new Date().toISOString();
  report.fatalError = { message: error.message, stack: error.stack };
  await browser?.close().catch(() => {});
  writeReport();
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
