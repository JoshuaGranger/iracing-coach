import { type Locator, type Page, type Route, expect, test } from "@playwright/test";
import { DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// The dates and times below are the driver's wall clock: 2026-09-28T18:00:00Z is 1:00 pm in Chicago.
test.use({ timezoneId: "America/Chicago" });

const BRISTOL = "Bristol Motor Speedway - Oval";
const bristol = (page: Page) => page.getByRole("region", { name: BRISTOL }).first();
const cell = (page: Page, sessionId: number) => page.locator(`li.session-row[data-session="${sessionId}"]`);
const slot = (c: Locator, key: string) => c.locator(`.sx-slot[data-slot="${key}"]`);
const value = (c: Locator, key: string) => slot(c, key).locator(".sx-v");
/** A practice or qualifying figure: the quieter preamble type (round 3, #33). */
const quiet = (c: Locator, key: string) => slot(c, key).locator(".sx-qv");
const bar = (page: Page) => page.locator(".fb");

// Token colours as the browser resolves them (config/theme.dark.json).
const GAIN = "rgb(61, 220, 132)";
const LOSS = "rgb(255, 90, 110)";
const CAUTION = "rgb(255, 212, 59)";
// Phase hues (round 4, #52): config/theme.dark.json phase.practiceHue, qualifyHue, raceHue.
const PHASE_P = "rgb(95, 135, 179)";
const PHASE_Q = "rgb(201, 138, 171)";
const PHASE_R = "rgb(238, 243, 247)";

type LibraryRow = { session_id: number; event_key: string; overview: Record<string, unknown>; start_time_utc: string };

/** Serve the library as the mock has it, after `edit` has changed it. */
async function editLibrary(page: Page, edit: (sessions: LibraryRow[]) => LibraryRow[]): Promise<void> {
  await page.route("**/api/v1/drivers/*/sessions**", async (route: Route) => {
    const res = await route.fetch();
    const body = (await res.json()) as { sessions: LibraryRow[]; next_cursor: string | null };
    await route.fulfill({ response: res, json: { ...body, sessions: edit(body.sessions) } });
  });
}

/** A closed circuit per analysis, so the rows have something to draw. */
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

/** The surface that holds the filters: the heading itself when they fit, else the popover or sheet behind `Filters`. */
async function openFilters(page: Page): Promise<Locator> {
  if ((await bar(page).getAttribute("data-layout")) === "inline") return bar(page);
  const surface = page.getByRole("dialog", { name: "Filters" });
  if (!(await surface.isVisible())) await page.getByRole("button", { name: /^Filters/ }).click();
  await expect(surface).toBeVisible();
  return surface;
}

/** Close the surface when it is a popover or sheet (the inline bar has nothing to close). */
async function closeFilters(page: Page): Promise<void> {
  const surface = page.getByRole("dialog", { name: "Filters" });
  if (await surface.isVisible()) await surface.getByRole("button", { name: "Done" }).click();
  await expect(surface).toHaveCount(0);
}

/** Open one filter, choose the options matching `pick`, leave the surface closed (or the inline bar idle). */
async function choose(page: Page, filter: string, ...pick: RegExp[]): Promise<void> {
  const surface = await openFilters(page);
  await surface.getByRole("button", { name: new RegExp(`^${filter}`) }).click();
  for (const p of pick) await page.getByRole("option", { name: p }).click();
  await page.keyboard.press("Escape");
  await closeFilters(page);
}

/** Computed colours and shadows of everything inside the list, as the colour-budget checks read them. */
async function paint(page: Page) {
  return page.evaluate(() => {
    const root = document.querySelector(".sx-events")!;
    const sat = (css: string) => {
      const m = css.match(/rgba?\((\d+), (\d+), (\d+)/);
      if (!m) return 0;
      const [r, g, b] = [Number(m[1]) / 255, Number(m[2]) / 255, Number(m[3]) / 255];
      const mx = Math.max(r, g, b);
      const mn = Math.min(r, g, b);
      const l = (mx + mn) / 2;
      return mx === mn ? 0 : (mx - mn) / (1 - Math.abs(2 * l - 1));
    };
    const chroma = (css: string) => {
      const m = css.match(/rgba?\((\d+), (\d+), (\d+)/);
      if (!m) return 0;
      const v = [Number(m[1]), Number(m[2]), Number(m[3])];
      return (Math.max(...v) - Math.min(...v)) / 255;
    };
    const glow: string[] = [];
    const fills: string[] = [];
    for (const el of root.querySelectorAll<HTMLElement>("*")) {
      const cs = getComputedStyle(el);
      if (cs.boxShadow !== "none") {
        // `color x y blur spread [inset]`: a glow has a blur; the phase edge has none.
        const m = cs.boxShadow.match(/(-?[\d.]+)px (-?[\d.]+)px (-?[\d.]+)px/);
        if (m && Number(m[3]) > 0) glow.push(`${el.className}: ${cs.boxShadow}`);
      }
      if (cs.textShadow !== "none") glow.push(`${el.className}: text-shadow ${cs.textShadow}`);
      const bg = cs.backgroundColor;
      // A fill is saturated when it carries real chroma: the dark neutral surfaces read high on HSL saturation alone.
      if (bg !== "rgba(0, 0, 0, 0)" && sat(bg) > 0.3 && chroma(bg) > 0.12) fills.push(`${el.tagName.toLowerCase()}.${el.className}: ${bg}`);
    }
    return { glow, fills };
  });
}

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    const phone = d.viewport.width < 700;
    const wide = d.viewport.width >= 1400;

    test("one row per event in three blocks: identity behind a divider, each phase its own panel, the race widest", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const row = bristol(page);
      await expect(row.getByRole("heading", { name: "Bristol Motor Speedway" })).toBeVisible();
      await expect(row.locator(".sx-meta span").first()).toHaveText("Oval · Mon Sep 28");
      await expect(row.getByRole("img", { name: "Fixed setup" })).toHaveCount(1);
      await expect(row.locator(".sx-car")).toContainText("NASCAR Xfinity Toyota Supra");
      await expect(row.locator(".sx-phase")).toHaveText([/Practice$/, /Qualifying$/, /Race$/]);
      await expect(row.locator(".sx-head .sx-l")).toHaveText(["1:00 pm", "1:30 pm", "2:00 pm · 2 rec"]);
      expect(await row.locator("li.session-row").evaluateAll((els) => els.map((e) => e.getAttribute("data-kind")))).toEqual(["practice", "qualify", "race"]);
      await expect(page.locator(".sx-events details, .sx-events [aria-expanded]")).toHaveCount(0);

      const id = (await row.locator(".sx-id").boundingBox())!;
      const cells = await Promise.all((await row.locator("li.session-row").all()).map(async (c) => (await c.boundingBox())!));
      expect(cells).toHaveLength(3);
      // Each phase is a panel: its own border all round and rounded corners.
      for (const c of await row.locator("li.session-row").all()) {
        const st = await c.evaluate((el) => {
          const cs = getComputedStyle(el);
          return { sides: [cs.borderTopWidth, cs.borderLeftWidth, cs.borderRightWidth, cs.borderBottomWidth], radius: parseFloat(cs.borderTopLeftRadius) };
        });
        expect(st.sides).toEqual(["1px", "1px", "1px", "1px"]);
        expect(st.radius).toBeGreaterThan(0);
      }
      // The identity block's divider: a visible rule at its right (wide) or under it (tablet, phone).
      const divider = await row.locator(".sx-id").evaluate((el) => {
        const cs = getComputedStyle(el);
        return { right: cs.borderRightWidth, bottom: cs.borderBottomWidth };
      });
      if (phone) {
        // Stacked phase panels: one column, in order, each as wide as the row, with a gutter between them.
        for (let i = 1; i < 3; i++) {
          expect(cells[i]!.y).toBeGreaterThanOrEqual(cells[i - 1]!.y + cells[i - 1]!.height + 6);
          expect(Math.abs(cells[i]!.x - cells[0]!.x)).toBeLessThan(2);
          expect(Math.abs(cells[i]!.width - cells[0]!.width)).toBeLessThan(2);
        }
        expect(divider.bottom).toBe("1px");
      } else {
        // Inline panels on one line with a real gutter; the race is the widest, qualifying the narrowest.
        for (let i = 1; i < 3; i++) {
          expect(Math.abs(cells[i]!.y - cells[0]!.y)).toBeLessThan(2);
          expect(cells[i]!.x).toBeGreaterThanOrEqual(cells[i - 1]!.x + cells[i - 1]!.width + 10);
        }
        const [p, q, r] = cells as [typeof id, typeof id, typeof id];
        expect(r.width).toBeGreaterThan(p.width * 1.5);
        expect(p.width).toBeGreaterThan(q.width);
        if (wide) {
          // Identity at the left, then the divider, then a gutter before the first panel; the row is about 5.5 rem.
          expect(divider.right).toBe("1px");
          expect(cells[0]!.x).toBeGreaterThanOrEqual(id.x + id.width + 10);
          const box = (await row.locator(".sx-row").boundingBox())!;
          expect(box.height).toBeGreaterThanOrEqual(80);
          expect(box.height).toBeLessThanOrEqual(140);
        } else {
          // Tablet: identity on its own line above the panels, the divider under it.
          expect(divider.bottom).toBe("1px");
          expect(cells[0]!.y).toBeGreaterThanOrEqual(id.y + id.height + 6);
        }
      }
      await expectNoHorizontalScroll(page);
      if (d.touch) await expectTouchTargets(page);
    });

    test("seven labelled slots per phase, a dash where the backend reported nothing", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const race = cell(page, 103);
      await expect(race.locator(".sx-slot:not(.sx-head) .sx-l")).toHaveText(["Finish", "Grn · Cau", "Best", "Pace", "Stops", "Burn", "Long run"]);
      await expect(value(race, "pos")).toHaveText("P4▲8from P12");
      await expect(value(race, "laps")).toHaveText("60·20");
      await expect(value(race, "best")).toHaveText("15.234");
      await expect(value(race, "pace")).toHaveText("15.612+0.021");
      await expect(value(race, "stops")).toHaveText("3");
      await expect(value(race, "burn")).toHaveText("0.47gal/lap");
      await expect(value(race, "run")).toHaveText("41");
      await expect(slot(race, "laps")).toHaveAttribute("title", "Green · caution laps");
      await expect(slot(race, "pos")).toHaveAttribute("title", "Finished P4, started P12");
      await expect(slot(race, "burn")).toHaveAttribute("title", "Fuel burn per green lap, gallons");
      // Fuel used over the race is no longer a slot (#57).
      await expect(slot(race, "fuel")).toHaveCount(0);

      await editLibrary(page, (rows) => rows.map((r) => (r.session_id === 103 ? { ...r, overview: { recorded_laps: 82, final_position: 4 } } : r)));
      await page.reload();
      const bare = cell(page, 103);
      await expect(value(bare, "pos")).toHaveText("P4");
      for (const key of ["laps", "best", "pace", "stops", "burn", "run"]) await expect(value(bare, key)).toHaveText("—");
    });

    test("round 4: the finish change names its reference, the official start, never the first recorded position (#56)", async ({ page }) => {
      // Bristol's race: first recorded P14, gridded P12, finished P4.
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const race = cell(page, 103);
      await expect(race.locator(".sx-p.gain")).toHaveText("▲8");
      await expect(slot(race, "pos").locator(".sx-p.unit")).toHaveText("from P12");
      // The reference is muted text, the arrow the one colour.
      const muted = await slot(race, "pos").locator(".sx-p.unit").evaluate((el) => getComputedStyle(el).color);
      expect([GAIN, LOSS]).not.toContain(muted);
      // Without an official start there is nothing to measure against: no arrow, no reference.
      await editLibrary(page, (rows) => rows.map((r) => (r.session_id === 103 ? { ...r, overview: { ...r.overview, start_position: null } } : r)));
      await page.reload();
      await expect(value(cell(page, 103), "pos")).toHaveText("P4");
      await expect(cell(page, 103).locator(".sx-p.gain, .sx-p.loss")).toHaveCount(0);
      // Printed whole at every width.
      await page.unrouteAll({ behavior: "ignoreErrors" });
      await page.reload();
      const v = value(cell(page, 103), "pos");
      await expect(v).toHaveText("P4▲8from P12");
      expect(await v.evaluate((el) => el.scrollWidth > el.clientWidth + 1), "the finish is clipped").toBe(false);
    });

    test("round 4: practice and qualifying read P3 / 24, the official place over the field; the recorded place alone without it (#55)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      // Bristol's qualifying ended P2 when its recording stopped and was classified P5 of 24.
      await expect(quiet(cell(page, 102), "pos")).toHaveText("P5/ 24");
      await expect(slot(cell(page, 102), "pos")).toHaveAttribute("title", "Classified P5 of 24");
      await expect(quiet(cell(page, 101), "pos")).toHaveText("P5/ 24");
      // A practice-only event has no later recording: the recorded place alone, never over a field.
      const solo = page.getByRole("region", { name: "Lime Rock Park - Grand Prix" }).locator("li.session-row").first();
      await expect(quiet(solo, "pos")).toHaveText("P7");
      await expect(slot(solo, "pos")).toHaveAttribute("title", "Last recorded P7");
      // The race keeps its finish and change: no field size there.
      await expect(value(cell(page, 103), "pos")).not.toContainText("/");
    });

    test("round 4: P, Q and R badges and phase hues; the green count in green, caution in amber (#52, #53)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const row = bristol(page);
      await expect(row.locator(".sx-badge")).toHaveText(["P", "Q", "R"]);
      // The badge is the cell's top-left mark.
      for (const c of await row.locator("li.session-row").all()) {
        const [cb, bb] = [(await c.boundingBox())!, (await c.locator(".sx-badge").boundingBox())!];
        expect(bb.x - cb.x).toBeLessThan(24);
        expect(bb.y - cb.y).toBeLessThan(16);
      }
      const bg = (l: Locator) => l.evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(await Promise.all((await row.locator(".sx-badge").all()).map(bg))).toEqual([PHASE_P, PHASE_Q, PHASE_R]);
      const edge = (id: number) => cell(page, id).locator(".sx-cell-body").evaluate((el) => getComputedStyle(el).boxShadow);
      expect(await edge(101)).toContain(PHASE_P);
      expect(await edge(102)).toContain(PHASE_Q);
      expect(await edge(103)).toContain(PHASE_R);
      const colour = (l: Locator) => l.evaluate((el) => getComputedStyle(el).color);
      const laps = slot(cell(page, 103), "laps");
      await expect(laps.locator(".sx-p.green")).toHaveText("60");
      expect(await colour(laps.locator(".sx-p.green"))).toBe(GAIN);
      expect(await colour(laps.locator(".sx-p.caution"))).toBe(CAUTION);
      // The investigation's P · Q · R selector uses the same three hues.
      await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
      const seg = (name: string) => page.getByRole("group", { name: "Session" }).getByRole("button", { name, exact: true });
      await expect(seg("Race")).toBeVisible();
      expect(await seg("Race").evaluate((el) => getComputedStyle(el).backgroundColor)).toBe(PHASE_R);
      expect(await seg("Practice").evaluate((el) => getComputedStyle(el, "::after").backgroundColor)).toBe(PHASE_P);
      expect(await seg("Qualifying").evaluate((el) => getComputedStyle(el, "::after").backgroundColor)).toBe(PHASE_Q);
    });

    test("round 4: each event its own card with a gap, the setup lock in its top-right corner (#50, #51)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const cards = page.locator("section.sx-event");
      await expect(cards.first()).toBeVisible();
      const boxes = await Promise.all((await cards.all()).slice(0, 3).map(async (c) => (await c.boundingBox())!));
      for (let i = 1; i < boxes.length; i++) {
        const gap = boxes[i]!.y - (boxes[i - 1]!.y + boxes[i - 1]!.height);
        expect(gap).toBeGreaterThanOrEqual(10);
        expect(gap).toBeLessThanOrEqual(14);
      }
      for (const c of (await cards.all()).slice(0, 3)) {
        const st = await c.evaluate((el) => {
          const cs = getComputedStyle(el);
          return { sides: [cs.borderTopWidth, cs.borderLeftWidth, cs.borderRightWidth, cs.borderBottomWidth], radius: parseFloat(cs.borderTopLeftRadius) };
        });
        expect(st.sides).toEqual(["1px", "1px", "1px", "1px"]);
        expect(st.radius).toBeGreaterThan(0);
      }
      const card = bristol(page);
      const lock = card.getByRole("img", { name: "Fixed setup" });
      await expect(lock).toHaveAttribute("title", "Fixed setup");
      const [cb, lb] = [(await card.boundingBox())!, (await lock.boundingBox())!];
      expect(cb.x + cb.width - (lb.x + lb.width)).toBeLessThan(12);
      expect(lb.y - cb.y).toBeLessThan(12);
      // Closed for fixed, open for open: different glyphs.
      const open = page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).first().getByRole("img", { name: "Open setup" });
      await expect(open).toHaveCount(1);
      expect(await lock.locator("path").getAttribute("d")).not.toBe(await open.locator("path").getAttribute("d"));
      // The lock overlaps no figure.
      for (const c of await card.locator(".sx-slot").all()) {
        const b = (await c.boundingBox())!;
        const overlap = !(b.x + b.width <= lb.x || lb.x + lb.width <= b.x || b.y + b.height <= lb.y || lb.y + lb.height <= b.y);
        expect(overlap).toBe(false);
      }
      await expectNoHorizontalScroll(page);
    });

    test("practice: position, fastest lap, longest run without a reset and its pace loss; qualifying: position, lap 1, lap 2", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const practice = cell(page, 101);
      await expect(practice.locator(".sx-slot:not(.sx-head) .sx-l")).toHaveText(["Pos", "Best", "Long run", "Fall-off"]);
      await expect(quiet(practice, "pos")).toHaveText("P5/ 24");
      await expect(quiet(practice, "best")).toHaveText("15.500");
      await expect(quiet(practice, "run")).toHaveText("9laps");
      await expect(quiet(practice, "loss")).toHaveText("+0.21s");
      await expect(slot(practice, "run")).toHaveAttribute("title", "Longest run without a reset, laps");
      const qualify = cell(page, 102);
      await expect(qualify.locator(".sx-slot:not(.sx-head) .sx-l")).toHaveText(["Pos", "Lap 1", "Lap 2"]);
      await expect(quiet(qualify, "pos")).toHaveText("P5/ 24");
      await expect(quiet(qualify, "lap1")).toHaveText("15.610");
      await expect(quiet(qualify, "lap2")).toHaveText("15.410");
      // No burn, stops, cautions or pace trend in the preamble.
      for (const c of [practice, qualify]) for (const key of ["burn", "fuel", "stops", "laps", "pace"]) await expect(slot(c, key)).toHaveCount(0);
      // A faster end is a true minus; a run with too few clean laps and a missing second lap are dashes.
      await expect(quiet(cell(page, 104), "loss")).toHaveText("−0.08s");
      await expect(quiet(page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).nth(1).locator("li.session-row").first(), "loss")).toHaveText("—");
      await expect(quiet(cell(page, 112), "lap2")).toHaveText("—");
      await expect(quiet(cell(page, 112), "lap1")).not.toHaveText("—");
      await expectNoHorizontalScroll(page);
    });

    test("practice and qualifying are quieter than the race: 14 px secondary figures against the race's 15 px primary", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const style = (l: Locator) => l.evaluate((el) => ({ size: getComputedStyle(el).fontSize, color: getComputedStyle(el).color }));
      const race = await style(value(cell(page, 103), "best").locator(".sx-p").first());
      const prac = await style(quiet(cell(page, 101), "best").locator(".sx-qp").first());
      const qual = await style(quiet(cell(page, 102), "lap1").locator(".sx-qp").first());
      expect(race.size).toBe("15px");
      expect(prac.size).toBe("14px");
      expect(qual.size).toBe("14px");
      expect(prac.color).not.toBe(race.color);
      expect(qual.color).toBe(prac.color);
      // The phase names follow: the race's at the figure size, the preamble's at the secondary size.
      expect((await style(cell(page, 103).locator(".sx-phase"))).size).toBe("15px");
      expect((await style(cell(page, 101).locator(".sx-phase"))).size).toBe("14px");
    });

    test("two events compare on stint length, pace and fuel without opening either", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const url = page.url();
      const a = cell(page, 103);
      const b = cell(page, 110);
      await expect(a).toBeVisible();
      for (const c of [a, b]) {
        await c.scrollIntoViewIfNeeded();
        for (const key of ["run", "pace", "burn"]) {
          const v = value(c, key);
          await expect(v).not.toHaveText("—");
          // Printed whole: never cut to an ellipsis.
          const clipped = await v.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
          expect(clipped, `${key} is clipped`).toBe(false);
        }
      }
      await expect(value(a, "run")).toHaveText("41");
      await expect(value(b, "run")).toHaveText("38");
      await expect(value(b, "burn")).toHaveText("0.44gal/lap");
      expect(page.url()).toBe(url);
      if (wide) {
        // Both figures sit on one screen at once.
        await page.evaluate(() => window.scrollTo(0, 0));
        const [ab, bb] = [(await a.boundingBox())!, (await b.boundingBox())!];
        expect(ab.y + ab.height).toBeLessThanOrEqual(d.viewport.height);
        expect(bb.y + bb.height).toBeLessThanOrEqual(d.viewport.height);
      }
    });

    test("the large colourful tile treatment is gone: no fills, no glow, no bands", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      for (const gone of [".sx-big", ".sx-rb", ".sx-band", ".sx-win", ".sx-flag", ".sx-chg", ".sx-dot", ".sx-stats", ".sx-tile", ".sx-tiles", "section.sx-event > header"]) await expect(page.locator(gone), gone).toHaveCount(0);
      const { glow, fills } = await paint(page);
      expect(glow, "glow or shadow").toEqual([]);
      // The only saturated fills are the car stripes (identity) and the phase letters (the P, Q, R badges, #52); no panel fill.
      expect(fills.filter((f) => !/sx-stripe|sx-badge/.test(f))).toEqual([]);
      // A phase keeps its hue as an edge.
      const edge = (c: Locator) => c.locator(".sx-cell-body").evaluate((el) => getComputedStyle(el).boxShadow);
      expect(await edge(cell(page, 101))).toContain(PHASE_P);
      expect(await edge(cell(page, 103))).toContain(PHASE_R);
    });

    test("colour carries data only: gain or loss on a position change, amber on cautions, the finish is a plain number", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const colour = (l: Locator) => l.evaluate((el) => getComputedStyle(el).color);
      const race = cell(page, 103);
      await expect(race.locator(".sx-p.gain")).toHaveText("▲8");
      expect(await colour(race.locator(".sx-p.gain"))).toBe(GAIN);
      await expect(race.locator(".sx-p.caution")).toHaveText("20");
      expect(await colour(race.locator(".sx-p.caution"))).toBe(CAUTION);
      const primary = await colour(race.locator(".sx-slot[data-slot=pos] .sx-p").first());
      expect(primary).not.toBe(GAIN);
      expect(await colour(race.locator(".sx-slot[data-slot=best] .sx-p"))).toBe(primary);
      // The pace trend is above +0.020 s/lap and still carries no colour; a figure with a unit is plain.
      expect(await colour(race.locator(".sx-slot[data-slot=pace] .sx-p").last())).not.toBe(LOSS);
      expect(await colour(race.locator(".sx-slot[data-slot=run] .sx-p"))).toBe(primary);
      // No cautions: no amber. No change: no gain or loss.
      await expect(cell(page, 101).locator(".sx-p.caution")).toHaveCount(0);
      await expect(cell(page, 101).locator(".sx-p.gain, .sx-p.loss")).toHaveCount(0);
      // A win is a plain number, no flag.
      await expect(value(cell(page, 110), "pos")).toHaveText("P1▲2from P3");
      await expect(cell(page, 110).locator(".sx-flag, .sx-win")).toHaveCount(0);
      // The fastest lap is not coloured across phases: qualifying's lap 2 holds NH's event best, marked by weight in the plain
      // secondary colour of every preamble figure.
      await expect(quiet(cell(page, 109), "lap2")).toHaveAttribute("data-event-best", "true");
      await expect(value(cell(page, 110), "best")).not.toHaveAttribute("data-event-best", "true");
      expect(await colour(quiet(cell(page, 109), "lap2").locator(".sx-qp"))).toBe(await colour(quiet(cell(page, 109), "lap1").locator(".sx-qp")));
      expect(await quiet(cell(page, 109), "lap2").evaluate((el) => getComputedStyle(el).fontWeight)).toBe("700");
      // At most three coloured figures in any phase cell: the change, the green count, the caution count.
      const counts = await page.locator("li.session-row").evaluateAll((cells, [g, l, c]) => cells.map((el) => [...el.querySelectorAll(".sx-p")].filter((p) => [g, l, c].includes(getComputedStyle(p).color)).length), [GAIN, LOSS, CAUTION]);
      expect(Math.max(...counts)).toBeLessThanOrEqual(3);
    });

    test("a place lost reads in the loss colour with its figure", async ({ page }) => {
      await editLibrary(page, (rows) => rows.map((r) => (r.session_id === 103 ? { ...r, overview: { ...r.overview, start_position: 7, final_position: 9 } } : r)));
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const race = cell(page, 103);
      await expect(race.locator(".sx-p.loss")).toHaveText("▼2");
      expect(await race.locator(".sx-p.loss").evaluate((el) => getComputedStyle(el).color)).toBe(LOSS);
      await expect(slot(race, "pos")).toHaveAttribute("title", "Finished P9, started P7");
    });

    test("owner's type minimums: 16 px names, 15 px figures, 14 px labels and secondary lines; the row may grow", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const race = cell(page, 103);
      await expect(race).toBeVisible();
      const px = (l: Locator) => l.evaluate((el) => getComputedStyle(el).fontSize);
      expect(await px(value(race, "best").locator(".sx-p").first())).toBe("15px");
      expect(await px(race.locator(".sx-slot[data-slot=best] .sx-l"))).toBe("14px");
      expect(await px(race.locator(".sx-slot[data-slot=burn] .sx-p.unit"))).toBe("14px");
      expect(await px(race.locator(".sx-slot[data-slot=pos] .sx-p.unit"))).toBe("14px");
      expect(await px(race.locator(".sx-badge"))).toBe("14px");
      expect(await px(bristol(page).getByRole("heading", { name: "Bristol Motor Speedway" }))).toBe("16px");
      expect(await px(bristol(page).locator(".sx-meta span").first())).toBe("14px");
      expect(await px(bristol(page).locator(".sx-car span").first())).toBe("14px");
      const box = (await race.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(80);
      // Larger type lets the row grow (the plan's about 5.5 rem is a floor); it must not run away. On a phone the race's header
      // takes its own line above three rows of figures, so a figure is never cut (round 4: `from P12`, `gal/lap`).
      expect(box.height).toBeLessThanOrEqual(phone ? 180 : 150);
      // The silhouette is about 1.5 rem tall.
    });

    test("Fixed and Open are a lock glyph, not a chip; a phase that differs carries its own; no status-normal chip", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const lock = (l: Locator) => l.locator(".sx-card-lock");
      await expect(lock(bristol(page))).toHaveAttribute("aria-label", "Fixed setup");
      await expect(lock(page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).first())).toHaveAttribute("aria-label", "Open setup");
      const hampshire = page.getByRole("region", { name: "New Hampshire Motor Speedway - Oval" });
      await expect(lock(hampshire)).toHaveAttribute("aria-label", "Fixed setup");
      await expect(cell(page, 108).locator(".sx-head .sx-lock")).toHaveAttribute("aria-label", "Open setup");
      await expect(cell(page, 109).locator(".sx-head .sx-lock")).toHaveCount(0);
      // The words are never drawn: only the glyph, its meaning on hover.
      await expect(page.locator(".sx-events .chip-constraint")).toHaveCount(0);
      await expect(page.locator(".sx-events").getByText(/^(Fixed|Open)$/)).toHaveCount(0);
      // An event with no recorded setup type shows none. The series is not drawn; it is in the car's tooltip.
      await expect(bristol(page).locator(".sx-car span[title]")).toHaveAttribute("title", "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series");
      await expect(bristol(page).getByText("NASCAR Xfinity Series")).toHaveCount(0);
      const none = page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).nth(1);
      await expect(none.locator(".sx-lock")).toHaveCount(0);
      await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" }).getByText("Global Mazda MX-5 Cup")).toHaveCount(1);
      // Only exceptions are chips; none of the retired status chips.
      const kinds = await page.locator(".sx-events .chip").evaluateAll((els) => [...new Set(els.map((e) => [...e.classList].find((c) => /^chip-(constraint|exception|action)$/.test(c)) ?? "other"))].sort());
      expect(kinds).toEqual(["chip-exception"]);
      for (const word of ["Analyzed", "Values known", "Recording"]) await expect(page.getByText(word, { exact: true })).toHaveCount(0);
      expect(await page.locator(".sx-events .chip").evaluateAll((els) => els.map((e) => getComputedStyle(e).borderRadius))).not.toContain("999px");
    });

    test("no Resume banner; only the exceptions are named, each with its verb", async ({ page }) => {
      await page.addInitScript((c) => localStorage.setItem(`coach.last-investigation.${c}`, JSON.stringify({ session_id: 103, analysis_id: "an-103", view: "traces", comparison_id: "c1" })), DRIVER);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      await expect(page.getByText("Resume")).toHaveCount(0);
      const notAnalyzed = cell(page, 106);
      await expect(notAnalyzed.locator(".chip-exception")).toContainText("Not analyzed");
      await expect(notAnalyzed.getByRole("button", { name: "Analyze" })).toBeVisible();
      await expect(value(notAnalyzed, "recorded")).toHaveText("83");
      await expect(notAnalyzed.locator(".sx-slot[data-slot=pos]")).toHaveCount(0);
      const failed = cell(page, 108);
      await expect(failed.locator(".chip-exception")).toContainText("Failed · worker crash");
      await expect(failed.getByRole("button", { name: "Retry" })).toBeVisible();
      await expect(cell(page, 113).locator(".chip-exception")).toHaveText("Deferred · exceeds memory budget");
      // A ready phase shows no state at all and is the only kind that opens.
      await expect(cell(page, 103).locator(".chip-exception")).toHaveCount(0);
      await expect(cell(page, 103).getByRole("link")).toHaveCount(1);
      await expect(notAnalyzed.getByRole("link")).toHaveCount(0);
      await expect(page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first()).toBeVisible();
      await expect(page.getByRole("link", { name: /^Race, Indianapolis Raceway Park - Oval/ })).toHaveCount(0);
      if (d.touch) await expectTouchTargets(page);
    });

    test("analyze queues the job and the phase opens when the server says so", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const waiting = page.locator("li.session-row[data-state=none]");
      await expect(page.locator("li.session-row").first()).toBeVisible();
      const before = await waiting.count();
      expect(before).toBeGreaterThan(0);
      await waiting.first().getByRole("button", { name: "Analyze" }).click();
      await expect(waiting).toHaveCount(before - 1, { timeout: 6000 });
      await expect(page.locator("li.session-row[data-state=ready]").first()).toBeVisible();
    });

    test("the filters sit in the heading at 1440 and collapse to one button below that; no Library control, no Clear beside the heading", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      await expect(page.locator(".filter-bar.fb-chips")).toHaveCount(0);
      if (wide) {
        await expect(bar(page)).toHaveAttribute("data-layout", "inline");
        const head = (await bar(page).locator(".fb-row").boundingBox())!;
        const h1 = (await bar(page).getByRole("heading", { name: "Sessions" }).boundingBox())!;
        for (const name of ["Car", "Track", "Series", "Session", "Setup"]) {
          const b = (await bar(page).locator(".fb-row").getByRole("button", { name: new RegExp(`^${name}`) }).boundingBox())!;
          expect(b.x, `${name} is right of the title`).toBeGreaterThan(h1.x + h1.width);
          expect(b.y + b.height, `${name} is on the heading row`).toBeLessThanOrEqual(head.y + head.height + 1);
        }
        await expect(page.getByRole("button", { name: /^Filters/ })).toHaveCount(0);
      } else {
        await expect(bar(page)).toHaveAttribute("data-layout", "collapsed");
        await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
        await expect(page.getByRole("button", { name: /^Car/ })).toHaveCount(0);
        await page.getByRole("button", { name: "Filters", exact: true }).click();
        const surface = page.getByRole("dialog", { name: "Filters" });
        await expect(surface).toHaveClass(d.touch ? /fb-sheet/ : /fb-pop/);
        for (const name of ["Car", "Track", "Series", "Session", "Setup"]) await expect(surface.getByRole("button", { name: new RegExp(`^${name}`) })).toBeVisible();
      }
      await expect(page.locator(".lib-ctl")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Lab", exact: true })).toHaveCount(0);
      await expect(page.locator(".filter-clear")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
    });

    test("searchable multi-select filters; the button carries the choice and clears from inside its list", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      await expect(page.getByRole("button", { name: "Tests" })).toHaveCount(0);
      const surface = await openFilters(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
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
      await closeFilters(page);
      await expect(page.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
      await expect(page.locator("section.sx-event")).toHaveCount(1);
      await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" })).toBeVisible();
      if (!phone && !wide) await expect(page.getByRole("button", { name: "Filters · 1" })).toBeVisible();

      // Clear lives inside the list.
      const again = await openFilters(page);
      await again.getByRole("button", { name: /^Car · Global Mazda/ }).click();
      await page.locator(".ms-clear-row").click();
      await page.keyboard.press("Escape");
      await closeFilters(page);
      expect(await page.locator("section.sx-event").count()).toBeGreaterThan(3);
      await expectNoHorizontalScroll(page);
      if (d.touch) await expectTouchTargets(page);
    });

    test("filters combine across and within; the Session filter offers practice, qualifying and race", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      let surface = await openFilters(page);
      await surface.getByRole("button", { name: /^Session/ }).click();
      await expect(page.locator(".ms-opt-label")).toHaveText(["Practice", "Qualifying", "Race"]);
      await expect(page.getByRole("combobox", { name: "Search session" })).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await expect(page.getByRole("option", { name: /Race/ })).toHaveAttribute("aria-selected", "true");
      await page.keyboard.press("Escape");
      await closeFilters(page);
      await expect(page.locator("li.session-row")).toHaveCount(4);
      await expect(page.locator("li.session-row .sx-phase")).toHaveText([/Race$/, /Race$/, /Race$/, /Race$/]);
      // Races only: one column, the race panel as wide as the phases area inside its gutter.
      const only = (await page.locator("li.session-row").first().boundingBox())!;
      const inner = await page.locator(".sx-cells").first().evaluate((el) => {
        const cs = getComputedStyle(el);
        return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      });
      expect(Math.abs(only.width - inner)).toBeLessThan(2);

      await choose(page, "Setup", /Fixed/);
      await expect(page.locator("li.session-row")).toHaveCount(2);
      await choose(page, "Track", /New Hampshire/);
      await expect(page.locator("li.session-row")).toHaveCount(1);
      await choose(page, "Series", /Global Mazda/);
      await expect(page.getByRole("heading", { name: "No matching sessions" })).toBeVisible();
      await page.getByRole("button", { name: "Clear filters" }).click();
      expect(await page.locator("section.sx-event").count()).toBeGreaterThan(3);
      surface = await openFilters(page);
      await closeFilters(page);
    });

    test("the selection is saved with the driver's prefs and comes back on another device", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await choose(page, "Car", /Global Mazda/);
      await expect.poll(async () => (await mockState(request)).writes.filter((w) => w.path.endsWith("/prefs")).map((w) => (w.body as { session_filters?: { cars: string[] } }).session_filters?.cars)).toContainEqual(["mx5-cup"]);
      // The device's own copy is only a hint for the first paint: without it the prefs still restore the chips.
      await page.evaluate((c) => localStorage.removeItem(`coach.sessions.filters.${c}`), DRIVER);
      await page.reload();
      await expect(page.locator("section.sx-event")).toHaveCount(1);
      const surface = await openFilters(page);
      await expect(surface.getByRole("button", { name: /^Car · Global Mazda MX-5 Cup/ })).toBeVisible();
    });

    test("Library: the driver's own by default; the lab only by ?library=lab in the URL, never saved", async ({ page }) => {
      const requested: string[] = [];
      page.on("request", (r) => {
        if (/\/api\/v1\/drivers\/\d+\/sessions(\?|$)/.test(r.url())) requested.push(new URL(r.url()).searchParams.get("library") ?? "(none)");
      });
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      await expect(cell(page, 900)).toHaveCount(0);
      expect([...new Set(requested)]).toEqual(["(none)"]);
      await page.goto(hash(`/d/${DRIVER}/sessions?library=lab`));
      await expect(cell(page, 900)).toBeVisible();
      await expect(cell(page, 901)).toBeVisible();
      expect(requested).toContain("lab");
      await expect(page.getByRole("region", { name: BRISTOL })).toHaveCount(0);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page)).toBeVisible();
      await expect(cell(page, 900)).toHaveCount(0);
      expect(await page.evaluate(() => JSON.stringify({ ...localStorage }).toLowerCase())).not.toContain("lab");
    });

    test("a session opens on the tab it was left on, without being asked", async ({ page }) => {
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

    test("the silhouette is about 1.5 rem and comes from the track's geometry, once per layout", async ({ page }) => {
      const asked = await serveGeometry(page);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const sil = bristol(page).getByTestId("silhouette");
      await expect(sil).toBeVisible();
      await expect(sil.locator("path")).toHaveAttribute("d", /^M[\d.,L-]+Z$/);
      const box = (await sil.boundingBox())!;
      expect(box.height).toBeGreaterThanOrEqual(22);
      expect(box.height).toBeLessThanOrEqual(26);
      for (const row of await page.locator("section.sx-event").all()) await row.scrollIntoViewIfNeeded();
      await expect.poll(() => asked.length).toBe(4);
      await page.waitForLoadState("networkidle");
      // Bristol, Indianapolis, Lime Rock and New Hampshire: four layouts across six events.
      expect(new Set(asked).size).toBe(asked.length);
      expect(asked.length).toBeLessThanOrEqual(4);
      await expectNoHorizontalScroll(page);
    });

    test("a track without usable geometry draws no silhouette and the row still reads", async ({ page }) => {
      await page.route("**/api/v1/analyses/*/track", (route) =>
        route.fulfill({ json: { analysis_id: "x", projection_version: 1, schema_version: 1, profile_version: "", coordinate_space: "normalized_local_vector", status: "usable", main_path: { pct: [0, 0.5], x: [0.1, 0.9], y: [0.5, 0.5] }, provenance: {} } }),
      );
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(bristol(page).getByRole("heading", { name: "Bristol Motor Speedway" })).toBeVisible();
      await expect(page.getByTestId("silhouette")).toHaveCount(0);
    });

    test("older events load in batches", async ({ page }) => {
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

    test("loaded rows stay on screen when the server drops, with a banner", async ({ page, context, request }) => {
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

// Acceptance screenshots (round 4 S1: web/target/round4/S1): the list at the three viewports, one event's card, the exceptions.
const dir = process.env.COACH_SCREENS_DIR;
test.describe("screenshots", () => {
  test.skip(!dir, "set COACH_SCREENS_DIR to write the screenshots");
  for (const device of ROUND2_DEVICES) {
    appTest(device, (d) => {
      const wide = d.viewport.width >= 1400;
      const phone = d.viewport.width < 700;
      test(`sessions: screenshots at ${d.viewport.width}`, async ({ page }) => {
        await serveGeometry(page);
        await page.goto(hash(`/d/${DRIVER}/sessions`));
        await expect(page.getByTestId("silhouette").first()).toBeVisible();
        await page.waitForLoadState("networkidle");
        await page.screenshot({ path: `${dir}/sessions-${d.viewport.width}.png` });
        if (phone) await page.screenshot({ path: `${dir}/sessions-${d.viewport.width}-full.png`, fullPage: true });

        // One event close up: the three blocks.
        await bristol(page).screenshot({ path: `${dir}/sessions-${d.viewport.width}-row.png` });

        // The not-ready phases (Not analyzed, Failed, Deferred).
        await cell(page, 108).scrollIntoViewIfNeeded();
        await page.waitForTimeout(150);
        await page.screenshot({ path: `${dir}/sessions-${d.viewport.width}-exceptions.png` });

        // The investigation's P · Q · R selector in the same phase hues (#52).
        await page.goto(hash(`/d/${DRIVER}/s/103/race`));
        const pqr = page.getByRole("group", { name: "Session" });
        await expect(pqr).toBeVisible();
        await pqr.screenshot({ path: `${dir}/investigation-pqr-${d.viewport.width}.png` });
      });
    });
  }
});
