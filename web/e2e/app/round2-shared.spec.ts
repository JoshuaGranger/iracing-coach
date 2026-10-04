import { expect, test } from "@playwright/test";
import { DRIVER, appTest, expectNoHorizontalScroll, hash, resetMock } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const phone = device.viewport.width < 700;

    test("the filter bar sits at the heading's right when it fits and collapses to one Filters button when it does not", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const bar = page.locator(".fb");
      await expect(bar.getByRole("heading", { name: "Components" })).toBeVisible();
      if (!phone) {
        await expect(bar).toHaveAttribute("data-layout", "inline");
        await expect(page.locator(".fb-trigger")).toHaveCount(0);
        const h1 = await bar.getByRole("heading", { name: "Components" }).boundingBox();
        const pill = await bar.getByRole("button", { name: /^Car/ }).boundingBox();
        expect(pill!.x, "controls are right of the title").toBeGreaterThan(h1!.x + h1!.width);
        expect(pill!.y + pill!.height, "same heading row").toBeLessThanOrEqual(h1!.y + h1!.height + 20);
        // The heading row holds the controls at the right edge of the page content.
        const row = await bar.locator(".fb-row").boundingBox();
        const last = await bar.getByRole("button", { name: /^Series/ }).boundingBox();
        expect(row!.x + row!.width - (last!.x + last!.width)).toBeLessThan(40);
      } else {
        await expect(bar).toHaveAttribute("data-layout", "collapsed");
        await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
        await expect(bar.getByRole("button", { name: /^Car/ })).toHaveCount(0);
      }
      // No Library control, no Clear in the heading, no chip row.
      await expect(page.getByText("Library", { exact: false })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Lab" })).toHaveCount(0);
      await expect(bar.locator(".fb-row").getByRole("button", { name: "Clear" })).toHaveCount(0);
      await expect(page.locator(".filter-chip, .fb-chips")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
    });

    test("a dropdown carries its selection and clears from inside its list; setting and clearing never move the heading", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const bar = page.locator(".fb");
      /** Position and size of everything in the heading row, to the tenth of a pixel. */
      const headingBoxes = () =>
        page.evaluate(() =>
          [...document.querySelectorAll(".fb-row h1, .fb-row .fb-controls > *, .fb-row .fb-trigger")].map((e) => {
            const r = e.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height].map((n) => Math.round(n * 10) / 10);
          }),
        );
      const surface = async () => {
        if (!phone) return bar;
        await page.getByRole("button", { name: /^Filters/ }).click();
        return page.getByRole("dialog", { name: "Filters" });
      };
      const closeSurface = async () => {
        if (phone) await page.getByRole("dialog", { name: "Filters" }).getByRole("button", { name: "Done" }).click();
      };
      await expect(bar.getByRole("heading", { name: "Components" })).toBeVisible();
      const before = await headingBoxes();
      expect(before.length).toBeGreaterThan(1);

      let s = await surface();
      await s.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("option", { name: /Dallara/ }).click();
      await page.keyboard.press("Escape");
      await expect(s.getByRole("button", { name: "Car · Dallara IR-18" })).toBeVisible();
      await s.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("option", { name: /Global Mazda/ }).click();
      await page.keyboard.press("Escape");
      await expect(s.getByRole("button", { name: "Car · 2" })).toBeVisible();
      await closeSurface();
      if (phone) await expect(page.getByRole("button", { name: "Filters · 2" })).toBeVisible();
      // Set: the title, the controls and the collapsed button sit exactly where they did.
      expect(await headingBoxes()).toEqual(before);
      await expect(page.locator(".filter-chip, .fb-chips")).toHaveCount(0);
      await expect(bar.locator(".fb-row").getByRole("button", { name: "Clear" })).toHaveCount(0);

      // Clear is the first row inside the dropdown; nothing outside it moves.
      s = await surface();
      await s.getByRole("button", { name: /^Car/ }).click();
      const panel = page.getByRole("group", { name: "Car filter" });
      const rowsBefore = await panel.getByRole("option").first().boundingBox();
      await panel.getByRole("button", { name: "Clear" }).click();
      await expect(s.getByRole("button", { name: "Car", exact: true })).toBeVisible();
      await expect(panel.getByRole("button", { name: "Clear" })).toBeDisabled();
      expect((await panel.getByRole("option").first().boundingBox())!.y, "options do not shift when the selection clears").toBeCloseTo(rowsBefore!.y, 0);
      await page.keyboard.press("Escape");
      await closeSurface();
      expect(await headingBoxes()).toEqual(before);
    });

    test("screenshots: the filter bar with a choice made, and its dropdown open", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const bar = page.locator(".fb");
      await expect(bar.getByRole("heading", { name: "Components" })).toBeVisible();
      if (phone) await page.getByRole("button", { name: /^Filters/ }).click();
      const where = phone ? page.getByRole("dialog", { name: "Filters" }) : bar;
      await where.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("option", { name: /Dallara/ }).click();
      await page.waitForTimeout(150);
      await page.screenshot({ path: `target/round3/R2/filters-open-${device.viewport.width}.png` });
      await page.keyboard.press("Escape");
      if (phone) await where.getByRole("button", { name: "Done" }).click();
      await page.mouse.move(0, 0);
      await page.waitForTimeout(150);
      await page.screenshot({ path: `target/round3/R2/filters-set-${device.viewport.width}.png`, clip: { x: 0, y: 0, width: device.viewport.width, height: 160 } });
    });

    test("the Clear row is reached by Tab from the search field and works from the keyboard", async ({ page }) => {
      await page.goto(hash("/__kit"));
      if (phone) await page.getByRole("button", { name: /^Filters/ }).click();
      const where = phone ? page.getByRole("dialog", { name: "Filters" }) : page.locator(".fb");
      await where.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("option", { name: /Dallara/ }).click();
      await page.getByRole("combobox", { name: "Search car" }).focus();
      await page.keyboard.press("Tab");
      await expect(page.getByRole("group", { name: "Car filter" }).getByRole("button", { name: "Clear" })).toBeFocused();
      await page.keyboard.press("Enter");
      await expect(where.getByRole("button", { name: "Car", exact: true })).toBeVisible();
    });

    test("chips are exceptions, constraints or actions: squared, never a pill, Retry works, no status-normal chip", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const marks = page.getByRole("region", { name: "Marks" });
      await expect(marks.locator(".chip-exception")).toHaveCount(2);
      await expect(marks.locator(".chip-constraint")).toHaveCount(1);
      await expect(marks.locator(".chip-action")).toHaveCount(1);
      for (const old of [".chip-good", ".chip-warn", ".chip-bad", ".chip-info"]) await expect(page.locator(old)).toHaveCount(0);
      for (const text of ["Values known", "Recording", "Analyzed"]) await expect(page.locator(".chip", { hasText: text })).toHaveCount(0);
      const radius = await marks.locator(".chip").first().evaluate((el) => parseFloat(getComputedStyle(el).borderTopLeftRadius));
      expect(radius, "a chip is not a pill").toBeLessThan(12);
      const pill = await page.locator(".ms-pill, .fb-trigger").first().evaluate((el) => parseFloat(getComputedStyle(el).borderTopLeftRadius));
      expect(pill).toBeGreaterThan(100);
      const failed = marks.locator(".chip-exception", { hasText: "Failed" });
      await expect(failed).toHaveCSS("color", "rgb(255, 90, 110)");
      await failed.getByRole("button", { name: "Retry" }).click();
      await expect(marks.getByRole("button", { name: "Retry 1" })).toBeVisible();
    });

    test("the selected segment ignores hover and an unselected one takes it", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const look = (locator: ReturnType<typeof page.locator>) =>
        locator.evaluate((el) => {
          const s = getComputedStyle(el);
          return [s.backgroundColor, s.color, s.boxShadow, s.borderColor].join("|");
        });
      for (const [group, selected, other] of [
        ["View", "All", "Green"],
        ["Session phase", "Race", "Practice"],
      ] as const) {
        const seg = page.getByRole("group", { name: group });
        const on = seg.getByRole("button", { name: selected, exact: true });
        const off = seg.getByRole("button", { name: other, exact: true });
        await page.mouse.move(0, 0);
        const before = await look(on);
        const offBefore = await look(off);
        await on.hover();
        await page.waitForTimeout(250);
        expect(await look(on), `${group}: ${selected} under the pointer`).toBe(before);
        await off.hover();
        await page.waitForTimeout(250);
        expect(await look(off), `${group}: ${other} under the pointer`).not.toBe(offBefore);
        expect(await look(on), `${group}: ${selected} after the pointer left`).toBe(before);
      }
    });

    test("a selected tab with no styles of its own still ignores hover; the global rule leaves only unselected items to hover", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const tabs = page.getByRole("tablist", { name: "Plain tabs" });
      const bg = (l: ReturnType<typeof page.locator>) => l.evaluate((el) => getComputedStyle(el).backgroundColor);
      const on = tabs.getByRole("tab", { name: "Selected tab" });
      const off = tabs.getByRole("tab", { name: "Other tab" });
      await page.mouse.move(0, 0);
      const [onBefore, offBefore] = [await bg(on), await bg(off)];
      await on.hover();
      await page.waitForTimeout(250);
      expect(await bg(on)).toBe(onBefore);
      await off.hover();
      await page.waitForTimeout(250);
      expect(await bg(off)).not.toBe(offBefore);
    });

    test("the run palette and the race-progress gradient come from the tokens", async ({ page }) => {
      await page.goto(hash("/__kit"));
      await expect(page.getByTestId("run-palette").locator("line")).toHaveCount(8);
      const runs = await page.getByTestId("run-palette").locator("line").evaluateAll((lines) => lines.map((l) => l.getAttribute("stroke")));
      expect(runs).toHaveLength(8);
      expect(runs.every((r) => r?.startsWith("var(--run-"))).toBe(true);
      const colours = await page.getByTestId("run-palette").locator("line").evaluateAll((lines) => lines.map((l) => getComputedStyle(l).stroke));
      expect(new Set(colours).size).toBe(8);
      const tokens = await page.evaluate(() => {
        const probe = document.createElement("i");
        document.body.append(probe);
        const resolve = (name: string) => {
          probe.style.color = `var(${name})`;
          return getComputedStyle(probe).color;
        };
        const out = { run1: resolve("--run-1"), progress1: resolve("--progress-1"), progress6: resolve("--progress-6"), toolbar: getComputedStyle(document.documentElement).getPropertyValue("--toolbar-height").trim() };
        probe.remove();
        return out;
      });
      expect(colours[0]).toBe(tokens.run1);
      const strip = await page.getByTestId("progress-strip").locator("i").evaluateAll((els) => els.map((e) => getComputedStyle(e).backgroundColor));
      expect(strip[0]).toBe(tokens.progress1);
      expect(strip[strip.length - 1]).toBe(tokens.progress6);
      expect(new Set(strip).size).toBe(strip.length);
      expect(tokens.toolbar).toBe("3rem");
      await expectNoHorizontalScroll(page);
    });

    test("the mock serves the lab library apart from the driver's own", async ({ request }) => {
      await resetMock(request);
      const main = await (await request.get(`/api/v1/drivers/${DRIVER}/sessions?limit=50`)).json();
      const lab = await (await request.get(`/api/v1/drivers/${DRIVER}/sessions?library=lab`)).json();
      expect(lab.sessions.map((s: { session_id: number }) => s.session_id)).toEqual([900, 901]);
      expect(main.sessions.some((s: { session_id: number }) => s.session_id >= 900)).toBe(false);
      const labSetups = await (await request.get(`/api/v1/drivers/${DRIVER}/setups?library=lab`)).json();
      expect(labSetups.setups.map((s: { names: string[] }) => s.names[0])).toEqual(["bot sweep.sto"]);
    });
  });
}

test.describe("narrow desktop window", () => {
  test.use({ viewport: { width: 520, height: 800 }, hasTouch: false });
  test.beforeEach(async ({ request }) => resetMock(request));

  test("a pointer device gets a popover, not a sheet, and Escape closes it", async ({ page }) => {
    await page.goto(hash("/__kit"));
    const trigger = page.getByRole("button", { name: "Filters", exact: true });
    await trigger.click();
    const pop = page.getByRole("dialog", { name: "Filters" });
    await expect(pop).toHaveClass(/fb-pop/);
    await expect(page.locator(".fb-backdrop")).toHaveCount(0);
    await pop.getByRole("button", { name: /^Car/ }).click();
    await page.getByRole("option", { name: /Dallara/ }).click();
    await page.keyboard.press("Escape");
    await expect(pop).toBeVisible();
    await expect(pop.getByRole("button", { name: "Car · Dallara IR-18" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Filters · 1" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(pop).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Filters · 1" })).toBeFocused();
    await page.getByRole("button", { name: "Filters · 1" }).click();
    await page.mouse.click(5, 400);
    await expect(page.getByRole("dialog", { name: "Filters" })).toHaveCount(0);
  });
});
