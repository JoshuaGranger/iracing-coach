import { type Page, expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState, setMock } from "./helpers";

// The `.sto` file library and editor in Setups (docs/08-rewrite/contracts/sto-editor.md) against web/mock/sto.mjs.
const SHOTS = "target/sto-editor";
const OWNED = "coach:1";
const fileUrl = (id: string, version?: number) => hash(`/d/${DRIVER}/setups/files/${encodeURIComponent(id)}${version ? `?version=${version}` : ""}`);
const filesBoard = (page: Page) => page.getByRole("region", { name: "Setup files" });
const field = (page: Page, name: string) => page.locator(`.sf-row[data-name="${name}"]`);
const input = (page: Page, name: string) => field(page, name).locator("input");

interface Write {
  path: string;
  body: Record<string, unknown>;
  key?: string;
  ifMatch?: string | null;
}
const stoWrites = async (page: Page) => ((await mockState(page.request)).writes as Write[]).filter((w) => w.path.includes("/sto-files/"));

async function openOwned(page: Page, version?: number) {
  await page.goto(fileUrl(OWNED, version));
  await expect(page.getByRole("heading", { name: "Bristol short-run.sto", level: 1 })).toBeVisible();
}

async function sourceId(page: Page, name: string, sha?: string): Promise<string> {
  const { files } = (await (await page.request.get(`/api/v1/drivers/${DRIVER}/sto-files`)).json()) as { files: { id: string; name: string; sha256: string }[] };
  const f = files.find((x) => x.name === name && (!sha || x.sha256 === sha));
  expect(f, name).toBeTruthy();
  return f!.id;
}

const desktop = DEVICES[0]!;

