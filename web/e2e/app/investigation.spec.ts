import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { raceProgressColour } from "../../src/core/palette";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { ROUND2_COMPARISONS } from "../../src/investigation/fixtures/round2";
import { ROUND4_NO_FUEL_LAP, ROUND4_SERVICE_LAP } from "../../src/investigation/fixtures/round4";
import { OFF_TRACK_LAP, OPEN_AIR_LAP, RESTART_LAP, TRAFFIC_AND_OFF_TRACK_LAP, TRAFFIC_LAP, TRAFFIC_ONLY_LAPS, buildTrafficAnalysis } from "../../src/investigation/fixtures/traffic";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState, resetMock } from "./helpers";
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

// Web rounds 2 to 4, Investigation (web-round-2-plan.md): the acceptance lines at 1440, 1024 and 390, on the synthetic race served
// in place of session 103's analysis (the round-2 race, round 3's per-lap context and no-tire stop, round 4's Vegas-shaped stop,
// one absent fuel reading and a fit under half its run's green laps, and #67's traffic, traffic-and-other and other screens).
const ANALYSIS = buildTrafficAnalysis("an-103");
// RI_SHOTS names another release record's folder (relative to web/); the default is round 6's B90 (#90–#93).
const SHOTS = path.resolve(process.cwd(), process.env.RI_SHOTS ?? path.join("target", "round6", "b90"));
// Backlog #96/#97's screenshots: B96_SHOTS, else the round's folder.
const B96_SHOTS = path.resolve(process.cwd(), process.env.B96_SHOTS ?? path.join("target", "round6", "b96"));
// The traffic marker's own screenshots (#67, #92): RI_T67_SHOTS, else the same folder.
const T67_SHOTS = path.resolve(process.cwd(), process.env.RI_T67_SHOTS ?? path.join("target", "round6", "b90"));

type Box = { x: number; y: number; width: number; height: number };

/** The smallest box around the chart marks of laps `from` to `to` (the `selector` marks), padded by `pad` px. */
async function marksBox(page: Page, selector: string, from: number, to: number, pad: number): Promise<Box> {
  return page.evaluate(([sel, lo, hi, p]) => {
    const rects = [...document.querySelectorAll(sel as string)].filter((el) => {
      const lap = Number(el.getAttribute("data-lap"));
      return lap >= (lo as number) && lap <= (hi as number);
    }).map((el) => el.getBoundingClientRect());
    const x1 = Math.min(...rects.map((r) => r.left)) - (p as number);
    const y1 = Math.min(...rects.map((r) => r.top)) - (p as number);
    const x2 = Math.max(...rects.map((r) => r.right)) + (p as number);
    const y2 = Math.max(...rects.map((r) => r.bottom)) + (p as number);
    return { x: Math.max(0, x1), y: Math.max(0, y1), width: x2 - Math.max(0, x1), height: y2 - Math.max(0, y1) };
  }, [selector, from, to, pad] as const);
}

type RawLap = { lap: number; lap_time_s: number | null; run_number: number | null; plot_pace: boolean };
type Trend = { slope_s_per_lap: number; intercept_s: number; first_lap: number; last_lap: number; laps_used: number };
/** The server's observed trend of `run` as the fixture's runs view carries it (#90). */
function trendOf(run: number): Trend {
  return (ANALYSIS.runs.runs as unknown as { run_number: number; observed_trend: Trend }[]).find((item) => item.run_number === run)!.observed_trend;
}
/** `+0.06 s/lap`: a slope as every trend label says it. */
function perLap(slope: number): string {
  const text = Math.abs(slope).toFixed(2);
  const sign = Number(text) === 0 ? "" : slope > 0 ? "+" : "−";
  return `${sign}${text} s/lap`;
}
/** The run line: the run's green span off the fixture's lap rows, the fall-off the server's trend slope, the burn. */
function expectedRunLine(run: number, name: string, burn: string): string {
  const laps = (ANALYSIS.investigation as unknown as { race_investigation: { laps: RawLap[] } }).race_investigation.laps
    .filter((lap) => lap.run_number === run && lap.plot_pace && (lap.lap_time_s ?? 0) > 0).sort((a, b) => a.lap - b.lap);
  const [first, last] = [laps[0]!, laps[laps.length - 1]!];
  return `${name} · L${first.lap}–${last.lap} · fall-off ${perLap(trendOf(run).slope_s_per_lap)} · ${burn} gal/lap`;
}

