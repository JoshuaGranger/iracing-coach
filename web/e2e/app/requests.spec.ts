import { type Page, expect, test } from "@playwright/test";
import { DEVICES, DRIVER, appTest, hash, setMock } from "./helpers";

/** API reads the page makes, as `path` (events and websockets are streams, not reads). */
function recordReads(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.pathname.startsWith("/api/") && u.pathname !== "/api/v1/events" && r.method() === "GET") seen.push(u.pathname);
  });
  return seen;
}

const count = (seen: string[], path: string) => seen.filter((p) => p === path).length;

function presence(sim: string, driver: number | null = DRIVER) {
  return {
    type: "presence",
    collector: {
      collector_id: "7d2d5a5e-6a3c-4b6e-9d0e-0f6c0c1c7a11",
      machine: "SIM",
      collector_version: "0.19.0",
      protocol: 1,
      compatible: true,
      online: true,
      last_seen_at: new Date().toISOString(),
      viewers: 0,
      status: {
        sim,
        connected: true,
        on_track: false,
        in_garage: false,
        on_pit_road: false,
        streaming: false,
        queue: { uploads_pending: 0, bytes_pending: 0 },
        session: null,
        driver: driver === null ? null : { customer_id: driver, name: "Joshua Granger" },
      },
    },
  };
}

for (const device of DEVICES) {
  appTest(device, () => {
    test("a first visit asks for each resource once and never for a driver it does not have", async ({ page }) => {
      const reads = recordReads(page);
      const errors: string[] = [];
      page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
      await page.goto("/");
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/sessions$`));
      await expect(page.getByRole("heading", { name: "Sessions" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(reads.filter((p) => p.includes("/drivers/0"))).toEqual([]);
      expect(count(reads, "/api/v1/drivers")).toBe(1);
      expect(count(reads, `/api/v1/drivers/${DRIVER}/prefs`)).toBe(1);
      expect(errors).toEqual([]);
    });

    test("collector presence never reloads the driver's resources, and the list only when what it shows changes", async ({ page, request }) => {
      await setMock(request, { live: false });
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      await expect(page.getByRole("tab", { name: "Traces" })).toBeVisible();
      await page.waitForLoadState("networkidle");
      const reads = recordReads(page);
      const send = (e: unknown) => request.post("/__mock/event", { data: e });

      // First sight of a collector the list already agrees with, then its status repeating as it does every 5 s.
      await send(presence("menus"));
      for (let i = 0; i < 4; i++) await send(presence("menus"));
      await page.mouse.move(100, 200);
      await page.waitForTimeout(900);
      expect(reads).toEqual([]);

      // The sim enters a session: the list changes, nothing else does.
      await setMock(request, { live: true });
      await send(presence("in_session"));
      await expect.poll(() => count(reads, "/api/v1/drivers")).toBe(1);
      await page.waitForTimeout(900);
      expect(reads.filter((p) => p !== "/api/v1/drivers" && !p.startsWith(`/api/v1/drivers/${DRIVER}/sessions`) && !p.startsWith("/api/v1/sessions/"))).toEqual([]);
      expect(count(reads, `/api/v1/drivers/${DRIVER}/prefs`)).toBe(0);
    });
  });
}
