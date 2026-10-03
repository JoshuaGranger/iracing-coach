import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.name.startsWith("phone");

    test("library lists every setup and the files whose values are unknown", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(page.getByRole("heading", { name: "Setups" })).toBeVisible();
      const list = page.getByRole("list", { name: "Setups" });
      await expect(list.getByRole("link")).toHaveCount(3);
      await expect(list.getByText("Bristol race v3.sto")).toBeVisible();
      await expect(list.getByText("Unnamed NASCAR Xfinity Toyota Supra setup")).toBeVisible();
      const unknown = page.getByRole("region", { name: "Setup files with values unknown" });
      await expect(unknown.getByText("Imported 1.sto")).toBeVisible();
      await expect(unknown.getByText("Values unknown").first()).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("filters to raced setups", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await page.getByLabel("Raced only").check();
      await expect(page.getByRole("list", { name: "Setups" }).getByRole("link")).toHaveCount(2);
      await page.getByPlaceholder("Name or car").fill("v2");
      await expect(page.getByRole("list", { name: "Setups" }).getByRole("link")).toHaveCount(1);
    });

    test("detail groups values, keeps readouts apart, and compares with changed and unknown first", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      const values = page.getByRole("region", { name: "Setup values" });
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).toContainText("-3.2 deg");
      await expect(values.getByText("Readouts, not setup values")).toBeVisible();
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).nth(1)).toContainText("Corner weight");
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).not.toContainText("Corner weight");

      await page.getByLabel("With").selectOption("2");
      const summary = page.getByTestId("compare-summary");
      await expect(summary).toContainText("3 changed");
      await expect(summary).toContainText("1 unknown");
      await expect(summary).toContainText("1 garage readout moved");
      const rows = page.locator("table tbody tr");
      await expect(rows.first()).toHaveAttribute("data-kind", "changed");
      await expect(page.locator("tr[data-kind=identical]")).toHaveCount(0);
      await page.getByRole("button", { name: "Show all values" }).click();
      await expect(page.locator("tr[data-kind=identical]").first()).toBeVisible();
      await expect(page.locator("tr[data-kind=unknown]")).toContainText("Track bar height");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
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

    test("find a baseline: only cars and tracks from recordings, then a starting point with its cautions", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/baseline`));
      const find = page.getByRole("button", { name: "Find starting point" });
      await expect(find).toBeDisabled();
      await page.getByLabel("Car").fill("Ferrari");
      await expect(page.getByText("Pick a car from your recordings.")).toBeVisible();
      await page.getByLabel("Car").fill("NASCAR Xfinity Toyota Supra");
      await page.getByLabel("Track and layout").fill("Bristol Motor Speedway - Oval");
      await expect(find).toBeEnabled();
      await find.click();
      const result = page.getByTestId("baseline-result");
      await expect(result).toBeVisible();
      await expect(result).toContainText("Bristol race v3");
      await expect(result).toContainText("New Hampshire");
      await expect(result).toContainText("Source files unchanged");
      await expect(result.getByRole("list", { name: "Cautions" })).toContainText("Transfer the tuning logic");
      const writes = (await mockState(request)).writes;
      expect(writes.at(-1)!.body).toMatchObject({ car: "NASCAR Xfinity Toyota Supra", track: "Bristol Motor Speedway - Oval", season: expect.stringMatching(/^\d{4}S\d$/) });
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });
  });
}
