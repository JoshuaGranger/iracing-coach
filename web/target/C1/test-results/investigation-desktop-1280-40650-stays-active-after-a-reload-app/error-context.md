# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: app\investigation.spec.ts >> desktop 1280x720 >> saved chart layouts persist: New is written to the driver's layouts and stays active after a reload
- Location: e2e\app\investigation.spec.ts:58:5

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
  6   | import { buildRound2Analysis, ROUND2_COMPARISONS } from "../../src/investigation/fixtures/round2";
  7   | import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState } from "./helpers";
  8   | import { ROUND2_DEVICES } from "./round2-shared";
  9   | 
  10  | // The shell around the workspace (W8's own view checks follow, on the round-2 fixture).
  11  | for (const device of DEVICES) {
  12  |   appTest(device, () => {
  13  |     test("the shell resolves the session to its analysis and mounts the workspace on the route's view", async ({ page }) => {
  14  |       const requests: string[] = [];
  15  |       page.on("request", (r) => requests.push(new URL(r.url()).pathname));
  16  |       await page.goto(hash(`/d/${DRIVER}/s/103/race`));
  17  |       await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
  18  |       expect(requests).toContain("/api/v1/sessions/103");
  19  |       await expect.poll(() => requests.some((p) => p === "/api/v1/analyses/an-103/race")).toBe(true);
  20  |     });
  21  | 
  22  |     test("switching views follows the route, is remembered on this device at once and in the prefs on leaving", async ({ page, request }) => {
  23  |       await page.goto(hash(`/d/${DRIVER}/s/103/race`));
  24  |       await page.getByRole("tab", { name: "Traces" }).click();
  25  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
  26  |       const stored = await page.evaluate((c) => JSON.parse(localStorage.getItem(`coach.last-investigation.${c}`) ?? "null"), DRIVER);
  27  |       expect(stored).toMatchObject({ session_id: 103, analysis_id: "an-103", view: "traces" });
  28  |       // Silent: no prefs write (and so no outbox bar over the workspace) while the investigation is open.
  29  |       await expect(page.getByTestId("outbox")).toHaveCount(0);
  30  |       expect((await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && "last_investigation" in (w.body as object))).toBe(false);
  31  |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  32  |       await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
  33  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { last_investigation?: { view: string; analysis_id: string } }).last_investigation?.view === "traces")).toBe(true);
  34  |       await expect(page.getByText("Resume")).toHaveCount(0);
  35  |       // The tile reopens the session on the tab it was left on.
  36  |       await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
  37  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
  38  |     });
  39  | 
  40  |     test("the P Q R selector moves between the phases of the event on the same view", async ({ page }) => {
  41  |       await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  42  |       await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
  43  |       const picker = page.getByRole("group", { name: "Session", exact: true });
  44  |       await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
  45  |       await expect(picker.getByRole("button", { name: "Race" })).toHaveAttribute("aria-pressed", "true");
  46  |       await picker.getByRole("button", { name: "Qualifying" }).click();
  47  |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/traces$`));
  48  |     });
  49  | 
  50  |     test("a phase that cannot open is disabled and names its state (S2)", async ({ page }) => {
  51  |       await page.goto(hash(`/d/${DRIVER}/s/104/race`));
  52  |       const picker = page.getByRole("group", { name: "Session", exact: true });
  53  |       await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
  54  |       await expect(picker.getByRole("button", { name: "Race · Not analyzed" })).toBeDisabled();
  55  |       await expect(picker.getByRole("button", { name: "Qualifying" })).toBeEnabled();
  56  |     });
  57  | 
  58  |     test("saved chart layouts persist: New is written to the driver's layouts and stays active after a reload", async ({ page, request }) => {
  59  |       await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
  60  |       await page.getByRole("button", { name: "Customize" }).click();
  61  |       const box = page.getByRole("region", { name: "Chart layouts" });
  62  |       await expect(box.locator("[data-active-layout]")).toHaveText("Default");
  63  |       await box.getByRole("button", { name: "New" }).click();
> 64  |       await expect(box.locator("[data-active-layout]")).toHaveText("Custom");
      |                                                         ^ Error: expect(locator).toHaveText(expected) failed
  65  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => /\/trace-layouts\/analysis-layout-/.test(w.path))).toBe(true);
  66  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && /^analysis-layout-/.test(String((w.body as { active_trace_layout_id?: string }).active_trace_layout_id)))).toBe(true);
  67  |       await page.reload();
  68  |       await page.getByRole("button", { name: "Customize" }).click();
  69  |       await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Custom");
  70  |       await page.getByRole("region", { name: "Chart layouts" }).getByRole("button", { name: "Delete" }).click();
  71  |       await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Default");
  72  |       await expect.poll(async () => (await mockState(request)).writes.some((w) => (w as { method?: string }).method === "DELETE")).toBe(true);
  73  |     });
  74  | 
  75  |     test("a session without an analysis says so instead of opening something else", async ({ page }) => {
  76  |       await page.goto(hash(`/d/${DRIVER}/s/106/race`));
  77  |       await expect(page.getByRole("heading", { name: "Not analyzed" })).toBeVisible();
  78  |     });
  79  |   });
  80  | }
  81  | 
  82  | // Web round 2, Investigation (web-round-2-plan.md): the acceptance lines at 1440, 1024 and 390, on the round-2 synthetic race served
  83  | // in place of session 103's analysis.
  84  | const ANALYSIS = buildRound2Analysis("an-103");
  85  | // RI_SHOTS names another release record's folder (relative to web/); the default is A3's.
  86  | const SHOTS = path.resolve(process.cwd(), process.env.RI_SHOTS ?? path.join("target", "round2", "A3"));
  87  | 
  88  | async function serveRound2(page: Page): Promise<void> {
  89  |   await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
  90  |     const url = new URL(route.request().url());
  91  |     const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
  92  |     await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  93  |   });
  94  | }
  95  | 
  96  | const open = (page: Page, view: string, query = "") => page.goto(hash(`/d/${DRIVER}/s/103/${view}${query}`));
  97  | 
  98  | for (const device of ROUND2_DEVICES) {
  99  |   appTest(device, () => {
  100 |     const touch = device.touch;
  101 |     const width = device.viewport.width;
  102 |     test.beforeEach(async ({ page }) => serveRound2(page));
  103 | 
  104 |     /** Hover on a pointer device, tap on touch: both must show the reason. */
  105 |     async function point(page: Page, selector: string): Promise<void> {
  106 |       const target = page.locator(selector).first();
  107 |       await target.scrollIntoViewIfNeeded();
  108 |       if (touch) await target.tap({ force: true });
  109 |       else await target.hover({ force: true });
  110 |     }
  111 | 
  112 |     test("Race: why a lap is absent from the fit reads from its hover, tap or focus; no EXCL anywhere; caution laps are dots", async ({ page }) => {
  113 |       await open(page, "race");
  114 |       await expect(page.locator(".ri-dot[data-lap='10']")).toHaveCount(1);
  115 |       await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
  116 |       await expect(page.locator("[data-legend=screened]")).toHaveText("Not used in pace fit");
  117 |       await point(page, ".ri-dot[data-lap='10']");
  118 |       await expect(page.locator("[data-race-readout]")).toContainText("Not used in Run 1 pace fit · close traffic");
  119 |       await page.locator(".ri-dot[data-lap='11']").focus();
  120 |       await expect(page.locator("[data-race-readout]")).toContainText("Lap 11");
  121 |       await expect(page.locator("[data-race-readout]")).toContainText("Not used in Run 1 pace fit");
  122 |       // Caution laps: actual lap times as subdued dots in the band; the dash row is gone.
  123 |       await expect(page.locator("[data-caution-dot]")).toHaveCount(3);
  124 |       await expect(page.locator(".ri-caution")).toHaveCount(0);
  125 |       await point(page, "[data-caution-dot='41']");
  126 |       await expect(page.locator("[data-race-readout]")).toContainText("Lap 41 · 38.200 s");
  127 |       // One comparison selector: the toolbar Compare dropdown is gone.
  128 |       await expect(page.getByRole("combobox", { name: "Compare" })).toHaveCount(0);
  129 |       // The Lap time title clears the pit and incident tags at every width (C3: they overprinted it at 390).
  130 |       const clashes = await page.evaluate(() => {
  131 |         const title = [...document.querySelectorAll(".ri-plot-svg .ri-axis-title")].find((el) => el.textContent === "Lap time · s")!.getBoundingClientRect();
  132 |         return [...document.querySelectorAll(".ri-pit-tag, .ri-inc-tag")].filter((tag) => {
  133 |           const r = tag.getBoundingClientRect();
  134 |           return r.left < title.right && title.left < r.right && r.top < title.bottom && title.top < r.bottom;
  135 |         }).length;
  136 |       });
  137 |       expect(clashes, "tags over the Lap time title").toBe(0);
  138 |       await expectNoHorizontalScroll(page);
  139 |     });
  140 | 
  141 |     test("selecting a comparison keeps the lap readout, the scroll and the view; nothing remounts", async ({ page }) => {
  142 |       await open(page, "race");
  143 |       const readout = page.locator("[data-race-readout]");
  144 |       await page.locator(".ri-dot[data-lap='11']").focus();
  145 |       await expect(readout).toContainText("Lap 11");
  146 |       const row = page.locator(`[data-comparison=${ROUND2_COMPARISONS.window}]`);
  147 |       await row.scrollIntoViewIfNeeded();
  148 |       await page.evaluate(() => {
  149 |         const w = window as unknown as { __c3: (Element | null)[] };
  150 |         w.__c3 = [".ri-workspace", ".ri-plot-svg", ".ri-comparisons"].map((s) => document.querySelector(s));
  151 |       });
  152 |       const scrollY = await page.evaluate(() => window.scrollY);
  153 |       const top = (await page.locator(".ri-workspace").boundingBox())!.y;
  154 |       await row.focus();
  155 |       await page.keyboard.press("Enter");
  156 |       await expect(row).toHaveAttribute("aria-pressed", "true");
  157 |       await expect(page).toHaveURL(new RegExp(`/race\\?comparison=${ROUND2_COMPARISONS.window}`));
  158 |       await expect(page.locator("[data-bracket=later]")).toHaveAttribute("data-laps", "77-81");
  159 |       expect(await page.evaluate(() => {
  160 |         const w = window as unknown as { __c3: (Element | null)[] };
  161 |         return [".ri-workspace", ".ri-plot-svg", ".ri-comparisons"].every((s, i) => w.__c3[i] === document.querySelector(s));
  162 |       }), "same nodes").toBe(true);
  163 |       await expect(readout, "the readout keeps its lap").toContainText("Lap 11");
  164 |       await expect(page.getByRole("tab", { name: "Race", exact: true })).toHaveAttribute("aria-selected", "true");
```