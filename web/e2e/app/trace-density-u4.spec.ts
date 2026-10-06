import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { decodeCcol } from "../../src/investigation/ccol";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { routeStatic, type StaticAnalysisFiles } from "../../src/investigation/fixtures/static-api";
import { buildSyntheticAnalysis } from "../../src/investigation/fixtures/synthetic";
import { createTraceData, signalColumn } from "../../src/investigation/geometry/trace-model";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// Backlog #77 (round 5, U4): the traces at 640 bins per lap, drawn through the measured bins with a monotone cubic, and a cursor
// that moves continuously, reading every value between the two measured bins around it. RI_U4_BINS=160 serves the old density
// (the "before" screenshots run against the previous build).
const BINS = Number(process.env.RI_U4_BINS ?? 640);
const ANALYSIS = buildSyntheticAnalysis({ laps: 82, bins: BINS, analysisId: "an-103" });
const DATA = createTraceData(ANALYSIS.catalog, decodeCcol(ANALYSIS.ccol.slice(0)));
const SHOTS = path.resolve(process.cwd(), process.env.RI_U4_SHOTS ?? path.join("target", "round5", "U4"));
const SHOT_TAG = process.env.RI_U4_TAG ?? "after";
/** The G2 race's view files: the analysis pipeline's own output for one synthetic recording (test-data/g2, or the previous ones). */
const G2_DIR = path.resolve(process.cwd(), process.env.RI_U4_G2 ?? path.join("..", "test-data", "g2"));

function g2Files(): StaticAnalysisFiles {
  const read = (name: string): string => readFileSync(path.join(G2_DIR, name), "utf8");
  const manifest = JSON.parse(read("fixture.json")) as { analysis_id: string };
  return { analysisId: manifest.analysis_id, race: read("race.json"), runs: read("runs.json"), investigation: read("investigation.json"), track: read("track.json"), catalog: read("traces-catalog.json"), ccol: new Uint8Array(readFileSync(path.join(G2_DIR, "traces.ccol"))) };
}

