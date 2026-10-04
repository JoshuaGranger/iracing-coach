import { type Page, expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";

const open = "an-103"; // a fixed-setup race, so it needs an open target
const openSetup = "an-110"; // an open-setup race
const draftKey = (analysis: string) => analysis.padEnd(64, "0").slice(0, 64);

type Ctx = Record<string, unknown> & { identity: Record<string, unknown> };

/** The mock's tuning context for one analysis, changed before the page reads it. */
async function patchContext(page: Page, analysis: string, change: (ctx: Ctx) => void) {
  await page.route(new RegExp(`/api/v1/analyses/${analysis}/tuning$`), async (route) => {
    const res = await route.fetch();
    const ctx = (await res.json()) as Ctx;
    change(ctx);
    await route.fulfill({ response: res, json: ctx });
  });
}

const turns = (page: Page) => page.getByRole("group", { name: "Selectable turns" });

/** Verify one turn exactly as the driver would: select it, open the correction, save it as it is. */
async function verifyTurn(page: Page, n: number) {
  await turns(page).getByRole("button", { name: new RegExp(`Turn ${n}`) }).click();
  await page.getByRole("button", { name: "Correct selected zone" }).click();
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(page.getByRole("button", { name: "Correct selected zone" })).toBeVisible();
}

const draftWrites = async (request: Parameters<typeof mockState>[0]) => (await mockState(request)).writes.filter((w) => w.path.includes("/tuning-drafts/"));

for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.name.startsWith("phone");

    test("entry lists analyzed recordings", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning`));
      await expect(page.getByRole("heading", { name: "Progressive tuning" })).toBeVisible();
      await page.getByRole("list", { name: "Recordings" }).getByRole("link").first().click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/tuning/an-`));
      await expect(page.getByRole("img", { name: "Track map with selectable turns" })).toBeVisible();
    });

    test("turn feedback is saved to the draft and survives a reload", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await turns(page).getByRole("button", { name: /Turn 2/ }).click();
      const editor = page.getByRole("region", { name: "Feedback for Turn 2" });
      await editor.getByRole("radiogroup", { name: "Entry balance" }).getByRole("radio", { name: "Tight" }).click();
      await editor.getByRole("radiogroup", { name: "Exit balance" }).getByRole("radio", { name: "Loose" }).click();
      await editor.getByLabel("Turn note").fill("Pushes on entry");
      await expect(editor.getByText("2 saved")).toBeVisible();
      await expect.poll(async () => (await draftWrites(request)).length).toBeGreaterThan(0);
      await expect(page.getByTestId("outbox")).toHaveCount(0);

      await page.reload();
      await turns(page).getByRole("button", { name: /Turn 2/ }).click();
      const again = page.getByRole("region", { name: "Feedback for Turn 2" });
      await expect(again.getByRole("radiogroup", { name: "Entry balance" }).getByRole("radio", { name: "Tight" })).toHaveAttribute("aria-checked", "true");
      await expect(again.getByLabel("Turn note")).toHaveValue("Pushes on entry");
      await expect(turns(page).getByRole("button", { name: /Turn 2/ })).toContainText("Entry tight");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("analysis waits for every turn verified, a run and an open target, then gives one controlled test", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      const begin = page.getByRole("button", { name: "Begin analysis" });
      await expect(begin).toBeDisabled();
      await expect(page.getByText("Select a turn and describe the balance.")).toBeVisible();

      await turns(page).getByRole("button", { name: /Turn 1/ }).click();
      const editor = page.getByRole("region", { name: "Feedback for Turn 1" });
      await editor.getByRole("radiogroup", { name: "Center balance" }).getByRole("radio", { name: "Loose" }).click();
      await expect(page.getByText("Verify all turns (0 of 6)")).toBeVisible();

      // verify the turn: the track click sets the active handle, the sliders refine it
      await page.getByRole("button", { name: "Correct selected zone" }).click();
      await expect(page.getByText("Click the track to set")).toBeVisible();
      await page.getByRole("button", { name: "Save correction" }).click();
      await expect(page.getByRole("button", { name: "Correct selected zone" })).toBeVisible();
      // one turn is not the map: the others are verified too, and the progress is counted
      await expect(page.getByText("Verify all turns (1 of 6)")).toBeVisible();
      for (const n of [2, 3, 4, 5, 6]) await verifyTurn(page, n);
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
      await expect(result.getByText("Result saved")).toBeVisible();

      const writes = (await mockState(request)).writes;
      const recommend = writes.find((w) => w.path.endsWith("/tuning/recommendations"))!.body as Record<string, unknown>;
      expect(recommend).toMatchObject({ maximum_changes: 1, goal: "long-run-pace", representative_run_ids: ["1"], open_target_analysis_id: "an-104" });
      const map = recommend.map_identity as { verified: boolean; source_type: string; corners: { corner_id: string; user_verified: boolean }[] };
      expect(map.verified).toBe(true);
      expect(map.source_type).toBe("verified-manual");
      expect(map.corners).toHaveLength(6);
      expect(map.corners.every((c) => c.user_verified)).toBe(true);
      expect((recommend.feedback as { corner_phases: string[] }[])[0]!.corner_phases).toEqual(["center"]);
      expect(writes.some((w) => w.path.includes("/turn-annotations/"))).toBe(true);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("a turn correction carries the feedback already entered to the new bounds (U1)", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await turns(page).getByRole("button", { name: /Turn 1/ }).click();
      await page.getByRole("region", { name: "Feedback for Turn 1" }).getByRole("radiogroup", { name: "Entry balance" }).getByRole("radio", { name: "Tight" }).click();
      await expect.poll(async () => (await draftWrites(request)).length).toBeGreaterThan(0);
      await page.getByRole("button", { name: "Correct selected zone" }).click();
      await page.locator(".bounds label", { hasText: "Entry" }).locator("input").fill("3");
      await page.locator(".bounds label", { hasText: "Apex" }).locator("input").fill("9");
      await page.locator(".bounds label", { hasText: "Exit" }).locator("input").fill("16");
      await page.getByRole("button", { name: "Save correction" }).click();
      await expect
        .poll(async () => {
          const last = (await draftWrites(request)).at(-1)!.body as { feedback: { start_pct: number; end_pct: number }[] };
          return [last.feedback[0]!.start_pct, last.feedback[0]!.end_pct];
        })
        .toEqual([0.03, 0.16]);
    });

    test("saving the draft keeps every field it holds: unknown fields, incomplete items, no zero bounds (U6)", async ({ page, request }) => {
      const body = {
        goal: "tire-life",
        future_field: { kept: true },
        feedback: [
          { corner_id: "detected-1", run_phase: "early", corner_phases: ["entry"], symptom_id: "tight", source: "legacy" },
          { corner_id: "detected-2", run_phase: "middle" },
        ],
      };
      expect((await request.put(`/api/v1/drivers/${DRIVER}/tuning-drafts/${draftKey(open)}`, { data: { body } })).ok()).toBe(true);
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await expect(page.getByLabel("Goal")).toHaveValue("tire-life");
      await page.getByLabel("Goal").selectOption("stability");
      await expect.poll(async () => (await draftWrites(request)).length).toBeGreaterThan(1);
      const saved = (await draftWrites(request)).at(-1)!.body as { goal: string; future_field: unknown; feedback: Record<string, unknown>[] };
      expect(saved.goal).toBe("stability");
      expect(saved.future_field).toEqual({ kept: true });
      expect(saved.feedback).toHaveLength(2);
      expect(saved.feedback[0]).toMatchObject({ source: "legacy", corner_id: "detected-1" });
      expect(["start_pct", "apex_pct", "end_pct"].some((k) => k in saved.feedback[0]!)).toBe(false);
      expect(saved.feedback[1]).toEqual({ corner_id: "detected-2", run_phase: "middle" });
    });

    test("an invalid turn correction is refused", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await turns(page).getByRole("button", { name: /Turn 1/ }).click();
      await page.getByRole("button", { name: "Correct selected zone" }).click();
      const entry = page.locator(".bounds label", { hasText: "Entry" }).locator("input");
      const apex = page.locator(".bounds label", { hasText: "Apex" }).locator("input");
      await entry.fill("30");
      await apex.fill("30");
      await page.getByRole("button", { name: "Save correction" }).click();
      await expect(page.getByRole("alert")).toContainText("different points");
    });

    test("a recording that cannot take a change says why; an unknown setup type is not called fixed (U13, U14)", async ({ page }) => {
      await patchContext(page, openSetup, (ctx) => {
        ctx.eligible = false;
        ctx.reasons = ["The embedded setup fingerprint is unavailable."];
      });
      await page.goto(hash(`/d/${DRIVER}/tuning/${openSetup}`));
      await expect(page.getByText("The embedded setup fingerprint is unavailable.")).toBeVisible();
      await expect(page.getByText("Open setup", { exact: true })).toBeVisible();
    });

    test("setup type unknown still asks for an open target (U14)", async ({ page }) => {
      await patchContext(page, openSetup, (ctx) => {
        ctx.identity.setup_type = "Unknown";
      });
      await page.goto(hash(`/d/${DRIVER}/tuning/${openSetup}`));
      await expect(page.getByText("Setup type unknown")).toBeVisible();
      await expect(page.getByText("Fixed setup")).toHaveCount(0);
      await expect(page.getByLabel("Open-setup target")).toBeVisible();
    });

    test("without a track configuration the correction is unavailable and says so (U15)", async ({ page }) => {
      await patchContext(page, open, (ctx) => {
        ctx.identity.track_configuration_key = null;
      });
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await expect(page.getByText("Track configuration unavailable").first()).toBeVisible();
      await turns(page).getByRole("button", { name: /Turn 1/ }).click();
      await expect(page.getByRole("button", { name: "Correct selected zone" })).toBeDisabled();
      await expect(page.getByRole("button", { name: "Begin analysis" })).toBeDisabled();
    });

    test("a saved open target the server no longer offers is not kept (U12)", async ({ page, request }) => {
      const body = { goal: "long-run-pace", representative_run_ids: ["1"], open_target_analysis_id: "an-999", feedback: [] };
      expect((await request.put(`/api/v1/drivers/${DRIVER}/tuning-drafts/${draftKey(open)}`, { data: { body } })).ok()).toBe(true);
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await expect(page.getByLabel("Open-setup target")).toHaveValue("");
    });

    test("a detected zone is marked Z, not as an official turn (U25)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/tuning/${open}`));
      await expect(page.locator(".turn-text").first()).toHaveText("Z1");
    });

    test("the result can be linked to a later recording, and the test is kept for after the next run (U2, U24)", async ({ page, request }) => {
      await page.route(/\/api\/v1\/jobs\/\d+$/, async (route) => {
        const res = await route.fetch();
        const job = await res.json();
        const cand = job.result?.candidate_whitelist?.[0];
        if (cand) {
          cand.rollback = { setup_name: "base.sto", instruction: "Reload the baseline setup" };
          cand.test_protocol = { control: "Match fuel and tire age", minimum_run: "Same run length", rollback: "Restore the baseline" };
        }
        await route.fulfill({ response: res, json: job });
      });
      await page.goto(hash(`/d/${DRIVER}/tuning/${openSetup}`));
      await turns(page).getByRole("button", { name: /Turn 1/ }).click();
      await page.getByRole("region", { name: "Feedback for Turn 1" }).getByRole("radiogroup", { name: "Exit balance" }).getByRole("radio", { name: "Loose" }).click();
      for (const n of [1, 2, 3, 4, 5, 6]) await verifyTurn(page, n);
      await page.getByRole("group", { name: "Representative runs" }).getByRole("button", { name: /Run 1/ }).click();
      await page.getByRole("button", { name: "Begin analysis" }).click();
      const result = page.getByTestId("tuning-result");
      await expect(result).toContainText("Rear springs");
      await expect(result).toContainText("Reload the baseline setup");
      await expect(result).toContainText("Match fuel and tire age");
      // an Open setup has no open targets, yet other analyzed recordings of the car and layout can answer the test
      const options = result.getByLabel("Comparison run").locator("option");
      await expect(options).not.toHaveCount(1);
      await expect(options.first()).toHaveText("Driver result only");
      await result.getByLabel("Comparison run").selectOption({ index: 1 });
      await result.getByRole("button", { name: "Improved" }).click();
      await expect(result.getByText("Result saved")).toBeVisible();
      const outcome = (await mockState(request)).writes.find((w) => w.path.endsWith("/outcome"))!.body as { result_analysis_id: string | null };
      expect(outcome.result_analysis_id).toMatch(/^an-/);
      // the experiment id is in the draft: after a reload the last test is still there to record
      await expect.poll(async () => JSON.stringify((await draftWrites(request)).at(-1)?.body ?? {})).toContain("exp-0001");
      await page.reload();
      const last = page.getByTestId("last-test");
      await expect(last).toBeVisible();
      await expect(last).toContainText("Rear springs");
      await expect(last.getByRole("button", { name: "Worse" })).toBeEnabled();
    });
  });
}
