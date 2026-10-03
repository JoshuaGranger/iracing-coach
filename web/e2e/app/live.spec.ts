import { type Page, expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState, setMock } from "./helpers";

const layoutSelect = (page: Page) => page.locator(".live-controls .select-field").first().locator("select");

for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.name.startsWith("phone");

    test("shows live values from binary frames, with units and the coach cue", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "true");
      await expect(page.getByTestId("live-status")).toContainText("Live");
      const grid = page.locator(".live-grid");
      await expect(grid.locator("[data-metric=position]")).toContainText("P8");
      await expect(grid.locator("[data-metric=last-lap]")).toContainText("15.234");
      await expect(grid.locator("[data-metric=last-lap]")).toContainText("+0.134 s vs leader last lap");
      await expect(grid.locator("[data-metric=leader-gap]")).toContainText("12.34");
      await expect(grid.locator("[data-metric=coach-cue]")).toContainText("No action: pace and race state within the supported band");
      await expect(grid.locator("[data-metric=fuel]")).toContainText("US gal");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("says Waiting for iRacing while no collector reports a session", async ({ page, request }) => {
      await setMock(request, { live: false });
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Waiting for iRacing");
      await expect(page.locator("[data-metric=position]")).toContainText("Waiting");
    });

    test("trend tiles draw from the backlog and live frames", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await layoutSelect(page).selectOption("factory-qualifying");
      const canvas = page.locator("[data-metric=speed] canvas");
      await expect(canvas).toBeVisible();
      await expect.poll(async () => canvas.evaluate((c: HTMLCanvasElement) => {
        const g = c.getContext("2d")!;
        const px = g.getImageData(0, 0, c.width, c.height).data;
        let lit = 0;
        for (let i = 3; i < px.length; i += 4) if (px[i]! > 0) lit++;
        return lit;
      })).toBeGreaterThan(50);
    });

    test("the update rate is a per-device choice that reaches the server socket", async ({ page }) => {
      const urls: string[] = [];
      page.on("websocket", (ws) => urls.push(ws.url()));
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByLabel("Update rate")).toHaveValue(phone ? "10" : d.name.startsWith("tablet") ? "30" : "60");
      await page.getByLabel("Update rate").selectOption("1");
      await expect(page.getByLabel("Update rate")).toHaveValue("1");
      expect(urls.some((u) => u.includes(`/ws/live/${DRIVER}?rate=`))).toBe(true);
    });

    test("leaving the page closes the socket so the collector can stop streaming", async ({ page }) => {
      let closed = false;
      page.on("websocket", (ws) => ws.on("close", () => (closed = true)));
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Live");
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Sessions" }).click();
      await expect.poll(() => closed).toBe(true);
    });

    test("built-in layouts are edited as a copy; the editor adds, moves and saves a layout, then shows it on the rig", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await page.getByRole("button", { name: "Edit layout" }).click();
      const editor = page.getByRole("region", { name: "Layout editor" });
      await expect(editor.getByLabel("Name")).toHaveValue("Default copy");
      await expect(editor.getByRole("button", { name: "Save as new layout" })).toBeVisible();

      await editor.getByLabel("Name").fill("Pit stop");
      await editor.getByRole("button", { name: "Columns more" }).click();
      await editor.getByRole("button", { name: "Columns more" }).click();
      await editor.getByLabel("Add tile").selectOption({ label: "Speed" });
      const tileSettings = editor.getByRole("group", { name: "Speed settings" });
      await expect(tileSettings).toBeVisible();
      await tileSettings.getByLabel("Style").selectOption("trend");
      await tileSettings.getByLabel("Unit").selectOption("km/h");
      await tileSettings.getByLabel("Window").selectOption("s60");
      await tileSettings.getByRole("button", { name: "Width more" }).click();
      await editor.getByRole("button", { name: "Save as new layout" }).click();

      await expect(layoutSelect(page)).toHaveValue("pit-stop");
      await expect(page.locator("[data-metric=speed]")).toBeVisible();
      const put = (await mockState(request)).writes.find((w) => w.path.endsWith("/live-layouts/pit-stop"))!.body as { name: string; body: { columns: number; tiles: { metric: string; style: string; unit: string; trend_window: string; column_span: number }[] } };
      expect(put.name).toBe("Pit stop");
      expect(put.body.columns).toBe(5);
      expect(put.body.tiles.find((t) => t.metric === "speed")).toMatchObject({ style: "trend", unit: "km/h", trend_window: "s60", column_span: 2 });

      await page.getByRole("button", { name: "Show on rig" }).click();
      await expect(layoutSelect(page)).toContainText("Pit stop (on rig)");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { active_live_layout_id?: string }).active_live_layout_id === "pit-stop")).toBe(true);
      await expectNoHorizontalScroll(page);
    });

    test("a tile is refused where it would overlap, and the keyboard moves the selected tile into free space", async ({ page }) => {
      test.skip(phone, "touch layout uses the steppers");
      await page.goto(hash(`/d/${DRIVER}/live`));
      await page.getByRole("button", { name: "Edit layout" }).click();
      const editor = page.getByRole("region", { name: "Layout editor" });
      const cue = page.getByRole("button", { name: "Coach cue tile" });
      const cell = page.locator(".tile-cell", { has: page.locator("[data-metric=coach-cue]") });
      await expect(cell).toHaveCSS("grid-column-start", "3");
      await cue.focus();
      await page.keyboard.press("ArrowLeft"); // occupied by Laps to pit: refused
      await expect(cell).toHaveCSS("grid-column-start", "3");
      await editor.getByRole("button", { name: "Columns more" }).click();
      await cue.focus();
      await page.keyboard.press("ArrowRight");
      await expect(cell).toHaveCSS("grid-column-start", "4");
      await page.keyboard.press("Shift+ArrowDown"); // would leave the grid: refused
      await expect(cell).toHaveCSS("grid-row-end", "span 1");
    });

    test("reduced motion is honoured", async ({ browser }) => {
      const ctx = await browser.newContext({ viewport: d.viewport, reducedMotion: "reduce" });
      const page = await ctx.newPage();
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.locator("html")).toHaveAttribute("data-motion", "reduced");
      const dur = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--motion-structure").trim());
      expect(parseFloat(dur)).toBe(0);
      await ctx.close();
    });
  });
}
