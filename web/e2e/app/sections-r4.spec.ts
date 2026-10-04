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
const SECTION_LAPS = (ANALYSIS.investigation as unknown as { section_laps: { sections: { section_id: string; median_s: number; fastest: { lap: number; section_s: number; delta_s: number }[]; slowest: { lap: number; section_s: number; delta_s: number }[] }[] } }).section_laps.sections;
const entry = (id: string) => SECTION_LAPS.find((s) => s.section_id === id)!;
/** The six laps of a section in list order (fastest … slowest). */
const listed = (id: string) => [...entry(id).fastest, ...[...entry(id).slowest].reverse()];
const SHOTS = path.resolve(process.cwd(), process.env.R4_SHOTS ?? path.join("target", "round3", "R4"));

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

    test("screenshot of Sections for the release record", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await open(page, "?section=B");
      await expect(page.locator(".ri-sl-list [data-lap]")).toHaveCount(6);
      await expect(page.locator("[data-sl-map] [data-strand]")).toHaveCount(6);
      if (!device.touch) await page.locator(`.ri-sl-list [data-lap="${listed("B")[0]!.lap}"]`).hover();
      await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(SHOTS, `sections-${width}.png`), fullPage: width < 700 });
    });
  });
}
