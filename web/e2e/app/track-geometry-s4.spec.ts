import { type Page, expect, test } from "@playwright/test";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

/**
 * Round 4 task S4 (backlog #65): the track outline comes from the backend's clean-lap centreline. The browser draws it as
 * delivered, keeps only a current outline (transform.longitude_scale present) in storage, and drops outlines stored before.
 */

/** A D-shaped loop in the backend's normalized space: a curved front stretch, a straight back stretch. */
function dOval(): { pct: number[]; x: number[]; y: number[] } {
  const n = 500;
  const pct: number[] = [];
  const x: number[] = [];
  const y: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    pct.push(i / n);
    x.push(0.5 + 0.5 * Math.cos(t));
    y.push(0.3 + Math.max(-0.12, 0.3 * Math.sin(t)));
  }
  return { pct, x, y };
}

/** The D-oval turned by `tilt` radians about (0.5, 0.3), with its start/finish line and a pit road on the straight (round 5, #81). */
function tiltedOval(tilt: number) {
  const { pct, x, y } = dOval();
  const turn = (px: number, py: number) => ({ x: 0.5 + (px - 0.5) * Math.cos(tilt) - (py - 0.3) * Math.sin(tilt), y: 0.3 + (px - 0.5) * Math.sin(tilt) + (py - 0.3) * Math.cos(tilt) });
  const main = x.map((value, i) => turn(value, y[i]!));
  const pit = [0.25, 0.37, 0.5, 0.62, 0.75].map((px) => turn(px, 0.2));
  return {
    main_path: { pct, x: main.map((p) => p.x), y: main.map((p) => p.y) },
    pit_lane: { pct: [0.7, 0.72, 0.74, 0.76, 0.78], x: pit.map((p) => p.x), y: pit.map((p) => p.y) },
    start_finish: { a: turn(0.98, 0.3), b: turn(1.02, 0.3) },
  };
}

const TRANSFORM = { source_bounds: { minimum_x: -92.776, maximum_x: -92.766, minimum_y: 36.268, maximum_y: 36.281 }, normalization_scale: 0.013 };

async function serveTrack(page: Page, current: boolean, layers: Record<string, unknown> = {}): Promise<void> {
  await page.route("**/api/v1/analyses/*/track", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/")[4] ?? "");
    return route.fulfill({
      json: {
        analysis_id: id, projection_version: 1, schema_version: 1, profile_version: current ? "post-race-foundations-v24" : "post-race-foundations-v23",
        coordinate_space: "normalized_local_vector", status: "usable", main_path: dOval(), provenance: {},
        pit_lane: { pct: [0.7, 0.72, 0.74, 0.76, 0.78], x: [0.25, 0.37, 0.5, 0.62, 0.75], y: [0.22, 0.22, 0.22, 0.22, 0.22] },
        transform: current ? { ...TRANSFORM, longitude_scale: 0.806 } : TRANSFORM,
        ...layers,
      },
    });
  });
}

/**
 * Track outlines from the old desktop app's geometry store on this machine, when present (never in the repository): real shapes for
 * the screenshots of the turned maps. That store predates the longitude scale, so x is scaled by cos(latitude) here as the current
 * backend does. No store, no screenshots.
 */
