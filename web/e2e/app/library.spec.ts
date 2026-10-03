import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, resetMock, setMock } from "./helpers";

for (const device of DEVICES) {
  appTest(device, (d) => {
    test("one driver opens straight to that driver's sessions", async ({ page }) => {
      await page.goto("/");
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/sessions$`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Sessions" })).toHaveAttribute("aria-current", "page");
      await expectNoHorizontalScroll(page);
    });

    test("several drivers list first, and the switch moves between them", async ({ page, request }) => {
      await setMock(request, { drivers: 2 });
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "Drivers" })).toBeVisible();
      await expect(page.getByRole("link", { name: /Sam Rivera/ })).toBeVisible();
      await page.getByRole("link", { name: /Joshua Granger/ }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/sessions$`));
      await page.locator(".driver-switch select").selectOption({ label: "Sam Rivera" });
      await expect(page).toHaveURL(/#\/d\/2000001\/sessions$/);
      await expectNoHorizontalScroll(page);
    });

    test("no data says what to do", async ({ page, request }) => {
      await setMock(request, { drivers: 0 });
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "No drivers yet" })).toBeVisible();
    });

    test("sessions group phases of one event and show analysis state", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const first = page.getByRole("region", { name: "Bristol Motor Speedway - Oval" }).first();
      await expect(first.getByText("Practice")).toBeVisible();
      await expect(first.getByText("Qualifying")).toBeVisible();
      await expect(first.getByText("Race", { exact: true })).toBeVisible();
      await expect(first.getByText("P12 to P4")).toBeVisible();
      await expect(page.getByText("Not analyzed").first()).toBeVisible();
      await expect(page.getByText("Failed: worker crash")).toBeVisible();
      await expect(page.getByText("Deferred: exceeds memory budget")).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (d.name.startsWith("phone")) await expectTouchTargets(page);
    });

    test("filters by type and text, server side", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await page.getByRole("button", { name: "Races" }).click();
      await expect(page.locator("li.session-row").first()).toBeVisible();
      await expect(page.locator("li.session-row", { hasText: "Practice" })).toHaveCount(0);
      await expect(page.locator("li.session-row", { hasText: "Race" }).first()).toBeVisible();
      await page.getByRole("button", { name: "All" }).click();
      await page.getByPlaceholder("Track, car, series").fill("lime rock");
      await expect(page.getByRole("heading", { name: "Lime Rock Park - Grand Prix" })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Bristol Motor Speedway - Oval" })).toHaveCount(0);
      await page.getByPlaceholder("Track, car, series").fill("zzz");
      await expect(page.getByRole("heading", { name: "No matching sessions" })).toBeVisible();
    });

    test("an analyzed session opens the investigation route", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await page.getByRole("link", { name: /NASCAR Xfinity Toyota Supra/ }).first().click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/\\d+/race$`));
    });

    test("analyze queues the job and the row becomes analyzed when the server says so", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const waiting = page.locator("li.session-row[data-state=none]");
      await expect(page.locator("li.session-row").first()).toBeVisible();
      const before = await waiting.count();
      expect(before).toBeGreaterThan(0);
      await waiting.first().getByRole("button", { name: "Analyze" }).click();
      await expect(waiting).toHaveCount(before - 1, { timeout: 6000 });
      await expect(page.locator("li.session-row[data-state=ready]").first()).toBeVisible();
    });

    test("resume offers the last investigation and never opens it by itself", async ({ page }) => {
      await page.addInitScript((c) => localStorage.setItem(`coach.last-investigation.${c}`, JSON.stringify({ session_id: 103, analysis_id: "an-103", view: "traces", comparison_id: "c1" })), DRIVER);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const resume = page.getByRole("link", { name: /Resume/ });
      await expect(resume).toContainText("Bristol Motor Speedway - Oval");
      await expect(resume).toContainText("Traces");
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/sessions$`));
      await resume.click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces\\?comparison=c1$`));
    });

    test("loaded sessions stay on screen when the server drops, with a banner", async ({ page, context, request }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("heading", { name: "Bristol Motor Speedway - Oval" }).first()).toBeVisible();
      await context.route("**/api/v1/drivers/**", (r) => r.abort());
      await request.post("/__mock/event", { data: { type: "library_changed", customer_id: DRIVER, scope: "sessions" } });
      await expect(page.getByTestId("offline-banner")).toBeVisible();
      await expect(page.getByText(/Could not refresh/)).toBeVisible();
      await expect(page.getByRole("heading", { name: "Bristol Motor Speedway - Oval" }).first()).toBeVisible();
      await context.unroute("**/api/v1/drivers/**");
    });
  });
}

test("reset helper leaves the mock usable", async ({ request }) => {
  await resetMock(request);
  expect((await request.get("/api/v1/drivers")).ok()).toBeTruthy();
});
