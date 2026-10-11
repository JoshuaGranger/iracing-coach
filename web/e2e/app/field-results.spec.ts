import { expect, test } from "@playwright/test";
import { buildTrafficAnalysis } from "../../src/investigation/fixtures/traffic";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { appTest, DEVICES, DRIVER, expectNoHorizontalScroll, hash } from "./helpers";
import { mkdirSync } from "node:fs";

const analysis = buildTrafficAnalysis("an-103");
const row = (index: number, name: string, player: boolean, position: number) => ({
  car_index: index, driver_name: name, car_number: `${index + 5}`, is_player: player, position, laps_completed: 82,
  fastest_lap_s: 29.4, pace_median_s: 30.2, pace_lap_count: 35, pit_visits: 2,
  tires_taken: player ? ["RF", "RR"] : null, fuel_used_l: player ? 65.3 : null,
  laps: Array.from({ length: 30 }, (_, i) => ({ lap: i + 1, lap_time_s: 29.4 + i * .04, position: i < 10 ? 3 : position, flag_state: "green", on_pit_road: i === 14, pace_eligible: i !== 14 })),
  pit_events: [{ lap: 15, session_time_s: 445 }],
});
analysis.race.field_pace = { results: { cars: [row(2, "Race Leader", false, 1), row(0, "Joshua Granger", true, 2), row(3, "Third Driver", false, 3)], pace_definition: "Recorded whole green laps; traffic included." } };

for (const device of DEVICES) appTest(device, () => {
  test("field comparison defaults to player, offers recorded order and opens competitor progression", async ({ page }) => {
    await page.route("**/api/v1/analyses/an-103/**", async (route) => {
      const url = new URL(route.request().url()); const result = routeSynthetic(analysis, `${url.pathname}${url.search}`);
      await route.fulfill({ status: result.status, contentType: result.contentType, body: typeof result.body === "string" ? result.body : Buffer.from(result.body) });
    });
    await page.goto(hash(`/d/${DRIVER}/s/103/race`));
    const picker = page.getByRole("combobox", { name: "Race car" });
    await expect(picker).toHaveValue("0");
    expect(await picker.locator("option").allTextContents()).toEqual(["P1 · #7 Race Leader", "P2 · #5 Joshua Granger · You", "P3 · #8 Third Driver"]);
    await picker.selectOption("2");
    await expect(page.locator('[data-ri-view="field-race"]')).toBeVisible();
    await expect(page.locator('[data-field-lap]')).toHaveCount(30);
    await expect(page.locator('[data-field-pit]')).toHaveCount(1);
    await expect(page.locator('[data-ri-view="race"]')).toHaveCount(0);
    await expectNoHorizontalScroll(page);
    await page.getByRole("tab", { name: "Results Summary" }).click();
    const tabs = await page.getByRole("tab").allTextContents();
    expect(tabs.indexOf("Results Summary")).toBe(tabs.indexOf("Traces") + 1);
    await expect(page.locator('[data-results-car]')).toHaveCount(3);
    await expect(page.locator('[data-results-car="2"]')).toContainText("—");
    await expectNoHorizontalScroll(page);
    if (device.viewport.width < 700) expect(await page.locator(".ri-results-scroll").evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
    mkdirSync("test-results/field-review", { recursive: true });
    await page.screenshot({ path: `test-results/field-review/results-${device.viewport.width}.png`, fullPage: true });
    await page.getByRole("button", { name: "Race Leader", exact: true }).click();
    await expect(picker).toHaveValue("2");
    await expect(page.locator('[data-ri-view="field-race"]')).toBeVisible();
    await page.screenshot({ path: `test-results/field-review/field-race-${device.viewport.width}.png`, fullPage: true });
  });
});