const STORE = "C:/Users/joshu/Documents/iRacing Coach/data/track-geometry";
type StoredPoint = { lap_pct: number; x: number; y: number };
function storedOutlines(): { name: string; layers: Record<string, unknown> }[] {
  const files: [string, string][] = [["new-hampshire", "131-oval-0eaa03786497"], ["indianapolis", "133-oval-ca7f4521cc47"], ["daytona", "191-oval-e2b5ba4febd5"], ["kentucky", "371-oval-5db7470bf5a9"], ["iowa", "559-oval-d97407cc953b"]];
  return files.flatMap(([name, file]) => {
    const source = `${STORE}/${file}.json`;
    if (!existsSync(source)) return [];
    const raw = JSON.parse(readFileSync(source, "utf8")) as { main_path: StoredPoint[]; pit_lane: StoredPoint[]; start_finish_line: { a: { x: number; y: number }; b: { x: number; y: number } } | null; transform: { source_bounds: { minimum_y: number; maximum_y: number } } };
    const k = Math.cos((((raw.transform.source_bounds.minimum_y + raw.transform.source_bounds.maximum_y) / 2) * Math.PI) / 180);
    const series = (points: StoredPoint[]) => ({ pct: points.map((p) => p.lap_pct), x: points.map((p) => p.x * k), y: points.map((p) => p.y) });
    const line = raw.start_finish_line;
    return [{ name, layers: { main_path: series(raw.main_path), pit_lane: series(raw.pit_lane), start_finish: line ? { a: { x: line.a.x * k, y: line.a.y }, b: { x: line.b.x * k, y: line.b.y } } : null } }];
  });
}
const SHOTS = path.resolve(process.cwd(), process.env.U3_SHOTS ?? path.join("target", "round5", "U3"));

const stored = (page: Page, prefix: string) =>
  page.evaluate((p) => Object.keys(localStorage).filter((k) => k.startsWith(p)), prefix);

