import { expect, test, type Page } from "@playwright/test";
import { DEVICES, DRIVER, appTest, hash, setMock } from "./helpers";

// Screens for docs/08-rewrite/reviews/web-round1-review-codex-sol.md findings 3, 4, 7 and 8, on web/mock's race 103.

async function openTraces(page: Page): Promise<void> {
  await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  await expect(page.locator("[data-analysis-trace-path]").first()).toBeAttached();
}

/** Scrolls the chart frame to its end and returns how far the last row ends below the frame's visible bottom (≤ 0: reachable). */
async function lastRowOverhang(page: Page): Promise<number> {
  return page.evaluate(() => {
    const frame = document.querySelector<HTMLElement>("[data-chart-scroll]")!;
    frame.scrollTop = frame.scrollHeight;
    const rows = [...document.querySelectorAll<SVGRectElement>("[data-analysis-chart-row]")];
    const last = rows[rows.length - 1]!.getBoundingClientRect();
    return last.bottom - frame.getBoundingClientRect().bottom;
  });
}

for (const device of DEVICES) {
  appTest(device, () => {
    test("every trace row can be reached in the chart, also in a short window (finding 3)", async ({ page }) => {
      await openTraces(page);
      expect(await lastRowOverhang(page)).toBeLessThanOrEqual(1);
      if (device.viewport.width >= 1024) {
        await page.setViewportSize({ width: device.viewport.width, height: 520 });
        // The six default rows no longer fit at their floor: the frame scrolls rather than clipping them.
        await expect.poll(() => page.locator("[data-chart-scroll]").evaluate((frame) => frame.scrollHeight - frame.clientHeight)).toBeGreaterThan(0);
        await expect.poll(() => lastRowOverhang(page)).toBeLessThanOrEqual(1);
        const rowHeights = await page.locator("[data-analysis-chart-row]").evaluateAll((rows) => rows.map((row) => row.getBoundingClientRect().height));
        expect(Math.min(...rowHeights)).toBeGreaterThanOrEqual(30);
      }
    });

    test("Clear removes the cursor's markers and the map readouts of the old laps (finding 4)", async ({ page }) => {
      await openTraces(page);
      const surface = page.locator("[data-analysis-trace-chart]");
      const box = (await surface.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.4, box.y + Math.min(box.height, 300) * 0.3);
      const speed = page.locator(".ri-track-panel [data-field=speed]");
      await expect(speed).not.toHaveText("—");
      await page.locator(".ri-lap-rail").getByRole("button", { name: "Clear", exact: true }).click();
      await expect(page.getByText("No laps selected")).toBeVisible();
      await expect(speed).toHaveText("—");
      await expect(page.locator(".ri-readout-head")).toContainText("No laps");
      await expect(page.locator(".ri-map-marker")).toHaveCSS("visibility", "hidden");
      await page.mouse.move(box.x + box.width * 0.6, box.y + Math.min(box.height, 300) * 0.3);
      await page.waitForTimeout(200);
      await expect(speed).toHaveText("—");
    });

    test("Tire load draws its ribbon once the stress channel arrives (finding 7)", async ({ page }) => {
      await openTraces(page);
      await expect(page.locator(".ri-metric-segment")).toHaveCount(0);
      await page.getByRole("combobox", { name: "Map colour" }).selectOption({ label: "Tire load" });
      await expect.poll(() => page.locator(".ri-metric-segment").count()).toBeGreaterThan(0);
    });

    test("a failed channel request says Failed and Retry fetches it (finding 8)", async ({ page, request }) => {
      await setMock(request, { failChannels: ["slip"] });
      await openTraces(page);
      const slipRow = page.locator("[data-row-empty]").filter({ hasText: "Failed" });
      await expect(slipRow).toHaveCount(1);
      await expect(page.locator("[data-channel-error]")).toHaveText("Slip angle failed");
      await setMock(request, { failChannels: [] });
      await page.getByRole("button", { name: "Retry" }).click();
      await expect(page.locator("[data-analysis-trace-path][data-signal=slip]").first()).toBeAttached();
      await expect(page.locator("[data-channel-error]")).toHaveCount(0);
    });
  });
}
