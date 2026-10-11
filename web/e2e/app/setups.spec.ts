import { type Page, expect, test } from "@playwright/test";
import { DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

const CAR = "NASCAR Xfinity Toyota Supra";
/** Screenshots of round 5, task U2: web/target/round5/U2/<name>-<viewport width>.png. */
const SHOTS = "target/round5/U2";

const section = (page: Page, car: string) => page.getByRole("region", { name: car, exact: true });

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    const phone = d.viewport.width < 700;
    const shot = async (page: Page, name: string) => page.screenshot({ path: `${SHOTS}/${name}-${d.viewport.width}.png`, fullPage: false });

    test("no Library control; the lab library only by ?library=lab in the URL", async ({ page }) => {
      const asked: string[] = [];
      page.on("request", (r) => {
        if (/\/api\/v1\/drivers\/\d+\/setups(\?|$)/.test(r.url())) asked.push(new URL(r.url()).searchParams.get("library") ?? "(none)");
      });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, CAR)).toBeVisible();
      await expect(page.getByText("Library")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Lab" })).toHaveCount(0);
      expect([...new Set(asked)]).toEqual(["(none)"]);
      await page.goto(hash(`/d/${DRIVER}/setups?library=lab`));
      await expect(page.getByText("bot sweep.sto")).toBeVisible();
      expect(asked).toContain("lab");
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, CAR)).toBeVisible();
      await expect(page.getByText("bot sweep.sto")).toHaveCount(0);
    });

    test("detail: the name once, raced count and first seen, no source or file-name chip, the file lower down, one tuning entry", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const head = page.locator("header.d-head");
      await expect(head.getByRole("heading", { name: "Bristol race v3.sto", level: 1 })).toBeVisible();
      expect(((await head.textContent())!.match(/Bristol race v3\.sto/gi) ?? []).length, "the name appears once in the header").toBe(1);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(head.locator(".raced")).toHaveText(/Raced 3×/);
      await expect(head).toContainText(/First seen [A-Z][a-z]{2} \d+, 2026/);
      await expect(head).toContainText(CAR);
      await expect(head.locator(".tag, .vk, .vu, .chip")).toHaveCount(0);
      await expect(page.getByLabel("Sources")).toHaveCount(0);
      await expect(head.getByText("Recording")).toHaveCount(0);
      // the file name identifies the artifact lower down
      const file = page.getByRole("region", { name: "File" });
      await expect(file).toContainText("nascar-xfinity/Bristol race v3.sto");
      const order = await page.evaluate(() => {
        const y = (sel: string) => document.querySelector(sel)!.getBoundingClientRect().top + window.scrollY;
        return { head: y("header.d-head"), file: y("[aria-label=File]"), values: y("[aria-label='Setup values']") };
      });
      expect(order.file).toBeGreaterThan(order.values);
      // one tuning entry
      await expect(page.getByRole("button", { name: "Tune this setup" })).toHaveCount(1);
      await expect(page.getByText("Progressive tuning")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      await shot(page, "setup-detail");
    });

    test("detail: a raced setup without a count says Raced alone; a setup with no decoded values says No values", async ({ page }) => {
      await page.route(/\/api\/v1\/setups\/1$/, async (route) => {
        const res = await route.fetch();
        const body = await res.json();
        delete body.setup.raced_count;
        body.values = {};
        await route.fulfill({ response: res, json: body });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const head = page.locator("header.d-head");
      await expect(head.locator(".raced")).toHaveText(/^\s*Raced\s*$/);
      await expect(page.getByRole("region", { name: "Setup values" })).toContainText("No values");
      await expect(page.getByText("Values unknown")).toHaveCount(0);
    });

    test("detail: Versions is collapsed, lists versions by date with changed counts, and a version opens to its changed keys and runs", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const compares: string[] = [];
      page.on("request", (r) => {
        if (r.method() === "POST" && /\/setups\/compare$/.test(new URL(r.url()).pathname)) compares.push(r.url());
      });
      const versions = page.getByTestId("versions");
      const toggle = versions.getByRole("button", { name: /^Versions/ });
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(versions.locator("[data-version]").first()).toBeHidden();
      await expect(versions).toContainText("2");
      expect(compares, "nothing is compared until the section opens").toEqual([]);
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      const v2 = versions.locator("[data-version='2']");
      const v1 = versions.locator("[data-version='1']");
      await expect(v2).toBeVisible();
      await expect(v2).toHaveAttribute("aria-current", "true");
      await expect(v2).toContainText(/Sep \d+, 2026/);
      await expect(v2).toContainText("1 changed");
      await expect(v1).toContainText(/Sep \d+, 2026/);
      await expect(v1).not.toContainText("changed");
      expect(compares.length, "one comparison per version pair").toBe(1);
      // newest first
      const order = await versions.locator("[data-version]").evaluateAll((els) => els.map((e) => e.getAttribute("data-version")));
      expect(order).toEqual(["2", "1"]);
      await v2.getByRole("button").first().click();
      const changes = v2.getByRole("table", { name: "Changed settings" });
      await expect(changes.locator("tbody tr")).toHaveCount(1);
      const row = changes.locator("tbody tr").first();
      await expect(row.getByRole("rowheader")).toHaveText("Chassis · Left front · Camber");
      await expect(row.locator("td").nth(0)).toHaveText("-3.4");
      await expect(row.locator("td").nth(1)).toHaveText("-3.2");
      await expect(row.locator("td").nth(2)).toHaveText("deg");
      const runs = v2.getByRole("region", { name: "Recorded runs" });
      await expect(runs.getByRole("link", { name: "Session 103" })).toHaveAttribute("href", /#\/d\/\d+\/s\/103\/race/);
      // the viewed version has no Open link; the other has one
      await expect(v2.getByRole("link", { name: /^Open version/ })).toHaveCount(0);
      await v1.getByRole("button").first().click();
      await expect(v1.getByRole("region", { name: "Recorded runs" }).getByRole("link", { name: "Session 112" })).toBeVisible();
      await expect(v1.getByRole("link", { name: "Open version 1" })).toHaveAttribute("href", /#\/d\/\d+\/setups\/4$/);
      await expect(v1.getByRole("table")).toHaveCount(0);
      // no effect claim and no tire life
      await expect(versions).not.toContainText(/pace|tire life|faster|slower/i);
      await shot(page, "setup-versions");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("detail groups values, keeps readouts and run readings apart, and compares with changed and unknown first", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      const values = page.getByRole("region", { name: "Setup values" });
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).toContainText("-3.2 deg");
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).not.toContainText("Corner weight");
      const readouts = values.getByRole("region", { name: "Garage readouts" });
      await expect(readouts.getByRole("heading", { name: "Garage readouts" })).toBeVisible();
      await expect(readouts).toContainText("Corner weight");
      await expect(readouts).toContainText("Ride height");
      const readings = values.getByRole("region", { name: "Run readings" });
      await expect(readings).toContainText("Last hot pressure");
      await expect(readings).not.toContainText("Corner weight");
      await expect(values.getByRole("region", { name: "Tires aero · Left front tire" }).first()).not.toContainText("Last hot pressure");
      await expect(page.getByLabel("With").locator("option").filter({ hasText: "Bristol race v3.sto" })).toContainText(/Sep \d+, 2026/);
      await expect(page.getByRole("region", { name: "Sightings" }).getByText("Unmodified").first()).toBeVisible();
      await expect(page.getByRole("region", { name: "Sightings" }).getByRole("link", { name: "Session 103" })).toBeVisible();

      await page.getByLabel("With").selectOption("2");
      const summary = page.getByTestId("compare-summary");
      await expect(summary).toContainText("3 changed");
      await expect(summary).toContainText("1 unknown");
      await expect(summary).toContainText("1 readout moved");
      const heads = page.locator("table thead th");
      await expect(heads.nth(1)).toContainText("Bristol race v3.sto");
      await expect(heads.nth(1)).toContainText(/Sep \d+, 2026/);
      await expect(heads.nth(2)).toContainText("Bristol race v2.sto");
      const rowsOf = page.locator("table tbody tr");
      await expect(rowsOf.first()).toHaveAttribute("data-kind", "changed");
      await expect(page.locator("tr[data-kind=identical]")).toHaveCount(0);
      await expect(rowsOf.first().locator("th")).toHaveText(/^[A-Z][^.]* · /);
      await page.getByRole("button", { name: "Show all values" }).click();
      await expect(page.locator("tr[data-kind=identical]").first()).toBeVisible();
      await expect(page.locator("tr[data-kind=unknown]")).toContainText("Track bar height");
      const order = await page.locator("table tbody tr").evaluateAll((trs) => trs.map((tr) => (tr.classList.contains("group-head") ? "head" : `${tr.getAttribute("data-kind")}${tr.getAttribute("data-readout") ? "-readout" : ""}`)));
      const head = order.indexOf("head");
      expect(head).toBeGreaterThan(0);
      expect(order.slice(0, head)).not.toContain("identical-readout");
      expect(order.slice(head + 1).every((k) => k.endsWith("-readout"))).toBe(true);
      expect(order.slice(0, head).filter((k) => k === "identical").length).toBeGreaterThan(0);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("compare offers only setups that compare, and gives the others a reason", async ({ page }) => {
      await page.route(/\/api\/v1\/drivers\/\d+\/setups\?.*car_path=/, async (route) => {
        const res = await route.fetch();
        const body = await res.json();
        body.setups.push({ ...body.setups[0], setup_id: 95, names: ["Imported sheet.htm"], sources: ["htm_export"], observation_count: 1, raced: false });
        await route.fulfill({ response: res, json: body });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const option = page.getByLabel("With").locator("option").filter({ hasText: "Imported sheet.htm" });
      await expect(option).toHaveAttribute("disabled", "");
      await expect(option).toContainText("Export only");
      await expect(page.getByLabel("With").locator("option").filter({ hasText: "Bristol race v2.sto" })).not.toHaveAttribute("disabled", "");
    });

    test("a note on a setup is queued, saved, and listed", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await page.getByPlaceholder("Add a note").fill("Loose on exit at lap 40");
      await page.getByRole("button", { name: "Add note" }).click();
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Loose on exit at lap 40")).toBeVisible();
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      expect((await mockState(request)).notes).toHaveLength(1);
      await page.getByRole("button", { name: "Edit" }).click();
      await page.getByLabel("Edit note").fill("Loose on exit, laps 40 to 60");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByText("Loose on exit, laps 40 to 60")).toBeVisible();
      await page.getByRole("button", { name: "Delete" }).click();
      await page.getByRole("button", { name: "Confirm delete" }).click();
      await expect(page.getByText("Loose on exit, laps 40 to 60")).toHaveCount(0);
    });

    test("tune this setup opens tuning for the recording that used it", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const sessionRequests: string[] = [];
      page.on("request", (r) => {
        if (/\/api\/v1\/sessions\/\d+$/.test(new URL(r.url()).pathname)) sessionRequests.push(r.url());
      });
      await page.getByRole("button", { name: "Tune this setup" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/tuning/an-103$`));
      await expect(page.getByRole("heading", { name: "Bristol Motor Speedway - Oval" })).toBeVisible();
      expect(sessionRequests, "the setup's observation carries the analysis id").toEqual([]);
    });

    test("find a baseline: suggestions only, the car folder is sent, then a starting point with its cautions", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/baseline`));
      const find = page.getByRole("button", { name: "Find starting point" });
      await expect(find).toBeDisabled();
      await page.getByLabel("Car", { exact: true }).fill("Ferrari");
      await page.getByLabel("Track and layout").fill("Martinsville Speedway");
      await expect(find).toBeEnabled();
      await expect(page.getByText("from your recordings")).toHaveCount(0);
      await page.getByLabel("Car", { exact: true }).fill(CAR);
      await page.getByLabel("Track and layout").fill("Bristol Motor Speedway - Oval");
      await page.getByLabel("Season").fill("26S4");
      await find.click();
      const result = page.getByTestId("baseline-result");
      await expect(result).toBeVisible();
      await expect(result).toContainText("Bristol race v3");
      await expect(result).toContainText("New Hampshire");
      await expect(result).toContainText("Source files unchanged");
      await expect(result.locator("[data-candidate=race]")).toContainText("Opaque");
      await expect(result.locator("[data-candidate=race]")).toContainText("bbbbbbbb");
      await expect(result.locator("[data-candidate=qualifying]")).toContainText("None");
      await expect(result.getByRole("list", { name: "Checks" }).getByRole("listitem")).toHaveCount(4);
      await expect(result.getByRole("list", { name: "Cautions" })).toContainText("Transfer the tuning logic");
      const writes = (await mockState(request)).writes;
      expect(writes.at(-1)!.body).toMatchObject({ car: "nascar-xfinity", track: "Bristol Motor Speedway - Oval", season: "2026S4" });
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("find a baseline: no matching setup file is not a starting point", async ({ page }) => {
      await page.route(/\/api\/v1\/jobs\/\d+$/, async (route) => {
        const res = await route.fetch();
        const job = await res.json();
        if (job.state === "succeeded") job.result = { ok: false, status: "needs-baseline-export-or-research", package_id: "p", package_path: "p", context: {}, baseline: null, baseline_confirmation: { status: "none", confirmed: false, reason: "No match" }, qualifying: null, donor: { donor: "New Hampshire", reason: "flat" }, simulator_loadable_setup_produced: false, source_setup_files_modified: false };
        await route.fulfill({ response: res, json: job });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/baseline`));
      await page.getByLabel("Car", { exact: true }).fill(CAR);
      await page.getByLabel("Track and layout").fill("Bristol Motor Speedway - Oval");
      await page.getByRole("button", { name: "Find starting point" }).click();
      await expect(page.getByText("No setup file for this car, track and season.")).toBeVisible();
      await expect(page.getByTestId("baseline-result")).toHaveCount(0);
      await expect(page.getByText("Source files unchanged")).toHaveCount(0);
      await expect(page.getByText("New Hampshire")).toHaveCount(0);
    });
  });
}
