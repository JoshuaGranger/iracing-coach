import { type Page, expect, test } from "@playwright/test";
import { DRIVER, DEVICES, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

const rootPx = (page: Page) => page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
const bodyPx = (page: Page) => page.evaluate(() => parseFloat(getComputedStyle(document.body).fontSize));
const savedSteps = async (request: Parameters<typeof mockState>[0]) =>
  (await mockState(request)).writes.filter((w) => w.path.endsWith("/prefs") && "text_size" in (w.body as object)).map((w) => (w.body as { text_size: string }).text_size);

for (const device of DEVICES) {
  appTest(device, (d) => {
    test("the app opens at the default step: body 16 px, with Default, Larger and Largest to choose from", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const group = page.getByRole("group", { name: "Text size" });
      await expect(group.getByRole("button")).toHaveText(["A", "A", "A"]);
      await expect(group.getByRole("button", { name: "Default" })).toHaveAttribute("aria-pressed", "true");
      await expect(group.getByRole("button", { name: "Larger" })).toHaveAttribute("aria-pressed", "false");
      await expect(group.getByRole("button", { name: "Largest" })).toHaveAttribute("aria-pressed", "false");
      expect(await rootPx(page)).toBe(16);
      expect(await bodyPx(page)).toBe(16);
      await expectNoHorizontalScroll(page);
    });

    test("a step resizes the whole app at once and is saved with the driver's prefs, the device keeping only a hint", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      const group = page.getByRole("group", { name: "Text size" });

      await group.getByRole("button", { name: "Larger" }).click();
      await expect(page.locator("html")).toHaveAttribute("data-text-size", "larger");
      expect(await rootPx(page)).toBe(18);
      expect(await bodyPx(page)).toBe(18);
      await expect(group.getByRole("button", { name: "Larger" })).toHaveAttribute("aria-pressed", "true");
      await expect.poll(() => savedSteps(request)).toEqual(["larger"]);
      expect(await page.evaluate(() => localStorage.getItem("coach.text-size"))).toBe("larger");
      await expectNoHorizontalScroll(page);

      await group.getByRole("button", { name: "Largest" }).click();
      expect(await rootPx(page)).toBe(20);
      await expect.poll(() => savedSteps(request)).toEqual(["larger", "largest"]);
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
      if (d.touch) await expectTouchTargets(page);

      await group.getByRole("button", { name: "Default" }).click();
      expect(await rootPx(page)).toBe(16);
    });

    test("the step follows the driver to a device that has none, and the hint paints a device that has one before the server answers", async ({ browser, page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await page.getByRole("group", { name: "Text size" }).getByRole("button", { name: "Largest" }).click();
      await expect.poll(() => savedSteps(request)).toEqual(["largest"]);

      // Another device: no hint, so it starts at the default step and takes the saved one when prefs load.
      const other = await browser.newContext({ viewport: d.viewport, hasTouch: d.touch });
      const second = await other.newPage();
      await second.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(second.locator("html")).toHaveAttribute("data-text-size", "largest");
      expect(await rootPx(second)).toBe(20);
      expect(await second.evaluate(() => localStorage.getItem("coach.text-size"))).toBe("largest");
      await other.close();

      // A device with a hint: the first paint is already that size, while prefs have not answered.
      const hinted = await browser.newContext({ viewport: d.viewport, hasTouch: d.touch });
      const third = await hinted.newPage();
      await third.addInitScript(() => localStorage.setItem("coach.text-size", "larger"));
      await third.route("**/api/v1/drivers/*/prefs", () => undefined);
      await third.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(third.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await expect(third.locator("html")).toHaveAttribute("data-text-size", "larger");
      expect(await rootPx(third)).toBe(18);
      await hinted.close();
    });

    test("every step keeps the page inside the window, and the touch targets on a phone or tablet", async ({ page }) => {
      for (const step of ["Larger", "Largest"]) {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await page.getByRole("group", { name: "Text size" }).getByRole("button", { name: step }).click();
        for (const route of [`/d/${DRIVER}/sessions`, `/d/${DRIVER}/setups`, `/d/${DRIVER}/live`, `/d/${DRIVER}/setups/baseline`]) {
          await page.goto(hash(route));
          await expect(page.locator("main h1, main h2").first()).toBeVisible();
          await expectNoHorizontalScroll(page);
          if (d.touch) await expectTouchTargets(page);
        }
      }
    });
  });
}

test("an unknown saved step reads as the default", async ({ page, request }) => {
  await request.post("/__mock/state", { data: { reset: true } });
  await page.addInitScript(() => localStorage.setItem("coach.text-size", "enormous"));
  await page.goto(hash(`/d/${DRIVER}/sessions`));
  await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-text-size", "default");
});
