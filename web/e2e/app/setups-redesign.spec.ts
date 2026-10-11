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
      const q = new URL(route.request().url()).searchParams.get("q");
      if (q) body.setups = body.setups.filter((s: { names: string[] }) => s.names.some((n) => n.toLowerCase().includes(q.toLowerCase())));
      body.unlinked_files = [];
      await route.fulfill({ response: res, json: body });
    });
    await page.goto(hash(`/d/${DRIVER}/setups`));
    await expect(page.getByRole("heading", { name: "Lime Rock", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Race base.sto" })).toBeVisible();
    await expect(page.getByLabel("2 sessions", { exact: true })).toBeVisible();
    await expect(page.getByRole("searchbox", { name: "Search setups" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Not raced", exact: true })).toBeVisible();
    await expectNoHorizontalScroll(page);
    if (device.touch) await expectTouchTargets(page);
    await page.screenshot({ path: `target/setups-redesign-${device.viewport.width}.png`, fullPage: true });
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
    await page.getByRole("link", { name: "Find a baseline for Summit Point" }).click();
    await expect(page.locator('.b-form input').nth(1)).toHaveValue("mx5");
    await expect(page.locator('.b-form input').nth(2)).toHaveValue("Summit Point");
  });
});
