import { type Locator, type Page, expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState, resetMock, setMock } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

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

    test("a trend keeps its backlog as live frames accumulate and draws a line, not isolated points", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await layoutSelect(page).selectOption("factory-qualifying");
      const canvas = page.locator("[data-metric=speed] canvas");
      const read = () => canvas.evaluate((c: HTMLCanvasElement) => {
        const px = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
        let lit = 0;
        let left = c.width;
        for (let i = 3; i < px.length; i += 4) if (px[i]! > 0) {
          lit++;
          left = Math.min(left, ((i - 3) / 4) % c.width);
        }
        return { lit, left };
      });
      await expect.poll(async () => (await read()).lit).toBeGreaterThan(300);
      const before = await read();
      await page.waitForTimeout(4000);
      const after = await read();
      expect(after.lit).toBeGreaterThanOrEqual(before.lit);
      expect(after.left).toBeLessThanOrEqual(before.left);
    });

    test("there is no update rate control: every device asks for 60 Hz", async ({ page }) => {
      const urls: string[] = [];
      page.on("websocket", (ws) => urls.push(ws.url()));
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Live");
      await expect(page.getByLabel("Update rate")).toHaveCount(0);
      await expect(page.getByText("Update rate")).toHaveCount(0);
      expect(urls.some((u) => u.includes(`/ws/live/${DRIVER}?rate=60`))).toBe(true);
    });

    test("frames that stop make every tile Waiting and the headline Waiting for iRacing, and a restart is Live again", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "true");
      await expect(page.locator("[data-metric=position]")).toContainText("P8");
      await setMock(request, { liveStalled: true });
      await expect(page.getByTestId("live-status")).toContainText("Waiting for iRacing", { timeout: 6000 });
      await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "false");
      for (const metric of ["position", "last-lap", "leader-gap", "fuel"]) await expect(page.locator(`[data-metric=${metric}]`)).toContainText("Waiting");
      await setMock(request, { liveStalled: false });
      await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "true", { timeout: 6000 });
      await expect(page.locator("[data-metric=position]")).toContainText("P8");
    });

    test("a session that starts after the page opened turns the headline Live", async ({ page, request }) => {
      await setMock(request, { live: false });
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Waiting for iRacing");
      await setMock(request, { live: true });
      await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "true", { timeout: 6000 });
      await expect(page.locator("[data-metric=position]")).toContainText("P8");
    });

    test("only Default is built in; Race and Qualifying are layouts the driver can edit", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await layoutSelect(page).selectOption("factory-race");
      await expect(page.getByText("Built in")).toHaveCount(0);
      await page.getByRole("button", { name: "Edit layout" }).click();
      await page.getByRole("toolbar", { name: "Layout editor" }).getByRole("button", { name: "Layout", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "Layout" }).getByLabel("Name")).toHaveValue("Race");
    });

    test("scrollbars follow the theme on the page and on a scrolling panel", async ({ page }) => {
      await page.goto(hash("/__kit"));
      await expect(page.getByTestId("kit-scroll")).toBeVisible();
      const read = (sel: string) => page.evaluate((s) => {
        const el = document.querySelector(s)!;
        const cs = getComputedStyle(el);
        return { scheme: getComputedStyle(document.documentElement).colorScheme, color: cs.scrollbarColor, width: cs.scrollbarWidth };
      }, sel);
      const token = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--color-border-strong").trim());
      for (const sel of ["html", "[data-testid=kit-scroll]"]) {
        const got = await read(sel);
        expect(got.scheme, sel).toBe("dark");
        expect(got.width, sel).toBe("thin");
        const hex = token.replace("#", "");
        const rgb = `rgb(${parseInt(hex.slice(0, 2), 16)}, ${parseInt(hex.slice(2, 4), 16)}, ${parseInt(hex.slice(4, 6), 16)})`;
        expect(got.color, sel).toContain(rgb);
      }
    });

    test("leaving the page closes the socket so the collector can stop streaming", async ({ page }) => {
      let closed = false;
      page.on("websocket", (ws) => ws.on("close", () => (closed = true)));
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Live");
      await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Sessions" }).click();
      await expect.poll(() => closed).toBe(true);
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

// ---- Live editor (web-round-2-plan.md, Live editor; backlog #25) ---------------------------------------------------------

type Point = { x: number; y: number };
const SHOTS = "target/round2/A4";
const LAYOUTS = `/api/v1/drivers/${DRIVER}/live-layouts`;

const tileCell = (page: Page, metric: string) => page.locator(".tile-cell", { has: page.locator(`[data-metric="${metric}"]`) });
const centre = (b: { x: number; y: number; width: number; height: number }): Point => ({ x: b.x + b.width / 2, y: b.y + b.height / 2 });

/** Centre of grid cell (row, col), 0-based, in viewport coordinates. */
async function cellCentre(page: Page, row: number, col: number): Promise<Point> {
  await settled(page);
  const grid = page.locator(".live-grid");
  const box = (await grid.boundingBox())!;
  const { rows, cols } = await grid.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { rows: cs.gridTemplateRows.split(" ").length, cols: cs.gridTemplateColumns.split(" ").length };
  });
  return { x: box.x + ((col + 0.5) * box.width) / cols, y: box.y + ((row + 0.5) * box.height) / rows };
}

