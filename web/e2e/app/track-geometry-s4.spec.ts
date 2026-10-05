import { type Page, expect, test } from "@playwright/test";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

/**
 * Round 4 task S4 (backlog #65): the track outline comes from the backend's clean-lap centreline. The browser draws it as
 * delivered, keeps only a current outline (transform.longitude_scale present) in storage, and drops outlines stored before.
 */

/** A D-shaped loop in the backend's normalized space: a curved front stretch, a straight back stretch. */
function dOval(): { pct: number[]; x: number[]; y: number[] } {
  const n = 500;
  const pct: number[] = [];
  const x: number[] = [];
  const y: number[] = [];
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    pct.push(i / n);
    x.push(0.5 + 0.5 * Math.cos(t));
    y.push(0.3 + Math.max(-0.12, 0.3 * Math.sin(t)));
  }
  return { pct, x, y };
}

const TRANSFORM = { source_bounds: { minimum_x: -92.776, maximum_x: -92.766, minimum_y: 36.268, maximum_y: 36.281 }, normalization_scale: 0.013 };

async function serveTrack(page: Page, current: boolean): Promise<void> {
  await page.route("**/api/v1/analyses/*/track", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/")[4] ?? "");
    return route.fulfill({
      json: {
        analysis_id: id, projection_version: 1, schema_version: 1, profile_version: current ? "post-race-foundations-v24" : "post-race-foundations-v23",
        coordinate_space: "normalized_local_vector", status: "usable", main_path: dOval(), provenance: {},
        pit_lane: { pct: [0.7, 0.72, 0.74, 0.76, 0.78], x: [0.25, 0.37, 0.5, 0.62, 0.75], y: [0.22, 0.22, 0.22, 0.22, 0.22] },
        transform: current ? { ...TRANSFORM, longitude_scale: 0.806 } : TRANSFORM,
      },
    });
  });
}

const stored = (page: Page, prefix: string) =>
  page.evaluate((p) => Object.keys(localStorage).filter((k) => k.startsWith(p)), prefix);

const dir = process.env.COACH_SCREENS_DIR;

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    test("a current outline is drawn closed and kept; one stored before is dropped", async ({ page }) => {
      await page.addInitScript(() => {
        // An outline stored by an older build (key version 1): never read again.
        if (!localStorage.getItem("s4-seeded")) {
          localStorage.setItem("coach.silhouette.1.bristol-motor-speedway-oval", JSON.stringify({ d: "M0,0L1,1", tick: [0, 0, 1, 1], width: 118, height: 78 }));
          localStorage.setItem("s4-seeded", "1");
        }
      });
      await serveTrack(page, true);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      const sil = page.getByTestId("silhouette").first();
      await expect(sil).toBeVisible();
      await expect(sil.locator("path")).toHaveAttribute("d", /^M[\d.,L-]+Z$/);
      await expect(sil.locator("path")).not.toHaveAttribute("d", "M0,0L1,1");
      await expect.poll(() => stored(page, "coach.silhouette.2.").then((k) => k.length)).toBeGreaterThan(0);
      await expectNoHorizontalScroll(page);
      if (dir) await page.screenshot({ path: `${dir}/sessions-${d.viewport.width}.png` });
    });

    test("an outline built before the current rule is drawn but not kept", async ({ page }) => {
      await serveTrack(page, false);
      await page.goto(hash(`/d/${DRIVER}/sessions`));
      await expect(page.getByTestId("silhouette").first()).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(await stored(page, "coach.silhouette.2.")).toEqual([]);
    });

    test("the investigation map draws the centreline closed with the start line on it", async ({ page }) => {
      await serveTrack(page, true);
      await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
      const map = page.locator("svg[data-analysis-track-map]");
      await expect(map).toBeVisible();
      await expect(map.locator("path.ri-main-ribbon")).toHaveAttribute("d", /^M [\d. L-]+ Z$/);
      await expect(map.locator("line.ri-start-line")).toHaveCount(1);
      // Pit road is its own faint path: drawn, but under half strength and without a halo cutting the track.
      const pit = map.locator("path.ri-pit-road");
      await expect(pit).toHaveCount(1);
      expect(Number(await pit.evaluate((el) => getComputedStyle(el).opacity))).toBeLessThan(0.6);
      await expect(map.locator("path.ri-pit-halo")).toBeHidden();
      await expectNoHorizontalScroll(page);
      if (dir) await page.locator(".ri-track-panel").screenshot({ path: `${dir}/traces-map-${d.viewport.width}.png` });
    });
  });
}
