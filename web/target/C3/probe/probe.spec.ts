import { type Route, test } from "@playwright/test";
import { routeSynthetic } from "../../../src/investigation/fixtures/api";
import { buildRound2Analysis } from "../../../src/investigation/fixtures/round2";
import { DRIVER, hash } from "../../../e2e/app/helpers";
const ANALYSIS = buildRound2Analysis("an-103");
for (const vp of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
  test(`probe ${vp.width}`, async ({ page }) => {
    await page.setViewportSize(vp);
    await page.route("**/api/v1/analyses/an-103/**", async (route: Route) => {
      const url = new URL(route.request().url());
      const r = routeSynthetic(ANALYSIS, `${url.pathname}${url.search}`);
      await route.fulfill({ status: r.status, contentType: r.contentType, body: typeof r.body === "string" ? r.body : Buffer.from(r.body) });
    });
    await page.goto(hash(`/d/${DRIVER}/s/103/traces`));
    await page.locator("[data-analysis-trace-path]").first().waitFor();
    await page.waitForTimeout(300);
    const out = await page.evaluate(() => {
      const kv = [...document.querySelectorAll(".ri-kv")].map((el) => ({ cls: el.className, client: el.clientWidth, scroll: el.scrollWidth }));
      const rows = [...document.querySelectorAll(".trace-row-label-shell")].map((el) => {
        const sig = el.querySelector(".ri-row-signal") as HTMLElement;
        return { name: sig.textContent, h: el.getBoundingClientRect().height, copyH: (el.querySelector(".ri-row-copy") as HTMLElement).getBoundingClientRect().height, sigClient: sig.clientWidth, sigScroll: sig.scrollWidth };
      });
      return { kv, rows };
    });
    console.log(vp.width, JSON.stringify(out));
  });
}
