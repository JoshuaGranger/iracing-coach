import { expect, test } from "@playwright/test";
import { DEVICES, appTest, expectNoHorizontalScroll, hash } from "./helpers";

for (const device of DEVICES) {
  appTest(device, () => {
    test("the phase selector reaches every enabled segment with the arrow keys, skips a disabled one, and Enter chooses", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const group = page.getByRole("group", { name: "Session phase" });
      await expect(group.getByRole("button", { name: "Race", exact: true })).toHaveAttribute("aria-pressed", "true");
      await group.getByRole("button", { name: "Practice" }).focus();
      await page.keyboard.press("ArrowRight");
      await expect(group.getByRole("button", { name: "Qualifying" })).toBeFocused();
      await page.keyboard.press("ArrowRight");
      await expect(group.getByRole("button", { name: "Race", exact: true })).toBeFocused();
      await page.keyboard.press("ArrowRight");
      await expect(group.getByRole("button", { name: "Practice" })).toBeFocused();
      await page.keyboard.press("ArrowRight");
      await page.keyboard.press("Enter");
      await expect(group.getByRole("button", { name: "Qualifying" })).toHaveAttribute("aria-pressed", "true");
      await expect(group.getByRole("button", { name: "Race", exact: true })).toHaveAttribute("aria-pressed", "false");
      const blocked = group.getByRole("button", { name: "Race, not analyzed" });
      await expect(blocked).toBeDisabled();
      await expect(blocked).toHaveAttribute("title", "Not analyzed");
      await expectNoHorizontalScroll(page);
    });

    test("the pit pictogram lights the changed tires, sizes the gauge by the gallons and draws none for no fuel", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const stops = page.getByRole("region", { name: "Pit stops" }).locator("[data-testid=pit-pictogram]");
      await expect(stops).toHaveCount(3);
      await expect(stops.nth(0).locator("rect[data-lit=true]")).toHaveCount(2);
      await expect(stops.nth(0).locator(".fuel-gauge")).toHaveCount(1);
      await expect(stops.nth(0)).toContainText("L37");
      await expect(stops.nth(0)).toContainText("13.8 s");
      const fill = async (i: number) => Number(await stops.nth(i).locator(".fuel-gauge").getAttribute("data-fill"));
      expect(await fill(0)).toBeCloseTo(11.2 / 18.5, 2);
      expect(await fill(2)).toBeGreaterThan(await fill(0));
      await expect(stops.nth(1).locator("rect[data-lit=true]")).toHaveCount(0);
      await expect(stops.nth(1).locator(".fuel-gauge")).toHaveCount(0);
      await expect(stops.nth(2).locator("rect[data-lit=true]")).toHaveCount(4);
      const card = page.getByTestId("pit-card").first();
      await expect(card).toContainText("RF RR");
      await expect(card).toContainText("+11.2");
      await expect(page.getByTestId("pit-card").nth(1)).toContainText("4 tires");
      await expect(page.getByTestId("pit-card").nth(1)).not.toContainText("gal");
    });

    test("colour carries racing data: flag dots, gain and loss, best, fuel and the pit tag use the race tokens", async ({ page }) => {
      await page.goto(hash("/__kit"));
      await expect(page.getByRole("region", { name: "Marks" })).toBeVisible();
      const tokens = await page.evaluate(() => {
        const css = getComputedStyle(document.documentElement);
        const rgb = (name: string) => {
          const probe = document.createElement("i");
          probe.style.color = `var(${name})`;
          document.body.append(probe);
          const value = getComputedStyle(probe).color;
          probe.remove();
          return value;
        };
        const bg = (sel: string) => getComputedStyle(document.querySelector(sel)!).backgroundColor;
        return {
          green: [bg(".dot-green"), rgb("--race-green")],
          caution: [bg(".dot-caution"), rgb("--race-caution")],
          pit: [bg(".dot-pit"), rgb("--race-pit")],
          gain: [bg(".change-gain"), rgb("--race-green")],
          loss: [bg(".change-loss"), rgb("--race-loss")],
          interaction: [bg(".btn-accent"), rgb("--color-interaction")],
          best: [getComputedStyle(document.querySelector(".fig-best")!).color, rgb("--race-best")],
          fuel: [getComputedStyle(document.querySelector(".fig-fuel")!).color, rgb("--race-fuel")],
          accent: [css.getPropertyValue("--color-accent").trim().toLowerCase(), css.getPropertyValue("--color-interaction").trim().toLowerCase()],
        };
      });
      for (const [name, [got, want]] of Object.entries(tokens)) expect(got, name).toBe(want);
    });

    test("the event card carries the car stripe, the track silhouette, one band per phase and the Fixed badge", async ({ page }) => {
      await page.goto(hash("/__kit"));
      const card = page.getByRole("region", { name: "Event" });
      await expect(card.locator(".bc-silhouette")).toHaveCount(1);
      await expect(card.locator(".bc-tile")).toHaveCount(3);
      await expect(card.locator(".bc-tile-p .bc-band")).toContainText("Practice");
      await expect(card.locator(".bc-tile-r .bc-result-gain")).toHaveCount(1);
      await expect(card.locator(".badge-fixed")).toContainText("Fixed");
      await expect(card.locator(".bc-id .stripe")).toHaveCount(1);
      await expectNoHorizontalScroll(page);
    });
  });
}
