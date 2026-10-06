import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { decodeCcol } from "../../src/investigation/ccol";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildSectionsAnalysis, tiltAnalysis } from "../../src/investigation/fixtures/sections";
import type { SyntheticAnalysis } from "../../src/investigation/fixtures/synthetic";
import { buildMapGeometry } from "../../src/investigation/geometry/map-model";
import { createTraceData } from "../../src/investigation/geometry/trace-model";
import { lapContexts, tiresText, trafficCellText } from "../../src/investigation/ui/section-context";
import { sectionRacingLines } from "../../src/investigation/ui/section-lines";
import type { RaceViewData } from "../../src/investigation/types";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// Round 5, U3 (backlog #75, #76, #81): the Sections lap rows carry the laps on the tires and the lap's traffic; the map draws each
// listed lap's recorded racing line through the section (no parallel strands); the map is turned so the centreline's long axis is
// horizontal and the lines, the segment and the cursor marker turn with it. Session 103's analysis is the sections fixture.
const ANALYSIS = buildSectionsAnalysis("an-103");
const TILTED = tiltAnalysis(buildSectionsAnalysis("an-103"), 1.15);
type Entry = { section_id: string; fastest: { lap: number }[]; slowest: { lap: number }[] };
const SECTION_LAPS = (ANALYSIS.investigation as unknown as { section_laps: { sections: Entry[] } }).section_laps.sections;
const listed = (id: string) => {
  const e = SECTION_LAPS.find((s) => s.section_id === id)!;
  return [...e.fastest, ...[...e.slowest].reverse()].map((lap) => lap.lap);
};
const SECTION = { A: { startPct: 0.1, endPct: 0.3 }, B: { startPct: 0.42, endPct: 0.58 }, C: { startPct: 0.7, endPct: 0.86 } } as const;
const SHOTS = path.resolve(process.cwd(), process.env.U3_SHOTS ?? path.join("target", "round5", "U3"));

/** What the map should draw for a section: the projector's racing lines computed from the same fixture, in map units. */
function expectedLines(analysis: SyntheticAnalysis, id: keyof typeof SECTION): Map<number, string> {
  const data = createTraceData(analysis.catalog, decodeCcol(analysis.ccol));
  const map = buildMapGeometry(analysis.track)!;
  return new Map(sectionRacingLines(data, map, SECTION[id], listed(id)).map((line) => [line.lap, line.d]));
}

