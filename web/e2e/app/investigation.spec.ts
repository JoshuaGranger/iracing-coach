import { expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, hash, mockState } from "./helpers";

// The workspace itself is W8's. These checks cover what the shell does around it.
for (const device of DEVICES) {
  appTest(device, () => {
    test("the shell resolves the session to its analysis and mounts the workspace on the route's view", async ({ page }) => {
      const requests: string[] = [];
      page.on("request", (r) => requests.push(new URL(r.url()).pathname));
      await page.goto(hash(`/d/${DRIVER}/s/103/race`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      expect(requests).toContain("/api/v1/sessions/103");
      await expect.poll(() => requests.some((p) => p === "/api/v1/analyses/an-103/race")).toBe(true);
    });

    test("switching views follows the route and is remembered as the last investigation", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/race`));
      await page.getByRole("tab", { name: "Traces" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { last_investigation?: { view: string; analysis_id: string } }).last_investigation?.view === "traces")).toBe(true);
      const stored = await page.evaluate((c) => JSON.parse(localStorage.getItem(`coach.last-investigation.${c}`) ?? "null"), DRIVER);
      expect(stored).toMatchObject({ session_id: 103, analysis_id: "an-103", view: "traces" });
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("link", { name: /Resume/ })).toContainText("Traces");
    });

    test("the one session picker moves between the phases of the event on the same view", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      const picker = page.getByLabel("Session", { exact: true });
      await expect(picker.locator("option")).toHaveText(["Practice", "Qualifying", "Race"]);
      await expect(picker.locator("option:checked")).toHaveText("Race");
      await picker.selectOption({ label: "Qualifying" });
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/traces$`));
    });

    test("a session without an analysis says so instead of opening something else", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/106/race`));
      await expect(page.getByRole("heading", { name: "Not analyzed yet" })).toBeVisible();
    });
  });
}