appTest(desktop, () => {
  test("library: every occurrence is listed and opens; no file repeats as No values; other cars' files stay reachable", async ({ page }) => {
    await page.goto(hash(`/d/${DRIVER}/setups`));
    const board = filesBoard(page);
    await expect(board).toBeVisible();
    // Coach setup first, then every locked occurrence: the two copies of Bristol race v3.sto are both there.
    await expect(board.locator("[data-sto]")).toHaveCount(8);
    await expect(board.locator("[data-sto]").first()).toContainText("Bristol short-run.sto");
    await expect(board.getByRole("link", { name: /Bristol race v3\.sto/ })).toHaveCount(2);
    await expect(board.getByRole("link", { name: /Old build\.sto/ })).toContainText("Archive/");
    // The setups response's unlinked .sto rows are the same files: never a second, unopenable "No values" row.
    await expect(page.getByText("Imported 1.sto")).toHaveCount(1);
    await expect(page.locator('[data-entry="file"]')).toHaveCount(0);
    // The files come first, then the recorded setups; Raced filters only those and never hides a file.
    const boards = await page.locator(".s-results > section").evaluateAll((s) => s.map((x) => x.getAttribute("aria-label")));
    expect(boards.slice(0, 2)).toEqual(["Setup files", "Recorded setups"]);
    const recorded = page.getByRole("region", { name: "Recorded setups" });
    await recorded.getByRole("button", { name: "Not raced", exact: true }).click();
    await expect(board.locator("[data-sto]")).toHaveCount(8);
    await recorded.getByRole("button", { name: "All", exact: true }).click();

    // A car with only files sits under the quiet divider and still opens.
    await page.getByRole("button", { name: /Files of other cars/ }).click();
    await page.locator('[data-car-files="dallara-ir18"] button').click();
    await page.getByRole("link", { name: "Road base.sto" }).click();
    await expect(page.getByRole("heading", { name: "Road base.sto", level: 1 })).toBeVisible();
    await expect(page.getByText("Not in folder")).toBeVisible();

    // Search narrows the files too.
    await page.goto(hash(`/d/${DRIVER}/setups`));
    await page.getByRole("searchbox", { name: "Search setups" }).fill("short-run");
    await expect(filesBoard(page).locator("[data-sto]")).toHaveCount(1);

    // The observed setup's file opens the same library entry.
    await page.goto(hash(`/d/${DRIVER}/setups/1`));
    await page.getByRole("link", { name: "nascar-xfinity/Bristol race v3.sto" }).click();
    await expect(page).toHaveURL(/#\/d\/1507810\/setups\/files\/source%3A[0-9a-f]{64}$/);
    await expect(page.getByTestId("sto-locked")).toBeVisible();
  });

  test("a locked original is read-only; Save as checks the name and creates an editable lineage", async ({ page }) => {
    const id = await sourceId(page, "nascar-xfinity/Bristol race v3.sto", "1".padStart(64, "a"));
    await page.goto(fileUrl(id));
    await expect(page.getByTestId("sto-locked")).toHaveText("Locked original");
    await expect(page.locator(".sf-params input")).toHaveCount(0);
    await expect(page.locator(".sf-notes textarea")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Save revision/ })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Download original" })).toHaveAttribute("href", `/api/v1/drivers/${DRIVER}/sto-files/${id}/download?version=1`);
    // Every native record as the codec reports it: the native name is the label, a unit only where the codec names one, never a
    // garage unit for raw geometry.
    const params = page.getByRole("region", { name: "Parameters" });
    await expect(params.locator(".sf-row")).toHaveCount(88);
    await expect(field(page, "fBrakeBias")).toContainText("54.500000");
    await expect(field(page, "fBrakeBias").locator(".sf-unit")).toHaveText("%");
    await expect(field(page, "lfTireColdPressPa").locator(".sf-unit")).toHaveText("Pa");
    await expect(field(page, "lfCamber").locator(".sf-unit")).toHaveText("");
    await expect(field(page, "unknownFutureParameter")).toContainText("1e+2");
    await expect(params.locator(".sf-label small")).toHaveCount(0);
    // The codec's garage check once, compactly at Parameters; no warning cards.
    await expect(params.getByTestId("sto-check")).toHaveCount(1);
    await expect(params.getByTestId("sto-check")).toContainText("check the garage");
    await expect(page.locator(".setup-file .notice")).toHaveCount(0);

    await page.getByRole("button", { name: "Save as…" }).click();
    const form = page.getByRole("form", { name: "Save as" });
    const name = form.getByRole("textbox", { name: "Name" });
    await expect(name).toHaveValue("Bristol race v3 copy.sto");
    await name.fill("Bristol short-run.sto");
    await form.getByRole("button", { name: "Create setup" }).click();
    await expect(form.getByRole("alert")).toHaveText(/already exists for this car/);
    await expect(name).toHaveAttribute("aria-invalid", "true");
    await name.fill("bad/name.sto");
    await form.getByRole("button", { name: "Create setup" }).click();
    await expect(form.getByRole("alert")).toContainText("folder separator");
    await name.fill("Bristol long-run.sto");
    await form.getByRole("textbox", { name: "Note (optional)" }).fill("Long-run start");
    await form.getByRole("button", { name: "Create setup" }).click();

    await expect(page.getByRole("heading", { name: "Bristol long-run.sto", level: 1 })).toBeVisible();
    await expect(page).toHaveURL(/files\/coach%3A2$/);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 1 of 1");
    await expect(page.getByRole("link", { name: "Bristol race v3.sto" })).toBeVisible();
    await expect(input(page, "fBrakeBias")).toBeEditable();
    const writes = await stoWrites(page);
    const created = writes.filter((w) => w.path.endsWith("/save-as"));
    expect(created.map((w) => w.body.name)).toEqual(["Bristol short-run.sto", "bad/name.sto", "Bristol long-run.sto"]);
    expect(new Set(created.map((w) => w.key)).size, "a changed request is a new key").toBe(3);
    expect(created[2]!.body).toEqual({ base_version: 1, changes: {}, name: "Bristol long-run.sto", message: "Long-run start" });
    // The original is unchanged.
    const original = await (await page.request.get(`/api/v1/drivers/${DRIVER}/sto-files/${encodeURIComponent(id)}`)).json();
    expect(original.file).toMatchObject({ locked: true, current_version: 1, sha256: "1".padStart(64, "a") });
  });

  test("Save appends a revision with exactly the typed values; history opens read-only and restores as a new revision", async ({ page }) => {
    await openOwned(page);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 2 of 2");
    await input(page, "fBrakeBias").fill("55.0");
    // The stored spelling typed again is no change.
    await input(page, "rTrackBarLeft").fill("0.254000");
    await expect(page.getByTestId("sto-change-count")).toHaveText("1 change");
    await expect(field(page, "fBrakeBias")).toHaveAttribute("data-changed", "true");
    await expect(field(page, "fBrakeBias")).toContainText("was 54.500000");
    // Outside the codec's safety bound: shown, not editable.
    await expect(field(page, "opaqueBuildToken").locator("input")).toHaveCount(0);
    await page.locator(".sf-notes textarea").fill("Bristol race baseline.\nShort-run balance.\nBias forward.");
    await expect(page.getByTestId("sto-change-count")).toHaveText("2 changes");
    await page.getByRole("textbox", { name: "Revision note" }).fill("Bias forward");
    await page.getByRole("button", { name: "Save revision 3" }).click();
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 3 of 3");
    await expect(page.getByTestId("sto-change-count")).toHaveCount(0);
    await expect(input(page, "fBrakeBias")).toHaveValue("55.0");
    const [save] = (await stoWrites(page)).filter((w) => !w.path.endsWith("/save-as"));
    expect(save!.ifMatch).toBe("2");
    expect(save!.key).toMatch(/^[0-9a-f-]{36}$/);
    expect(save!.body).toEqual({ base_version: 2, changes: { fBrakeBias: "55.0" }, notes: "Bristol race baseline.\nShort-run balance.\nBias forward.", message: "Bias forward" });

    const revisions = page.getByRole("region", { name: "Revisions" });
    await expect(revisions.locator(".sf-rev")).toHaveCount(3);
    await expect(revisions.locator(".sf-rev").first()).toContainText("Bias forward");
    await revisions.getByRole("link", { name: "Revision 1" }).click();
    await expect(page).toHaveURL(/\?version=1$/);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 1 of 3");
    await expect(page.getByText(/read-only history/)).toBeVisible();
    await expect(page.locator(".sf-params input")).toHaveCount(0);
    await expect(field(page, "fBrakeBias")).toContainText("54.500000");
    await expect(page.getByRole("link", { name: "Download revision 1" })).toHaveAttribute("href", `/api/v1/drivers/${DRIVER}/sto-files/${OWNED}/download?version=1`);
    const dl = await page.request.get(`/api/v1/drivers/${DRIVER}/sto-files/${OWNED}/download?version=1`);
    expect(dl.headers()["content-disposition"]).toMatch(/filename=".*r1\.sto"/);

    await page.getByRole("button", { name: "Restore as revision 4" }).click();
    await expect(page).toHaveURL(/files\/coach%3A1$/);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 4 of 4");
    await expect(input(page, "fBrakeBias")).toHaveValue("54.500000");
    const restore = (await stoWrites(page)).at(-1)!;
    expect(restore.ifMatch, "restore matches the current revision").toBe("3");
    expect(restore.body).toEqual({ base_version: 1, changes: {}, message: "Restored revision 1" });
  });

  test("Save as, Save, then revision 1 at once: read-only from the cache, and one Restore appends revision 3", async ({ page }) => {
    const id = await sourceId(page, "nascar-xfinity/Bristol race v3.sto", "1".padStart(64, "a"));
    await page.goto(fileUrl(id));
    await page.getByRole("button", { name: "Save as…" }).click();
    const form = page.getByRole("form", { name: "Save as" });
    await form.getByRole("textbox", { name: "Name" }).fill("Cache check.sto");
    await form.getByRole("button", { name: "Create setup" }).click();
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 1 of 1");
    const owned = decodeURIComponent(/files\/([^?]+)$/.exec(page.url())![1]!);
    await input(page, "fBrakeBias").fill("56.000000");
    await page.getByRole("button", { name: "Save revision 2" }).click();
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 2 of 2");

    // Every server read of revision 1 is held, so what shows is the copy cached at Save as: it must already know revision 2.
    let release: () => void = () => undefined;
    const held = new Promise<void>((r) => {
      release = () => r();
    });
    await page.route(new RegExp(`/sto-files/${encodeURIComponent(owned)}\\?version=1$`), async (route) => {
      await held;
      await route.continue().catch(() => undefined);
    });
    await page.getByRole("combobox", { name: "Open revision" }).selectOption("1");
    await expect(page).toHaveURL(/\?version=1$/);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 1 of 2");
    await expect(page.getByText(/read-only history/)).toBeVisible();
    await expect(page.locator(".sf-params input")).toHaveCount(0);
    await expect(field(page, "fBrakeBias")).toContainText("54.500000");
    await page.getByRole("button", { name: "Restore as revision 3" }).click();
    await expect(page).toHaveURL(new RegExp(`files/${encodeURIComponent(owned)}$`));
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 3 of 3");
    await expect(input(page, "fBrakeBias")).toHaveValue("54.500000");
    release();

    const saves = (await stoWrites(page)).filter((w) => !w.path.endsWith("/save-as"));
    expect(saves.map((w) => w.ifMatch), "one Restore, no 412 round").toEqual(["1", "2"]);
    expect(saves[1]!.body).toEqual({ base_version: 1, changes: {}, message: "Restored revision 1" });
  });

  test("a revision saved elsewhere is a clear 412: edits stay, the overlap is marked, Save then appends", async ({ page }) => {
    await openOwned(page);
    await input(page, "fBrakeBias").fill("56.000000");
    await input(page, "lfTireColdPressPa").fill("160000.000000");
    await setMock(page.request, { stoConflictNext: true });
    await page.getByRole("button", { name: "Save revision 3" }).click();
    await expect(page.getByTestId("sto-error")).toContainText("Revision 3 was saved elsewhere while you edited.");
    await expect(page.getByTestId("sto-error")).toContainText("fBrakeBias also changed there");
    await expect(field(page, "fBrakeBias")).toHaveAttribute("data-conflict", "true");
    await expect(field(page, "fBrakeBias")).toContainText("was 55.000000");
    await expect(input(page, "fBrakeBias")).toHaveValue("56.000000");
    await expect(input(page, "crossWeightPct")).toHaveValue("52.400000");
    await page.getByRole("button", { name: "Save revision 4" }).click();
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 4 of 4");
    const saves = (await stoWrites(page)).filter((w) => !w.path.endsWith("/save-as"));
    expect(saves.map((w) => w.ifMatch)).toEqual(["2", "3"]);
    expect(saves[1]!.body.changes).toEqual({ fBrakeBias: "56.000000", lfTireColdPressPa: "160000.000000" });
  });

  test("an unanswered Save keeps its request through a reload; Retry sends the same key and never saves twice", async ({ page }) => {
    await openOwned(page);
    await input(page, "rArbDiameter").fill("0.026500");
    await setMock(page.request, { stoDropResponse: true });
    await page.getByRole("button", { name: "Save revision 3" }).click();
    await expect(page.getByTestId("sto-pending")).toContainText("did not confirm");
    await expect(input(page, "rArbDiameter")).toBeDisabled();
    page.on("dialog", (d) => void d.accept());
    await page.reload();
    await expect(page.getByTestId("sto-pending")).toContainText("not confirmed");
    await page.getByTestId("sto-pending").getByRole("button", { name: "Retry" }).click();
    await expect(page.getByTestId("sto-pending")).toHaveCount(0);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 3 of 3");
    await expect(input(page, "rArbDiameter")).toHaveValue("0.026500");
    const saves = (await stoWrites(page)).filter((w) => !w.path.endsWith("/save-as"));
    expect(saves).toHaveLength(2);
    expect(saves[1]!.key).toBe(saves[0]!.key);
    expect(saves[1]!.body).toEqual(saves[0]!.body);
    expect(saves[1]!.ifMatch).toBe("2");
    const detail = await (await page.request.get(`/api/v1/drivers/${DRIVER}/sto-files/${OWNED}`)).json();
    expect(detail.file.version_count).toBe(3);
    expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("coach.sto.pending.")))).toEqual([]);
  });

  test("a rejected value marks its field; unsaved changes ask before leaving and before unload", async ({ page }) => {
    await openOwned(page);
    // The codec rejects text that is not a native number; its message names no parameter, so the one changed value carries it.
    await input(page, "lfTireColdPressPa").fill("165 kPa");
    await page.getByRole("button", { name: "Save revision 3" }).click();
    await expect(input(page, "lfTireColdPressPa")).toHaveAttribute("aria-invalid", "true");
    await expect(field(page, "lfTireColdPressPa")).toContainText("Use a finite decimal string");
    await expect(page.getByTestId("sto-error")).toHaveCount(1);

    expect(await page.evaluate(() => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    })).toBe(true);
    const asked: string[] = [];
    page.once("dialog", (d) => {
      asked.push(d.message());
      void d.dismiss();
    });
    await page.locator(".crumbs").getByRole("link", { name: "Setups" }).click();
    await expect.poll(() => asked).toEqual(["Discard unsaved changes to Bristol short-run.sto?"]);
    await expect(page).toHaveURL(/files\/coach%3A1$/);
    await expect(input(page, "lfTireColdPressPa")).toHaveValue("165 kPa");
    page.once("dialog", (d) => void d.accept());
    await page.locator(".crumbs").getByRole("link", { name: "Setups" }).click();
    await expect(page).toHaveURL(/\/setups$/);
    await expect(filesBoard(page)).toBeVisible();
    // Discarded edits leave nothing to ask about.
    await openOwned(page);
    await page.locator(".crumbs").getByRole("link", { name: "Setups" }).click();
    await expect(page).toHaveURL(/\/setups$/);
  });

  test("leaving while a save is in flight: the late answer settles the record and never lands on another page", async ({ page }) => {
    await openOwned(page);
    await input(page, "crossWeightPct").fill("52.200000");
    await setMock(page.request, { delayMs: 600 });
    await page.getByRole("button", { name: "Save revision 3" }).click();
    await expect(page.getByTestId("sto-pending")).toContainText("Saving…");
    page.once("dialog", (d) => void d.accept());
    await page.locator(".crumbs").getByRole("link", { name: "Setups" }).click();
    await expect(page).toHaveURL(/\/setups$/);
    await expect.poll(async () => (await stoWrites(page)).length).toBe(1);
    await expect.poll(() => page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("coach.sto.pending.")).length)).toBe(0);
    await setMock(page.request, { delayMs: 0 });
    await expect(page).toHaveURL(/\/setups$/);
    await openOwned(page);
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 3 of 3");
    await expect(input(page, "crossWeightPct")).toHaveValue("52.200000");
  });

  test("an unreadable file explains why and still downloads; another driver's ids do not resolve", async ({ page }) => {
    const id = await sourceId(page, "nascar-xfinity/Archive/Old build.sto");
    await page.goto(fileUrl(id));
    await expect(page.getByTestId("sto-unavailable")).toHaveText(/Unsupported STO build/);
    await expect(page.getByRole("button", { name: "Save as…" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Download original" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Parameters" })).toHaveCount(0);
    expect((await page.request.get(`/api/v1/drivers/2000001/sto-files/${encodeURIComponent(id)}`)).status()).toBe(404);
  });
});

