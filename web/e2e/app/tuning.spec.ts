import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.name.startsWith("phone");
    const open = "an-103"; // a fixed-setup race, so it needs an open target

    test("entry lists analyzed recordings", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning`));
      await expect(page.getByRole("heading", { name: "Progressive tuning" })).toBeVisible();
      await page.getByRole("list", { name: "Recordings" }).getByRole("link").first().click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/tuning/an-`));
      await expect(page.getByRole("img", { name: "Track map with selectable turns" })).toBeVisible();
    });

    test("turn feedback is saved to the draft and survives a reload", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await page.getByRole("group", { name: "Selectable turns" }).getByRole("button", { name: /Turn 2/ }).click();
      const editor = page.getByRole("region", { name: "Feedback for Turn 2" });
      await editor.getByRole("radiogroup", { name: "Entry balance" }).getByRole("radio", { name: "Tight" }).click();
      await editor.getByRole("radiogroup", { name: "Exit balance" }).getByRole("radio", { name: "Loose" }).click();
      await editor.getByLabel("Turn note").fill("Pushes on entry");
      await expect(editor.getByText("2 saved")).toBeVisible();
      await expect.poll(async () => (await mockState(request)).writes.filter((w) => w.path.includes("/tuning-drafts/")).length).toBeGreaterThan(0);
      await expect(page.getByTestId("outbox")).toHaveCount(0);

      await page.reload();
      await page.getByRole("group", { name: "Selectable turns" }).getByRole("button", { name: /Turn 2/ }).click();
      const again = page.getByRole("region", { name: "Feedback for Turn 2" });
      await expect(again.getByRole("radiogroup", { name: "Entry balance" }).getByRole("radio", { name: "Tight" })).toHaveAttribute("aria-checked", "true");
      await expect(again.getByLabel("Turn note")).toHaveValue("Pushes on entry");
      await expect(page.getByRole("group", { name: "Selectable turns" }).getByRole("button", { name: /Turn 2/ })).toContainText("Entry tight");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("analysis waits for verified bounds, a run and an open target, then gives one controlled test", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      const begin = page.getByRole("button", { name: "Begin analysis" });
      await expect(begin).toBeDisabled();
      await expect(page.getByText("Select a turn and describe the balance.")).toBeVisible();

      await page.getByRole("group", { name: "Selectable turns" }).getByRole("button", { name: /Turn 1/ }).click();
      const editor = page.getByRole("region", { name: "Feedback for Turn 1" });
      await editor.getByRole("radiogroup", { name: "Center balance" }).getByRole("radio", { name: "Loose" }).click();
      await expect(page.getByText("Verify the turn bounds before analysis.")).toBeVisible();

      // verify the turn: the track click sets the active handle, the sliders refine it
      await page.getByRole("button", { name: "Correct selected zone" }).click();
      await expect(page.getByText("Click the track to set")).toBeVisible();
      await page.getByRole("button", { name: "Save correction" }).click();
      await expect(page.getByRole("button", { name: "Correct selected zone" })).toBeVisible();
      await expect(page.getByText("Choose a representative run.")).toBeVisible();

      await page.getByRole("group", { name: "Representative runs" }).getByRole("button", { name: /Run 1/ }).click();
      await expect(page.getByText("Choose a matching open setup.")).toBeVisible();
      await page.getByLabel("Open-setup target").selectOption({ index: 1 });
      await expect(begin).toBeEnabled();
      await begin.click();

      const result = page.getByTestId("tuning-result");
      await expect(result).toContainText("Rear springs");
      await expect(result).toContainText("Lower the right-rear spring rate");
      await expect(result).toContainText("Less late-run push");
      await result.getByRole("button", { name: "Improved" }).click();
      await expect(result.getByText("Result saved.")).toBeVisible();

      const writes = (await mockState(request)).writes;
      const recommend = writes.find((w) => w.path.endsWith("/tuning/recommendations"))!.body as Record<string, unknown>;
      expect(recommend).toMatchObject({ maximum_changes: 1, goal: "long-run-pace", representative_run_ids: ["1"], open_target_analysis_id: "an-104" });
      expect((recommend.map_identity as { verified: boolean; source_type: string }).verified).toBe(true);
      expect((recommend.map_identity as { source_type: string }).source_type).toBe("verified-manual");
      expect((recommend.feedback as { corner_phases: string[] }[])[0]!.corner_phases).toEqual(["center"]);
      expect(writes.some((w) => w.path.includes("/turn-annotations/"))).toBe(true);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("an invalid turn correction is refused", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await page.getByRole("group", { name: "Selectable turns" }).getByRole("button", { name: /Turn 1/ }).click();
      await page.getByRole("button", { name: "Correct selected zone" }).click();
      const entry = page.locator(".bounds label", { hasText: "Entry" }).locator("input");
      const apex = page.locator(".bounds label", { hasText: "Apex" }).locator("input");
      await entry.fill("30");
      await apex.fill("30");
      await page.getByRole("button", { name: "Save correction" }).click();
      await expect(page.getByRole("alert")).toContainText("different points");
    });
  });
}
