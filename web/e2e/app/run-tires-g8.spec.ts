import { type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildTrafficAnalysis } from "../../src/investigation/fixtures/traffic";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// G8: the four-tire picture on the Runs tab's run cards. Runs 1 and 2 carry a measured tire reading; run 3 does not (no measured picture).
const ANALYSIS = buildTrafficAnalysis("an-103");
type Json = Record<string, unknown>;
const tire = (wear: [number, number, number], heat: [number, number, number], psi: number): Json => ({
  remaining_percent: { L: wear[0], M: wear[1], R: wear[2] },
  surface_temperature_f: { L: heat[0], M: heat[1], R: heat[2] },
  pressure: { psi, kind: "live" },
});
const runs = (ANALYSIS.runs as Json).runs as Json[];
runs[0]!.tire_observation = {
  lowest_remaining_tire: "RF",
  lowest_remaining_percent: 61.3,
  tires: { LF: tire([74, 76, 72], [158, 166, 176], 31.2), RF: tire([60, 64, 59], [184, 196, 206], 33.4), LR: tire([82, 83, 81], [146, 150, 154], 30.1), RR: tire([79, 80, 77], [160, 168, 178], 30.8) },
};
runs[1]!.tire_observation = {
  lowest_remaining_tire: "LF",
  lowest_remaining_percent: 38,
  tires: { LF: tire([40, 36, 38], [210, 218, 224], 36), RF: tire([52, 55, 50], [150, 152, 156], 33), LR: tire([61, 62, 60], [170, 172, 178], 31), RR: tire([58, 60, 57], [172, 176, 182], 31) },
};
delete runs[2]!.tire_observation;

const shots = process.env.TIRE_SHOTS;

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    test.beforeEach(async ({ page }) => {
      await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
        const url = new URL(route.request().url());
        const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
        await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
      });
    });

    test("Runs (G8): a run with a measured reading shows its four tires at the run's end; a run without shows none", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/runs`));
      await expect(page.locator(".ri-run-card").first()).toBeVisible();
      await expect(page.locator(".ri-run-card [data-tire-wear]")).toHaveCount(0);
      const pictures = page.locator(".ri-pit-tire-picture [data-tire-wear]");
      await expect(pictures).toHaveCount(2);
      const first = page.locator(".ri-pit-tire-picture").first();
      await expect(first.locator("[data-band]")).toHaveCount(12);
      await expect(first.locator("rect[data-remaining]")).toHaveCount(12);
      const heights = await page.locator(".ri-run-card").evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().height));
      expect(new Set(heights).size).toBe(1);
      await first.focus();
      await expect(page.locator("[data-run-tire-details]")).toBeVisible();
      await expect(page.locator("[data-detail-tire]")).toHaveCount(4);
      await expect(page.locator(".ri-tire-detail-band")).toHaveCount(12);
      await expect(page.locator("[data-detail-tire='LF']")).toContainText("74%");
      await expect(page.locator("[data-detail-tire='RF']")).toContainText("33.4 psi");
      const requests: string[] = [];
      page.on("request", (request) => requests.push(request.url()));
      await first.hover();
      expect(requests).toEqual([]);
      const sequence = await page.locator(".ri-run-cards > *").evaluateAll((cards) => cards.map((card) => card.hasAttribute("data-run-card") ? `run-${card.getAttribute("data-run-card")}` : "pit"));
      expect(sequence[0]).toBe("run-1");
      expect(sequence).toContain("pit");
      for (let i = 0; i < sequence.length; i++) if (sequence[i] === "pit") {
        expect(sequence[i - 1]).toMatch(/^run-/);
        expect(sequence[i + 1]).toMatch(/^run-/);
      }

      // Nothing overflows the card and the page does not scroll sideways.
      const fits = await page.locator(".ri-run-card.has-tires").evaluateAll((cards) => cards.map((card) => {
        const box = card.getBoundingClientRect();
        return [...card.querySelectorAll<HTMLElement>(".ri-run-tires *")].every((el) => { const r = el.getBoundingClientRect(); return r.right <= box.right + 0.5 && r.left >= box.left - 0.5; });
      }));
      expect(fits).toEqual([true, true]);
      await expectNoHorizontalScroll(page);
      await page.evaluate(() => { document.documentElement.style.fontSize = "20px"; });
      const clipped = await page.locator(".ri-run-card").evaluateAll((cards) => cards.some((card) => {
        const box = card.getBoundingClientRect();
        return [...card.querySelectorAll(".ri-facts, .ri-run-tires")].some((el) => el.getBoundingClientRect().bottom > box.bottom);
      }));
      expect(clipped).toBe(false);
      await page.evaluate(() => { document.documentElement.style.fontSize = ""; });
      if (shots) {
        mkdirSync(shots, { recursive: true });
        await page.screenshot({ path: path.join(shots, `runs-${device.viewport.width}.png`), fullPage: device.viewport.width < 700 });
        await page.locator(".ri-run-cards").screenshot({ path: path.join(shots, `runs-cards-${device.viewport.width}.png`) });
      }
    });
  });
}