/**
 * Measure only once the saves have landed: the shared pending-writes bar (ui/outbox-bar.tsx) sits in the page flow above the
 * grid while a write is in flight and moves it.
 */
async function settled(page: Page) {
  await expect(page.getByTestId("outbox")).toHaveCount(0);
}

async function handle(page: Page, metric: string, which: "grip" | "corner"): Promise<Point> {
  await settled(page);
  const el = tileCell(page, metric).locator(which === "grip" ? ".le-grip" : ".le-corner");
  return centre((await el.boundingBox())!);
}

/** A drag with the mouse, or with a finger through CDP touch events (Chromium turns them into touch pointer events). */
async function drag(page: Page, touch: boolean, from: Point, to: Point, during?: () => Promise<void>) {
  const steps = 10;
  if (!touch) {
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps });
    if (during) await during();
    await page.mouse.up();
    return;
  }
  const cdp = await page.context().newCDPSession(page);
  const at = (i: number) => ({ x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps });
  await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [from] });
  for (let i = 1; i <= steps; i++) await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [at(i)] });
  if (during) await during();
  await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  await cdp.detach();
}

interface SavedLayout {
  name: string;
  body: { rows: number; columns: number; tiles: { metric: string; row: number; column: number; row_span: number; column_span: number; style: string; accent?: string | null }[] };
}
async function lastWrite(request: Parameters<typeof mockState>[0], id: string): Promise<SavedLayout | undefined> {
  const writes = (await mockState(request)).writes.filter((w) => w.path === `${LAYOUTS}/${id}`);
  return writes.at(-1)?.body as SavedLayout | undefined;
}
const tileIn = (w: SavedLayout | undefined, metric: string) => w?.body.tiles.find((t) => t.metric === metric);

async function openEditor(page: Page) {
  await page.goto(hash(`/d/${DRIVER}/live`));
  await expect(page.locator("[data-metric=position]")).toContainText("P8");
  await page.getByRole("button", { name: "Edit layout" }).click();
  await expect(page.getByRole("toolbar", { name: "Layout editor" })).toBeVisible();
}

