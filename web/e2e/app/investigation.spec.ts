import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { raceProgressColour } from "../../src/core/palette";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildRound2Analysis, ROUND2_COMPARISONS } from "../../src/investigation/fixtures/round2";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// The shell around the workspace (W8's own view checks follow, on the round-2 fixture).
for (const device of DEVICES) {
  appTest(device, () => {
    test("the shell resolves the session to its analysis and mounts the workspace on the route's view", async ({ page }) => {
      const requests: string[] = [];
      page.on("request", (r) => requests.push(new URL(r.url()).pathname));
      await page.goto(hash(`/d/${DRIVER}/s/103/race`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      expect(requests).toContain("/api/v1/sessions/103");
      await expect.poll(() => requests.some((p) => p === "/api/v1/analyses/an-103/race")).toBe(true);
    });

    test("switching views follows the route, is remembered on this device at once and in the prefs on leaving", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/race`));
      await page.getByRole("tab", { name: "Traces" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
      const stored = await page.evaluate((c) => JSON.parse(localStorage.getItem(`coach.last-investigation.${c}`) ?? "null"), DRIVER);
      expect(stored).toMatchObject({ session_id: 103, analysis_id: "an-103", view: "traces" });
      // Silent: no prefs write (and so no outbox bar over the workspace) while the investigation is open.
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      expect((await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && "last_investigation" in (w.body as object))).toBe(false);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { last_investigation?: { view: string; analysis_id: string } }).last_investigation?.view === "traces")).toBe(true);
      await expect(page.getByText("Resume")).toHaveCount(0);
      // The tile reopens the session on the tab it was left on.
      await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
    });

    test("the P Q R selector moves between the phases of the event on the same view", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      const picker = page.getByRole("group", { name: "Session", exact: true });
      await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
      await expect(picker.getByRole("button", { name: "Race" })).toHaveAttribute("aria-pressed", "true");
      await picker.getByRole("button", { name: "Qualifying" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/traces$`));
    });

    test("a phase that cannot open is disabled and names its state (S2)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/104/race`));
      const picker = page.getByRole("group", { name: "Session", exact: true });
      await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
      await expect(picker.getByRole("button", { name: "Race · Not analyzed" })).toBeDisabled();
      await expect(picker.getByRole("button", { name: "Qualifying" })).toBeEnabled();
    });

    test("saved chart layouts persist: New is written to the driver's layouts and stays active after a reload", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await page.getByRole("button", { name: "Customize" }).click();
      const box = page.getByRole("region", { name: "Chart layouts" });
      await expect(box.locator("[data-active-layout]")).toHaveText("Default");
      await box.getByRole("button", { name: "New" }).click();
      await expect(box.locator("[data-active-layout]")).toHaveText("Custom");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => /\/trace-layouts\/analysis-layout-/.test(w.path))).toBe(true);
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && /^analysis-layout-/.test(String((w.body as { active_trace_layout_id?: string }).active_trace_layout_id)))).toBe(true);
      await page.reload();
      await page.getByRole("button", { name: "Customize" }).click();
      await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Custom");
      await page.getByRole("region", { name: "Chart layouts" }).getByRole("button", { name: "Delete" }).click();
      await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Default");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => (w as { method?: string }).method === "DELETE")).toBe(true);
    });

    test("a session without an analysis says so instead of opening something else", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/106/race`));
      await expect(page.getByRole("heading", { name: "Not analyzed" })).toBeVisible();
    });
  });
}

// Web round 2, Investigation (web-round-2-plan.md): the acceptance lines at 1440, 1024 and 390, on the round-2 synthetic race served
// in place of session 103's analysis.
const ANALYSIS = buildRound2Analysis("an-103");
// RI_SHOTS names another release record's folder (relative to web/); the default is A3's.
const SHOTS = path.resolve(process.cwd(), process.env.RI_SHOTS ?? path.join("target", "round2", "A3"));

async function serveRound2(page: Page): Promise<void> {
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

const open = (page: Page, view: string, query = "") => page.goto(hash(`/d/${DRIVER}/s/103/${view}${query}`));

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const touch = device.touch;
    const width = device.viewport.width;
    test.beforeEach(async ({ page }) => serveRound2(page));

    /** Hover on a pointer device, tap on touch: both must show the reason. */
    async function point(page: Page, selector: string): Promise<void> {
      const target = page.locator(selector).first();
      await target.scrollIntoViewIfNeeded();
      if (touch) await target.tap({ force: true });
      else await target.hover({ force: true });
    }

    test("Race: why a lap is absent from the fit reads from its hover, tap or focus; no EXCL anywhere; caution laps are dots", async ({ page }) => {
      await open(page, "race");
      await expect(page.locator(".ri-dot[data-lap='10']")).toHaveCount(1);
      await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
      await expect(page.locator("[data-legend=screened]")).toHaveText("Not used in pace fit");
      await point(page, ".ri-dot[data-lap='10']");
      await expect(page.locator("[data-race-readout]")).toContainText("Not used in Run 1 pace fit · close traffic");
      await page.locator(".ri-dot[data-lap='11']").focus();
      await expect(page.locator("[data-race-readout]")).toContainText("Lap 11");
      await expect(page.locator("[data-race-readout]")).toContainText("Not used in Run 1 pace fit");
      // Caution laps: actual lap times as subdued dots in the band; the dash row is gone.
      await expect(page.locator("[data-caution-dot]")).toHaveCount(3);
      await expect(page.locator(".ri-caution")).toHaveCount(0);
      await point(page, "[data-caution-dot='41']");
      await expect(page.locator("[data-race-readout]")).toContainText("Lap 41 · 38.200 s");
      // One comparison selector: the toolbar Compare dropdown is gone.
      await expect(page.getByRole("combobox", { name: "Compare" })).toHaveCount(0);
      // The Lap time title clears the pit and incident tags at every width (C3: they overprinted it at 390).
      const clashes = await page.evaluate(() => {
        const title = [...document.querySelectorAll(".ri-plot-svg .ri-axis-title")].find((el) => el.textContent === "Lap time · s")!.getBoundingClientRect();
        return [...document.querySelectorAll(".ri-pit-tag, .ri-inc-tag")].filter((tag) => {
          const r = tag.getBoundingClientRect();
          return r.left < title.right && title.left < r.right && r.top < title.bottom && title.top < r.bottom;
        }).length;
      });
      expect(clashes, "tags over the Lap time title").toBe(0);
      await expectNoHorizontalScroll(page);
    });

    test("selecting a comparison keeps the lap readout, the scroll and the view; nothing remounts", async ({ page }) => {
      await open(page, "race");
      const readout = page.locator("[data-race-readout]");
      await page.locator(".ri-dot[data-lap='11']").focus();
      await expect(readout).toContainText("Lap 11");
      const row = page.locator(`[data-comparison=${ROUND2_COMPARISONS.window}]`);
      await row.scrollIntoViewIfNeeded();
      await page.evaluate(() => {
        const w = window as unknown as { __c3: (Element | null)[] };
        w.__c3 = [".ri-workspace", ".ri-plot-svg", ".ri-comparisons"].map((s) => document.querySelector(s));
      });
      const scrollY = await page.evaluate(() => window.scrollY);
      const top = (await page.locator(".ri-workspace").boundingBox())!.y;
      await row.focus();
      await page.keyboard.press("Enter");
      await expect(row).toHaveAttribute("aria-pressed", "true");
      await expect(page).toHaveURL(new RegExp(`/race\\?comparison=${ROUND2_COMPARISONS.window}`));
      await expect(page.locator("[data-bracket=later]")).toHaveAttribute("data-laps", "77-81");
      expect(await page.evaluate(() => {
        const w = window as unknown as { __c3: (Element | null)[] };
        return [".ri-workspace", ".ri-plot-svg", ".ri-comparisons"].every((s, i) => w.__c3[i] === document.querySelector(s));
      }), "same nodes").toBe(true);
      await expect(readout, "the readout keeps its lap").toContainText("Lap 11");
      await expect(page.getByRole("tab", { name: "Race", exact: true })).toHaveAttribute("aria-selected", "true");
      expect(Math.abs((await page.evaluate(() => window.scrollY)) - scrollY), "page scroll").toBeLessThanOrEqual(1);
      expect(Math.abs((await page.locator(".ri-workspace").boundingBox())!.y - top), "no shift").toBeLessThanOrEqual(1);
    });

    test("Sections zone and axis labels never overprint; the section boundary label always shows", async ({ page }) => {
      await open(page, "sections");
      await page.locator("[data-ri-chart]").first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      await page.waitForTimeout(100);
      const labels = await page.evaluate(() => {
        const shown = (selector: string) => [...document.querySelectorAll(selector)].filter((el) => getComputedStyle(el).visibility !== "hidden").map((el) => el.getBoundingClientRect());
        const overlap = (boxes: DOMRect[]) => boxes.some((a, i) => boxes.some((b, j) => j > i && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom));
        return { zones: overlap(shown(".ri-zone:not(.missing)")), axis: overlap(shown(".ri-x-axis span")), section: shown(".ri-zone.section").length, middle: shown(".ri-x-axis .middle").length };
      });
      expect(labels).toEqual({ zones: false, axis: false, section: 1, middle: 1 });
    });

    test("selecting one comparison changes its exact groups on the chart, the panel and the Traces rail", async ({ page }) => {
      await open(page, "race");
      const earlier = page.locator("[data-bracket=earlier]");
      await expect(earlier).toHaveAttribute("data-laps", "31-35");
      await expect(earlier).toContainText("Earlier L31–35");
      await expect(page.locator("[data-bracket=later]")).toHaveAttribute("data-laps", "61-65");
      await expect(page.locator("[data-comparison=run-2-vs-run-3] .ri-comparison-title")).toHaveText("Run 2 → Run 3");
      await expect(page.locator(".ri-ring")).toHaveCount(0);
      const row = page.locator(`[data-comparison=${ROUND2_COMPARISONS.window}]`);
      await expect(row.locator(".ri-comparison-title")).toHaveText("Run 3: L61–62, 64–65 → L77–81");
      await row.scrollIntoViewIfNeeded();
      await row.click();
      await expect(row).toHaveAttribute("aria-pressed", "true");
      await expect(page).toHaveURL(new RegExp(`comparison=${ROUND2_COMPARISONS.window}`));
      // Split around the omitted lap 63.
      await expect(earlier).toHaveAttribute("data-laps", "61-62,64-65");
      await expect(earlier.locator(".ri-bracket")).toHaveCount(2);
      await expect(earlier).toContainText("Earlier L61–62, 64–65");
      await expect(page.locator("[data-bracket=later]")).toHaveAttribute("data-laps", "77-81");
      const panel = page.locator(`[data-selected-comparison=${ROUND2_COMPARISONS.window}]`);
      await expect(panel.locator("[data-direction]")).toHaveAttribute("data-direction", "faster");
      await expect(panel.locator(".ri-delta b")).toHaveText("−0.052");
      await expect(panel.locator("[data-group=later] .ri-gdot.hollow")).toHaveCount(1);
      // The rail in Traces marks the same groups; the view and the comparison survive the switch.
      await page.getByRole("tab", { name: "Traces" }).click();
      await expect(page.locator(".ri-lap-row[data-lap='61']")).toHaveAttribute("data-group", "earlier");
      await expect(page.locator(".ri-lap-row[data-lap='63']")).not.toHaveAttribute("data-group", /./);
      await expect(page.locator(".ri-lap-row[data-lap='79']")).toHaveAttribute("data-group", "later");
      await expect(page.locator(".ri-lap-row[data-lap='33']")).not.toHaveAttribute("data-group", /./);
      await expect(page.locator(`[data-comparison-strip=${ROUND2_COMPARISONS.window}]`)).toContainText("Run 3: L61–62, 64–65 → L77–81");
    });

    test("the picture shows the faster group and the unequal exposure with the cursor and inputs still visible", async ({ page }) => {
      await open(page, "race");
      const panel = page.locator(`[data-selected-comparison=${ROUND2_COMPARISONS.runs}]`);
      await expect(panel.locator("[data-direction]")).toHaveAttribute("data-direction", "slower");
      await expect(panel.locator(".ri-delta")).toContainText("Later slower");
      await expect(panel.locator("[data-metric=traffic] [data-side=earlier]")).toHaveText("4");
      await expect(panel.locator("[data-metric=traffic] [data-side=later]")).toHaveText("22");
      await expect(panel.locator("[data-metric=fuel] .ri-pbar-scale")).toHaveText("020");
      await expect(panel.locator("[data-counts]")).toHaveText(["5/5 green", "5/5 green"]);
      await expect(panel.locator(".ri-median-mark")).toHaveCount(2);
      await expect(panel.locator("p")).toHaveCount(0);
      // Traces: the same picture beside the chart cursor, the map and the inputs.
      await page.getByRole("tab", { name: "Traces" }).click();
      const strip = page.locator(`[data-comparison-strip=${ROUND2_COMPARISONS.runs}]`);
      await expect(strip.locator("[data-direction]")).toHaveAttribute("data-direction", "slower");
      await expect(strip.locator("[data-metric=traffic] [data-side=later]")).toHaveText("22");
      const surface = page.locator(".ri-chart-surface");
      await expect(surface).toBeVisible();
      await strip.scrollIntoViewIfNeeded();
      const s = (await strip.boundingBox())!;
      const c = (await surface.boundingBox())!;
      expect(s.y, "strip in view").toBeGreaterThanOrEqual(0);
      expect(c.y + 100, "chart rows in view under the strip").toBeLessThanOrEqual(device.viewport.height);
      await page.mouse.move(c.x + c.width * 0.4, c.y + 40);
      await expect(page.locator(".ri-track-panel [data-field=speed]")).not.toHaveText(/^—?$/);
      // With no pace result: one line and nothing else.
      await open(page, "race", `?comparison=${ROUND2_COMPARISONS.none}`);
      const none = page.locator(`[data-selected-comparison=${ROUND2_COMPARISONS.none}]`);
      await expect(none.locator("[data-pace-unusable]")).toHaveText("No pace result · every lap screened · close traffic");
      await expect(none.locator(".ri-pbar, .ri-dotrow, .ri-delta, button")).toHaveCount(0);
    });

    test("clicking a section is a state change: cursor, scroll and comparison survive, nothing remounts or shifts", async ({ page }) => {
      await open(page, "sections");
      const chart = page.locator("[data-ri-chart]").first();
      await expect(chart).toBeVisible();
      await chart.focus();
      for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowRight");
      const cursor = chart.locator("[data-ri-cursor]");
      const before = Number(await cursor.getAttribute("x1"));
      await page.evaluate(() => {
        const w = window as unknown as { __a3: Record<string, Element | null> };
        w.__a3 = { ws: document.querySelector(".ri-workspace"), tel: document.querySelector(".ri-telemetry"), chart: document.querySelector("[data-ri-chart]") };
      });
      const button = page.locator("[data-section=B]");
      await button.scrollIntoViewIfNeeded();
      const scrollY = await page.evaluate(() => window.scrollY);
      const top = (await page.locator(".ri-workspace").boundingBox())!.y;
      await button.click();
      await expect(button).toHaveAttribute("aria-pressed", "true");
      await expect(page).toHaveURL(/section=B/);
      await expect(page.locator(".ri-pass-head h2")).toContainText("Section B");
      expect(await page.evaluate(() => {
        const w = window as unknown as { __a3: Record<string, Element | null> };
        return w.__a3.ws === document.querySelector(".ri-workspace") && w.__a3.tel === document.querySelector(".ri-telemetry") && w.__a3.chart === document.querySelector("[data-ri-chart]");
      }), "same nodes").toBe(true);
      expect(Math.abs(Number(await cursor.getAttribute("x1")) - before), "cursor keeps its place").toBeLessThan(20);
      expect(Math.abs((await page.evaluate(() => window.scrollY)) - scrollY), "page scroll").toBeLessThanOrEqual(1);
      expect(Math.abs((await page.locator(".ri-workspace").boundingBox())!.y - top), "no shift").toBeLessThanOrEqual(1);
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      await expect(page.locator("[data-section-comparison]")).toHaveText("Run 2 → Run 3");
      // Gain and loss against the selected comparison, neutral where unsupported.
      await expect(page.locator("[data-section=A] strong")).toHaveAttribute("data-tone", "loss");
      await expect(page.locator("[data-section=B] strong")).toHaveAttribute("data-tone", "gain");
      await expect(page.locator("[data-section=C] strong")).toHaveAttribute("data-tone", "neutral");
    });

    test("the lap rail scrolls without a jump: pixel 0 of scrolling moves nothing", async ({ page }) => {
      await open(page, "traces");
      await expect(page.locator(".ri-run-head").first()).toBeVisible();
      await page.locator(".ri-lap-scroll").scrollIntoViewIfNeeded();
      const offsets = await page.evaluate(async () => {
        const scroller = document.querySelector<HTMLElement>(".ri-lap-scroll")!;
        const head = scroller.querySelector<HTMLElement>(".ri-run-head")!;
        const bar = scroller.querySelector<HTMLElement>(".ri-rail-bar")!;
        const out: { at: number; head: number; bar: number }[] = [];
        for (const at of [0, 1, 2, 3, 8, 30]) {
          scroller.scrollTop = at;
          await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
          const base = scroller.getBoundingClientRect().top;
          out.push({ at, head: head.getBoundingClientRect().top - base, bar: bar.getBoundingClientRect().top - base });
        }
        const root = parseFloat(getComputedStyle(document.documentElement).fontSize);
        return { out, barHeight: bar.getBoundingClientRect().height, toolbar: 3 * root, marginTop: getComputedStyle(head).marginTop, stickyTop: getComputedStyle(head).top };
      });
      for (const step of offsets.out) {
        expect(step.bar, `bar at ${step.at}`).toBeCloseTo(offsets.out[0]!.bar, 0);
        expect(step.head, `run row at ${step.at}`).toBeCloseTo(offsets.out[0]!.head, 0);
      }
      expect(offsets.marginTop).toBe("0px");
      expect(offsets.barHeight).toBeCloseTo(offsets.toolbar, 0);
      expect(parseFloat(offsets.stickyTop)).toBeCloseTo(offsets.toolbar, 0);
    });

    test("the selected tab and a pressed toggle ignore hover", async ({ page }) => {
      await open(page, "traces");
      const tab = page.getByRole("tab", { name: "Traces" });
      await expect(tab).toHaveAttribute("aria-selected", "true");
      await page.mouse.move(0, 0);
      const resting = await tab.evaluate((el) => getComputedStyle(el).backgroundColor);
      await tab.hover();
      expect(await tab.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(resting);
      const green = page.locator(".ri-lap-rail").getByRole("button", { name: "Green" });
      await green.click();
      await expect(green).toHaveAttribute("aria-pressed", "true");
      await page.mouse.move(0, 0);
      const pressed = await green.evaluate((el) => getComputedStyle(el).backgroundColor);
      await green.hover();
      expect(await green.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(pressed);
    });

    test("Traces: race-progress lap colour that no filter changes, screening on the lap chip, fuel figures only, endpoints in the rail", async ({ page }) => {
      await open(page, "traces");
      const lap = (n: number) => page.locator(`.ri-lap-row[data-lap='${n}']`);
      await expect(lap(1)).toHaveAttribute("style", new RegExp(`--lap:\\s*${raceProgressColour(0, 2)}`, "i"));
      await expect(lap(82)).toHaveAttribute("style", new RegExp(`--lap:\\s*${raceProgressColour(1, 2)}`, "i"));
      await expect(page.locator("[data-progress-ends='1-82']")).toContainText("L1");
      await expect(page.locator("[data-progress-ends='1-82']")).toContainText("L82");
      const colour12 = await lap(12).getAttribute("style");
      await page.locator(".ri-lap-rail").getByRole("button", { name: "Green" }).click();
      await expect(lap(41)).toHaveCount(0);
      expect(await lap(12).getAttribute("style")).toBe(colour12);
      await expect(lap(10).locator("[data-lap-chip=screened]")).toHaveAttribute("title", "Not used in Run 1 pace fit · close traffic");
      await expect(page.locator(".ri-lap-rail")).not.toContainText(/excl/i);
      for (const text of await page.locator(".ri-lap-row .ri-fu").allTextContents()) expect(text).toMatch(/^(\d+\.\d{3}|—)$/);
      await expect(page.locator(".ri-run-head").first()).toHaveAttribute("style", /--run:\s*var\(--run-1\)/);
      // The cursor readouts keep label, figure and unit whole (C3: `151.3 mp` at 1024); a desktop row wraps a long name instead of cutting it.
      expect(await page.evaluate(() => [...document.querySelectorAll(".ri-kv")].filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.className)), "clipped readouts").toEqual([]);
      if (width >= 1440) expect(await page.evaluate(() => [...document.querySelectorAll(".ri-row-signal")].filter((el) => el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent)), "cut row names").toEqual([]);
      // The stop's detail: nothing drawn for repair or penalty, one line saying why.
      await page.locator(".ri-lap-rail").getByRole("button", { name: "Green" }).click();
      const pit = page.locator("[data-pit-stop='28']");
      await pit.scrollIntoViewIfNeeded();
      // A rail scroll closes the card; let the scroll event pass before pointing at the stop.
      await page.waitForTimeout(250);
      await pit.hover({ force: true });
      await expect(page.locator("[data-pit-card='28'] [data-pit-unextracted]")).toHaveText("Repairs and penalties: not extracted");
      await expect(page.locator("[data-pit-card='28']")).not.toContainText(/repair\s*\d|penalt(y|ies)\s*\d/i);
    });

    test("Runs: run colours on run-level marks, the fit drawn over its span with the backend slope, run cards instead of a paragraph", async ({ page }) => {
      await open(page, "runs");
      await expect(page.locator("[data-trend-run='1']").first()).toHaveAttribute("stroke", "var(--run-1)");
      await expect(page.locator("[data-trend-run='2']").first()).toHaveAttribute("stroke", "var(--run-2)");
      await expect(page.locator("[data-fit-run='1']")).toHaveAttribute("data-fit-span", "3-24");
      await expect(page.locator("[data-fit-run='1'] [data-fit-label]")).toHaveText("+0.033 s/lap · 18 laps");
      await expect(page.locator("[data-fit-run='2'] [data-fit-label]")).toHaveText("+0.029 s/lap · 21 laps");
      await expect(page.locator("[data-fit-run='3']")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Screened fit" })).toHaveCount(0);
      const cards = page.locator("[data-run-card]");
      await expect(cards).toHaveCount(3);
      await expect(cards.nth(0).locator("[data-run-slope]")).toHaveText("+0.033 s/lap · 18 laps");
      await expect(cards.nth(2).locator("[data-run-slope]")).toHaveText("No fit");
      await expect(cards.nth(0)).toContainText("15.9 → 3.8 gal");
      await expect(cards.nth(1)).toContainText("25–52");
      await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
      await expectNoHorizontalScroll(page);
    });

    test("screenshots of Race, Runs, Sections and Traces for the release record", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      const ready: Record<string, string> = { race: ".ri-dot", runs: "[data-run-card]", sections: "[data-ri-chart]", traces: "[data-analysis-trace-path]" };
      for (const view of ["race", "runs", "sections", "traces"]) {
        await open(page, view, view === "race" ? `?comparison=${ROUND2_COMPARISONS.window}` : "");
        await page.locator(ready[view]!).first().waitFor();
        await page.waitForTimeout(400);
        if (view === "traces") {
          const surface = (await page.locator(".ri-chart-surface").boundingBox())!;
          await page.mouse.move(surface.x + surface.width * 0.38, surface.y + Math.min(80, surface.height * 0.4));
          await page.waitForTimeout(150);
        }
        await page.screenshot({ path: path.join(SHOTS, `${view}-${width}.png`), fullPage: width < 700 });
      }
    });
  });
}
