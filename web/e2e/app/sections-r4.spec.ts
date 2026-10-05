import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildSectionsAnalysis } from "../../src/investigation/fixtures/sections";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// Web round 3, R4 (backlog #48, #49): Sections lists, per detected section, the three fastest and three slowest clean laps around the
// section median, drawn on the map and as lap rows under a section tab; Open in Traces opens exactly those laps at the section start.
// Session 103's analysis is served from the round-2 synthetic race with the projector's section_laps.
const ANALYSIS = buildSectionsAnalysis("an-103");
type Entry = { section_id: string; median_s: number; median_lap?: number; fastest: { lap: number; section_s: number; delta_s: number }[]; slowest: { lap: number; section_s: number; delta_s: number }[] };
const SECTION_LAPS = (ANALYSIS.investigation as unknown as { section_laps: { sections: Entry[] } }).section_laps.sections;
const entry = (id: string) => SECTION_LAPS.find((s) => s.section_id === id)!;
/** The six laps of a section in list order (fastest … slowest). */
const listed = (id: string) => [...entry(id).fastest, ...[...entry(id).slowest].reverse()];
// Round 4, S3 (#64): `median_lap` is the optional field the view reads for the grey median line. The fixture stands in for the server
// with a clean lap outside the six of each section (an invented value, like every figure here).
{
  const clean = (((ANALYSIS.investigation as unknown as { race_investigation: { laps: { lap: number; eligible: boolean }[] } }).race_investigation.laps) ?? [])
    .filter((lap) => lap.eligible).map((lap) => lap.lap);
  for (const s of SECTION_LAPS) {
    const shown = new Set(listed(s.section_id).map((lap) => lap.lap));
    s.median_lap = clean.find((lap) => !shown.has(lap) && lap > 5);
  }
}
const SHOTS = path.resolve(process.cwd(), process.env.R4_SHOTS ?? path.join("target", "round4", "S3"));
const REFERENCE_GREY = "rgb(114, 136, 146)";