async function press(touch: boolean, target: Locator) {
  if (touch) await target.tap();
  else await target.click();
}

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    const touch = d.touch;
    const tag = d.name.split(" ")[0]!;
    const phone = d.viewport.width < 700;

    test("the editor moves, swaps, resizes, changes a representation and a colour, undoes, and the reload keeps it", async ({ page, request }) => {
      await openEditor(page);
      const bar = page.getByRole("toolbar", { name: "Layout editor" });
      await expect(bar.getByText("Built in · edits save a copy")).toBeVisible();
      await expect(bar.getByRole("button", { name: "Undo" })).toBeDisabled();
      // Rows and Columns are not in the normal flow: they live in the Layout surface.
      await expect(page.getByRole("group", { name: "Rows" })).toHaveCount(0);
      await press(touch, bar.getByRole("button", { name: "Layout", exact: true }));
      const sheet = page.getByRole("dialog", { name: "Layout" });
      await expect(sheet.getByRole("button", { name: "Save as new layout" })).toBeVisible();
      await press(touch, sheet.getByRole("button", { name: "Rows more" }));
      await press(touch, sheet.getByRole("button", { name: "Close" }));
      await expect(page.getByRole("dialog", { name: "Layout" })).toHaveCount(0);
      // The first change saved the built-in layout as a copy, and the select names it.
      await expect.poll(async () => (await lastWrite(request, "default-copy"))?.body.rows).toBe(3);
      await expect(layoutSelect(page)).toHaveValue("default-copy");
      await expect(page.locator(".le-slot")).toHaveCount(3);

      // Move: Position to row 3, column 1 (drag the grip with a mouse; select, then place on touch).
      if (touch) {
        await tileCell(page, "position").tap();
        await page.getByTestId("le-strip").getByRole("button", { name: "Move", exact: true }).tap();
        await page.getByRole("button", { name: "Place at row 3, column 1" }).tap();
      } else {
        await drag(page, false, await handle(page, "position", "grip"), await cellCentre(page, 2, 0), async () => {
          await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "valid");
        });
      }
      await expect(tileCell(page, "position")).toHaveCSS("grid-row-start", "3");
      await expect(tileCell(page, "position")).toHaveCSS("grid-column-start", "1");

      // Swap: Last lap onto the equal-sized gap tile.
      if (touch) {
        await tileCell(page, "last-lap").tap();
        await page.getByTestId("le-strip").getByRole("button", { name: "Move", exact: true }).tap();
        await page.getByRole("button", { name: "Swap with Physical gap to leader" }).tap();
      } else {
        await drag(page, false, await handle(page, "last-lap", "grip"), await cellCentre(page, 0, 2), async () => {
          await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "swap");
        });
      }
      await expect(tileCell(page, "last-lap")).toHaveCSS("grid-column-start", "3");
      await expect(tileCell(page, "leader-gap")).toHaveCSS("grid-column-start", "2");

      // Resize: a corner dragged over an occupied cell previews "No room" and changes nothing; Position spans the free third row.
      if (touch) {
        await tileCell(page, "position").tap();
        const strip = page.getByTestId("le-strip");
        await expect(strip.getByRole("button", { name: "Height more" })).toBeDisabled();
        await strip.getByRole("button", { name: "Width more" }).tap();
        await strip.getByRole("button", { name: "Width more" }).tap();
        await expect(strip.getByRole("button", { name: "Width more" })).toBeDisabled();
      } else {
        await drag(page, false, await handle(page, "last-lap", "corner"), await cellCentre(page, 1, 2), async () => {
          await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "invalid");
          await expect(page.getByTestId("le-preview")).toContainText("No room");
        });
        await expect(tileCell(page, "last-lap")).toHaveCSS("grid-row-end", "span 1");
        await drag(page, false, await handle(page, "position", "corner"), await cellCentre(page, 2, 2), async () => {
          await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "valid");
          await expect(page.getByTestId("le-preview")).toContainText("3×1");
        });
      }
      await expect(tileCell(page, "position")).toHaveCSS("grid-column-end", "span 3");
      await expect(page.getByTestId("le-preview")).toHaveCount(0);

      // Representation: fuel from Bar to Gauge (the stream carries the capacity), previewed at once.
      await press(touch, tileCell(page, "fuel"));
      const strip = page.getByTestId("le-strip");
      const show = strip.getByRole("group", { name: "Show" }).getByRole("button");
      await expect(show).toHaveText(["Text", "Trend", "Bar", "Gauge"]);
      await press(touch, strip.getByRole("group", { name: "Show" }).getByRole("button", { name: "Gauge" }));
      await expect(page.locator("[data-metric=fuel]")).toHaveClass(/style-gauge/);
      await expect(page.locator("[data-metric=fuel] svg.gauge")).toBeVisible();

      // Colour: Position offers Text only; Last lap takes Blue on its neutral value.
      await press(touch, tileCell(page, "position"));
      await expect(show).toHaveText(["Text"]);
      await press(touch, tileCell(page, "last-lap"));
      await press(touch, strip.getByRole("group", { name: "Colour" }).getByRole("button", { name: "Blue" }));
      const lastLap = page.locator("[data-metric=last-lap]");
      await expect(lastLap).toHaveClass(/has-accent/);
      const blue = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--race-pit").trim());
      const rgb = (hex: string) => `rgb(${parseInt(hex.slice(1, 3), 16)}, ${parseInt(hex.slice(3, 5), 16)}, ${parseInt(hex.slice(5, 7), 16)})`;
      await expect(lastLap.locator(".tile-primary")).toHaveCSS("color", rgb(blue));
      await expect.poll(async () => tileIn(await lastWrite(request, "default-copy"), "last-lap")?.accent).toBe("blue");
      await page.screenshot({ path: `${SHOTS}/editor-edited-${tag}.png`, fullPage: true });

      // Undo is one tap: the colour goes back, and the undone layout is what is saved.
      await press(touch, bar.getByRole("button", { name: "Undo" }));
      await expect(lastLap).not.toHaveClass(/has-accent/);
      await expect.poll(async () => tileIn(await lastWrite(request, "default-copy"), "last-lap")?.accent ?? "default").toBe("default");
      await expect.poll(async () => tileIn(await lastWrite(request, "default-copy"), "fuel")?.style).toBe("gauge");

      await press(touch, bar.getByRole("button", { name: "Done" }));
      await expect(page.locator(".le-grid")).toHaveCount(0);

      // Reload: this device shows the edited layout and every change is there.
      await page.reload();
      await expect(layoutSelect(page)).toHaveValue("default-copy");
      await expect(layoutSelect(page)).toContainText("Default (on rig)");
      await expect(page.locator("[data-metric=position]")).toContainText("P8");
      if (!phone) {
        await expect(tileCell(page, "position")).toHaveCSS("grid-row-start", "3");
        await expect(tileCell(page, "position")).toHaveCSS("grid-column-end", "span 3");
        await expect(tileCell(page, "last-lap")).toHaveCSS("grid-column-start", "3");
      }
      await expect(page.locator("[data-metric=fuel]")).toHaveClass(/style-gauge/);
      await expect(page.locator("[data-metric=last-lap]")).not.toHaveClass(/has-accent/);
      const saved = (await lastWrite(request, "default-copy"))!;
      expect(saved.name).toBe("Default copy");
      expect(tileIn(saved, "position")).toMatchObject({ row: 2, column: 0, column_span: 3 });
      expect(tileIn(saved, "leader-gap")).toMatchObject({ row: 0, column: 1 });
      await page.screenshot({ path: `${SHOTS}/board-after-reload-${tag}.png`, fullPage: true });
      await expectNoHorizontalScroll(page);
    });

    test("the palette adds by tap and by drag; a full grid grows a row; nothing is saved while the pointer moves", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await layoutSelect(page).selectOption("factory-race");
      await page.getByRole("button", { name: "Edit layout" }).click();
      const palette = page.getByRole("region", { name: "Add a tile" });
      // Tap-to-add on the full 2 x 3 grid adds a third row.
      await press(touch, palette.getByRole("button", { name: "Speed", exact: true }));
      await expect(tileCell(page, "speed")).toHaveCSS("grid-row-start", "3");
      await expect(page.getByTestId("le-strip").getByRole("heading", { name: "Speed" })).toBeVisible();
      await expect.poll(async () => (await lastWrite(request, "factory-race"))?.body.rows).toBe(3);
      await expect(palette.getByRole("button", { name: "Speed", exact: true })).toHaveCount(0);
      await press(touch, page.getByTestId("le-strip").getByRole("heading", { name: "Speed" }));
      await page.keyboard.press("Escape");
      await expect(page.getByTestId("le-strip")).toHaveCount(0);

      // Drag-in: Air temperature onto row 3, column 3, with the placement preview on the way and no write until the drop.
      const chip = palette.getByRole("button", { name: "Air temperature", exact: true });
      const target = page.locator(".le-slot").last();
      await settled(page);
      await target.scrollIntoViewIfNeeded();
      await chip.scrollIntoViewIfNeeded();
      const from = centre((await chip.boundingBox())!);
      const to = centre((await target.boundingBox())!);
      expect(to.y, "the drop cell is on screen").toBeGreaterThan(0);
      const writesBefore = (await mockState(request)).writes.length;
      await drag(page, touch, from, to, async () => {
        await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "valid");
        expect((await mockState(request)).writes.length, "no write while dragging").toBe(writesBefore);
      });
      await expect(tileCell(page, "air-temperature")).toHaveCSS("grid-column-start", "3");
      await expect(tileCell(page, "air-temperature")).toHaveCSS("grid-row-start", "3");
      await expect.poll(async () => tileIn(await lastWrite(request, "factory-race"), "air-temperature")).toMatchObject({ row: 2, column: 2, style: "number" });
    });

    test("tiles carrying a state offer no colour; Rows, Columns and Save as new layout sit in the Layout surface; Show on rig stays", async ({ page, request }) => {
      await openEditor(page);
      await press(touch, tileCell(page, "coach-cue"));
      const strip = page.getByTestId("le-strip");
      await expect(strip.getByRole("heading", { name: "Coach cue" })).toBeVisible();
      await expect(strip.getByRole("group", { name: "Colour" })).toHaveCount(0);
      await expect(strip.getByRole("group", { name: "Show" }).getByRole("button")).toHaveText(["Text"]);
      await expect(page.getByRole("button", { name: "Save as new layout" })).toHaveCount(0);
      await press(touch, page.getByRole("toolbar", { name: "Layout editor" }).getByRole("button", { name: "Layout", exact: true }));
      const sheet = page.getByRole("dialog", { name: "Layout" });
      await sheet.getByLabel("Name").fill("Pit stop");
      await sheet.getByLabel("Name").press("Enter");
      await expect.poll(async () => (await lastWrite(request, "pit-stop"))?.name).toBe("Pit stop");
      await press(touch, sheet.getByRole("button", { name: "Columns more" }));
      await expect.poll(async () => (await lastWrite(request, "pit-stop"))?.body.columns).toBe(4);
      if (touch) await expectTouchTargets(page);
      await press(touch, sheet.getByRole("button", { name: "Save as new layout" }));
      await expect.poll(async () => (await lastWrite(request, "pit-stop-copy"))?.name).toBe("Pit stop copy");
      await expect(layoutSelect(page)).toHaveValue("pit-stop-copy");
      await press(touch, page.getByRole("button", { name: "Done" }));
      await press(touch, page.getByRole("button", { name: "Show on rig" }));
      await expect(layoutSelect(page)).toContainText("Pit stop copy (on rig)");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { active_live_layout_id?: string }).active_live_layout_id === "pit-stop-copy")).toBe(true);
      await expectNoHorizontalScroll(page);
    });

    test("Waiting shows each tile's shape", async ({ page, request }) => {
      await setMock(request, { live: false });
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.getByTestId("live-status")).toContainText("Waiting for iRacing");
      await expect(page.locator("[data-metric=position] .tile-primary")).toHaveText("P–");
      await expect(page.locator("[data-metric=last-lap] .tile-primary")).toHaveText("–:––.–––");
      await expect(page.locator("[data-metric=leader-gap] .tile-primary")).toHaveText("–.––");
      await expect(page.locator("[data-metric=leader-gap] .tile-unit")).toHaveText("s");
      await expect(page.locator("[data-metric=fuel] .tile-bar")).toBeVisible();
      await expect(page.locator("[data-metric=fuel] .tile-unit")).toHaveText("US gal");
      await page.screenshot({ path: `${SHOTS}/board-waiting-${tag}.png`, fullPage: true });
    });

    test("screenshots: the board, the editor with a tile selected, and select-then-place", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/live`));
      await expect(page.locator("[data-metric=position]")).toContainText("P8");
      await page.waitForTimeout(600);
      await page.screenshot({ path: `${SHOTS}/board-${tag}.png`, fullPage: true });
      await page.getByRole("button", { name: "Edit layout" }).click();
      await press(touch, tileCell(page, "fuel"));
      await expect(page.getByTestId("le-strip")).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/editor-${tag}.png`, fullPage: true });
      // The owner's type minimums hold in the editor too: text 14 px, controls 36 px (44 px where the app's touch rule applies).
      const type = await page.evaluate(() => {
        let text = Infinity;
        let control = Infinity;
        let small = "";
        for (const el of document.querySelectorAll<HTMLElement>(".le-bar *, .le-side *")) {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if ([...el.childNodes].some((n) => n.nodeType === 3 && n.textContent!.trim())) text = Math.min(text, parseFloat(getComputedStyle(el).fontSize));
          if (el.matches("button, select, input") && r.height < control) {
            control = r.height;
            small = `${el.tagName} ${el.className} ${el.textContent}`;
          }
        }
        return { text, control, small, touchRule: matchMedia("(max-width: 700px), (pointer: coarse)").matches };
      });
      expect(type.text).toBeGreaterThanOrEqual(14);
      expect(type.control, type.small).toBeGreaterThanOrEqual(type.touchRule ? 43.5 : 35.5);
      await press(touch, page.getByTestId("le-strip").getByRole("button", { name: "Move", exact: true }));
      await expect(page.getByRole("button", { name: /^Swap with/ }).first()).toBeVisible();
      await page.screenshot({ path: `${SHOTS}/editor-place-${tag}.png` });
      await expectNoHorizontalScroll(page);
    });
  });
}

