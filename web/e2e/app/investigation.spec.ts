import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { raceProgressColour } from "../../src/core/palette";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { ROUND2_COMPARISONS } from "../../src/investigation/fixtures/round2";
import { buildRound3Analysis, ROUND3_SLIP_LAPS, ROUND3_TRAFFIC_LAPS } from "../../src/investigation/fixtures/round3";
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

// Web rounds 2 and 3, Investigation (web-round-2-plan.md): the acceptance lines at 1440, 1024 and 390, on the synthetic race served
// in place of session 103's analysis (the round-2 race plus round 3's per-lap context and a no-tire stop).
const ANALYSIS = buildRound3Analysis("an-103");
// RI_SHOTS names another release record's folder (relative to web/); the default is round 3's R3.
const SHOTS = path.resolve(process.cwd(), process.env.RI_SHOTS ?? path.join("target", "round3", "R3"));

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
      // #40: the hollow/filled rule is not a legend item; the header says once how many laps the fit used.
      await expect(page.locator("[data-legend=screened]")).toHaveCount(0);
      await expect(page.locator("[data-pace-fit]")).toHaveText(/^Pace fit: 39 of \d+ laps$/);
      await point(page, ".ri-dot[data-lap='10']");
      await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit · close traffic");
      await page.locator(".ri-dot[data-lap='11']").focus();
      await expect(page.locator("[data-race-readout]")).toContainText("Lap 11");
      await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit");
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

    test("Race (#43 #45): no comparison machinery; the chart takes the height; one line per run under it, in the run's colour", async ({ page }) => {
      await open(page, "race", `?comparison=${ROUND2_COMPARISONS.window}`);
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      await expect(page.locator(".ri-comparisons, [data-comparison], [data-bracket], [data-selected-comparison], .ri-race-bottom")).toHaveCount(0);
      await expect(page.locator(".ri-race-view > *")).toHaveCount(1);
      if (width >= 1000) {
        const view = (await page.locator(".ri-race-view").boundingBox())!;
        const card = (await page.locator(".ri-race-view > .ri-card").boundingBox())!;
        expect(Math.abs(card.height - view.height), "the chart's card fills the tab").toBeLessThanOrEqual(1);
      }
      const lines = page.locator("[data-run-line]");
      await expect(lines).toHaveText([
        `Run 1 · L3–24 · +0.033 s/lap · ${(60 / 3.785411784).toFixed(1)} gal`,
        `Run 2 · L31–52 · +0.029 s/lap · ${(59.2 / 3.785411784).toFixed(1)} gal`,
        `Run 3 · same tires · L57–82 · ${(52.1 / 3.785411784).toFixed(1)} gal`,
      ]);
      await expect(lines.nth(0)).toHaveAttribute("style", /--run:\s*var\(--run-1\)/);
      await expect(lines.nth(2)).toHaveAttribute("style", /--run:\s*color-mix\(in srgb, var\(--run-2\)/);
      expect(await lines.nth(0).evaluate((el) => getComputedStyle(el).color)).toBe(await page.evaluate(() => {
        const probe = document.createElement("i");
        probe.style.color = "var(--run-1)";
        document.body.append(probe);
        const value = getComputedStyle(probe).color;
        probe.remove();
        return value;
      }));
      await expectNoHorizontalScroll(page);
    });

    test("Race (#40 #42 #44): legend is the runs only; dots plus the backend's smooth fit over its span; no lap-to-lap line; tire-set colour", async ({ page }) => {
      await open(page, "race");
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      const legend = page.locator(".ri-progress .ri-legend");
      await expect(legend.locator(".ri-run-key")).toHaveText(["Run 1", "Run 2", "Run 3 · same tires"]);
      await expect(legend).not.toContainText(/caution|pit|incident|fastest|position|lap|fit/i);
      // No polyline through the laps and no observed-trend line; the fit is the only line, confined to its span.
      await expect(page.locator("[data-trend-run], .ri-trend, polyline")).toHaveCount(0);
      await expect(page.locator("[data-fit-run]")).toHaveCount(2);
      await expect(page.locator("[data-fit-run='1']")).toHaveAttribute("data-fit-span", "3-24");
      await expect(page.locator("[data-fit-run='2']")).toHaveAttribute("data-fit-span", "31-52");
      await expect(page.locator("[data-fit-run='3']")).toHaveCount(0);
      expect(await page.locator("[data-fit-run='1'] path.ri-fit").getAttribute("d")).toMatch(/^M[\d.]+,[\d.]+C/);
      // Run 3 shares run 2's tires: run 2's hue, lighter; run 1 and run 2 have their own.
      await expect(page.locator(".ri-dot[data-lap='12']")).toHaveAttribute("stroke", "var(--run-1)");
      await expect(page.locator(".ri-dot[data-lap='40']")).toHaveAttribute("stroke", "var(--run-2)");
      await expect(page.locator(".ri-dot[data-lap='70']")).toHaveAttribute("stroke", /^color-mix\(in srgb, var\(--run-2\) \d+%, white\)$/);
      const [run2, run3] = await page.evaluate(() => ["40", "70"].map((lap) => getComputedStyle(document.querySelector(`.ri-dot[data-lap='${lap}']`)!).stroke));
      expect(run3, "lighter shade, not the same colour").not.toBe(run2);
      // Hollow dots stay for screened laps; the fastest lap carries the magenta ring and says so on hover.
      await expect(page.locator(".ri-dot.excluded[data-lap='10']")).toHaveAttribute("fill", "var(--ri-card)");
      const fastest = page.locator("[data-fastest]");
      await expect(fastest).toHaveCount(1);
      await point(page, `.ri-dot[data-lap='${await fastest.getAttribute("data-fastest")}']`);
      await expect(page.locator("[data-race-readout]")).toContainText("Fastest");
      // The pit marker explains itself: a fuel-only stop reads `No tires`, and nothing about fields the backend did not report.
      await point(page, "[data-pit-marker='56'] .ri-pit-tag");
      const card = page.locator("[data-pit-card='56']");
      await expect(card).toContainText("No tires");
      await expect(card).toContainText("+8.0");
      await expect(card).not.toContainText(/extract|repair|penalt/i);
      await expect(page.locator("[data-pit-unextracted]")).toHaveCount(0);
    });

    test("Race (#41): every top-edge marker keeps its glow inside the chart", async ({ page }) => {
      await open(page, "race");
      await page.locator("[data-pit-marker]").first().waitFor();
      const report = await page.evaluate(() => {
        const svg = document.querySelector<SVGSVGElement>(".ri-plot-svg")!;
        const top = svg.getBoundingClientRect().top;
        const tags = [...document.querySelectorAll(".ri-pit-tag, .ri-inc-tag")].map((tag) => tag.getBoundingClientRect().top - top);
        const blur = parseFloat(getComputedStyle(document.documentElement).fontSize) * 0.375;
        return { tags, overflow: getComputedStyle(svg).overflow, blur };
      });
      expect(report.tags.length).toBeGreaterThanOrEqual(3);
      for (const offset of report.tags) expect(offset, "tag top inside the chart, clear of its glow").toBeGreaterThanOrEqual(report.blur * 1.5);
      expect(report.overflow, "the marker layer may draw past the plot box").toBe("visible");
      mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, `race-top-edge-${width}.png`), clip: await page.locator(".ri-progress").boundingBox().then((b) => ({ x: b!.x, y: b!.y, width: b!.width, height: Math.min(140, b!.height) })) });
    });

    test("Race (#46): context layers are off by default, one mark per lap, read out with their evidence, remembered on the device", async ({ page }) => {
      const requests: string[] = [];
      await open(page, "race");
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      const toggles = page.getByRole("group", { name: "Context layers" }).getByRole("button");
      // Track usage has no series in this recording: not offered.
      await expect(toggles).toHaveText(["Track tempmeasured", "Trafficderived", "Wheel slipproxy", "Fuelmeasured"]);
      for (const toggle of await toggles.all()) await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator("[data-layer], .ri-traffic-tick, .ri-slip-ring, [data-lap-column]")).toHaveCount(0);
      for (const key of ["temp", "traffic", "slip", "fuel"]) await page.locator(`[data-layer-toggle=${key}]`).click();
      await expect(page.locator("[data-layer=temp] .ri-temp-line")).toHaveCount(3);
      await expect(page.locator("[data-layer=temp]")).toContainText("Track °F");
      await expect(page.locator("[data-layer=fuel] .ri-fuel-line")).toHaveCount(3);
      await expect(page.locator("[data-layer=fuel] .ri-fuel-label").first()).toHaveText(`${(60 / 3.785411784).toFixed(1)} gal`);
      // Traffic: a tick under each lap with close traffic, its length the share of the lap; a lap with none draws nothing.
      const tick = async (lap: number) => page.locator(`[data-traffic-lap='${lap}']`).evaluate((el) => Number(el.getAttribute("y2")) - Number(el.getAttribute("y1")));
      expect(await tick(ROUND3_TRAFFIC_LAPS[0])).toBeGreaterThan(await tick(12));
      await expect(page.locator("[data-traffic-lap='9']")).toHaveCount(0);
      const ticks = await page.locator(".ri-traffic-tick").count();
      expect(ticks).toBeGreaterThan(40);
      await expect(page.locator(".ri-slip-ring")).toHaveCount(ROUND3_SLIP_LAPS.length);
      // Never a second chart.
      await expect(page.locator(".ri-race-view .ri-plot-svg")).toHaveCount(1);
      // One readout per lap, each figure with its evidence label.
      await point(page, `.ri-dot[data-lap='${ROUND3_TRAFFIC_LAPS[0]}']`);
      const readout = page.locator("[data-race-readout]");
      await expect(readout).toContainText("Traffic 62 % derived");
      await expect(readout).toContainText(/Track \d+\.\d °F measured/);
      await expect(readout).toContainText(/Fuel \d+\.\d gal measured/);
      await point(page, `.ri-dot[data-lap='${ROUND3_SLIP_LAPS[0]}']`);
      await expect(readout).toContainText("Spin 0.4 s · lock 0.1 s proxy");
      // Pointer movement over the chart with every layer on makes no request.
      page.on("request", (request) => requests.push(request.url()));
      if (!touch) {
        const box = (await page.locator(".ri-plot-svg").boundingBox())!;
        for (let i = 0; i <= 40; i++) await page.mouse.move(box.x + (box.width * i) / 40, box.y + box.height * 0.35);
      }
      expect(requests, "requests under pointer movement").toEqual([]);
      // Remembered on this device.
      await page.reload();
      await expect(page.locator("[data-layer-toggle=traffic]")).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator(".ri-traffic-tick")).toHaveCount(ticks);
      await page.locator("[data-layer-toggle=traffic]").click();
      await expect(page.locator(".ri-traffic-tick")).toHaveCount(0);
      expect(await page.evaluate(() => localStorage.getItem("coach.race-layers"))).toBe(JSON.stringify(["temp", "slip", "fuel"]));
      await expectNoHorizontalScroll(page);
    });

    test("a comparison in the route still marks its exact groups on the Traces rail (it left the Race tab, #43)", async ({ page }) => {
      await open(page, "traces", `?comparison=${ROUND2_COMPARISONS.window}`);
      await expect(page.locator(".ri-lap-row[data-lap='61']")).toHaveAttribute("data-group", "earlier");
      await expect(page.locator(".ri-lap-row[data-lap='63']")).not.toHaveAttribute("data-group", /./);
      await expect(page.locator(".ri-lap-row[data-lap='79']")).toHaveAttribute("data-group", "later");
      await expect(page.locator(".ri-lap-row[data-lap='33']")).not.toHaveAttribute("data-group", /./);
      await expect(page.locator(`[data-comparison-strip=${ROUND2_COMPARISONS.window}]`)).toContainText("Run 3: L61–62, 64–65 → L77–81");
    });

    test("Traces: the comparison strip shows the faster group and the unequal exposure with the cursor and inputs still visible", async ({ page }) => {
      await open(page, "traces");
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
      await open(page, "traces", `?comparison=${ROUND2_COMPARISONS.none}`);
      const none = page.locator(`[data-comparison-strip=${ROUND2_COMPARISONS.none}]`);
      await expect(none.locator("[data-pace-unusable]")).toHaveText("No pace result · every lap screened · close traffic");
      await expect(none.locator(".ri-pbar, .ri-delta, button")).toHaveCount(0);
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
      // The stop's detail (#39): tires, fuel and the stop time; a field the backend did not report is absent, with no sentence about it.
      await page.locator(".ri-lap-rail").getByRole("button", { name: "Green" }).click();
      const pit = page.locator("[data-pit-stop='28']");
      await pit.scrollIntoViewIfNeeded();
      // A rail scroll closes the card; let the scroll event pass before pointing at the stop.
      await page.waitForTimeout(250);
      await pit.hover({ force: true });
      const card = page.locator("[data-pit-card='28']");
      await expect(card).toContainText("4 tires");
      await expect(card).toContainText("13.8");
      await expect(card.locator("[data-pit-unextracted], p")).toHaveCount(0);
      await expect(card).not.toContainText(/extract|repair|penalt/i);
      // The rail's run rows carry the tire-set identity: run 3 is on run 2's tires.
      await expect(page.locator(".ri-lap-group[data-run='3'] .ri-run-head")).toHaveAttribute("style", /--run:\s*color-mix\(in srgb, var\(--run-2\)/);
      await expect(page.locator(".ri-lap-group[data-run='3'] .ri-run-head")).toHaveAttribute("title", "Run 3 · same tires");
    });

    test("Runs (#44 #47): dots plus the backend's fit, no polyline; each run card toggles its run, one stays, the chart rescales, the session remembers", async ({ page }) => {
      await open(page, "runs");
      await expect(page.locator("[data-trend-run], .ri-trend, polyline")).toHaveCount(0);
      await expect(page.locator(".ri-runs .ri-dot[data-run='1']").first()).toHaveAttribute("stroke", "var(--run-1)");
      await expect(page.locator(".ri-runs .ri-dot[data-run='2']").first()).toHaveAttribute("stroke", "var(--run-2)");
      await expect(page.locator(".ri-runs .ri-dot[data-run='3']").first()).toHaveAttribute("stroke", /^color-mix\(in srgb, var\(--run-2\)/);
      await expect(page.locator(".ri-runs [data-legend=runs] .ri-run-key")).toHaveText(["Run 1", "Run 2", "Run 3 · same tires"]);
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
      await expect(cards.nth(2)).toContainText("Run 3 · same tires");
      await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
      // Each card is its run's toggle: off hides the run's dots and fit and dims the card; the chart rescales to the visible runs.
      for (const card of await cards.all()) await expect(card).toHaveAttribute("aria-pressed", "true");
      const svg = page.locator(".ri-runs .ri-plot-svg");
      const domain = await svg.getAttribute("data-domain");
      await cards.nth(0).click();
      await expect(cards.nth(0)).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator(".ri-runs .ri-dot[data-run='1']")).toHaveCount(0);
      await expect(page.locator("[data-fit-run='1']")).toHaveCount(0);
      await expect(page.locator(".ri-runs [data-legend=runs] .ri-run-key")).toHaveText(["Run 2", "Run 3 · same tires"]);
      await expect.poll(async () => Number(await cards.nth(0).evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(0.6);
      await cards.nth(2).click();
      await expect(page.locator(".ri-runs .ri-dot[data-run='3']")).toHaveCount(0);
      await expect(svg).not.toHaveAttribute("data-domain", domain ?? "");
      // The last visible run stays (its card says so and ignores the click).
      await cards.nth(1).click({ force: true });
      await expect(cards.nth(1)).toHaveAttribute("aria-pressed", "true");
      await expect(cards.nth(1)).toHaveAttribute("aria-disabled", "true");
      await expect(page.locator(".ri-runs .ri-dot[data-run='2']").first()).toBeVisible();
      // Remembered for the session: a reload keeps the choice.
      await page.reload();
      await expect(page.locator("[data-run-card]").nth(0)).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator("[data-run-card]").nth(2)).toHaveAttribute("aria-pressed", "false");
      await page.locator("[data-run-card]").nth(0).click();
      await expect(page.locator(".ri-runs .ri-dot[data-run='1']").first()).toBeVisible();
      await expectNoHorizontalScroll(page);
    });

    test("screenshots of Race, Runs, Sections and Traces for the release record", async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      const ready: Record<string, string> = { race: ".ri-dot", runs: "[data-run-card]", sections: ".ri-body > :not(.ri-empty)", traces: "[data-analysis-trace-path]" };
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
      // Race with every context layer on and a lap read out; Runs with run 1 hidden.
      await open(page, "race");
      await page.locator(".ri-dot").first().waitFor();
      for (const key of ["temp", "traffic", "slip", "fuel"]) await page.locator(`[data-layer-toggle=${key}]`).click();
      await point(page, `.ri-dot[data-lap='${ROUND3_SLIP_LAPS[1]}']`);
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(SHOTS, `race-layers-${width}.png`), fullPage: width < 700 });
      await open(page, "runs");
      await page.locator("[data-run-card]").first().click();
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(SHOTS, `runs-run1-hidden-${width}.png`), fullPage: width < 700 });
    });
  });
}
