import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, expectNoHorizontalScroll, hash, mockState, setMock } from "./helpers";

for (const device of DEVICES) {
  appTest(device, () => {
    async function addNote(page: import("@playwright/test").Page, text: string) {
      await page.getByPlaceholder("Add a note").fill(text);
      await page.getByRole("button", { name: "Add note" }).click();
    }

    test("a write made while the server is unreachable stays visible, then lands exactly once", async ({ page, context, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Notes" })).toBeVisible();
      await context.route("**/api/v1/drivers/*/notes", (r) => (r.request().method() === "POST" ? r.abort() : r.continue()));
      await addNote(page, "Pressures were low");
      const bar = page.getByTestId("outbox");
      await expect(bar).toContainText("1 change waiting to save");
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

    test("a version conflict is an explicit choice: keep mine", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await addNote(page, "First");
      await expect(page.getByRole("region", { name: "Notes" }).getByText("First")).toBeVisible();
      await setMock(request, { conflictNext: true });
      await page.getByRole("button", { name: "Edit" }).click();
      await page.getByLabel("Edit note").fill("Mine");
      await page.getByRole("button", { name: "Save" }).click();
      const bar = page.getByTestId("outbox");
      await expect(bar).toContainText("1 conflict");
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
      await expect(page.getByTestId("outbox")).toContainText("1 change waiting to save");
      await setMock(request, { failWrites: false });
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(page.getByTestId("outbox")).toHaveCount(0, { timeout: 8000 });
      expect((await mockState(request)).notes).toHaveLength(1);
    });
  });
}
