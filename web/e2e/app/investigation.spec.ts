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

    test("switching views follows the route and is remembered silently as the last investigation", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/race`));
      await page.getByRole("tab", { name: "Traces" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && (w.body as { last_investigation?: { view: string; analysis_id: string } }).last_investigation?.view === "traces")).toBe(true);
      const stored = await page.evaluate((c) => JSON.parse(localStorage.getItem(`coach.last-investigation.${c}`) ?? "null"), DRIVER);
      expect(stored).toMatchObject({ session_id: 103, analysis_id: "an-103", view: "traces" });
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await expect(page.getByText("Resume")).toHaveCount(0);
      // The tile reopens the session on the tab it was left on.
      await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
    });

    test("the P Q R selector moves between the phases of the event on the same view", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      const picker = page.getByRole("group", { name: "Session", exact: true });
      await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
      await expect(picker.getByRole("button", { name: "Race" })).toHaveAttribute("aria-pressed", "true");
      await picker.getByRole("button", { name: "Qualifying" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/traces$`));
    });

    test("a phase that cannot open is disabled and names its state (S2)", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/104/race`));
      const picker = page.getByRole("group", { name: "Session", exact: true });
      await expect(picker.getByRole("button")).toHaveText(["P", "Q", "R"]);
      await expect(picker.getByRole("button", { name: "Race · Not analyzed" })).toBeDisabled();
      await expect(picker.getByRole("button", { name: "Qualifying" })).toBeEnabled();
    });

    test("saved chart layouts persist: New is written to the driver's layouts and stays active after a reload", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await page.getByRole("button", { name: "Customize" }).click();
      const box = page.getByRole("region", { name: "Chart layouts" });
      await expect(box.locator("[data-active-layout]")).toHaveText("Default");
      await box.getByRole("button", { name: "New" }).click();
      await expect(box.locator("[data-active-layout]")).toHaveText("Custom");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => /\/trace-layouts\/analysis-layout-/.test(w.path))).toBe(true);
      await expect.poll(async () => (await mockState(request)).writes.some((w) => w.path.endsWith("/prefs") && /^analysis-layout-/.test(String((w.body as { active_trace_layout_id?: string }).active_trace_layout_id)))).toBe(true);
      await page.reload();
      await page.getByRole("button", { name: "Customize" }).click();
      await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Custom");
      await page.getByRole("region", { name: "Chart layouts" }).getByRole("button", { name: "Delete" }).click();
      await expect(page.getByRole("region", { name: "Chart layouts" }).locator("[data-active-layout]")).toHaveText("Default");
      await expect.poll(async () => (await mockState(request)).writes.some((w) => (w as { method?: string }).method === "DELETE")).toBe(true);
    });

    test("a session without an analysis says so instead of opening something else", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/106/race`));
      await expect(page.getByRole("heading", { name: "Not analyzed" })).toBeVisible();
    });
  });
}