test.describe("tablet 1024x768 touch", () => {
  test.use({ viewport: { width: 1024, height: 768 }, hasTouch: true });
  test.beforeEach(async ({ request }) => resetMock(request));

  test("touch drags: the grip moves, a drop on an equal tile swaps, the corner resizes with a valid-placement preview", async ({ page, request }) => {
    await page.goto(hash(`/d/${DRIVER}/live`));
    await layoutSelect(page).selectOption("factory-race");
    await page.getByRole("button", { name: "Edit layout" }).tap();
    await page.getByRole("toolbar", { name: "Layout editor" }).getByRole("button", { name: "Layout", exact: true }).tap();
    await page.getByRole("dialog", { name: "Layout" }).getByRole("button", { name: "Rows more" }).tap();
    await page.getByRole("dialog", { name: "Layout" }).getByRole("button", { name: "Close" }).tap();
    // Handles are touch-sized.
    const grip = (await tileCell(page, "position").locator(".le-grip").boundingBox())!;
    expect(grip.width).toBeGreaterThanOrEqual(44);
    expect(grip.height).toBeGreaterThanOrEqual(44);

    await drag(page, true, await handle(page, "position", "grip"), await cellCentre(page, 2, 1), async () => {
      await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "valid");
    });
    await expect(tileCell(page, "position")).toHaveCSS("grid-row-start", "3");
    await expect(tileCell(page, "position")).toHaveCSS("grid-column-start", "2");

    await drag(page, true, await handle(page, "ahead-gap", "grip"), await cellCentre(page, 0, 2), async () => {
      await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "swap");
    });
    await expect(tileCell(page, "ahead-gap")).toHaveCSS("grid-column-start", "3");
    await expect(tileCell(page, "behind-gap")).toHaveCSS("grid-column-start", "2");

    await drag(page, true, await handle(page, "position", "corner"), await cellCentre(page, 2, 2), async () => {
      await expect(page.getByTestId("le-preview")).toHaveAttribute("data-verdict", "valid");
      await expect(page.getByTestId("le-preview")).toContainText("2×1");
    });
    await expect(tileCell(page, "position")).toHaveCSS("grid-column-end", "span 2");
    await expect.poll(async () => tileIn(await lastWrite(request, "factory-race"), "position")).toMatchObject({ row: 2, column: 1, column_span: 2 });
    await page.screenshot({ path: `${SHOTS}/editor-touch-tablet.png` });
  });
});