async function serve(page: Page, analysis: SyntheticAnalysis = ANALYSIS): Promise<void> {
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(analysis, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

const open = (page: Page, query = "") => page.goto(hash(`/d/${DRIVER}/s/103/sections${query}`));

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const width = device.viewport.width;

    test("each lap row shows the laps on its tires and its traffic share, with the side when the backend names it", async ({ page }) => {
      await serve(page);
      await open(page, "?section=B");
      const rows = page.locator(".ri-sl-list [data-lap]");
      await expect(rows).toHaveCount(6);
      await expect(page.locator(".ri-sl-cols")).toContainText("Tires");
      await expect(page.locator(".ri-sl-cols")).toContainText("Traffic");
      const contexts = lapContexts(ANALYSIS.race as RaceViewData, ANALYSIS.runs.runs as unknown[]);
      const laps = listed("B");
      expect(await rows.locator("[data-sl-tires]").allTextContents()).toEqual(laps.map((lap) => tiresText(contexts.get(lap)?.tires)));
      expect(await rows.locator("[data-sl-traffic]").allTextContents()).toEqual(laps.map((lap) => trafficCellText(contexts.get(lap)?.traffic)));
      // At least one listed lap names the side, and its traffic bar is as long as the share (the phone shows the share as text only).
      expect(laps.some((lap) => contexts.get(lap)?.traffic?.side)).toBe(true);
      if (width < 700) await expect(rows.first().locator(".ri-sl-tbar")).toBeHidden();
      else {
      const bar = await rows.first().evaluate((row) => {
        const track = row.querySelector(".ri-sl-tbar")!.getBoundingClientRect();
        const fill = row.querySelector(".ri-sl-tbar i")?.getBoundingClientRect();
        return { track: track.width, fill: fill?.width ?? 0 };
      });
      const share = contexts.get(laps[0]!)!.traffic!.fraction!;
      expect(bar.fill / bar.track).toBeCloseTo(share, 1);
      }
      // The hover carries the two facts as a short tag.
      await expect(rows.first()).toHaveAttribute("title", new RegExp(`${tiresText(contexts.get(laps[0]!)?.tires)} laps on tires · traffic`));
      // Nothing in a row is clipped or spills out of the list.
      const spill = await rows.evaluateAll((els) => els.map((el) => {
        const box = el.getBoundingClientRect();
        return [...el.children].some((child) => {
          const c = child.getBoundingClientRect();
          return c.right > box.right + 0.5 || c.left < box.left - 0.5 || (child as HTMLElement).scrollWidth > (child as HTMLElement).clientWidth + 1;
        });
      }));
      expect(spill).toEqual([false, false, false, false, false, false]);
      await expectNoHorizontalScroll(page);
    });

    test("the Traffic cell keeps share and side on one line: the side in words where the list is wide, the Race chart's mark where it is narrow", async ({ page }) => {
      // The longest cells on every lap: the whole lap in traffic, each side named in turn (`100 % · both sides` the longest).
      const sides = ["both", "ahead", "behind"] as const;
      const laps = (ANALYSIS.race.laps ?? []) as unknown as Record<string, unknown>[];
      const race = { ...ANALYSIS.race, laps: laps.map((lap) => ({ ...lap, clean_context: { ...(lap.clean_context as Record<string, unknown>), traffic_proximity_fraction: 1, traffic_side: sides[(lap.lap as number) % 3] } })) } as unknown as RaceViewData;
      const worst: SyntheticAnalysis = { ...ANALYSIS, race };
      await serve(page, worst);
      await open(page, "?section=B");
      const rows = page.locator(".ri-sl-list [data-lap]");
      await expect(rows).toHaveCount(6);
      await page.evaluate(() => document.fonts.ready);
      // The cell's text is the words at every width (what a screen reader reads), `both sides` as every traffic readout says it.
      const contexts = lapContexts(race, ANALYSIS.runs.runs as unknown[]);
      const texts = await rows.locator("[data-sl-traffic]").allTextContents();
      expect(texts).toEqual(listed("B").map((lap) => trafficCellText(contexts.get(lap)?.traffic)));
      expect(texts).toContain("100 % · both sides");
      const cells = await rows.evaluateAll((els) => els.map((row) => {
        const cell = row.querySelector<HTMLElement>("[data-sl-traffic]")!;
        const side = cell.querySelector<HTMLElement>(".ri-sl-side")!;
        const word = side.querySelector<HTMLElement>(".ri-sl-sword")!;
        const box = row.getBoundingClientRect();
        const centre = box.top + box.height / 2;
        const lines = (el: Element): number => {
          const range = document.createRange();
          range.selectNodeContents(el);
          return new Set([...range.getClientRects()].filter((r) => r.width > 0).map((r) => Math.round(r.top))).size;
        };
        const wordShown = word.getBoundingClientRect().width > 1;
        return {
          side: side.dataset.side,
          wordShown,
          mark: getComputedStyle(side, "::after").content !== "none",
          lines: lines(cell.querySelector(".ri-sl-tq")!) + (wordShown ? lines(word) - 1 : 0),
          offsets: [cell.querySelector(".ri-sl-tq")!, side].map((el) => { const r = el.getBoundingClientRect(); return Math.abs(r.top + r.height / 2 - centre); }),
          overflow: cell.scrollWidth - cell.clientWidth,
          beyondRow: cell.getBoundingClientRect().right - box.right,
        };
      }));
      const list = await page.locator(".ri-sl-list").evaluate((el) => el.getBoundingClientRect().width / parseFloat(getComputedStyle(document.documentElement).fontSize));
      const wide = list >= 34;
      // At 1440 the list is wide; at 1024 and on the phone it is narrow, so both forms are checked.
      expect(wide, `list ${list.toFixed(1)} rem`).toBe(width >= 1440);
      for (const cell of cells) {
        expect(cell.wordShown, `side ${cell.side} in words`).toBe(wide);
        expect(cell.mark, `side ${cell.side} as a mark`).toBe(!wide);
        expect(cell.lines, `side ${cell.side} on one line`).toBe(1);
        for (const offset of cell.offsets) expect(offset, `side ${cell.side} on the row's centre line`).toBeLessThanOrEqual(1);
        expect(cell.overflow, `side ${cell.side} fits its cell`).toBeLessThanOrEqual(0);
        expect(cell.beyondRow, `side ${cell.side} inside the row`).toBeLessThanOrEqual(0.5);
      }
      expect(new Set(cells.map((cell) => cell.side))).toEqual(new Set(sides));
      await expectNoHorizontalScroll(page);
      mkdirSync(SHOTS, { recursive: true });
      await page.locator(".ri-sl-laps").screenshot({ path: path.join(SHOTS, `traffic-cell-${width}.png`) });
    });

    test("the map draws each listed lap's recorded racing line through the section, not parallel strands", async ({ page }) => {
      await serve(page);
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const lines = page.locator("[data-sl-map] [data-sl-line]");
      await expect(lines).toHaveCount(6);
      await expect(page.locator("[data-strand]")).toHaveCount(0);
      const expected = expectedLines(ANALYSIS, "B");
      const drawn = await lines.evaluateAll((els) => els.map((el) => [Number(el.getAttribute("data-sl-line")), el.getAttribute("d")] as const));
      for (const [lap, d] of drawn) expect(d, `L${lap}`).toBe(expected.get(lap));
      // Real lines cross and change their spacing; offsets of one line would keep it constant. Measure the gap between the first
      // two laps' lines at both ends of the section.
      const spread = await page.evaluate(() => {
        const [a, b] = [...document.querySelectorAll<SVGPathElement>("[data-sl-line]")];
        const at = (p: SVGPathElement, f: number) => p.getPointAtLength(p.getTotalLength() * f);
        const gap = (f: number) => Math.hypot(at(a!, f).x - at(b!, f).x, at(a!, f).y - at(b!, f).y);
        return [gap(0.02), gap(0.5), gap(0.98)];
      });
      expect(Math.max(...spread) - Math.min(...spread)).toBeGreaterThan(0.2);
      // A new section redraws the lines from the same store: no request.
      const requests: string[] = [];
      page.on("request", (r) => requests.push(r.url()));
      await page.getByRole("tablist", { name: "Sections" }).getByRole("tab", { name: "C", exact: true }).click();
      await expect(lines).toHaveCount(6);
      const c = expectedLines(ANALYSIS, "C");
      for (const [lap, d] of await lines.evaluateAll((els) => els.map((el) => [Number(el.getAttribute("data-sl-line")), el.getAttribute("d")] as const))) expect(d).toBe(c.get(lap));
      expect(requests.filter((url) => url.includes("/analyses/"))).toEqual([]);
    });

    test("a lap without recorded positions in the section gets no line", async ({ page }) => {
      // Lap positions removed for the fastest lap of section B.
      const holed = buildSectionsAnalysis("an-103");
      const table = decodeCcol(holed.ccol);
      const lap = listed("B")[0]!;
      const index = table.laps.indexOf(lap);
      const { encodeCcol, channelAsFloat } = await import("../../src/investigation/ccol");
      const channels = [...table.channels.values()].map((ch) => {
        const values = channelAsFloat(ch).slice();
        if (ch.id === "latitude" || ch.id === "longitude") for (let bin = 0; bin < table.bins; bin++) values[index * table.bins + bin] = Number.NaN;
        return { id: ch.id, dtype: ch.dtype, values, missing: ch.missing };
      });
      holed.ccol = encodeCcol(table.analysisId, table.projectionVersion, table.bins, table.laps, channels);
      await serve(page, holed);
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      await expect(page.locator("[data-sl-map] [data-sl-line]")).toHaveCount(5);
      await expect(page.locator(`[data-sl-line="${lap}"]`)).toHaveCount(0);
      // The lap still lists, with its time.
      await expect(page.locator(`.ri-sl-list [data-lap="${lap}"]`)).toBeVisible();
    });

    test("a tilted circuit is turned so its long axis is horizontal; the lines and the cursor marker turn with it", async ({ page }) => {
      await serve(page, TILTED);
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const svg = page.locator(".ri-sl-svg");
      expect(Math.abs(Number(await svg.getAttribute("data-map-rotation")))).toBeGreaterThan(30);
      const track = await page.locator(".ri-sl-track").evaluate((el: SVGPathElement) => {
        const b = el.getBBox();
        return { w: b.width, h: b.height };
      });
      expect(track.w / track.h).toBeGreaterThan(1.3);
      // The racing lines are the tilted fixture's positions through the same turn: they lie in the section band on the outline.
      const expected = expectedLines(TILTED, "B");
      for (const [lap, d] of await page.locator("[data-sl-line]").evaluateAll((els) => els.map((el) => [Number(el.getAttribute("data-sl-line")), el.getAttribute("d")] as const))) {
        expect(d).toBe(expected.get(lap));
      }
      const off = await page.evaluate(() => {
        const segment = document.querySelector<SVGPathElement>(".ri-sl-scrub")!;
        const ctm = segment.getScreenCTM()!;
        const total = segment.getTotalLength();
        const along = Array.from({ length: 301 }, (_, i) => segment.getPointAtLength((total * i) / 300).matrixTransform(ctm));
        let worst = 0;
        for (const line of document.querySelectorAll<SVGPathElement>("[data-sl-line]")) {
          const lineCtm = line.getScreenCTM()!;
          const length = line.getTotalLength();
          for (let i = 0; i <= 40; i++) {
            const p = line.getPointAtLength((length * i) / 40).matrixTransform(lineCtm);
            worst = Math.max(worst, Math.min(...along.map((q) => Math.hypot(q.x - p.x, q.y - p.y))));
          }
        }
        return worst;
      });
      // Within the section band (1.625 rem wide): a car's line is on the track, not drifted off by the turn.
      expect(off).toBeLessThan(13);
      // The cursor marker sits on the turned segment.
      const chart = page.locator("[data-sl-chart]");
      await chart.scrollIntoViewIfNeeded();
      const box = (await chart.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.3);
      await page.mouse.move(box.x + box.width * 0.51, box.y + box.height * 0.3);
      await expect(page.locator("[data-sl-marker] i")).toBeVisible();
      if (width >= 700) {
        const onSegment = await page.evaluate(() => {
          const m = document.querySelector<HTMLElement>("[data-sl-marker] i")!.getBoundingClientRect();
          const segment = document.querySelector<SVGPathElement>(".ri-sl-scrub")!;
          const ctm = segment.getScreenCTM()!;
          const total = segment.getTotalLength();
          let best = Infinity;
          for (let i = 0; i <= 300; i++) {
            const p = segment.getPointAtLength((total * i) / 300).matrixTransform(ctm);
            best = Math.min(best, Math.hypot(p.x - (m.left + m.width / 2), p.y - (m.top + m.height / 2)));
          }
          return best;
        });
        expect(onSegment).toBeLessThan(4);
      }
      await expectNoHorizontalScroll(page);
    });

    test("screenshots for the release record: racing lines, the context columns and a turned map", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await serve(page);
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      await expect(page.locator("[data-sl-map] [data-sl-line]")).toHaveCount(6);
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS, `sections-lines-${width}.png`), fullPage: width < 700 });
      await page.locator("[data-sl-map]").screenshot({ path: path.join(SHOTS, `sections-map-${width}.png`) });
      await page.locator(".ri-sl-laps").screenshot({ path: path.join(SHOTS, `sections-rows-${width}.png`) });
      await page.unroute("**/api/v1/analyses/an-103/**");
      await serve(page, TILTED);
      await page.reload();
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      await expect(page.locator("[data-sl-map] [data-sl-line]")).toHaveCount(6);
      await page.waitForTimeout(300);
      await page.locator("[data-sl-map]").screenshot({ path: path.join(SHOTS, `sections-map-turned-${width}.png`) });
    });
  });
}