// Layout on every device: no horizontal scroll, touch-sized controls, and screenshots for review.
for (const device of DEVICES) {
  appTest(device, (d) => {
    const phone = d.viewport.width < 700;
    const shot = (page: Page, name: string) => page.screenshot({ path: `${SHOTS}/${name}-${d.viewport.width}.png`, fullPage: true });

    test("library, editor with a change, and history fit the screen", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(filesBoard(page)).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      await shot(page, "library");

      await openOwned(page);
      // A group of the long native list is one tap away, and so is any revision.
      await expect(page.getByRole("combobox", { name: "Open revision" })).toBeInViewport();
      await page.getByRole("navigation", { name: "Parameter groups" }).getByRole("button", { name: /^Rear\s*\d+$/ }).click();
      await expect(page.getByRole("region", { name: "Rear", exact: true })).toBeInViewport();
      await input(page, "fBrakeBias").fill("55.0");
      await expect(page.getByRole("region", { name: "Unsaved changes" })).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      if (phone) {
        const bar = await page.getByRole("region", { name: "Unsaved changes" }).boundingBox();
        expect(bar!.y + bar!.height, "the save bar stays on screen").toBeLessThanOrEqual(d.viewport.height + 1);
      }
      await shot(page, "editor");
      await page.getByRole("button", { name: "Discard" }).click();

      await openOwned(page, 1);
      await expect(page.getByText(/read-only history/)).toBeVisible();
      await expectNoHorizontalScroll(page);
      await shot(page, "history");

      const id = await sourceId(page, "nascar-xfinity/Bristol race v3.sto", "1".padStart(64, "a"));
      await page.goto(fileUrl(id));
      await page.getByRole("button", { name: "Save as…" }).click();
      await expect(page.getByRole("form", { name: "Save as" })).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      await shot(page, "locked-save-as");
    });
  });
}

test.describe("sto routes", () => {
  test("a malformed version shows the current revision", async ({ page, request }) => {
    await request.post("/__mock/state", { data: { reset: true } });
    await page.goto(hash(`/d/${DRIVER}/setups/files/${encodeURIComponent(OWNED)}?version=abc`));
    await expect(page.getByTestId("sto-revision")).toHaveText("Revision 2 of 2");
  });
});
