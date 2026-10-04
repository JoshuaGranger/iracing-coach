import { type Locator, type Page, type Route, expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

// The dates and times below are the driver's wall clock: 2026-09-28T18:00:00Z is 1:00 pm in Chicago.
test.use({ timezoneId: "America/Chicago" });

const BRISTOL = "Bristol Motor Speedway - Oval";
const bristol = (page: Page) => page.getByRole("region", { name: BRISTOL }).first();
const tile = (page: Page, sessionId: number) => page.locator(`li.session-row[data-session="${sessionId}"]`);
const dropdown = (page: Page, label: string) => page.getByRole("button", { name: new RegExp(`^${label}`) });
const fig = (t: Locator, label: string) => t.locator(".sx-fig", { hasText: label }).locator(".sx-fig-v");

type LibraryRow = { session_id: number; event_key: string; overview: Record<string, unknown>; start_time_utc: string };

/** Serve the library as the mock has it, after `edit` has changed it. */
async function editLibrary(page: Page, edit: (sessions: LibraryRow[]) => LibraryRow[]): Promise<void> {
  await page.route("**/api/v1/drivers/*/sessions**", async (route: Route) => {
    const res = await route.fetch();
    const body = (await res.json()) as { sessions: LibraryRow[]; next_cursor: string | null };
    await route.fulfill({ response: res, json: { ...body, sessions: edit(body.sessions) } });
  });
}

/** A closed circuit per analysis, so the cards have something to draw. */
async function serveGeometry(page: Page): Promise<string[]> {
  const asked: string[] = [];
  await page.route("**/api/v1/analyses/*/track", async (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/")[4] ?? "");
    asked.push(id);
    const k = Number(id.replace("an-", "")) % 3;
    const n = 180;
    const x: number[] = [];
    const y: number[] = [];
    const pct: number[] = [];
    for (let i = 0; i < n; i++) {
      const t = (i / n) * Math.PI * 2;
      pct.push(i / n);
      x.push(0.5 + (0.42 + 0.06 * Math.cos(3 * t) * k) * Math.cos(t));
      y.push(0.5 + (0.28 + 0.05 * Math.sin(2 * t) * (k + 1)) * Math.sin(t));
    }
    await route.fulfill({
      json: { analysis_id: id, projection_version: 1, schema_version: 1, profile_version: "p", coordinate_space: "normalized_local_vector", status: "usable", main_path: { pct, x, y }, provenance: {} },
    });
  });
  return asked;
}

test.describe("sessions", () => {
  for (const device of DEVICES) {
    appTest(device, (d) => {
      test("sessions: one card per event, phases in running order with local times", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        const card = bristol(page);
        await expect(card.getByRole("heading", { name: "Bristol Motor Speedway" })).toBeVisible();
        await expect(card.locator(".sx-band b")).toHaveText(["Practice", "Qualifying", "Race"]);
        await expect(card.locator(".sx-band span")).toHaveText(["1:00 pm", "1:30 pm", "2:00 pm · 2 rec"]);
        await expect(card.locator(".sx-meta")).toHaveText("Oval · Mon Sep 28");
        await expect(card.getByText("NASCAR Xfinity Toyota Supra")).toBeVisible();
        await expectNoHorizontalScroll(page);
        if (d.name.startsWith("phone") || d.name.startsWith("tablet")) await expectTouchTargets(page);
      });

      test("sessions: every figure is colour coded by what it means", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        const race = tile(page, 103);
        await expect(race.locator(".sx-big")).toHaveText("P4");
        await expect(race.locator(".sx-chg.gain")).toHaveText("▲8");
        await expect(race.locator(".sx-from")).toHaveText("from P12");
        await expect(race.locator(".sx-rb.gain")).toHaveCount(1);
        await expect(fig(race, "Green")).toHaveText("60");
        await expect(fig(race, "Caution")).toHaveText("20");
        await expect(race.locator(".sx-fig", { hasText: "Green" }).locator(".sx-dot.g")).toHaveCount(1);
        await expect(race.locator(".sx-fig", { hasText: "Caution" }).locator(".sx-dot.y")).toHaveCount(1);
        await expect(fig(race, "Best")).toHaveText("15.234");
        await expect(fig(race, "Best")).toHaveClass(/best/);
        await expect(fig(race, "Pace")).toHaveText("+0.021");
        await expect(fig(race, "Pace")).toHaveClass(/pace-loss/);
        await expect(fig(race, "Stops")).toHaveText("3");
        await expect(fig(race, "Stops")).toHaveClass(/pit/);
        await expect(fig(race, "Long run")).toHaveText("41");
        await expect(fig(race, "Fuel")).toHaveText("21.6gal");
        await expect(fig(race, "Laps")).toHaveText("82");
        // The same colours resolve to the race tokens.
        const green = await race.locator(".sx-dot.g").evaluate((el) => getComputedStyle(el).backgroundColor);
        expect(green).toBe("rgb(61, 220, 132)");
        const loss = await fig(race, "Pace").evaluate((el) => getComputedStyle(el).color);
        expect(loss).toBe("rgb(255, 90, 110)");
        // Practice: classification only, a hollow dot for no caution laps, an improving pace in green.
        const practice = tile(page, 101);
        await expect(practice.locator(".sx-big")).toHaveText("P5");
        await expect(practice.locator(".sx-chg")).toHaveCount(0);
        await expect(practice.locator(".sx-fig", { hasText: "Caution" }).locator(".sx-dot.zero")).toHaveCount(1);
        await expect(fig(practice, "Pace")).toHaveClass(/pace-gain/);
        await expect(fig(tile(page, 102), "Pace")).toHaveText("—");
      });

      test("sessions: a win carries the checkered flag and the event's fastest lap is the one marked best", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        const race = tile(page, 110);
        await expect(race.locator(".sx-big")).toHaveText("P1");
        await expect(race.locator(".sx-win")).toContainText("Win");
        await expect(race.locator(".sx-rb.win")).toHaveCount(1);
        await expect(race.locator(".sx-from")).toHaveText("from P3");
        // Qualifying beat the race lap in this event, so it is the one marked.
        await expect(fig(tile(page, 109), "Best")).toHaveClass(/best/);
        await expect(fig(race, "Best")).not.toHaveClass(/best/);
      });

      test("sessions: a place lost reads red with its starting position", async ({ page }) => {
        await editLibrary(page, (rows) => rows.map((r) => (r.session_id === 103 ? { ...r, overview: { ...r.overview, starting_position: 7, final_position: 9 } } : r)));
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        const race = tile(page, 103);
        await expect(race.locator(".sx-chg.loss")).toHaveText("▼2");
        await expect(race.locator(".sx-from")).toHaveText("from P7");
        await expect(race.locator(".sx-rb.loss")).toHaveCount(1);
        await expect(race.locator(".sx-win")).toHaveCount(0);
      });

      test("sessions: no Resume banner, no Analyzed chip, only the exceptions are named", async ({ page }) => {
        await page.addInitScript((c) => localStorage.setItem(`coach.last-investigation.${c}`, JSON.stringify({ session_id: 103, analysis_id: "an-103", view: "traces", comparison_id: "c1" })), DRIVER);
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page)).toBeVisible();
        await expect(page.getByText("Resume")).toHaveCount(0);
        await expect(page.getByText("Analyzed", { exact: true })).toHaveCount(0);
        await expect(page.locator(".chip-good")).toHaveCount(0);
        const notAnalyzed = tile(page, 106);
        await expect(notAnalyzed.locator(".sx-state")).toHaveText("Not analyzed");
        await expect(notAnalyzed.getByRole("button", { name: "Analyze" })).toBeVisible();
        await expect(notAnalyzed.locator(".sx-big")).toHaveText("83 laps");
        const failed = tile(page, 108);
        await expect(failed.locator(".sx-state")).toHaveText("Failed · worker crash");
        await expect(failed.getByRole("button", { name: "Retry" })).toBeVisible();
        await expect(tile(page, 113).locator(".sx-state")).toHaveText("Deferred · exceeds memory budget");
        // A ready session shows no state at all and is the only kind that opens.
        await expect(tile(page, 103).locator(".sx-state")).toHaveCount(0);
        await expect(tile(page, 103).getByRole("link")).toHaveCount(1);
        await expect(notAnalyzed.getByRole("link")).toHaveCount(0);
        await expect(page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first()).toBeVisible();
        await expect(page.getByRole("link", { name: /^Race, Indianapolis Raceway Park - Oval/ })).toHaveCount(0);
      });

      test("sessions: analyze queues the job and the tile opens when the server says so", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        const waiting = page.locator("li.session-row[data-state=none]");
        await expect(page.locator("li.session-row").first()).toBeVisible();
        const before = await waiting.count();
        expect(before).toBeGreaterThan(0);
        await waiting.first().getByRole("button", { name: "Analyze" }).click();
        await expect(waiting).toHaveCount(before - 1, { timeout: 6000 });
        await expect(page.locator("li.session-row[data-state=ready]").first()).toBeVisible();
      });

      test("sessions: fixed and open per event, and a phase that differs carries its own badge", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page).locator(".sx-ser .sx-badge")).toHaveText("Fixed");
        await expect(page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).first().locator(".sx-ser .sx-badge")).toHaveText("Open");
        const hampshire = page.getByRole("region", { name: "New Hampshire Motor Speedway - Oval" });
        await expect(hampshire.locator(".sx-ser .sx-badge")).toHaveText("Fixed");
        await expect(tile(page, 108).locator(".sx-band .sx-badge")).toHaveText("Open");
        await expect(tile(page, 109).locator(".sx-band .sx-badge")).toHaveCount(0);
        // An event with no recorded setup type shows no badge, and a series that repeats the car name is left out.
        await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" }).getByText("Global Mazda MX-5 Cup")).toHaveCount(1);
      });

      test("sessions: searchable multi-select filters, chips that remove, no Tests and no type tabs", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page)).toBeVisible();
        for (const name of ["Car", "Track", "Series", "Session", "Setup"]) await expect(dropdown(page, name)).toBeVisible();
        await expect(page.getByRole("button", { name: "Tests" })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Races" })).toHaveCount(0);
        await expect(page.getByRole("searchbox")).toHaveCount(0);

        await dropdown(page, "Car").click();
        const search = page.getByRole("combobox", { name: "Search car" });
        await expect(search).toBeFocused();
        await expect(page.getByRole("option")).toHaveCount(2);
        await search.fill("mazda");
        await expect(page.getByRole("option")).toHaveCount(1);
        await expect(page.locator(".ms-opt-label u")).toHaveText("Mazda");
        await page.getByRole("option", { name: /Global Mazda MX-5 Cup/ }).click();
        await expect(page.getByRole("option", { name: /Global Mazda MX-5 Cup/ })).toHaveAttribute("aria-selected", "true");
        await expect(page.locator(".ms-foot")).toContainText("1 of 2 cars");
        await page.keyboard.press("Escape");
        await expect(page.getByRole("combobox")).toHaveCount(0);
        await expect(dropdown(page, "Car")).toBeFocused();
        await expect(dropdown(page, "Car")).toContainText("1");
        await expect(page.getByRole("button", { name: "Remove Global Mazda MX-5 Cup" })).toBeVisible();
        await expect(page.locator("section.sx-event")).toHaveCount(1);
        await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" })).toBeVisible();

        await page.getByRole("button", { name: "Remove Global Mazda MX-5 Cup" }).click();
        await expect(page.locator("section.sx-event").first()).toBeVisible();
        expect(await page.locator("section.sx-event").count()).toBeGreaterThan(1);
        await expect(page.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
        await expectNoHorizontalScroll(page);
        if (d.name.startsWith("phone") || d.name.startsWith("tablet")) await expectTouchTargets(page);
      });

      test("sessions: the Session filter offers practice, qualifying and race; filters combine across and within", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await dropdown(page, "Session").click();
        await expect(page.locator(".ms-opt-label")).toHaveText(["Practice", "Qualifying", "Race"]);
        await expect(page.getByRole("combobox", { name: "Search session" })).toBeFocused();
        // keyboard: arrows move, Enter chooses
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("Enter");
        await expect(page.getByRole("option", { name: /Race/ })).toHaveAttribute("aria-selected", "true");
        await page.keyboard.press("Escape");
        await expect(page.locator("li.session-row")).toHaveCount(4);
        await expect(page.locator("li.session-row .sx-band b")).toHaveText(["Race", "Race", "Race", "Race"]);

        await dropdown(page, "Setup").click();
        await page.getByRole("option", { name: /Fixed/ }).click();
        await page.keyboard.press("Escape");
        // Races of fixed-setup events only: Bristol and New Hampshire.
        await expect(page.locator("li.session-row")).toHaveCount(2);
        await dropdown(page, "Track").click();
        await page.getByRole("combobox", { name: "Search track" }).fill("hampshire");
        await page.getByRole("option", { name: /New Hampshire/ }).click();
        await page.keyboard.press("Escape");
        await expect(page.locator("li.session-row")).toHaveCount(1);

        await dropdown(page, "Series").click();
        await page.getByRole("option", { name: /Global Mazda/ }).click();
        await page.keyboard.press("Escape");
        await expect(page.getByRole("heading", { name: "No matching sessions" })).toBeVisible();
        await page.getByRole("button", { name: "Clear filters" }).click();
        await expect(page.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
        expect(await page.locator("section.sx-event").count()).toBeGreaterThan(3);
      });

      test("sessions: the selection is saved with the driver's prefs and comes back on another device", async ({ page, request }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await dropdown(page, "Car").click();
        await page.getByRole("option", { name: /Global Mazda/ }).click();
        await page.keyboard.press("Escape");
        await expect.poll(async () => (await mockState(request)).writes.filter((w) => w.path.endsWith("/prefs")).map((w) => (w.body as { session_filters?: { cars: string[] } }).session_filters?.cars)).toContainEqual(["mx5-cup"]);
        // The device's own copy is only a hint for the first paint: without it the prefs still restore the chips.
        await page.evaluate((c) => localStorage.removeItem(`coach.sessions.filters.${c}`), DRIVER);
        await page.reload();
        await expect(page.getByRole("button", { name: "Remove Global Mazda MX-5 Cup" })).toBeVisible();
        await expect(page.locator("section.sx-event")).toHaveCount(1);
      });

      test("sessions: a session opens on the tab it was left on, without being asked", async ({ page }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
        await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/race$`));
        await page.getByRole("tab", { name: "Traces" }).click();
        await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page)).toBeVisible();
        await expect(page.getByText("Resume")).toHaveCount(0);
        await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
        await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces`));
        // A session never opened opens on Race.
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page)).toBeVisible();
        await page.getByRole("link", { name: /^Qualifying, Bristol Motor Speedway - Oval/ }).first().click();
        await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/race$`));
      });

      test("sessions: the silhouette comes from the track's geometry, once per layout", async ({ page }) => {
        const asked = await serveGeometry(page);
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page).getByTestId("silhouette")).toBeVisible();
        await expect(bristol(page).getByTestId("silhouette").locator("path")).toHaveAttribute("d", /^M[\d.,L-]+Z$/);
        for (const card of await page.locator("section.sx-event").all()) await card.scrollIntoViewIfNeeded();
        await expect.poll(() => asked.length).toBe(4);
        await page.waitForLoadState("networkidle");
        // Bristol, Indianapolis, Lime Rock and New Hampshire: four layouts across six events.
        expect(new Set(asked).size).toBe(asked.length);
        expect(asked.length).toBeLessThanOrEqual(4);
        await expectNoHorizontalScroll(page);
      });

      test("sessions: a track without usable geometry draws no silhouette and the card still reads", async ({ page }) => {
        // A path too short to be a circuit (the mock's own geometry is a closed oval).
        await page.route("**/api/v1/analyses/*/track", (route) =>
          route.fulfill({ json: { analysis_id: "x", projection_version: 1, schema_version: 1, profile_version: "", coordinate_space: "normalized_local_vector", status: "usable", main_path: { pct: [0, 0.5], x: [0.1, 0.9], y: [0.5, 0.5] }, provenance: {} } }),
        );
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page).getByRole("heading", { name: "Bristol Motor Speedway" })).toBeVisible();
        await expect(page.getByTestId("silhouette")).toHaveCount(0);
      });

      test("sessions: older events load in batches", async ({ page }) => {
        await editLibrary(page, (rows) => {
          const out: LibraryRow[] = [];
          for (let k = 0; k < 60; k++)
            for (const r of rows.slice(0, 3)) out.push({ ...r, session_id: r.session_id + 1000 * (k + 1), event_key: `${r.event_key}-${k}`, ...({ analysis_id: null, analysis_state: "none" } as object) });
          return out;
        });
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(page.locator("section.sx-event")).toHaveCount(24);
        await page.getByRole("button", { name: "Older sessions" }).click();
        await expect(page.locator("section.sx-event")).toHaveCount(48);
        await page.getByRole("button", { name: "Older sessions" }).click();
        await expect(page.locator("section.sx-event")).toHaveCount(60);
        await expect(page.getByRole("button", { name: "Older sessions" })).toHaveCount(0);
      });

      test("sessions: loaded cards stay on screen when the server drops, with a banner", async ({ page, context, request }) => {
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(bristol(page)).toBeVisible();
        await context.route("**/api/v1/drivers/**", (r) => r.abort());
        await request.post("/__mock/event", { data: { type: "library_changed", customer_id: DRIVER, scope: "sessions" } });
        await expect(page.getByTestId("offline-banner")).toBeVisible();
        await expect(page.getByText(/Could not refresh/)).toBeVisible();
        await expect(bristol(page)).toBeVisible();
        await context.unroute("**/api/v1/drivers/**");
      });
    });
  }

  const dir = process.env.COACH_SCREENS_DIR;
  test.describe("screenshots", () => {
    test.skip(!dir, "set COACH_SCREENS_DIR to write the 1536x864 screenshots");
    test.use({ viewport: { width: 1536, height: 864 } });
    test.beforeEach(async ({ request }) => {
      await request.post("/__mock/state", { data: { reset: true } });
    });

    test("sessions: screenshots", async ({ page }) => {
      await serveGeometry(page);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByTestId("silhouette").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      await page.screenshot({ path: `${dir}/sessions-library.png` });

      await dropdown(page, "Car").click();
      await page.getByRole("option", { name: /NASCAR Xfinity/ }).click();
      await page.waitForTimeout(150);
      await page.screenshot({ path: `${dir}/sessions-filter-open.png` });
      await page.keyboard.press("Escape");
      await dropdown(page, "Setup").click();
      await page.getByRole("option", { name: /Fixed/ }).click();
      await page.keyboard.press("Escape");
      await page.waitForTimeout(150);
      await page.screenshot({ path: `${dir}/sessions-filtered.png` });
      await page.getByRole("button", { name: "Clear" }).click();

      await page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).first().scrollIntoViewIfNeeded();
      await page.mouse.wheel(0, 460);
      await page.waitForTimeout(200);
      await page.screenshot({ path: `${dir}/sessions-exceptions.png` });
    });
  });
});