const dir = process.env.COACH_SCREENS_DIR;

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    test("a current outline is drawn closed and kept; one stored before is dropped", async ({ page }) => {
      await page.addInitScript(() => {
        // Outlines stored by older builds (key version 1, and 2 from before the outline was turned, #81): never read again.
        if (!localStorage.getItem("s4-seeded")) {
          for (const version of [1, 2]) localStorage.setItem(`coach.silhouette.${version}.bristol-motor-speedway-oval`, JSON.stringify({ d: "M0,0L1,1", tick: [0, 0, 1, 1], width: 118, height: 78 }));
          localStorage.setItem("s4-seeded", "1");
        }
      });
      await serveTrack(page, true);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const sil = page.getByTestId("silhouette").first();
      await expect(sil).toBeVisible();
      await expect(sil.locator("path")).toHaveAttribute("d", /^M[\d.,L-]+Z$/);
      await expect(sil.locator("path")).not.toHaveAttribute("d", "M0,0L1,1");
      await expect.poll(() => stored(page, "coach.silhouette.3.").then((k) => k.length)).toBeGreaterThan(0);
      await expectNoHorizontalScroll(page);
      if (dir) await page.screenshot({ path: `${dir}/sessions-${d.viewport.width}.png` });
    });

    test("an outline built before the current rule is drawn but not kept", async ({ page }) => {
      await serveTrack(page, false);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByTestId("silhouette").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(await stored(page, "coach.silhouette.3.")).toEqual([]);
    });

    test("the investigation map draws the centreline closed with the start line on it", async ({ page }) => {
      await serveTrack(page, true);
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      const map = page.locator("svg[data-analysis-track-map]");
      await expect(map).toBeVisible();
      await expect(map.locator("path.ri-main-ribbon")).toHaveAttribute("d", /^M [\d. L-]+ Z$/);
      await expect(map.locator("line.ri-start-line")).toHaveCount(1);
      // Pit road is its own faint path: drawn, but under half strength and without a halo cutting the track.
      const pit = map.locator("path.ri-pit-road");
      await expect(pit).toHaveCount(1);
      expect(Number(await pit.evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(0.6);
      await expect(map.locator("path.ri-pit-halo")).toBeHidden();
      await expectNoHorizontalScroll(page);
      if (dir) await page.locator(".ri-track-panel").screenshot({ path: `${dir}/traces-map-${d.viewport.width}.png` });
    });

    // Round 5, U3 (#81): every map and silhouette turned so the centreline's long axis is horizontal, every layer with it.

    test("a tilted outline is turned so its long axis is horizontal; start/finish and pit road turn with it", async ({ page }) => {
      await serveTrack(page, true, tiltedOval(1.05));
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      const map = page.locator("svg[data-analysis-track-map]");
      await expect(map.locator("path.ri-main-ribbon")).toBeVisible();
      expect(Math.abs(Number(await map.getAttribute("data-map-rotation")))).toBeGreaterThan(40);
      const shape = await page.evaluate(() => {
        const ribbon = document.querySelector<SVGPathElement>("svg[data-analysis-track-map] path.ri-main-ribbon")!;
        const box = ribbon.getBBox();
        const total = ribbon.getTotalLength();
        const near = (x: number, y: number) => {
          let best = Infinity;
          for (let i = 0; i <= 400; i++) {
            const p = ribbon.getPointAtLength((total * i) / 400);
            best = Math.min(best, Math.hypot(p.x - x, p.y - y));
          }
          return best;
        };
        const start = document.querySelector<SVGLineElement>("svg[data-analysis-track-map] line.ri-start-line")!;
        const mid = { x: (start.x1.baseVal.value + start.x2.baseVal.value) / 2, y: (start.y1.baseVal.value + start.y2.baseVal.value) / 2 };
        const pit = document.querySelector<SVGPathElement>("svg[data-analysis-track-map] path.ri-pit-road")!;
        const a = pit.getPointAtLength(0);
        const b = pit.getPointAtLength(pit.getTotalLength());
        return { w: box.width, h: box.height, cy: box.y + box.height / 2, startGap: near(mid.x, mid.y), startY: mid.y, pitSlope: Math.abs((b.y - a.y) / (b.x - a.x)) };
      });
      expect(shape.w / shape.h).toBeGreaterThan(1.5);
      // The start/finish mark stays on the line and, turned, sits in the lower half.
      expect(shape.startGap).toBeLessThan(2);
      expect(shape.startY).toBeGreaterThan(shape.cy);
      // The pit road ran along the straight before the turn and still does: horizontal now.
      expect(shape.pitSlope).toBeLessThan(0.05);
      await expectNoHorizontalScroll(page);
      mkdirSync(SHOTS, { recursive: true });
      await page.locator(".ri-track-panel").screenshot({ path: path.join(SHOTS, `traces-map-turned-${d.viewport.width}.png`) });
    });

    test("a tilted silhouette on Sessions is turned the same way", async ({ page }) => {
      await serveTrack(page, true, tiltedOval(1.05));
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const sil = page.getByTestId("silhouette").first();
      await expect(sil).toBeVisible();
      const box = await sil.locator("path").evaluate((el: SVGPathElement) => {
        const b = el.getBBox();
        return { w: b.width, h: b.height };
      });
      expect(box.w / box.h).toBeGreaterThan(1.5);
      await expectNoHorizontalScroll(page);
      mkdirSync(SHOTS, { recursive: true });
      await sil.screenshot({ path: path.join(SHOTS, `silhouette-turned-${d.viewport.width}.png`) });
    });

    test("real outlines from this machine's geometry store are drawn turned (screenshots)", async ({ page }) => {
      const outlines = storedOutlines();
      test.skip(outlines.length === 0, "no local geometry store");
      mkdirSync(SHOTS, { recursive: true });
      for (const outline of outlines) {
        await page.unroute("**/api/v1/analyses/*/track");
        await serveTrack(page, true, outline.layers);
        await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
        await page.reload();
        const map = page.locator("svg[data-analysis-track-map]");
        await expect(map.locator("path.ri-main-ribbon")).toBeVisible();
        const box = await map.locator("path.ri-main-ribbon").evaluate((el: SVGPathElement) => {
          const b = el.getBBox();
          return { w: b.width, h: b.height };
        });
        expect(box.w, outline.name).toBeGreaterThanOrEqual(box.h);
        await page.locator(".ri-track-panel").screenshot({ path: path.join(SHOTS, `map-${outline.name}-${d.viewport.width}.png`) });
      }
    });
  });
}