/** Text boxes inside each `root` element that overlap another or the sparkline (leaf elements with text, by their text's rects). */
async function overlappingText(page: Page, root: string): Promise<string[]> {
  return page.locator(root).evaluateAll((cards) => cards.flatMap((card) => {
    const leaves = [...card.querySelectorAll("*")].filter((el) => el.children.length === 0 && (el.textContent ?? "").trim() !== "" && !(el instanceof SVGElement));
    const boxes = leaves.map((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return { text: (el.textContent ?? "").trim(), r: range.getBoundingClientRect() };
    }).filter((box) => box.r.width > 0 && box.r.height > 0);
    const hit = (a: DOMRect, b: DOMRect): boolean => a.left < b.right - 0.5 && b.left < a.right - 0.5 && a.top < b.bottom - 0.5 && b.top < a.bottom - 0.5;
    const out: string[] = [];
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) if (hit(boxes[i]!.r, boxes[j]!.r)) out.push(`${boxes[i]!.text} / ${boxes[j]!.text}`);
    const spark = card.querySelector(".ri-spark")?.getBoundingClientRect();
    if (spark) for (const box of boxes) if (hit(box.r, spark)) out.push(`${box.text} / sparkline`);
    return out;
  }));
}

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
      // #40: the hollow/filled rule is not a legend item; #59a: no "Pace fit" header.
      await expect(page.locator("[data-legend=screened]")).toHaveCount(0);
      await expect(page.locator("[data-pace-fit]")).toHaveCount(0);
      await expect(page.locator(".ri-chart-head")).not.toContainText(/pace fit/i);
      // A hollow dot is a lap screened for something other than traffic alone; its hover names the fit and the backend's reasons (#67).
      await point(page, `.ri-dot[data-lap='${RESTART_LAP}']`);
      await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit · restart");
      await page.locator(`.ri-dot[data-lap='${TRAFFIC_AND_OFF_TRACK_LAP}']`).focus();
      await expect(page.locator("[data-race-readout]")).toContainText(`Lap ${TRAFFIC_AND_OFF_TRACK_LAP}`);
      await expect(page.locator("[data-race-readout]")).toContainText("Not in pace fit · off track, close traffic");
      // A traffic lap reads its traffic instead (#67, the Race traffic test below).
      await page.locator(".ri-dot[data-lap='11']").focus();
      await expect(page.locator("[data-race-readout]")).toContainText("Lap 11");
      await expect(page.locator("[data-race-readout]")).toContainText("traffic 62 % of lap");
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
      // #90: the fall-off is the server's observed-trend slope, the line's label; the green burn per lap; no screened slope.
      await expect(lines).toHaveText([expectedRunLine(1, "Run 1", "0.42"), expectedRunLine(2, "Run 2", "0.42"), expectedRunLine(3, "Run 3 · same tires", "0.43")]);
      await expect(lines.first()).not.toContainText("+0.033");
      // Its hover (tap on touch) labels it the observed trend, with its laps.
      const t1 = trendOf(1);
      await expect(lines.first().locator("[data-part=falloff]")).toHaveAttribute("title", `observed trend · L${t1.first_lap}–${t1.last_lap} · ${t1.laps_used} laps, traffic included`);
      await point(page, "[data-run-line='1']");
      await expect(page.locator("[data-race-readout]")).toHaveText(`Run 1 · fall-off ${perLap(t1.slope_s_per_lap)} · observed trend · L${t1.first_lap}–${t1.last_lap} · ${t1.laps_used} laps, traffic included · 0.42 gal/lap`);
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

    test("Race (#40 #42 #90): legend is the runs only; dots plus each run's straight observed trend over its laps, slope at its end; no lap-to-lap line; tire-set colour", async ({ page }) => {
      await open(page, "race");
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      const legend = page.locator(".ri-progress .ri-legend");
      await expect(legend.locator(".ri-run-key")).toHaveText(["Run 1", "Run 2", "Run 3 · same tires"]);
      await expect(legend).not.toContainText(/caution|pit|incident|fastest|position|lap|fit/i);
      // #90: no polyline through the laps and no screened fit; each run's observed trend is one straight line over its counted laps.
      await expect(page.locator("polyline, [data-fit-run]")).toHaveCount(0);
      await expect(page.locator("[data-trend-run]")).toHaveCount(3);
      for (const run of [1, 2, 3]) {
        const t = trendOf(run);
        const group = page.locator(`[data-trend-run='${run}']`);
        await expect(group).toHaveAttribute("data-trend-span", `${t.first_lap}-${t.last_lap}`);
        await expect(group.locator("line.ri-trend")).toHaveCount(1);
        await expect(group.locator("[data-trend-label]")).toHaveText(perLap(t.slope_s_per_lap));
        // The line starts and ends on its first and last counted laps' columns, never beyond.
        const [x1, x2] = await group.locator("line.ri-trend").evaluate((el) => [Number(el.getAttribute("x1")), Number(el.getAttribute("x2"))]);
        const lapX = async (lap: number) => page.locator(`.ri-dot[data-lap='${lap}']`).evaluate((el) => Number(el.getAttribute("cx")));
        expect(Math.abs(x1! - (await lapX(t.first_lap)))).toBeLessThanOrEqual(0.02);
        expect(Math.abs(x2! - (await lapX(t.last_lap)))).toBeLessThanOrEqual(0.02);
      }
      // The labels never print over one another.
      const labels = await page.locator("[data-trend-label]").evaluateAll((els) => els.map((el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }));
      for (let i = 0; i < labels.length; i++) for (let j = i + 1; j < labels.length; j++) {
        const [a, b] = [labels[i]!, labels[j]!];
        expect(a[0]! < b[2]! && b[0]! < a[2]! && a[1]! < b[3]! && b[1]! < a[3]!, `labels ${i} and ${j} overlap`).toBe(false);
      }
      await expect(page.locator(".ri-race-view")).not.toContainText(/insufficient|coverage|no fit/i);
      // Run 3 shares run 2's tires: run 2's hue, lighter; run 1 and run 2 have their own.
      await expect(page.locator(".ri-dot[data-lap='12']")).toHaveAttribute("stroke", "var(--run-1)");
      await expect(page.locator(".ri-dot[data-lap='40']")).toHaveAttribute("stroke", "var(--run-2)");
      await expect(page.locator(".ri-dot[data-lap='70']")).toHaveAttribute("stroke", /^color-mix\(in srgb, var\(--run-2\) \d+%, white\)$/);
      const [run2, run3] = await page.evaluate(() => ["40", "70"].map((lap) => getComputedStyle(document.querySelector(`.ri-dot[data-lap='${lap}']`)!).stroke));
      expect(run3, "lighter shade, not the same colour").not.toBe(run2);
      // Hollow dots stay for laps screened for anything but traffic alone; the fastest lap carries the magenta ring and says so on hover.
      await expect(page.locator(`.ri-dot.excluded[data-lap='${RESTART_LAP}']`)).toHaveAttribute("fill", "var(--ri-card)");
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

    test("Race (#67, #92): a lap screened for close traffic alone is the solid dot at 55 % with a thin outline in its run colour; open air the solid dot, any other screen the hollow dot", async ({ page }) => {
      const requests: string[] = [];
      await open(page, "race");
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      const dot = (lap: number) => page.locator(`.ri-dot[data-lap='${lap}']`);
      const readout = page.locator("[data-race-readout]");
      // Traffic alone: one solid dot in the run colour, fill at 55 %, a thin full-colour outline; no ring, no chevron.
      for (const lap of TRAFFIC_ONLY_LAPS) {
        await expect(dot(lap)).toHaveAttribute("data-mark", "traffic");
        const stroke = (await dot(lap).getAttribute("stroke"))!;
        await expect(dot(lap)).toHaveAttribute("fill", stroke);
        await expect(dot(lap)).toHaveAttribute("fill-opacity", "0.55");
      }
      await expect(page.locator(".ri-dot[data-mark=traffic]")).toHaveCount(TRAFFIC_ONLY_LAPS.length);
      await expect(page.locator("[data-traffic-ring], [data-traffic-chevron], .ri-dot-ring, .ri-dot-chevron")).toHaveCount(0);
      await expect(dot(10)).toHaveAttribute("stroke", "var(--run-1)");
      await expect(dot(TRAFFIC_LAP)).toHaveAttribute("stroke", "var(--run-1)");
      await expect(dot(79)).toHaveAttribute("stroke", /^color-mix\(in srgb, var\(--run-2\) \d+%, white\)$/);
      // Open air: the plain filled dot at full fill.
      await expect(dot(OPEN_AIR_LAP)).toHaveAttribute("data-mark", "open");
      await expect(dot(OPEN_AIR_LAP)).toHaveAttribute("fill", "var(--run-1)");
      expect(await dot(OPEN_AIR_LAP).getAttribute("fill-opacity")).toBeNull();
      // Screened for anything else, with traffic or without: the hollow dot.
      const hollow = [1, TRAFFIC_AND_OFF_TRACK_LAP, RESTART_LAP, OFF_TRACK_LAP, 57];
      for (const lap of hollow) {
        await expect(dot(lap)).toHaveAttribute("data-mark", "screened");
        await expect(dot(lap)).toHaveAttribute("fill", "var(--ri-card)");
      }
      await expect(page.locator(".ri-dot[data-mark=screened]")).toHaveCount(hollow.length);
      // One size for every mark: the traffic dot and the hollow dot are the plain dot's size; computed, the traffic fill is 55 %.
      const radius = (lap: number) => dot(lap).evaluate((el) => Number(el.getAttribute("r")));
      const plain = await radius(OPEN_AIR_LAP);
      expect(await radius(RESTART_LAP), "the hollow dot is the plain dot size").toBe(plain);
      expect(await radius(TRAFFIC_LAP), "the traffic dot is the plain dot size").toBe(plain);
      expect(Number(await dot(TRAFFIC_LAP).evaluate((el) => getComputedStyle(el).fillOpacity))).toBeCloseTo(0.55, 2);
      // The legend lists the runs only; no dot kind is a legend item.
      await expect(page.locator(".ri-progress .ri-legend .ri-run-key")).toHaveText(["Run 1", "Run 2", "Run 3 · same tires"]);
      await expect(page.locator(".ri-progress .ri-legend")).not.toContainText(/traffic|open|screen/i);
      // Hover, tap or focus: the lap, its time and the share of the lap in traffic; no side (the backend names none), no fit sentence.
      await point(page, `.ri-dot[data-lap='${TRAFFIC_LAP}']`);
      await expect(readout).toHaveText(/^Lap 20 · \d+\.\d{3} s · P4 · Run 1 · 20\.0 green · traffic 75 % of lap$/);
      await expect(readout).not.toContainText(/ahead|behind|pace fit/i);
      await expect(dot(TRAFFIC_LAP)).toHaveAttribute("aria-label", /traffic 75 % of lap$/);
      await point(page, ".ri-dot[data-lap='10']");
      await expect(readout).toContainText("traffic 62 % of lap");
      // A lap screened for traffic and something else reads its reasons as before.
      await point(page, `.ri-dot[data-lap='${TRAFFIC_AND_OFF_TRACK_LAP}']`);
      await expect(readout).toContainText("Not in pace fit · off track, close traffic");
      await expect(readout).not.toContainText("traffic 40 %");
      // The pointer finds the lap at the middle of its dot.
      const centre = (await dot(TRAFFIC_LAP).boundingBox())!;
      const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x!, y!)?.getAttribute("data-lap") ?? null, [centre.x + centre.width / 2, centre.y + centre.height / 2]);
      expect(hit, "the element under the middle of the traffic dot").toBe(String(TRAFFIC_LAP));
      // The trend lines and the run lines do not change with the mark (#90).
      await expect(page.locator("[data-trend-run='1']")).toHaveAttribute("data-trend-span", `${trendOf(1).first_lap}-${trendOf(1).last_lap}`);
      await expect(page.locator("[data-run-line='1'] [data-part=falloff]")).toHaveText(/^fall-off [+−]?\d\.\d\d s\/lap$/);
      // The fastest lap ring clears the mark it surrounds, whichever mark that is.
      const best = await page.evaluate(() => {
        const ringEl = document.querySelector("[data-fastest]")!;
        const lap = ringEl.getAttribute("data-fastest");
        const mark = document.querySelector(`.ri-dot[data-lap='${lap}']`)!;
        return { lap: Number(lap), r: Number(ringEl.getAttribute("r")), outer: Number(mark.getAttribute("r")), kind: mark.getAttribute("data-mark") };
      });
      expect(best.r - best.outer, `fastest lap ${best.lap} (${best.kind}): ring over the mark`).toBeGreaterThanOrEqual(2.5);
      // Pointer movement over the chart makes no request.
      page.on("request", (request) => requests.push(request.url()));
      if (!touch) {
        const box = (await page.locator(".ri-plot-svg").boundingBox())!;
        for (let i = 0; i <= 40; i++) await page.mouse.move(box.x + (box.width * i) / 40, box.y + box.height * 0.3);
      }
      expect(requests, "requests under pointer movement").toEqual([]);
      await expectNoHorizontalScroll(page);
    });

    test("Runs (#67, #92): the chart dots and the run card sparklines draw the same traffic dot and hollow dot", async ({ page }) => {
      await open(page, "runs");
      await expect(page.locator(".ri-runs .ri-dot").first()).toBeVisible();
      const dot = (lap: number) => page.locator(`.ri-runs .ri-dot[data-lap='${lap}']`);
      for (const lap of TRAFFIC_ONLY_LAPS) {
        await expect(dot(lap)).toHaveAttribute("data-mark", "traffic");
        await expect(dot(lap)).toHaveAttribute("fill-opacity", "0.55");
        await expect(dot(lap)).toHaveAttribute("fill", (await dot(lap).getAttribute("stroke"))!);
      }
      await expect(page.locator(".ri-runs [data-traffic-ring], .ri-runs .ri-dot-ring, .ri-runs .ri-dot-chevron")).toHaveCount(0);
      await expect(dot(TRAFFIC_LAP)).toHaveAttribute("data-run", "1");
      await expect(dot(TRAFFIC_LAP)).toHaveAttribute("stroke", "var(--run-1)");
      await expect(dot(79)).toHaveAttribute("data-run", "3");
      await expect(dot(OPEN_AIR_LAP)).toHaveAttribute("data-mark", "open");
      await expect(dot(OPEN_AIR_LAP)).toHaveAttribute("fill", "var(--run-1)");
      const hollow = [1, TRAFFIC_AND_OFF_TRACK_LAP, RESTART_LAP, OFF_TRACK_LAP, 57];
      for (const lap of hollow) {
        await expect(dot(lap)).toHaveAttribute("data-mark", "screened");
        await expect(dot(lap)).toHaveAttribute("fill", "var(--ri-card)");
      }
      // The same readout as Race.
      await point(page, `.ri-runs .ri-dot[data-lap='${TRAFFIC_LAP}']`);
      await expect(page.locator("[data-runs-readout]")).toHaveText(/^Lap 20 · \d+\.\d{3} s · P4 · Run 1 · 20\.0 green · traffic 75 % of lap$/);
      await point(page, `.ri-runs .ri-dot[data-lap='${OFF_TRACK_LAP}']`);
      await expect(page.locator("[data-runs-readout]")).toContainText("Not in pace fit · off track");
      // The card sparklines: the fainter dot for traffic, a hollow dot for any other screen, a plain dot for open air; no rings.
      await expect(page.locator("[data-spark-mark=traffic]")).toHaveCount(TRAFFIC_ONLY_LAPS.length);
      await expect(page.locator(".ri-spark-dot.traffic")).toHaveCount(TRAFFIC_ONLY_LAPS.length);
      await expect(page.locator(".ri-spark-ring, [data-spark-traffic]")).toHaveCount(0);
      await expect(page.locator(".ri-spark-dot.hollow")).toHaveCount(hollow.length);
      await expectNoHorizontalScroll(page);
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

    test("Race (#46, #60–#63): layers are names only, evidence in the readout; no traffic or slip; the fuel line steps at a stop", async ({ page }) => {
      const requests: string[] = [];
      await open(page, "race");
      await expect(page.locator(".ri-dot").first()).toBeVisible();
      const group = page.getByRole("group", { name: "Context layers" });
      const toggles = group.getByRole("button");
      // Track usage has no series in this recording; traffic and slip are gone (#61, #62); a toggle is its name (#60).
      await expect(toggles).toHaveText(["Track temp", "Fuel"]);
      await expect(group).not.toContainText(/measured|derived|proxy/i);
      await expect(page.locator("[data-layer-toggle=traffic], [data-layer-toggle=slip], [data-layer-toggle=gaps]")).toHaveCount(0);
      for (const toggle of await toggles.all()) await expect(toggle).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator("[data-layer], [data-lap-column]")).toHaveCount(0);
      for (const key of ["temp", "fuel"]) await page.locator(`[data-layer-toggle=${key}]`).click();
      await expect(page.locator(".ri-traffic-tick, .ri-slip-ring, [data-traffic-lap], [data-slip-lap]")).toHaveCount(0);
      await expect(page.locator("[data-layer=temp]")).toContainText("Track °F");
      await expect(page.locator("[data-layer=temp] .ri-temp-line")).toHaveCount(1);
      // #63: one fuel line through every lap with a reading, the service lap included; the only break is the absent reading.
      const fuel = page.locator("[data-layer=fuel] .ri-fuel-line");
      await expect(fuel).toHaveCount(2);
      await expect(fuel.nth(0)).toHaveAttribute("data-fuel-from", "1");
      await expect(fuel.nth(0)).toHaveAttribute("data-fuel-to", String(ROUND4_NO_FUEL_LAP - 1));
      await expect(fuel.nth(1)).toHaveAttribute("data-fuel-from", String(ROUND4_NO_FUEL_LAP + 1));
      // A stop is a step up: a vertical segment at the lap boundary, labelled with the refilled level.
      const steps = async (index: number) => fuel.nth(index).evaluate((el) => [...(el.getAttribute("d") ?? "").matchAll(/L([\d.]+),([\d.]+)(?=L([\d.]+),([\d.]+))/g)].filter((m) => m[1] === m[3] && Number(m[4]) < Number(m[2])).length);
      expect(await steps(0), "refill steps in the first fuel line").toBe(1);
      expect(await steps(1), "refill steps in the second fuel line").toBe(1);
      await expect(page.locator("[data-fuel-label='29']")).toHaveText(/^\d+\.\d gal$/);
      await expect(page.locator("[data-fuel-label='57']")).toHaveText(/^\d+\.\d gal$/);
      // Never a second chart.
      await expect(page.locator(".ri-race-view .ri-plot-svg")).toHaveCount(1);
      // The evidence is a short tag in the readout; the absent reading reads `no reading`.
      const readout = page.locator("[data-race-readout]");
      await point(page, ".ri-dot[data-lap='12']");
      await expect(readout).toContainText(/Track \d+\.\d °F · measured/);
      await expect(readout).toContainText(/Fuel \d+\.\d gal · measured/);
      await expect(readout).not.toContainText(/derived|proxy|Traffic|Spin/);
      await point(page, `[data-lap-column='${ROUND4_NO_FUEL_LAP}']`);
      await expect(readout).toContainText(`Lap ${ROUND4_NO_FUEL_LAP}`);
      await expect(readout).toContainText("Fuel no reading");
      // A lap after the service lap reads the refilled tank.
      await point(page, `[data-lap-column='${ROUND4_SERVICE_LAP + 5}']`);
      await expect(readout).toContainText(/Fuel \d+\.\d gal · measured/);
      // Pointer movement over the chart with every layer on makes no request.
      page.on("request", (request) => requests.push(request.url()));
      if (!touch) {
        const box = (await page.locator(".ri-plot-svg").boundingBox())!;
        for (let i = 0; i <= 40; i++) await page.mouse.move(box.x + (box.width * i) / 40, box.y + box.height * 0.35);
      }
      expect(requests, "requests under pointer movement").toEqual([]);
      // Remembered on this device; a device that remembered traffic or slip keeps only what is left.
      await page.evaluate(() => localStorage.setItem("coach.race-layers", JSON.stringify(["traffic", "slip", "fuel"])));
      await page.reload();
      await expect(page.locator("[data-layer-toggle=fuel]")).toHaveAttribute("aria-pressed", "true");
      await expect(page.locator("[data-layer-toggle=temp]")).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator("[data-layer=fuel] .ri-fuel-line")).toHaveCount(2);
      await page.locator("[data-layer-toggle=fuel]").click();
      await expect(page.locator("[data-layer=fuel]")).toHaveCount(0);
      expect(await page.evaluate(() => localStorage.getItem("coach.race-layers"))).toBe("[]");
      await expectNoHorizontalScroll(page);
    });

    test("Traces (#66): no comparison strip and no earlier/later marks, with or without a comparison in the route", async ({ page }) => {
      for (const query of [`?comparison=${ROUND2_COMPARISONS.window}`, "", `?comparison=${ROUND2_COMPARISONS.none}`]) {
        await open(page, "traces", query);
        await expect(page.locator("[data-analysis-trace-path]").first()).toBeAttached();
        await expect(page.locator("[data-comparison-strip], .ri-cmp-strip, .ri-pbar, .ri-delta, .ri-grp-mark, [data-group]")).toHaveCount(0);
        await expect(page.locator(".ri-workspace")).not.toContainText(/earlier|later|→/i);
      }
      // The rail, the chart, the map and the cursor readouts stay.
      const surface = page.locator(".ri-chart-surface");
      await surface.scrollIntoViewIfNeeded();
      await expect(surface).toBeVisible();
      const box = (await surface.boundingBox())!;
      await page.mouse.move(box.x + box.width * 0.4, box.y + 40);
      await expect(page.locator(".ri-track-panel [data-field=speed]")).not.toHaveText(/^—?$/);
      await expect(page.locator(".ri-lap-rail")).toBeVisible();
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

    test("Traces: race-progress lap colour that no filter changes, plain lap numbers with screening in the hover, fuel figures only, endpoints in the rail", async ({ page }) => {
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
      // #58: no box on any lap number; a screened lap shows the chart's hollow dot in the flag column and its reason in the row's hover.
      await expect(lap(10)).toHaveAttribute("title", "Not used in Run 1 pace fit · close traffic");
      await expect(lap(10).locator(".ri-fl [data-screened]")).toHaveAttribute("title", "Not used in Run 1 pace fit · close traffic");
      await expect(lap(12).locator("[data-screened]")).toHaveCount(0);
      await expect(page.locator("[data-lap-chip]")).toHaveCount(0);
      await lap(12).click();
      await page.getByRole("button", { name: /Spotlight/ }).click();
      await page.getByRole("option", { name: /^L12 · / }).click();
      const boxed = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>(".ri-lap-row .ri-n")].filter((el) => {
        const style = getComputedStyle(el);
        const clear = (colour: string) => colour === "rgba(0, 0, 0, 0)" || colour === "transparent";
        return (parseFloat(style.borderTopWidth) > 0 && style.borderTopStyle !== "none") || !clear(style.backgroundColor) || style.outlineStyle !== "none" || style.boxShadow !== "none";
      }).map((el) => el.textContent));
      expect(boxed, "lap numbers with a box, ring or badge").toEqual([]);
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

    test("Runs (#47 #90): dots plus each run's observed trend, no polyline; each run card toggles its run, one stays, the chart rescales, the session remembers", async ({ page }) => {
      await open(page, "runs");
      await expect(page.locator("polyline, [data-fit-run]")).toHaveCount(0);
      await expect(page.locator(".ri-runs .ri-dot[data-run='1']").first()).toHaveAttribute("stroke", "var(--run-1)");
      await expect(page.locator(".ri-runs .ri-dot[data-run='2']").first()).toHaveAttribute("stroke", "var(--run-2)");
      await expect(page.locator(".ri-runs .ri-dot[data-run='3']").first()).toHaveAttribute("stroke", /^color-mix\(in srgb, var\(--run-2\)/);
      await expect(page.locator(".ri-runs [data-legend=runs] .ri-run-key")).toHaveText(["Run 1", "Run 2", "Run 3 · same tires"]);
      // #90: the server's observed trend per run, over its counted laps, labelled with its slope and laps; the card's Fit says the same.
      const label = (run: number) => `${perLap(trendOf(run).slope_s_per_lap)} · ${trendOf(run).laps_used} laps`;
      for (const run of [1, 2, 3]) {
        await expect(page.locator(`[data-trend-run='${run}']`)).toHaveAttribute("data-trend-span", `${trendOf(run).first_lap}-${trendOf(run).last_lap}`);
        await expect(page.locator(`[data-trend-run='${run}'] [data-trend-label]`)).toHaveText(label(run));
      }
      await expect(page.locator("[data-pace-fit]")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Screened fit" })).toHaveCount(0);
      const cards = page.locator("[data-run-card]");
      await expect(cards).toHaveCount(3);
      for (const run of [1, 2, 3]) await expect(cards.nth(run - 1).locator("[data-run-slope]")).toHaveText(label(run));
      // #93: the separate fall-off figure is gone (the Fit is the same number).
      await expect(page.locator("[data-run-falloff]")).toHaveCount(0);
      await expect(cards.first()).not.toContainText(/fall-off/i);
      await expect(page.locator(".ri-runs-view")).not.toContainText(/no fit|insufficient/i);
      await expect(cards.nth(0)).toContainText("15.9 → 3.8 gal");
      await expect(cards.nth(1)).toContainText("25–52");
      await expect(cards.nth(2)).toContainText("Run 3 · same tires");
      await expect(page.locator(".ri-workspace")).not.toContainText(/excl/i);
      // Each card is its run's toggle: off hides the run's dots and line and dims the card; the chart rescales to the visible runs.
      for (const card of await cards.all()) await expect(card).toHaveAttribute("aria-pressed", "true");
      const svg = page.locator(".ri-runs .ri-plot-svg");
      const domain = await svg.getAttribute("data-domain");
      await cards.nth(0).click();
      await expect(cards.nth(0)).toHaveAttribute("aria-pressed", "false");
      await expect(page.locator(".ri-runs .ri-dot[data-run='1']")).toHaveCount(0);
      await expect(page.locator("[data-trend-run='1']")).toHaveCount(0);
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

    test("Runs (#93): each run card is an even grid, label above figure, at the Sessions sizes; no two text boxes overlap", async ({ page }) => {
      await open(page, "runs");
      const cards = page.locator("[data-run-card]");
      await expect(cards).toHaveCount(3);
      await page.evaluate(() => document.fonts.ready);
      expect(await overlappingText(page, "[data-run-card]"), "overlapping text in a run card").toEqual([]);
      // Label above figure, every fact; the five facts in the order Fit, Fuel, Used, Screened, Tire laps.
      const facts = await cards.first().locator(".ri-fact").evaluateAll((els) => els.map((el) => {
        const [label, value] = [el.querySelector(".ri-fact-label")!, el.querySelector(".ri-fact-value")!].map((x) => x.getBoundingClientRect());
        return { name: el.querySelector(".ri-fact-label")!.textContent, above: label!.bottom <= value!.top + 0.5 };
      }));
      expect(facts.map((fact) => fact.name)).toEqual(["Fit", "Fuel", "Used", "Screened", "Tire laps"]);
      for (const fact of facts) expect(fact.above, `${fact.name}: label above figure`).toBe(true);
      // The grid's rhythm: 0.5 rem between rows, 1.25 rem between columns, 0.75 rem padding; the label and figure at the Sessions
      // list's sizes (.sx-l: --text-compact semibold; .sx-v: --text-data medium).
      const style = await cards.first().evaluate((el) => {
        const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
        const grid = getComputedStyle(el.querySelector(".ri-facts")!);
        const card = getComputedStyle(el);
        const label = getComputedStyle(el.querySelector(".ri-fact-label")!);
        const value = getComputedStyle(el.querySelector(".ri-fact-value")!);
        const probe = (v: string): string => { const i = document.createElement("i"); i.style.fontSize = `var(${v})`; document.body.append(i); const out = getComputedStyle(i).fontSize; i.remove(); return out; };
        return {
          rowGap: parseFloat(grid.rowGap) / rem, columnGap: parseFloat(grid.columnGap) / rem, padding: parseFloat(card.paddingTop) / rem,
          label: [label.fontSize, label.fontWeight, label.textTransform], value: [value.fontSize, value.fontWeight],
          compact: probe("--text-compact"), data: probe("--text-data"),
        };
      });
      expect(style).toMatchObject({ rowGap: 0.5, columnGap: 1.25, padding: 0.75 });
      expect(style.label).toEqual([style.compact, "600", "none"]);
      expect(style.value).toEqual([style.data, "500"]);
      // The sparkline sits on the head's row at the right, clear of the title.
      const head = (await cards.first().locator(".ri-run-card-head").boundingBox())!;
      const spark = (await cards.first().locator(".ri-spark").boundingBox())!;
      const card = (await cards.first().boundingBox())!;
      expect(spark.x, "sparkline right of the head").toBeGreaterThanOrEqual(head.x + head.width - 0.5);
      expect(spark.x + spark.width, "sparkline inside the card").toBeLessThanOrEqual(card.x + card.width);
      await expectNoHorizontalScroll(page);
      mkdirSync(SHOTS, { recursive: true });
      await page.locator(".ri-run-cards").screenshot({ path: path.join(SHOTS, `runs-cards-${width}.png`) });
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
      for (const key of ["temp", "fuel"]) await page.locator(`[data-layer-toggle=${key}]`).click();
      await point(page, `[data-lap-column='${ROUND4_NO_FUEL_LAP}']`);
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(SHOTS, `race-layers-${width}.png`), fullPage: width < 700 });
      await open(page, "runs");
      await page.locator("[data-run-card]").first().click();
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(SHOTS, `runs-run1-hidden-${width}.png`), fullPage: width < 700 });
    });

    test("#96 #97: the position line starts at the official start on lap 0, joined to lap 1; a race left before the flag reads DNF", async ({ page }) => {
      // Session 103: gridded P12 (`start_position`), P4 after lap 1 in the synthetic race.
      await open(page, "race");
      const start = page.locator("[data-position-start]");
      await expect(start).toHaveCount(1);
      await expect(start).toHaveAttribute("data-position-lap", "0");
      await expect(start).toHaveAttribute("data-position", "12");
      await expect(page.locator("[data-position-lap='1']")).toHaveAttribute("data-position", "4");
      // The lap axis begins at 0 and the start is joined to lap 1 by the opening lap's change.
      const joined = await page.evaluate(() => {
        const dot = (lap: number) => document.querySelector(`[data-position-lap='${lap}']`)!.getBoundingClientRect();
        const [a, b] = [dot(0), dot(1)];
        const centre = (r: DOMRect) => ({ x: r.x + r.width / 2, y: r.y + r.height / 2 });
        const [p, q] = [centre(a), centre(b)];
        return [...document.querySelectorAll(".ri-position-seg")].some((seg) => {
          const r = seg.getBoundingClientRect();
          return Math.abs(r.left - Math.min(p.x, q.x)) < 3 && Math.abs(r.right - Math.max(p.x, q.x)) < 3 && Math.abs(r.top - Math.min(p.y, q.y)) < 3 && Math.abs(r.bottom - Math.max(p.y, q.y)) < 3;
        });
      });
      expect(joined, "a segment from lap 0 to lap 1").toBe(true);
      await expect(page.locator(".ri-plot-svg text.ri-axis", { hasText: /^0$/ })).toHaveCount(1);
      await point(page, "[data-position-start]");
      await expect(page.locator("[data-race-readout]")).toHaveText("Lap 0 · Start · P12");
      await expect(page.locator("[data-retired]")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
      mkdirSync(B96_SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(B96_SHOTS, `race-start-${width}.png`), fullPage: width < 700 });

      // The race left before the flag (#97): the session says so, the band's corner reads DNF and the lap, its hover the running place.
      await page.route("**/api/v1/sessions/103", async (route: Route) => {
        const res = await route.fetch();
        const body = (await res.json()) as { session: { overview: Record<string, unknown> } };
        body.session.overview = { ...body.session.overview, finished: false, retired_lap: 82, retired_position: 2 };
        await route.fulfill({ response: res, json: body });
      });
      await page.reload();
      const dnf = page.locator("[data-retired]");
      await expect(dnf).toHaveAttribute("data-retired", "82");
      await expect(dnf.locator("text")).toHaveText("DNF · L82");
      await expect(dnf.locator("title")).toHaveText("Running P2 when the recording ended");
      // Inside the chart, clear of the Position title.
      const clear = await page.evaluate(() => {
        const label = document.querySelector("[data-retired] text")!.getBoundingClientRect();
        const svg = document.querySelector(".ri-plot-svg")!.getBoundingClientRect();
        const title = [...document.querySelectorAll(".ri-position-band .ri-axis-title")].find((el) => el.textContent === "Position")!.getBoundingClientRect();
        return label.right <= svg.right + 0.5 && label.left >= svg.left && (label.left >= title.right + 4 || label.top >= title.bottom);
      });
      expect(clear, "the DNF label sits inside the chart, clear of the Position title").toBe(true);
      const last = page.locator(".ri-pos-dot").last();
      await point(page, `[data-position-lap='${await last.getAttribute("data-position-lap")}']`);
      await expect(page.locator("[data-race-readout]")).toContainText("DNF, running when the recording ended");
      await expectNoHorizontalScroll(page);
      await page.screenshot({ path: path.join(B96_SHOTS, `race-dnf-${width}.png`), fullPage: width < 700 });
    });

    test("screenshots of the traffic marker on Race and Runs for the release record (#67, #92)", async ({ page }) => {
      mkdirSync(T67_SHOTS, { recursive: true });
      // Race with a traffic lap read out, then the marks of laps 8 to 31 (open air, traffic, traffic and off track, restart, off track).
      await open(page, "race");
      await page.locator(".ri-dot").first().waitFor();
      await point(page, `.ri-dot[data-lap='${TRAFFIC_LAP}']`);
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(T67_SHOTS, `race-traffic-${width}.png`), fullPage: width < 700 });
      // Runs with the same lap read out.
      await open(page, "runs");
      await page.locator(".ri-runs .ri-dot").first().waitFor();
      await point(page, `.ri-runs .ri-dot[data-lap='${TRAFFIC_LAP}']`);
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(T67_SHOTS, `runs-traffic-${width}.png`), fullPage: width < 700 });
    });
  });
}

