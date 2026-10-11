import { expect, test } from "@playwright/test";
import { buildSectionsAnalysis } from "../../src/investigation/fixtures/sections";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { appTest, DEVICES, DRIVER, expectNoHorizontalScroll, hash } from "./helpers";

type Json = Record<string, any>;
function observed(count: 1 | 2) {
  const analysis = buildSectionsAnalysis("an-103");
  const inv = analysis.investigation as unknown as Json;
  const entry = inv.section_laps.sections.find((section: Json) => section.section_id === "B");
  const fast = { ...entry.fastest[0], context_reasons: ["close_traffic", "damage_repair_context"] };
  const slow = { ...entry.slowest[0], context_reasons: ["close_traffic"] };
  inv.section_laps = { population: "observed_green_racing_laps", sections: [{
    section_id: "B", clean_lap_count: 0, observed_lap_count: count,
    median_s: entry.median_s, median_lap: fast.lap,
    fastest: count === 2 ? [fast] : [], slowest: count === 2 ? [slow] : [],
  }] };
  return { analysis, fast, slow };
}

for (const device of DEVICES) appTest(device, () => {
  for (const count of [2, 1] as const) test(`observed green section population ${count} works with zero strict-clean laps`, async ({ page }) => {
    const { analysis, fast } = observed(count);
    await page.route("**/api/v1/analyses/an-103/**", async (route) => {
      const url = new URL(route.request().url());
      const result = routeSynthetic(analysis, `${url.pathname}${url.search}`);
      await route.fulfill({ status: result.status, contentType: result.contentType, body: typeof result.body === "string" ? result.body : Buffer.from(result.body) });
    });
    await page.goto(hash(`/d/${DRIVER}/s/103/sections?section=B`));
    await expect(page.locator(".ri-sl-facts")).toContainText("Green laps");
    await expect(page.locator(".ri-sl-facts")).toContainText("Traffic included");
    await expect(page.locator(".ri-sl-facts")).not.toContainText("Clean laps");
    await expect(page.locator("[data-sl-traces]")).toHaveAttribute("data-status", "ready");
    await expect(page.locator("[data-sl-chart] svg")).toBeVisible();
    if (count === 2) {
      await expect(page.locator(".ri-sl-list [data-lap]")).toHaveCount(2);
      const repair = page.locator(`.ri-sl-list [data-lap="${fast.lap}"]`);
      await expect(repair.locator('sup[aria-label="Repair context"]')).toHaveText("*");
      await expect(repair).toHaveAttribute("title", /Repair context/);
      await expect(page.locator("[data-sl-median]")).toBeVisible();
    } else {
      await expect(page.locator(".ri-sl-list [data-lap]")).toHaveCount(0);
      await expect(page.locator("[data-sl-median-key]")).toContainText(`Reference L${fast.lap}`);
      await expect(page.getByRole("button", { name: "Open in Traces", exact: true })).toBeEnabled();
      await expect(page.locator(".ri-sl-none")).toContainText(`L${fast.lap}`);
    }
    await expectNoHorizontalScroll(page);
  });
});