async function serve(page: Page): Promise<void> {
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

const open = (page: Page, query = "") => page.goto(hash(`/d/${DRIVER}/s/103/sections${query}`));
const sectionTab = (page: Page, id: string) => page.getByRole("tablist", { name: "Sections" }).getByRole("tab", { name: id, exact: true });

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const width = device.viewport.width;
    test.beforeEach(async ({ page }) => serve(page));

    test("a tab per detected section; under it six laps in time order, green shades faster and red shades slower than the median", async ({ page }) => {
      await open(page, "?section=B");
      const tabs = page.getByRole("tablist", { name: "Sections" }).getByRole("tab");
      await expect(tabs).toHaveText(["A", "B", "C"]);
      await expect(sectionTab(page, "B")).toHaveAttribute("aria-selected", "true");
      const rows = page.locator(".ri-sl-list [data-lap]");
      await expect(rows).toHaveCount(6);
      const expected = listed("B");
      await expect(rows).toHaveText(expected.map((lap) => new RegExp(`^${lap.lap}${lap.section_s.toFixed(3)}[+−]${Math.abs(lap.delta_s).toFixed(3)}$`)));
      expect(await rows.evaluateAll((els) => els.map((el) => el.getAttribute("data-shade")))).toEqual(["g1", "g2", "g3", "r3", "r2", "r1"]);
      await expect(page.locator("[data-sl-median]")).toContainText(entry("B").median_s.toFixed(3));
      // The comparison column and reference are gone.
      await expect(page.getByText("Later − earlier")).toHaveCount(0);
      await expect(page.locator("[data-section-comparison]")).toHaveCount(0);
      // Shades: the extreme laps carry the race green and loss tokens; the next shades are weaker versions of the same hue.
      const colours = await rows.evaluateAll((els) => els.map((el) => getComputedStyle(el.querySelector(".ri-sl-sw")!).backgroundColor));
      expect(colours[0]).toBe("rgb(61, 220, 132)");
      expect(colours[5]).toBe("rgb(255, 90, 110)");
      expect(new Set(colours).size).toBe(6);
      const rgb = (text: string) => text.match(/[\d.]+/g)!.map(Number);
      for (const c of colours.slice(0, 3)) expect(rgb(c)[1]!).toBeGreaterThan(rgb(c)[0]!);
      for (const c of colours.slice(3)) expect(rgb(c)[0]!).toBeGreaterThan(rgb(c)[1]!);
      // The map draws the same six laps along the section, in the same shades.
      const strands = page.locator("[data-sl-map] [data-strand]");
      await expect(strands).toHaveCount(6);
      expect(await strands.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-strand"))))).toEqual(expected.map((lap) => lap.lap));
      await expectNoHorizontalScroll(page);
    });

    test("hovering or tapping a lap reads its section time and delta; nothing is requested", async ({ page }) => {
      await open(page, "?section=A");
      const lap = listed("A")[0]!;
      const row = page.locator(`.ri-sl-list [data-lap="${lap.lap}"]`);
      await expect(row).toBeVisible();
      // The section's telemetry loads once on opening; hovering afterwards requests nothing.
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const requests: string[] = [];
      page.on("request", (r) => requests.push(r.url()));
      if (device.touch) await row.focus();
      else await row.hover();
      await expect(page.locator("[data-sl-read]")).toHaveText(`L${lap.lap} · ${lap.section_s.toFixed(3)} s · −${Math.abs(lap.delta_s).toFixed(3)} s`);
      await expect(page.locator(`[data-strand="${lap.lap}"]`)).toHaveClass(/hot/);
      expect(requests).toEqual([]);
    });

    test("choosing a tab is a state change: the URL keeps the section, nothing remounts or shifts", async ({ page }) => {
      await open(page, "?section=A");
      await expect(page.locator(".ri-sl-list [data-lap]")).toHaveCount(6);
      await page.evaluate(() => {
        (window as unknown as { __r4: Element | null }).__r4 = document.querySelector(".ri-sl");
      });
      const tab = sectionTab(page, "C");
      await tab.scrollIntoViewIfNeeded();
      const scrollY = await page.evaluate(() => window.scrollY);
      const top = (await page.locator(".ri-workspace").boundingBox())!.y;
      await tab.click();
      await expect(tab).toHaveAttribute("aria-selected", "true");
      await expect(page).toHaveURL(/section=C/);
      await expect(page.locator(".ri-sl-open h2")).toContainText("Section C");
      expect(await page.locator(".ri-sl-list [data-lap]").evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-lap"))))).toEqual(listed("C").map((lap) => lap.lap));
      expect(await page.evaluate(() => (window as unknown as { __r4: Element | null }).__r4 === document.querySelector(".ri-sl")), "same node").toBe(true);
      expect(Math.abs((await page.evaluate(() => window.scrollY)) - scrollY), "page scroll").toBeLessThanOrEqual(1);
      expect(Math.abs((await page.locator(".ri-workspace").boundingBox())!.y - top), "no shift").toBeLessThanOrEqual(1);
      // A map label chooses the section too.
      await page.locator('[data-map-section="B"]').click();
      await expect(sectionTab(page, "B")).toHaveAttribute("aria-selected", "true");
    });

    test("Open in Traces opens exactly the six laps with the cursor at the section start", async ({ page }) => {
      await open(page, "?section=B");
      const button = page.getByRole("button", { name: "Open in Traces" });
      await expect(page.locator(".ri-sl-action > span")).toHaveText("6 laps");
      await button.scrollIntoViewIfNeeded();
      await button.click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces`));
      await page.locator("[data-analysis-trace-path]").first().waitFor();
      const selected = page.locator(".ri-lap-rail .ri-lap-row[aria-pressed=true]");
      await expect.poll(async () => (await selected.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-lap"))))).sort((a, b) => a - b))
        .toEqual(listed("B").map((lap) => lap.lap).sort((a, b) => a - b));
      await expect(page.locator("[data-investigation-region]").first()).toBeAttached();
      await expect(page.locator('strong[title="Lap distance at the cursor"]')).toHaveText("42.0%");
    });

    test("a selected subset opens only those laps", async ({ page }) => {
      await open(page, "?section=C");
      const [first, , , , , last] = listed("C");
      await page.locator(`.ri-sl-list [data-lap="${first!.lap}"]`).click();
      await page.locator(`.ri-sl-list [data-lap="${last!.lap}"]`).click();
      await expect(page.locator(`.ri-sl-list [data-lap="${first!.lap}"]`)).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(".ri-sl-action > span")).toHaveText("2 laps");
      await page.getByRole("button", { name: "Open in Traces" }).click();
      await page.locator("[data-analysis-trace-path]").first().waitFor();
      const selected = page.locator(".ri-lap-rail .ri-lap-row[aria-pressed=true]");
      await expect.poll(async () => (await selected.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-lap"))))).sort((a, b) => a - b))
        .toEqual([first!.lap, last!.lap].sort((a, b) => a - b));
      await expect(page.locator('strong[title="Lap distance at the cursor"]')).toHaveText("70.0%");
    });

    // Round 4, S3 (#64): the section's telemetry in the right panel.

    test("the right panel draws the six laps' speed, throttle, brake and steering over the section, the median lap in grey", async ({ page }) => {
      await open(page, "?section=B");
      const panel = page.locator("[data-sl-traces]");
      await expect(panel).toHaveAttribute("data-status", "ready");
      const six = listed("B").map((lap) => lap.lap);
      const median = entry("B").median_lap!;
      for (const signal of ["speed", "throttle", "brake", "steering"]) {
        const lines = page.locator(`[data-sl-row="${signal}"] [data-sl-trace]`);
        await expect(lines).toHaveCount(7);
        expect(await lines.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-sl-trace"))))).toEqual([...six, median]);
      }
      // Each lap's line is its lap-list shade; the median lap is the chart reference grey.
      const strokes = await page.locator('[data-sl-row="speed"] [data-sl-trace]').evaluateAll((els) => els.map((el) => getComputedStyle(el).stroke));
      const swatches = await page.locator(".ri-sl-list [data-lap] .ri-sl-sw").evaluateAll((els) => els.map((el) => getComputedStyle(el).backgroundColor));
      expect(strokes.slice(0, 6)).toEqual(swatches);
      expect(strokes[6]).toBe(REFERENCE_GREY);
      await expect(page.locator(`[data-sl-trace="${median}"][data-median]`).first()).toBeAttached();
      await expect(page.locator("[data-sl-median-key]")).toHaveText(`Median L${median}`);
      // The x axis is lap distance across the section.
      const ticks = await page.locator("[data-sl-traces] [data-sl-tick]").allTextContents();
      expect(ticks[0]).toBe("42.0%");
      expect(ticks[ticks.length - 1]).toBe("58.0%");
      // The header stays above the traces and Open in Traces below.
      const head = (await page.locator(".ri-sl-open h2").boundingBox())!;
      const chart = (await page.locator("[data-sl-chart]").boundingBox())!;
      const button = (await page.getByRole("button", { name: "Open in Traces" }).boundingBox())!;
      expect(head.y + head.height).toBeLessThanOrEqual(chart.y + 1);
      expect(button.y).toBeGreaterThanOrEqual(chart.y + chart.height - 1);
      const list = (await page.locator(".ri-sl-laps").boundingBox())!;
      const map = (await page.locator("[data-sl-map]").boundingBox())!;
      if (width < 700) {
        // Phone: the traces stack under the lap list.
        expect(chart.y).toBeGreaterThanOrEqual(list.y + list.height - 1);
      } else {
        // One screen: the map, the laps and the traces side by side, all inside the window.
        expect(chart.x).toBeGreaterThanOrEqual(list.x + list.width - 1);
        expect(chart.x).toBeGreaterThanOrEqual(map.x + map.width - 1);
        const viewport = page.viewportSize()!;
        for (const box of [map, list, chart, button]) expect(box.y + box.height).toBeLessThanOrEqual(viewport.height + 1);
      }
      await expectNoHorizontalScroll(page);
    });

    test("selecting laps draws only those laps and the median; a new section redraws; nothing is requested", async ({ page }) => {
      await open(page, "?section=C");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const requests: string[] = [];
      page.on("request", (r) => requests.push(r.url()));
      const [first, , , , , last] = listed("C");
      await page.locator(`.ri-sl-list [data-lap="${first!.lap}"]`).click();
      await page.locator(`.ri-sl-list [data-lap="${last!.lap}"]`).click();
      const lines = page.locator('[data-sl-row="brake"] [data-sl-trace]');
      await expect(lines).toHaveCount(3);
      expect(await lines.evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-sl-trace"))))).toEqual([first!.lap, last!.lap, entry("C").median_lap!]);
      await sectionTab(page, "A").click();
      await expect(page.locator('[data-sl-row="brake"] [data-sl-trace]')).toHaveCount(7);
      expect(await page.locator('[data-sl-row="brake"] [data-sl-trace]').evaluateAll((els) => els.map((el) => Number(el.getAttribute("data-sl-trace")))))
        .toEqual([...listed("A").map((lap) => lap.lap), entry("A").median_lap!]);
      // (A tab is a selection the shell may save as a preference; no analysis view or trace is fetched again.)
      expect(requests.filter((url) => url.includes("/analyses/")), "analysis requests after the first load").toEqual([]);
    });

    test("the cursor is synchronized both ways: traces move the marker on the section segment, the segment moves the cursor; pointer movement requests nothing", async ({ page }) => {
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const chart = page.locator("[data-sl-chart]");
      await chart.scrollIntoViewIfNeeded();
      const requests: string[] = [];
      page.on("request", (r) => requests.push(r.url()));
      const marker = page.locator("[data-sl-marker]");
      const pct = page.locator("[data-sl-cursor-pct]");
      const cardPixels = () => page.locator("[data-sl-traces] canvas").evaluate((c: HTMLCanvasElement) => {
        const px = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
        let painted = 0;
        for (let i = 3; i < px.length; i += 4) if (px[i]! > 0) painted++;
        return painted;
      });
      const markerAt = () => marker.evaluate((el: HTMLElement) => el.style.transform);
      // Traces -> map: a quarter of the way across, then three quarters.
      let box = (await chart.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.3);
      await page.mouse.move(box.x + box.width * 0.26, box.y + box.height * 0.3);
      await expect(marker.locator("i")).toBeVisible();
      await expect.poll(cardPixels).toBeGreaterThan(200);
      const early = Number((await pct.textContent())!.replace("%", ""));
      const earlyMarker = await markerAt();
      await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.6);
      await page.mouse.move(box.x + box.width * 0.76, box.y + box.height * 0.6);
      await expect.poll(markerAt).not.toBe(earlyMarker);
      const late = Number((await pct.textContent())!.replace("%", ""));
      expect(early).toBeGreaterThan(42);
      expect(late).toBeGreaterThan(early + 4);
      expect(late).toBeLessThan(58);
      // The marker sits on the section segment: within a few pixels of the middle strand's path.
      const onSegment = await page.evaluate(() => {
        const marker = document.querySelector<HTMLElement>("[data-sl-marker] i")!.getBoundingClientRect();
        const cx = marker.left + marker.width / 2;
        const cy = marker.top + marker.height / 2;
        const strand = document.querySelectorAll<SVGPathElement>("[data-strand]")[2]!;
        const ctm = strand.getScreenCTM()!;
        const total = strand.getTotalLength();
        let best = Infinity;
        for (let i = 0; i <= 200; i++) {
          const p = strand.getPointAtLength((total * i) / 200).matrixTransform(ctm);
          best = Math.min(best, Math.hypot(p.x - cx, p.y - cy));
        }
        return best;
      });
      expect(onSegment).toBeLessThan(14);
      // Map -> traces: a point a fifth of the way along the segment sets the cursor there.
      await page.mouse.move(2, 2);
      const target = await page.evaluate(() => {
        const strand = document.querySelectorAll<SVGPathElement>("[data-strand]")[2]!;
        const p = strand.getPointAtLength(strand.getTotalLength() * 0.2).matrixTransform(strand.getScreenCTM()!);
        return { x: p.x, y: p.y };
      });
      const mapBox = (await page.locator("[data-sl-map]").boundingBox())!;
      if (target.y < 0 || target.y > page.viewportSize()!.height) await page.locator("[data-sl-map]").scrollIntoViewIfNeeded();
      const again = await page.evaluate(() => {
        const strand = document.querySelectorAll<SVGPathElement>("[data-strand]")[2]!;
        const p = strand.getPointAtLength(strand.getTotalLength() * 0.2).matrixTransform(strand.getScreenCTM()!);
        return { x: p.x, y: p.y };
      });
      expect(mapBox.width).toBeGreaterThan(0);
      if (device.touch) await page.touchscreen.tap(again.x, again.y);
      else await page.mouse.move(again.x, again.y);
      await expect.poll(async () => Number((await pct.textContent())!.replace("%", ""))).toBeLessThan(early + 0.5);
      const fromMap = Number((await pct.textContent())!.replace("%", ""));
      expect(fromMap).toBeGreaterThan(42);
      expect(fromMap).toBeLessThan(47);
      await expect.poll(cardPixels).toBeGreaterThan(200);
      box = (await chart.boundingBox())!;
      expect(box.width).toBeGreaterThan(0);
      expect(requests, "requests during pointer movement").toEqual([]);
    });

    test("touch targets: tabs, lap rows, map labels and Open in Traces are 44 px on the phone and tablet", async ({ page }) => {
      test.skip(!device.touch, "touch devices only");
      await open(page, "?section=B");
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      const heights = async (selector: string) => page.locator(selector).evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
      for (const h of await heights(".ri-sl-tab")) expect(h).toBeGreaterThanOrEqual(43.5);
      for (const h of await heights(".ri-sl-list [data-lap]")) expect(h).toBeGreaterThanOrEqual(43.5);
      for (const h of await heights(".ri-sl-label .ri-sl-hit")) expect(h).toBeGreaterThanOrEqual(43.5);
      const button = (await page.getByRole("button", { name: "Open in Traces" }).boundingBox())!;
      expect(button.height).toBeGreaterThanOrEqual(43.5);
    });

    test("screenshot of Sections for the release record", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await open(page, "?section=B");
      await expect(page.locator(".ri-sl-list [data-lap]")).toHaveCount(6);
      await expect(page.locator("[data-sl-map] [data-strand]")).toHaveCount(6);
      await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
      await expect(page.locator('[data-sl-row="speed"] [data-sl-trace]')).toHaveCount(7);
      if (!device.touch) await page.locator(`.ri-sl-list [data-lap="${listed("B")[0]!.lap}"]`).hover();
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS, `sections-${width}.png`), fullPage: width < 700 });
      // With the cursor in the section: per-lap readouts on the traces, the marker on the segment.
      const chart = page.locator("[data-sl-chart]");
      await chart.scrollIntoViewIfNeeded();
      const box = (await chart.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.4);
      await page.mouse.move(box.x + box.width * 0.56, box.y + box.height * 0.4);
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS, `sections-cursor-${width}.png`) });
    });
  });
}