// The marks at three times the device pixels, so the rings read at the size they are drawn: laps 8 to 31 of Race (open air, traffic,
// traffic and off track, restart, off track) and the run cards' sparklines.
for (const device of ROUND2_DEVICES) {
  test.describe(`${device.name} close-ups`, () => {
    test.use({ viewport: device.viewport, hasTouch: device.touch, deviceScaleFactor: 3 });
    test.beforeEach(async ({ request, page }) => {
      await resetMock(request);
      await serveRound2(page);
    });

    test("close-ups of the traffic marker and the run card for the release record (#67, #92, #93)", async ({ page }) => {
      mkdirSync(T67_SHOTS, { recursive: true });
      const w = device.viewport.width;
      await open(page, "race");
      await page.locator(".ri-dot").first().waitFor();
      await page.waitForTimeout(250);
      await page.screenshot({ path: path.join(T67_SHOTS, `race-traffic-marks-${w}.png`), clip: await marksBox(page, ".ri-dot", 8, 31, 14) });
      await open(page, "runs");
      await page.locator("[data-run-card]").first().waitFor();
      await page.waitForTimeout(250);
      const card = (await page.locator("[data-run-card='1']").boundingBox())!;
      await page.screenshot({ path: path.join(T67_SHOTS, `runs-traffic-card-${w}.png`), clip: card });
    });
  });
}