/** Serves the G2 race as session 103's analysis. */
async function serveG2(page: Page): Promise<void> {
  const files = g2Files();
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeStatic(files, `${url.pathname.replace("/an-103/", `/${files.analysisId}/`)}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

async function serve(page: Page, requests: string[]): Promise<void> {
  page.on("request", (request) => requests.push(request.url()));
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

async function openTraces(page: Page): Promise<void> {
  await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  await page.locator("[data-analysis-trace-path][data-signal='speed']").first().waitFor();
  await page.waitForTimeout(300);
}

/** The chart surface in the viewport (on a phone it sits below the map). */
async function chartBox(page: Page): Promise<{ x: number; y: number; width: number; height: number }> {
  const surface = page.locator(".ri-chart-surface");
  await surface.evaluate((element) => element.scrollIntoView({ block: "start" }));
  await page.waitForTimeout(150);
  return (await surface.boundingBox())!;
}

/** The speed readout beside the map at a pointer x: the selected laps' mean speed, linear between the two bins around the cursor. */
function expectedSpeed(selected: readonly number[], fraction: number): number {
  const speed = signalColumn(DATA, "speed")!;
  const mean = (bin: number): number => {
    let sum = 0;
    for (const lap of selected) sum += speed[DATA.lapIndex.get(lap)! * DATA.bins + bin]!;
    return sum / selected.length;
  };
  const x = DATA.x;
  if (fraction <= x[0]!) return mean(0);
  if (fraction >= x[DATA.bins - 1]!) return mean(DATA.bins - 1);
  let upper = 1;
  while (x[upper]! <= fraction) upper++;
  const t = (fraction - x[upper - 1]!) / (x[upper]! - x[upper - 1]!);
  return mean(upper - 1) + (mean(upper) - mean(upper - 1)) * t;
}

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const width = device.viewport.width;

    test("traces are drawn through every measured bin the width allows, as monotone curves; gear as steps", async ({ page }) => {
      test.skip(BINS !== 640, "after only");
      await serve(page, []);
      await openTraces(page);
      const surface = (await page.locator(".ri-chart-surface").boundingBox())!;
      const segments = await page.locator("[data-analysis-trace-path][data-signal='speed']").evaluateAll((paths) =>
        paths.map((p) => ({ curves: (p.getAttribute("d") ?? "").split("C").length - 1, lines: (p.getAttribute("d") ?? "").split("L").length - 1, laps: (p.getAttribute("data-laps") ?? "").split(",").length })));
      const curves = segments.reduce((sum, s) => sum + s.curves, 0);
      const laps = segments.reduce((sum, s) => sum + s.laps, 0);
      // Up to 1.25 points per pixel of plot, at most the 640 measured bins; the lines are curves, not straight segments.
      const perLap = Math.min(640, Math.ceil((surface.width - 16) * 1.25)) - 1;
      expect(curves).toBeGreaterThanOrEqual(laps * perLap - laps * 2);
      expect(segments.every((s) => s.lines === 0)).toBe(true);
      const gear = await page.locator("[data-analysis-trace-path][data-signal='gear']").evaluateAll((paths) => paths.map((p) => p.getAttribute("d") ?? ""));
      for (const d of gear) {
        expect(d).not.toContain("C");
        expect(d).toContain("H");
      }
      await expectNoHorizontalScroll(page);
    });

    test("the cursor reads between the two measured bins around it, and pointer movement makes no request", async ({ page }) => {
      test.skip(BINS !== 640, "after only");
      const requests: string[] = [];
      await serve(page, requests);
      await openTraces(page);
      const selected = await page.locator(".ri-lap-row.selected").evaluateAll((rows) => rows.map((row) => Number(row.getAttribute("data-lap"))));
      expect(selected.length).toBeGreaterThan(0);
      const surface = await chartBox(page);
      const y = surface.y + Math.min(60, surface.height * 0.3);
      const field = page.locator("[data-field='speed']");
      const before = requests.length;
      const texts: string[] = [];
      const plotWidth = surface.width - 16;
      // Twelve pointer positions a pixel apart across a few bins.
      for (let step = 0; step < 12; step++) {
        const clientX = Math.round(surface.x + 6 + plotWidth * 0.37) + step;
        await page.mouse.move(clientX, y);
        await page.waitForTimeout(140);
        const text = (await field.textContent()) ?? "";
        texts.push(text);
        const fraction = Math.min(1, Math.max(0, (clientX - surface.x - 6) / plotWidth));
        const want = expectedSpeed(selected, fraction);
        expect(Math.abs(Number(text) - want), `${text} at ${fraction}`).toBeLessThanOrEqual(0.051);
      }
      // A continuous cursor: more distinct readings than bins crossed would give with a cursor that steps from bin to bin.
      const binsCrossed = Math.ceil((11 / plotWidth) * 640) + 1;
      expect(new Set(texts).size).toBeGreaterThan(Math.min(binsCrossed, 11) - 1);
      expect(requests.slice(before)).toEqual([]);
    });

    test("Traces screenshots of the G2 race (the analysis pipeline's own traces) for the release record", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await serveG2(page);
      await openTraces(page);
      const surface = width < 700 ? await chartBox(page) : (await page.locator(".ri-chart-surface").boundingBox())!;
      const x = surface.x + 6 + (surface.width - 16) * 0.47;
      await page.mouse.move(x, surface.y + Math.min(80, surface.height * 0.4));
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(SHOTS, `traces-g2-${SHOT_TAG}-${width}.png`), fullPage: width < 700 });
      const viewport = page.viewportSize()!;
      const clip = { x: Math.max(surface.x, x - 260), y: Math.max(0, surface.y), width: Math.min(surface.width, 520), height: Math.min(surface.height, 300, viewport.height - Math.max(0, surface.y)) };
      await page.screenshot({ path: path.join(SHOTS, `traces-g2-${SHOT_TAG}-closeup-${width}.png`), clip });
    });

    test(`Traces screenshots at ${BINS} bins for the release record`, async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await serve(page, []);
      await openTraces(page);
      const surface = width < 700 ? await chartBox(page) : (await page.locator(".ri-chart-surface").boundingBox())!;
      await page.mouse.move(surface.x + 6 + (surface.width - 16) * 0.38, surface.y + Math.min(80, surface.height * 0.4));
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(SHOTS, `traces-${SHOT_TAG}-${width}.png`), fullPage: width < 700 });
      // A close-up of the top rows at the cursor.
      const viewport = page.viewportSize()!;
      const clip = { x: surface.x, y: Math.max(0, surface.y), width: Math.min(surface.width, 520), height: Math.min(surface.height, 260, viewport.height - Math.max(0, surface.y)) };
      clip.x = Math.max(surface.x, surface.x + 6 + (surface.width - 16) * 0.38 - clip.width / 2);
      await page.screenshot({ path: path.join(SHOTS, `traces-${SHOT_TAG}-closeup-${width}.png`), clip });
    });
  });
}
