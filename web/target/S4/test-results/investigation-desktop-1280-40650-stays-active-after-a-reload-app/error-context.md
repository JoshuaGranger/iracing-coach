# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: investigation.spec.ts >> desktop 1280x720 >> saved chart layouts persist: New is written to the driver's layouts and stays active after a reload
- Location: e2e\app\investigation.spec.ts:59:5

# Error details

```
Error: expect(locator).toHaveText(expected) failed

Locator:  getByRole('region', { name: 'Chart layouts' }).locator('[data-active-layout]')
Expected: "Custom"
Received: "Default"
Timeout:  5000ms

Call log:
  - Expect "toHaveText" getByRole('region', { name: 'Chart layouts' }).locator('[data-active-layout]') with timeout 5000ms
  - waiting for getByRole('region', { name: 'Chart layouts' }).locator('[data-active-layout]')
    14 × locator resolved to <strong data-active-layout="default">Default</strong>
       - unexpected value "Default"

```

```yaml
- strong: Default
```

# Test source

```ts
  1   | import { type Page, type Route, expect, test } from "@playwright/test";
  2   | import { mkdirSync } from "node:fs";
  3   | import path from "node:path";
  4   | import { raceProgressColour } from "../../src/core/palette";
  5   | import { routeSynthetic } from "../../src/investigation/fixtures/api";
  6   | import { ROUND2_COMPARISONS } from "../../src/investigation/fixtures/round2";
  7   | import { buildRound4Analysis, ROUND4_NO_FUEL_LAP, ROUND4_SERVICE_LAP } from "../../src/investigation/fixtures/round4";
  8   | import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState } from "./helpers";
  9   | import { ROUND2_DEVICES } from "./round2-shared";
  10  | 
  11  | // The shell around the workspace (W8's own view checks follow, on the round-2 fixture).
  12  | for (const device of DEVICES) {
  13  |   appTest(device, () => {
  14  |     test("the shell resolves the session to its analysis and mounts the workspace on the route's view", async ({ page }) => {
  15  |       const requests: string[] = [];
  16  |       page.on("request", (r) => requests.push(new URL(r.url()).pathname));
  17  |       await page.goto(hash(`/d/${DRIVER}/s/103/race`));
  18  |       await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
  19  |       expect(requests).toContain("/api/v1/sessions/103");
  20  |       await expect.poll(() => requests.some((p) => p === "/api/v1/analyses/an-103/race")).toBe(true);
  21  |     });
  22  | 
  23  |     test("switching views follows the route, is remembered on this device at once and in the prefs on leaving", async ({ page, request }) => {
  24  |       await page.goto(hash(`/d/${DRIVER}/s/103/race`));
  25  |       await page.getByRole("tab", { name: "Traces" }).click();
  26  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
  27  |       const stored = await page.evaluate((c) => JSON.parse(localStorage.getItem(`coach.last-investigation.${c}`) ?? "null"), DRIVER);
  28  |       expect(stored).toMatchObject({ session_id: 103, analysis_id: "an-103", view: "traces" });
  29  |       // Silent: no prefs write (and so no outbox bar over the workspace) while the investigation is open.
  30  |       await expect(page.getByTestId("outbox")).toHaveCount(0);
  31  |       expect((await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && "last_investigation" in (w.body as object))).toBe(false);
  32  |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  33  |       await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  34  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { last_investigation?: { view: string; analysis_id: string } }).last_investigation?.view === "traces")).toBe(true);
  35  |       await expect(page.getByText("Resume")).toHaveCount(0);
  36  |       // The tile reopens the session on the tab it was left on.
  37  |       await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
  38  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
  39  |     });
  40  | 
  41  |     test("the P Q R selector moves between the phases of the event on the same view", async ({ page }) => {
  42  |       await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  43  |       await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
  44  |       const picker = page.getByRole("group", { name: "Session", exact: true });
  45  |       await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
  46  |       await expect(picker.getByRole("button", { name: "Race" })).toHaveAttribute("aria-pressed", "true");
  47  |       await picker.getByRole("button", { name: "Qualifying" }).click();
  48  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/traces$`));
  49  |     });
  50  | 
  51  |     test("a phase that cannot open is disabled and names its state (S2)", async ({ page }) => {
  52  |       await page.goto(hash(`/d/${DRIVER}/s/104/race`));
  53  |       const picker = page.getByRole("group", { name: "Session", exact: true });
  54  |       await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
  55  |       await expect(picker.getByRole("button", { name: "Race · Not analyzed" })).toBeDisabled();
  56  |       await expect(picker.getByRole("button", { name: "Qualifying" })).toBeEnabled();
  57  |     });
  58  | 
  59  |     test("saved chart layouts persist: New is written to the driver's layouts and stays active after a reload", async ({ page, request }) => {
  60  |       await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  61  |       await page.getByRole("button", { name: "Customize" }).click();
  62  |       const box = page.getByRole("region", { name: "Chart layouts" });
  63  |       await expect(box.locator("[data-active-layout]")).toHaveText("Default");
  64  |       await box.getByRole("button", { name: "New" }).click();
> 65  |       await expect(box.locator("[data-active-layout]")).toHaveText("Custom");
      |                                                         ^ Error: expect(locator).toHaveText(expected) failed
  66  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => /\/trace-layouts\/analysis-layout-/.test(w.path))).toBe(true);
  67  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && /^analysis-layout-/.test(String((w.body as { active_trace_layout_id?: string }).active_trace_layout_id)))).toBe(true);
  68  |       await page.reload();
  69  |       await page.getByRole("button", { name: "Customize" }).click();
  70  |       await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Custom");
  71  |       await page.getByRole("region", { name: "Chart layouts" }).getByRole("button", { name: "Delete" }).click();
  72  |       await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Default");
  73  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => (w as { method?: string }).method === "DELETE")).toBe(true);
  74  |     });
  75  | 
  76  |     test("a session without an analysis says so instead of opening something else", async ({ page }) => {
  77  |       await page.goto(hash(`/d/${DRIVER}/s/106/race`));
  78  |       await expect(page.getByRole("heading", { name: "Not analyzed" })).toBeVisible();
  79  |     });
  80  |   });
  81  | }
  82  | 
  83  | // Web rounds 2 to 4, Investigation (web-round-2-plan.md): the acceptance lines at 1440, 1024 and 390, on the synthetic race served
  84  | // in place of session 103's analysis (the round-2 race, round 3's per-lap context and no-tire stop, round 4's Vegas-shaped stop,
  85  | // one absent fuel reading and a fit under half its run's green laps).
  86  | const ANALYSIS = buildRound4Analysis("an-103");
  87  | // RI_SHOTS names another release record's folder (relative to web/); the default is round 4's S2.
  88  | const SHOTS = path.resolve(process.cwd(), process.env.RI_SHOTS ?? path.join("target", "round4", "S2"));
  89  | 
  90  | type RawLap = { lap: number; lap_time_s: number | null; run_number: number | null; plot_pace: boolean };
  91  | /** The run line read straight off the fixture's lap rows: the run's last green lap time less its first, over the laps between. */
  92  | function expectedRunLine(run: number, name: string, burn: string): string {
  93  |   const laps = (ANALYSIS.investigation as unknown as { race_investigation: { laps: RawLap[] } }).race_investigation.laps
  94  |     .filter((lap) => lap.run_number === run && lap.plot_pace && (lap.lap_time_s ?? 0) > 0).sort((a, b) => a.lap - b.lap);
  95  |   const [first, last] = [laps[0]!, laps[laps.length - 1]!];
  96  |   const rate = ((last.lap_time_s as number) - (first.lap_time_s as number)) / (last.lap - first.lap);
  97  |   const text = Math.abs(rate).toFixed(2);
  98  |   const sign = Number(text) === 0 ? "" : rate > 0 ? "+" : "\u2212";
  99  |   return `${name} · L${first.lap}–${last.lap} · fall-off ${sign}${text} s/lap · ${burn} gal/lap`;
  100 | }
  101 | 
  102 | async function serveRound2(page: Page): Promise<void> {
  103 |   await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
  104 |     const url = new URL(route.request().url());
  105 |     const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
  106 |     await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  107 |   });
  108 | }
  109 | 
  110 | const open = (page: Page, view: string, query = "") => page.goto(hash(`/d/${DRIVER}/s/103/${view}${query}`));
  111 | 
  112 | for (const device of ROUND2_DEVICES) {
  113 |   appTest(device, () => {
  114 |     const touch = device.touch;
  115 |     const width = device.viewport.width;
  116 |     test.beforeEach(async ({ page }) => serveRound2(page));
  117 | 
  118 |     /** Hover on a pointer device, tap on touch: both must show the reason. */
  119 |     async function point(page: Page, selector: string): Promise<void> {
  120 |       const target = page.locator(selector).first();
  121 |       await target.scrollIntoViewIfNeeded();
  122 |       if (touch) await target.tap({ force: true });
  123 |       else await target.hover({ force: true });
  124 |     }
  125 | 
  126 |     test("Race: why a lap is absent from the fit reads from its hover, tap or focus; no EXCL anywhere; caution laps are dots", async ({ page }) => {
  127 |       await open(page, "race");
  128 |       await expect(page.locator(".ri-dot[data-lap='10']")).toHaveCount(1);
  129 |       await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
  130 |       // #40: the hollow/filled rule is not a legend item; #59a: no "Pace fit" header.
  131 |       await expect(page.locator("[data-legend=screened]")).toHaveCount(0);
  132 |       await expect(page.locator("[data-pace-fit]")).toHaveCount(0);
  133 |       await expect(page.locator(".ri-chart-head")).not.toContainText(/pace fit/i);
  134 |       await point(page, ".ri-dot[data-lap='10']");
  135 |       await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit · close traffic");
  136 |       await page.locator(".ri-dot[data-lap='11']").focus();
  137 |       await expect(page.locator("[data-race-readout]")).toContainText("Lap 11");
  138 |       await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit");
  139 |       // Caution laps: actual lap times as subdued dots in the band; the dash row is gone.
  140 |       await expect(page.locator("[data-caution-dot]")).toHaveCount(3);
  141 |       await expect(page.locator(".ri-caution")).toHaveCount(0);
  142 |       await point(page, "[data-caution-dot='41']");
  143 |       await expect(page.locator("[data-race-readout]")).toContainText("Lap 41 · 38.200 s");
  144 |       // One comparison selector: the toolbar Compare dropdown is gone.
  145 |       await expect(page.getByRole("combobox", { name: "Compare" })).toHaveCount(0);
  146 |       // The Lap time title clears the pit and incident tags at every width (C3: they overprinted it at 390).
  147 |       const clashes = await page.evaluate(() => {
  148 |         const title = [...document.querySelectorAll(".ri-plot-svg .ri-axis-title")].find((el) => el.textContent === "Lap time · s")!.getBoundingClientRect();
  149 |         return [...document.querySelectorAll(".ri-pit-tag, .ri-inc-tag")].filter((tag) => {
  150 |           const r = tag.getBoundingClientRect();
  151 |           return r.left < title.right && title.left < r.right && r.top < title.bottom && title.top < r.bottom;
  152 |         }).length;
  153 |       });
  154 |       expect(clashes, "tags over the Lap time title").toBe(0);
  155 |       await expectNoHorizontalScroll(page);
  156 |     });
  157 | 
  158 |     test("Race (#43 #45): no comparison machinery; the chart takes the height; one line per run under it, in the run's colour", async ({ page }) => {
  159 |       await open(page, "race", `?comparison=${ROUND2_COMPARISONS.window}`);
  160 |       await expect(page.locator(".ri-dot").first()).toBeVisible();
  161 |       await expect(page.locator(".ri-comparisons, [data-comparison], [data-bracket], [data-selected-comparison], .ri-race-bottom")).toHaveCount(0);
  162 |       await expect(page.locator(".ri-race-view > *")).toHaveCount(1);
  163 |       if (width >= 1000) {
  164 |         const view = (await page.locator(".ri-race-view").boundingBox())!;
  165 |         const card = (await page.locator(".ri-race-view > .ri-card").boundingBox())!;
```