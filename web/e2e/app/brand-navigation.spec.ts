import { expect, test } from "@playwright/test";
import { DRIVER, expectNoHorizontalScroll, expectTouchTargets, resetMock, setMock } from "./helpers";

for (const width of [320, 390, 1024, 1280]) {
  test.describe(`Coach navigation at ${width}px`, () => {
    test.use({ viewport: { width, height: width < 700 ? 844 : 768 }, hasTouch: width < 1100 });
    test.beforeEach(async ({ request }) => resetMock(request));

    test("keeps the brand and three destinations visible in a compact header", async ({ page }) => {
      await page.goto(`/#/d/${DRIVER}/setups`);
      const header = page.locator(".topbar");
      const nav = page.getByRole("navigation", { name: "Main", exact: true });
      await expect(page.getByRole("link", { name: "iRacing Coach", exact: true })).toBeVisible();
      for (const label of ["Sessions", "Setups", "Live"]) {
        const link = nav.getByRole("link", { name: new RegExp(`^${label}`) });
        await expect(link).toBeVisible();
        const box = (await link.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
      await expect(nav.getByRole("link", { name: "Setups", exact: true })).toHaveAttribute("aria-current", "page");
      expect((await header.boundingBox())!.height).toBeLessThanOrEqual(72);
      await expectNoHorizontalScroll(page);
      if (width < 1100) await expectTouchTargets(page);
    });

    test("keeps driver switching usable without pushing navigation out of view", async ({ page, request }) => {
      await setMock(request, { drivers: 2 });
      await page.goto(`/#/d/${DRIVER}/setups/1`);
      const nav = page.getByRole("navigation", { name: "Main", exact: true });
      await expect(nav.getByRole("link", { name: "Setups", exact: true })).toHaveAttribute("aria-current", "page");
      await page.locator(".driver-switch select").selectOption({ label: "Sam Rivera" });
      await expect(page).toHaveURL(/#\/d\/2000001\/sessions$/);
      await expect(nav.getByRole("link", { name: "Sessions", exact: true })).toHaveAttribute("aria-current", "page");
      await expectNoHorizontalScroll(page);
      for (const link of await nav.getByRole("link").all()) {
        const box = (await link.boundingBox())!;
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(width);
      }
    });
  });
}

test("keyboard navigation retains visible focus and real links under reduced motion", async ({ page, request }) => {
  await resetMock(request);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/#/d/${DRIVER}/setups`);
  await page.getByRole("link", { name: "iRacing Coach", exact: true }).focus();
  await page.keyboard.press("Tab");
  const sessions = page.getByRole("navigation", { name: "Main", exact: true }).getByRole("link", { name: "Sessions", exact: true });
  await expect(sessions).toBeFocused();
  expect(await sessions.evaluate((el) => parseFloat(getComputedStyle(el).outlineWidth))).toBeGreaterThanOrEqual(2);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/sessions$`));
});
