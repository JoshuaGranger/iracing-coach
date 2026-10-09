import { type Page, type Route, expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { routeSynthetic } from "../../src/investigation/fixtures/api";
import { buildSectionsAnalysis } from "../../src/investigation/fixtures/sections";
import { DRIVER, appTest, expectNoHorizontalScroll, hash } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

// Phase 1 acceptance (gap ledger G6, G7-lite, G8) through the normal loader: the Sections tab (six-channel chart with the approach, the pass
// pair, section headers with the median-minus-best time and tire energy, lap rows with Green) and the Runs tab (tire pictures).
// Invented figures stand in for the producer: the investigation view's top-level `pace_attribution` and two runs' `tire_observation`.
const ANALYSIS = buildSectionsAnalysis("an-103");
type Json = Record<string, unknown>;
const investigation = ANALYSIS.investigation as unknown as Json;
const detected = (((investigation.race_investigation as Json).sections as Json).sections as { id: string; start_pct: number; end_pct: number }[]);
const FIGURES: Record<string, [number, number, "slip_weighted" | "load_only" | null]> = { A: [0.12, 0.31, "load_only"], B: [0.31, 0.18, "slip_weighted"], C: [0.05, 0.51, "slip_weighted"] };
investigation.pace_attribution = {
  schema_version: 1,
  priorities: detected.map((s) => ({
    section_id: s.id, start_pct: s.start_pct, end_pct: s.end_pct, recoverable_s: FIGURES[s.id]![0], best_lap: 17,
    tire_energy_share: FIGURES[s.id]![1], tire_energy_grade: FIGURES[s.id]![2],
  })),
};
// `median_lap` is the optional field the view reads for the grey median line, the gap row and the entry-speed reference: a clean lap
// outside each section's six (an invented value, as in sections-r4.spec.ts).
{
  type Entry = { section_id: string; median_lap?: number; fastest: { lap: number }[]; slowest: { lap: number }[] };
  const sectionLaps = (investigation.section_laps as { sections: Entry[] }).sections;
  const clean = ((investigation.race_investigation as { laps: { lap: number; eligible: boolean }[] }).laps).filter((lap) => lap.eligible).map((lap) => lap.lap);
  for (const s of sectionLaps) {
    const shown = new Set([...s.fastest, ...s.slowest].map((lap) => lap.lap));
    s.median_lap = clean.find((lap) => !shown.has(lap) && lap > 5);
  }
}
const tire = (wear: [number, number, number], heat: [number, number, number], psi: number): Json => ({
  remaining_percent: { L: wear[0], M: wear[1], R: wear[2] },
  surface_temperature_f: { L: heat[0], M: heat[1], R: heat[2] },
  pressure: { psi, kind: "live" },
});
const runs = (ANALYSIS.runs as Json).runs as Json[];
runs[0]!.tire_observation = {
  lowest_remaining_tire: "RF", lowest_remaining_percent: 61.3,
  tires: { LF: tire([74, 76, 72], [158, 166, 176], 31.2), RF: tire([60, 64, 59], [184, 196, 206], 33.4), LR: tire([82, 83, 81], [146, 150, 154], 30.1), RR: tire([79, 80, 77], [160, 168, 178], 30.8) },
};
runs[1]!.tire_observation = {
  lowest_remaining_tire: "LF", lowest_remaining_percent: 38,
  tires: { LF: tire([40, 36, 38], [210, 218, 224], 36), RF: tire([52, 55, 50], [150, 152, 156], 33), LR: tire([61, 62, 60], [170, 172, 178], 31), RR: tire([58, 60, 57], [172, 176, 182], 31) },
};
delete runs[2]?.tire_observation;

const SHOTS = process.env.PHASE1_SHOTS ? path.resolve(process.cwd(), process.env.PHASE1_SHOTS) : null;

async function serve(page: Page): Promise<void> {
  await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const routed = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
    await route.fulfill({ status: routed.status, contentType: routed.contentType, body: typeof routed.body === "string" ? routed.body : Buffer.from(routed.body) });
  });
}

for (const device of ROUND2_DEVICES) {
  appTest(device, () => {
    const width = device.viewport.width;
    test.beforeEach(async ({ page }) => serve(page));

    test("Sections: headers carry the two figures from normal loading; lap rows carry Green; six channels; nothing scrolls sideways", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/sections?section=B`));
      const panel = page.locator("[data-sl-traces]");
      await expect(panel).toHaveAttribute("data-status", "ready");
      // Worst section first (B 0.31, A 0.12, C 0.05), the two figures in each section's tab header.
      await expect(page.getByRole("tablist", { name: "Sections" }).getByRole("tab")).toHaveText(["B-0.31 s · best L1718 % tire energy", "A-0.12 s · best L1731 % tire energy proxy", "C-0.05 s · best L1751 % tire energy"]);
      expect(await page.locator("[data-sl-row]").evaluateAll((els) => els.map((el) => el.getAttribute("data-sl-row")))).toEqual(["speed", "gap", "throttle", "brake", "steering", "slip"]);
      const rows = page.locator(".ri-sl-list [data-lap]");
      await expect(rows).toHaveCount(6);
      await expect(page.locator("[data-sl-green]")).toHaveCount(6);
      // No browser-computed Entry figure (backend authority, Phase 1 review): entry speed reads from the dashed approach.
      await expect(page.locator("[data-sl-entry-cell]")).toHaveCount(0);
      await expect(page.locator("[data-sl-green]").first()).toHaveText(/^\d+$/);
      await expect(page.locator("[data-sl-boundary]")).toHaveCount(1);
      await expectNoHorizontalScroll(page);
      if (SHOTS) {
        mkdirSync(SHOTS, { recursive: true });
        await page.mouse.move(0, 0);
        await page.screenshot({ path: path.join(SHOTS, `sections-${width}.png`), fullPage: width < 700 });
      }
      // Every lap-row cell stays inside the list at this width.
      const inside = await page.locator(".ri-sl-list").evaluate((list) => {
        const box = list.getBoundingClientRect();
        return [...list.querySelectorAll<HTMLElement>(".ri-lap-row > *, .ri-sl-cols > *")].filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > box.right + 0.5 || r.left < box.left - 0.5); }).map((el) => `${el.className}:${el.textContent}:${Math.round(el.getBoundingClientRect().right - box.right)}`);
      });
      expect(inside).toEqual([]);
      // The weaker proxy label shows for the load-only section (A) and the others carry none.
    });

    test("Runs: a run with a measured reading shows its four tires; a run without shows none", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/s/103/runs`));
      await expect(page.locator(".ri-run-card").first()).toBeVisible();
      await expect(page.locator("[data-run-tires-picture]")).toHaveCount(2);
      await expectNoHorizontalScroll(page);
      if (SHOTS) {
        mkdirSync(SHOTS, { recursive: true });
        await page.screenshot({ path: path.join(SHOTS, `runs-${width}.png`), fullPage: width < 700 });
      }
    });
  });
}
