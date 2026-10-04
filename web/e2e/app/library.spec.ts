import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, resetMock, setMock } from "./helpers";

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
  });
}

test("reset helper leaves the mock usable", async ({ request }) => {
  await resetMock(request);
  expect((await request.get("/api/v1/drivers")).ok()).toBeTruthy();
});
