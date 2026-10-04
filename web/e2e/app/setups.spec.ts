import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

const CAR = "NASCAR Xfinity Toyota Supra";

/** A second car in the library, added to the mock's answer, so grouping and the car filter have something to separate. */
async function withSecondCar(page: import("@playwright/test").Page) {
  await page.route(/\/api\/v1\/drivers\/\d+\/setups(\?.*)?$/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const at = "2026-09-29T12:00:00Z";
    body.setups.push({
      setup_id: 90,
      customer_id: DRIVER,
      car_path: "mx5-cup",
      car_name: "Global Mazda MX-5 Cup",
      names: ["Lime Rock fixed.sto"],
      sto_files: [],
      sources: ["recording"],
      observation_count: 4,
      raced: true,
      first_seen_at: at,
      last_seen_at: at,
    });
    await route.fulfill({ response: res, json: body });
  });
}

for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.name.startsWith("phone");

    test("library groups setups by car and tells values known from values unknown", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(page.getByRole("heading", { name: "Setups", exact: true })).toBeVisible();
      const car = page.getByRole("region", { name: CAR });
      await expect(car).toBeVisible();
      await expect(car.getByRole("heading", { name: CAR })).toBeVisible();
      await expect(car).toContainText("4 setups · 2 raced");
      // one entry per name: v3 (two versions), v2, the garage capture, and the three files whose values are unknown
      await expect(car.locator("[data-entry]")).toHaveCount(6);
      await expect(car.locator("[data-entry=setup]")).toHaveCount(2);
      await expect(car.getByRole("link", { name: "Bristol race v3.sto", exact: true })).toHaveCount(1);
      await expect(car.locator("[data-entry=file]")).toHaveCount(3);
      await expect(car.locator("[data-entry=file]").first()).toContainText("Values unknown");
      await expect(car.locator("[data-entry=file]").first()).toContainText("Imported 1.sto");
      await expect(car.locator(".vk")).toHaveCount(3);
      await expect(car.locator(".vu")).toHaveCount(3);
      await expect(car.locator("[data-entry=captures]")).toContainText("Garage capture");
      // the source of a setup is for the detail view
      await expect(page.getByText("Export", { exact: true })).toHaveCount(0);
      await expect(page.getByText("Recording", { exact: true })).toHaveCount(0);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("a file name with other values is one entry; its versions open as rows with their dates", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = page.getByRole("region", { name: CAR });
      const v3 = car.locator("[data-entry=setup]").filter({ hasText: "Bristol race v3.sto" });
      await expect(v3).toContainText("2 versions");
      await expect(v3).toContainText("6×");
      await expect(v3.locator("[data-version]")).toHaveCount(0);
      await v3.getByRole("button", { name: /2 versions/ }).click();
      const versions = v3.getByRole("list", { name: "Bristol race v3.sto versions" });
      await expect(versions.locator("[data-version]")).toHaveCount(2);
      await expect(versions.locator("[data-version='2']")).toContainText("Latest");
      await expect(versions.locator("[data-version='1']")).not.toContainText("Latest");
      await expect(versions.locator("[data-version='2']")).toContainText(/Sep \d+, 2026/);
      await expect(versions.locator("[data-version='2']").getByRole("link")).toHaveAttribute("href", /#\/d\/\d+\/setups\/1$/);
      await expect(car.locator("[data-entry=setup]").filter({ hasText: "Bristol race v2.sto" })).not.toContainText("versions");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("filters by raced and by name", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = page.getByRole("region", { name: CAR });
      await page.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Raced", exact: true }).click();
      await expect(car.locator("[data-entry]")).toHaveCount(2);
      await expect(car.locator("[data-entry=file]")).toHaveCount(0);
      await expect(car).toContainText("2 setups · 2 raced");
      await page.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Not raced" }).click();
      await expect(car.locator("[data-entry=setup]")).toHaveCount(1);
      await expect(car.locator("[data-entry=file]")).toHaveCount(3);
      await page.getByRole("group", { name: "Raced" }).getByRole("button", { name: "All" }).click();
      await page.getByPlaceholder("Name").fill("v2");
      await expect(car.locator("[data-entry]")).toHaveCount(1);
      await expect(car).toContainText("Bristol race v2.sto");
    });

    test("the car filter is a searchable multi-select with removable chips", async ({ page }) => {
      await withSecondCar(page);
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(page.getByRole("region", { name: "Global Mazda MX-5 Cup" })).toBeVisible();
      await expect(page.getByRole("region", { name: CAR })).toBeVisible();
      // colour is the car's: two cards, two stripe colours
      const colours = await page.locator(".s-car").evaluateAll((els) => els.map((e) => getComputedStyle(e).getPropertyValue("--car").trim()));
      expect(new Set(colours).size).toBe(2);
      await page.getByRole("button", { name: /^Car/ }).click();
      const panel = page.getByRole("group", { name: "Car filter" });
      await expect(panel.getByRole("combobox", { name: "Search car" })).toBeFocused();
      await expect(panel).toContainText("0 of 2 cars");
      await panel.getByRole("combobox", { name: "Search car" }).fill("mazda");
      await expect(panel.getByRole("option")).toHaveCount(1);
      await panel.getByRole("option", { name: /Global Mazda/ }).click();
      await expect(panel).toContainText("1 of 2 cars");
      await expect(page.getByRole("region", { name: CAR })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Car/ })).toContainText("1");
      await page.keyboard.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^Car/ })).toBeFocused();
      await page.getByRole("button", { name: "Remove Global Mazda MX-5 Cup" }).click();
      await expect(page.getByRole("region", { name: CAR })).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("detail groups values, keeps readouts and run readings apart, and compares with changed and unknown first", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      // the source is shown here, not in the library
      await expect(page.getByLabel("Sources")).toContainText("Recording");
      await expect(page.getByLabel("Sources")).toContainText("Garage");
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

      const versions = page.getByTestId("versions");
      await expect(versions).toContainText("Version 2");
      await expect(versions).toContainText("Version 1");
      await expect(versions.locator("[data-version='2']")).toContainText("Latest");
      await expect(versions.locator("[data-version='2']")).toHaveAttribute("aria-current", "true");
      await expect(versions.getByRole("link")).toHaveCount(1);
      await expect(page.getByLabel("With").locator("option").filter({ hasText: "Bristol race v3.sto" })).toContainText(/Sep \d+, 2026/);
      await expect(page.getByRole("region", { name: "Sightings" }).getByText("Unmodified").first()).toBeVisible();
      await expect(page.getByRole("region", { name: "Sightings" }).getByRole("link", { name: "Session 103" })).toBeVisible();

      await page.getByLabel("With").selectOption("2");
      const summary = page.getByTestId("compare-summary");
      await expect(summary).toContainText("3 changed");
      await expect(summary).toContainText("1 unknown");
      await expect(summary).toContainText("1 readout moved");
      // the two columns are told apart by name and the dates they were seen
      const heads = page.locator("table thead th");
      await expect(heads.nth(1)).toContainText("Bristol race v3.sto");
      await expect(heads.nth(1)).toContainText(/Sep \d+, 2026/);
      await expect(heads.nth(2)).toContainText("Bristol race v2.sto");
      const rows = page.locator("table tbody tr");
      await expect(rows.first()).toHaveAttribute("data-kind", "changed");
      await expect(page.locator("tr[data-kind=identical]")).toHaveCount(0);
      // our labels, not the backend's field names
      await expect(rows.first().locator("th")).toHaveText(/^[A-Z][^.]* · /);
      await page.getByRole("button", { name: "Show all values" }).click();
      await expect(page.locator("tr[data-kind=identical]").first()).toBeVisible();
      await expect(page.locator("tr[data-kind=unknown]")).toContainText("Track bar height");
      // identical settings come before the readout heading
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
      // a car or track that is not in the recordings is allowed: the lists are suggestions
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
      // the backend matches the car folder: the display name is sent as its path, the season in its long form
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