test.describe("editor idle cost", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test.beforeEach(async ({ request }) => resetMock(request));

  test("the open editor keeps the 60 Hz board inside the frame budget at 4x CPU throttle", async ({ page }) => {
    const sent: string[] = [];
    page.on("websocket", (ws) => ws.on("framesent", (f) => sent.push(String(f.payload))));
    await page.goto(hash(`/d/${DRIVER}/live`));
    await layoutSelect(page).selectOption("factory-qualifying");
    await expect(page.locator("[data-metric=speed] canvas")).toBeVisible();
    await expect(page.getByTestId("live-status")).toHaveAttribute("data-live", "true");
    const cdp = await page.context().newCDPSession(page);
    await cdp.send("Performance.enable");
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
    const seconds = 4;
    const sample = async () => {
      const metric = (list: { name: string; value: number }[], name: string) => list.find((x) => x.name === name)?.value ?? 0;
      const before = (await cdp.send("Performance.getMetrics")).metrics;
      const frames = await page.evaluate(
        (ms) =>
          new Promise<number[]>((resolve) => {
            const out: number[] = [];
            let last = performance.now();
            const end = last + ms;
            const step = (now: number) => {
              out.push(now - last);
              last = now;
              if (now < end) requestAnimationFrame(step);
              else resolve(out);
            };
            requestAnimationFrame(step);
          }),
        seconds * 1000,
      );
      const after = (await cdp.send("Performance.getMetrics")).metrics;
      const delta = (name: string) => ((metric(after, name) - metric(before, name)) * 1000) / seconds;
      const sorted = [...frames].sort((a, b) => a - b);
      return {
        frames: frames.length,
        meanFrameMs: Math.round((frames.reduce((a, b) => a + b, 0) / frames.length) * 100) / 100,
        p95FrameMs: Math.round(sorted[Math.floor(sorted.length * 0.95)]! * 100) / 100,
        scriptMsPerS: Math.round(delta("ScriptDuration")),
        styleLayoutMsPerS: Math.round(delta("LayoutDuration") + delta("RecalcStyleDuration")),
      };
    };
    const board = await sample();
    await page.getByRole("button", { name: "Edit layout" }).click();
    await page.waitForTimeout(300);
    const editorIdle = await sample();
    await tileCell(page, "speed").click();
    await page.waitForTimeout(300);
    const editorSelected = await sample();
    await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });
    const result = { cpuThrottle: 4, seconds, board, editorIdle, editorSelected, rateMessages: sent.filter((s) => s.includes('"rate"')) };
    mkdirSync(SHOTS, { recursive: true });
    writeFileSync(`${SHOTS}/idle-cost.json`, `${JSON.stringify(result, null, 2)}\n`);
    // The governor never stepped down, and the open editor stays inside the board's frame budget (rate.ts FRAME_BUDGET_MS, 25 ms).
    expect(result.rateMessages).toEqual([]);
    expect(editorIdle.meanFrameMs).toBeLessThan(25);
    expect(editorSelected.meanFrameMs).toBeLessThan(25);
  });
});
