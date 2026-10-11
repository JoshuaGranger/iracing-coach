import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState, setMock } from "./helpers";

for (const device of DEVICES) {
  appTest(device, () => {
    async function addNote(page: import("@playwright/test").Page, text: string) {
      await page.getByPlaceholder("Add a note").fill(text);
      await page.getByRole("button", { name: "Add note" }).click();
    }

    test("restored writes save on first load without a reconnect or another edit", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await page.evaluate((driver) => localStorage.setItem("coach.outbox.v1", JSON.stringify([{ id: "33333333-3333-4333-8333-333333333333", method: "POST", path: `/api/v1/drivers/${driver}/notes`, body: { subject_kind: "setup", subject_id: "1", body: "Restored note" }, label: "New note", createdAt: Date.now(), attempts: 1, state: "sending" }])), DRIVER);
      await page.reload();
      await expect.poll(async () => (await mockState(request)).notes.length).toBe(1);
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("coach.outbox.v1") ?? "[]").length)).toBe(0);
    });

    test("a write made while the server is unreachable stays visible, then lands exactly once", async ({ page, context, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Notes" })).toBeVisible();
      await context.route("**/api/v1/drivers/*/notes", (r) => (r.request().method() === "POST" ? r.abort() : r.continue()));
      await addNote(page, "Pressures were low");
      const bar = page.getByTestId("outbox");
      await expect(bar).toContainText("New note waiting to save");
      await expect(page.getByTestId("offline-banner")).toContainText("1 change waiting");
      await expect(page.locator(".note-queued")).toContainText("Waiting to save");
      await expectNoHorizontalScroll(page);
      expect((await mockState(request)).notes).toHaveLength(0);

      // a reload keeps the queued write and its idempotency key
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("coach.outbox.v1") ?? "[]") as { id: string; state: string }[]);
      expect(stored).toHaveLength(1);
      expect(stored[0]!.id).toMatch(/^[0-9a-f-]{36}$/);

      await context.unroute("**/api/v1/drivers/*/notes");
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.getByTestId("outbox")).toHaveCount(0, { timeout: 5000 });
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Pressures were low")).toBeVisible();
      expect((await mockState(request)).notes).toHaveLength(1);
      await expect(page.getByTestId("offline-banner")).toHaveCount(0);
    });

    test("a write in flight never moves the page: the bar is an overlay at the bottom edge", async ({ page, context }) => {
      const pages: { name: string; path: string; ready: string; probes: string[] }[] = [
        { name: "sessions", path: `/d/${DRIVER}/sessions`, ready: "li.session-row", probes: ["main", "main h1", ".sx-events", ".sx-event"] },
        { name: "setups", path: `/d/${DRIVER}/setups`, ready: ".s-board", probes: ["main", "main h1", ".s-board"] },
        { name: "live", path: `/d/${DRIVER}/live`, ready: ".live-grid", probes: ["main", "main h1", ".live-grid", ".tile-cell"] },
        { name: "setup detail", path: `/d/${DRIVER}/setups/1`, ready: "main h1", probes: ["main", "main h1", "main section"] },
      ];
      // A held request keeps one write queued, so the bar stays up for the whole measurement.
      await context.route("**/api/v1/drivers/*/notes", () => undefined);
      const layout = async (probes: string[]) =>
        page.evaluate((selectors) => selectors.map((sel) => {
          const el = document.querySelector(sel);
          const r = el?.getBoundingClientRect();
          return r ? { sel, x: Math.round(r.x), y: Math.round(r.y + window.scrollY), w: Math.round(r.width), h: Math.round(r.height) } : { sel, missing: true };
        }), probes);
      for (const p of pages) {
        await page.goto(hash(p.path));
        await page.evaluate(() => localStorage.removeItem("coach.outbox.v1"));
        await page.reload();
        await page.waitForSelector(p.ready);
        await page.waitForTimeout(400);
        await expect(page.getByTestId("outbox")).toHaveCount(0);
        const before = await layout(p.probes);
        await page.evaluate((c) => {
          localStorage.setItem("coach.outbox.v1", JSON.stringify([{ id: "11111111-1111-4111-8111-111111111111", method: "POST", path: `/api/v1/drivers/${c}/notes`, body: {}, label: "Autosave", createdAt: Date.now(), attempts: 0, state: "pending" }]));
        }, DRIVER);
        await page.reload();
        await page.waitForSelector(p.ready);
        const bar = page.getByTestId("outbox");
        await expect(bar).toContainText("waiting to save");
        await page.waitForTimeout(400);
        expect(await layout(p.probes), `${p.name} moved while a write was in flight`).toEqual(before);
        const box = (await bar.boundingBox())!;
        const view = page.viewportSize()!;
        expect(await bar.evaluate((el) => getComputedStyle(el).position)).toBe("fixed");
        expect(box.y + box.height).toBeLessThanOrEqual(view.height);
        expect(box.x).toBeGreaterThanOrEqual(0);
        expect(box.x + box.width).toBeLessThanOrEqual(view.width);
        await expectNoHorizontalScroll(page);
      }
    });

    test("a version conflict is an explicit choice: keep mine", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await addNote(page, "First");
      await expect(page.getByRole("region", { name: "Notes" }).getByText("First")).toBeVisible();
      await setMock(request, { conflictNext: true });
      await page.getByRole("button", { name: "Edit" }).click();
      await page.getByLabel("Edit note").fill("Mine");
      await page.getByRole("button", { name: "Save" }).click();
      const bar = page.getByTestId("outbox");
      await expect(bar).toContainText("Edit note conflict");
      await expect(bar).toContainText("changed on the server");
      await bar.getByRole("button", { name: "Keep mine" }).click();
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Mine")).toBeVisible();
      expect(((await mockState(request)).notes as { body: string }[])[0]!.body).toBe("Mine");
    });

    test("a version conflict is an explicit choice: use theirs", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await addNote(page, "Original");
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Original")).toBeVisible();
      await setMock(request, { conflictNext: true });
      await page.getByRole("button", { name: "Edit" }).click();
      await page.getByLabel("Edit note").fill("Discarded");
      await page.getByRole("button", { name: "Save" }).click();
      await page.getByTestId("outbox").getByRole("button", { name: "Use theirs" }).click();
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Original")).toBeVisible();
      expect(((await mockState(request)).notes as { body: string }[])[0]!.body).toBe("Original");
    });

    test("a server outage (5xx) keeps the write queued until the server recovers", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await setMock(request, { failWrites: true });
      await addNote(page, "During an outage");
      await expect(page.getByTestId("outbox")).toContainText("New note waiting to save");
      await setMock(request, { failWrites: false });
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.getByTestId("outbox")).toHaveCount(0, { timeout: 8000 });
      expect((await mockState(request)).notes).toHaveLength(1);
    });
  });
}
