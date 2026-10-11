import { expect, test } from "@playwright/test";
import { DRIVER, DEVICES, appTest, expectNoHorizontalScroll, expectTouchTargets, hash } from "./helpers";

for (const device of DEVICES) appTest(device, () => {
  test("car browser shows recorded tracks and immediate setup rows", async ({ page }) => {
    await page.route(/\/api\/v1\/drivers\/\d+\/setups(\?.*)?$/, async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      body.setups = [
        { setup_id: 501, customer_id: DRIVER, car_path: "supra", car_name: "Toyota Supra", names: ["Race base.sto"], sources: ["recording"], observation_count: 3, raced: true, raced_count: 1, first_seen_at: "2026-10-01T12:00:00Z", last_seen_at: "2026-10-01T12:00:00Z", last_used_at: "2026-10-08T12:00:00Z", session_count: 2, tracks: [{ track: "Lime Rock", session_count: 2, last_used_at: "2026-10-08T12:00:00Z" }] },
        { setup_id: 502, customer_id: DRIVER, car_path: "mx5", car_name: "Mazda MX-5", names: ["Practice.sto"], sources: ["recording"], observation_count: 1, raced: false, first_seen_at: "2026-10-09T12:00:00Z", last_seen_at: "2026-10-09T12:00:00Z", last_used_at: "2026-10-05T12:00:00Z", session_count: 1, tracks: [{ track: "Summit Point", session_count: 1, last_used_at: "2026-10-05T12:00:00Z" }] },
      ];
      const base = body.setups[0];
      body.setups.push(...["Bristol", "Kentucky"].map((track, index) => ({ ...base, setup_id: 503 + index, names: [`${track} race.sto`], session_count: 3 + index, last_used_at: "2026-10-07T12:00:00Z", tracks: [{ track, session_count: 3 + index, last_used_at: "2026-10-07T12:00:00Z" }] })));
      body.setups.push(...["Toyota Tundra TRD Pro", "Ford Taurus", "Toyota GR86", "Dallara IR18", "Outlaw Winged Micro Sprint"].map((car, index) => ({ ...base, setup_id: 510 + index, car_path: `car-${index}`, car_name: car, names: [`${car} baseline.sto`], last_used_at: "2026-09-21T12:00:00Z", tracks: [{ track: "Practice track", session_count: 2, last_used_at: "2026-09-21T12:00:00Z" }] })));
      const q = new URL(route.request().url()).searchParams.get("q");
      if (q) body.setups = body.setups.filter((s: { names: string[] }) => s.names.some((n) => n.toLowerCase().includes(q.toLowerCase())));
      body.unlinked_files = [];
      await route.fulfill({ response: res, json: body });
    });
    let initialPrefs = true;
    await page.route(/\/api\/v1\/drivers\/\d+\/prefs$/, async (route) => {
      if (route.request().method() !== "GET" || !initialPrefs) { await route.continue(); return; }
      initialPrefs = false;
      const response = await route.fetch();
      const body = await response.json();
      body.setup_filters = { car_paths: ["supra", "mx5"], car_path: null, text: null, raced: null };
      await route.fulfill({ response, json: body });
    });
    await page.goto(hash(`/d/${DRIVER}/setups`));
    await expect(page.getByRole("heading", { name: "Lime Rock", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Race base.sto" })).toBeVisible();
    await expect(page.locator(".s-board")).toHaveCount(1);
    await expect(page.locator(".s-board > .s-cols")).toHaveCount(1);
    await expect(page.locator(".s-board .s-track")).toHaveCount(3);
    await expect(page.locator('.s-car-choice[aria-pressed="true"]')).toHaveCount(1);
    await expect(page.getByRole("link", { name: "Practice.sto" })).toHaveCount(0);

    await expect(page.getByRole("heading", { name: "Bristol", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Kentucky", exact: true })).toBeVisible();
    await expect(page.locator(".s-desktop-cars .s-car-choice")).toHaveCount(7);
    const containedLabels = await page.locator(".s-desktop-cars .s-car-choice").evaluateAll((choices) => choices.every((choice) => {
      const box = choice.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return true;
      return [...choice.querySelectorAll("span, small")].every((label) => {
        const bounds = label.getBoundingClientRect();
        return bounds.left >= box.left - .5 && bounds.right <= box.right + .5 && bounds.top >= box.top - .5 && bounds.bottom <= box.bottom + .5;
      });
    }));
    expect(containedLabels).toBe(true);

    await expect(page.getByRole("link", { name: "Find a baseline", exact: true })).toHaveCount(1);
    await expect(page.getByRole("link", { name: "Baseline for Lime Rock", exact: true })).toBeVisible();
    await expect(page.locator(".s-board")).not.toContainText("Find a baseline");
    const separatorHeights = await page.locator(".s-track-head").evaluateAll((heads) => heads.map((head) => head.getBoundingClientRect().height));
    expect(separatorHeights.every((height) => height <= 60)).toBe(true);

    await expect(page.getByLabel("2 sessions", { exact: true })).toBeVisible();
    await expect(page.getByRole("searchbox", { name: "Search setups" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Not raced", exact: true })).toBeVisible();
    await expectNoHorizontalScroll(page);
    if (device.touch) await expectTouchTargets(page);
    await page.screenshot({ path: `test-results/setups-redesign-${device.viewport.width}.png`, fullPage: true });
    if (device.viewport.width <= 760) await page.getByLabel("Car", { exact: true }).selectOption("mx5");
    else await page.getByRole("button", { name: "Mazda MX-5 1 setup" }).click();
    await expect(page.getByRole("heading", { name: "Summit Point", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Practice.sto" })).toBeVisible();
    await page.getByRole("searchbox", { name: "Search setups" }).fill("Practice");
    await expect(page.getByRole("link", { name: "Practice.sto" })).toBeVisible();
    await page.waitForTimeout(300);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Summit Point", exact: true })).toBeVisible();
    await expect(page.getByRole("searchbox", { name: "Search setups" })).toHaveValue("Practice");
    await page.getByRole("button", { name: "Raced", exact: true }).click();
    await expect(page.getByRole("heading", { name: "No setups match" })).toBeVisible();
    await page.getByRole("button", { name: "All", exact: true }).click();
    await page.getByRole("link", { name: "Baseline for Summit Point" }).click();
    await expect(page.locator('.b-form input').nth(1)).toHaveValue("mx5");
    await expect(page.locator('.b-form input').nth(2)).toHaveValue("Summit Point");
  });
});
