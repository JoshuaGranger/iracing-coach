import { type Locator, type Page, expect, test } from "@playwright/test";
import { DRIVER, appTest, expectNoHorizontalScroll, expectTouchTargets, hash, mockState } from "./helpers";
import { ROUND2_DEVICES } from "./round2-shared";

const CAR = "NASCAR Xfinity Toyota Supra";
const MX5 = "Global Mazda MX-5 Cup";
/** Screenshots of the round's record: web/target/round3/R2/<name>-<viewport width>.png. */
const SHOTS = "target/round3/R2";

/**
 * A second car with 25 setups added to the mock's answer: the car has more than ten, odd numbers raced (row eleven is raced twice),
 * the rest never. Newest first, so row N is setup N.
 */
async function withManySetups(page: Page, { count = 25, racedCount = true, extraFiles = [] }: { count?: number; racedCount?: boolean; extraFiles?: unknown[] } = {}) {
  await page.route(/\/api\/v1\/drivers\/\d+\/setups(\?.*)?$/, async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    for (let i = 1; i <= count; i++) {
      const at = new Date(Date.parse("2026-10-01T12:00:00Z") - i * 86_400_000).toISOString();
      const raced = i % 2 === 1;
      body.setups.push({
        setup_id: 100 + i,
        customer_id: DRIVER,
        car_path: "mx5-cup",
        car_name: MX5,
        names: [`Lime Rock ${String(i).padStart(2, "0")}.sto`],
        sto_files: [],
        sources: ["recording"],
        observation_count: 4,
        raced,
        ...(raced && racedCount ? { raced_count: i === 11 ? 2 : 1 } : {}),
        first_seen_at: at,
        last_seen_at: at,
      });
    }
    if (extraFiles.length > 0) body.unlinked_files = [...(body.unlinked_files ?? []), ...extraFiles];
    await route.fulfill({ response: res, json: body });
  });
}

/** The scrollable elements below the page itself. */
async function innerScrollRegions(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>("body *")) {
      const cs = getComputedStyle(el);
      if (/(auto|scroll)/.test(cs.overflowY) && el.clientHeight > 0 && el.scrollHeight > el.clientHeight + 1) out.push(`${el.tagName.toLowerCase()}.${el.className}`);
    }
    return out;
  });
}

/** The only inner scroll regions are car sections past ten rows; the page scrolls as well. */
async function expectInnerScrollOnlyInCarSections(page: Page) {
  const regions = await innerScrollRegions(page);
  expect(regions.length, "a car section scrolls inside").toBeGreaterThan(0);
  expect(regions.filter((r) => !/\bs-scroll\b/.test(r)), "an inner scroll region that is not a car section").toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight + 1), "the page scrolls").toBe(true);
}

/** The surface holding the filter controls: the inline row, or the popover or sheet opened from the Filters button. */
async function filterSurface(page: Page): Promise<Locator> {
  const bar = page.locator(".fb");
  await expect(bar).toHaveAttribute("data-layout", /^(inline|collapsed)$/);
  if ((await bar.getAttribute("data-layout")) === "collapsed") {
    await page.getByRole("button", { name: /^Filters/ }).click();
    return page.getByRole("dialog", { name: "Filters" });
  }
  return page.locator(".fb-controls");
}

async function closeFilters(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Filters" });
  if (await dialog.count()) await dialog.getByRole("button", { name: "Done" }).click();
}

const section = (page: Page, car: string) => page.getByRole("region", { name: car, exact: true });
const rows = (car: Locator) => car.locator("[data-entry]");

for (const device of ROUND2_DEVICES) {
  appTest(device, (d) => {
    const phone = d.viewport.width < 700;
    const wide = d.viewport.width >= 1440;
    const shot = async (page: Page, name: string) => page.screenshot({ path: `${SHOTS}/${name}-${d.viewport.width}.png`, fullPage: false });

    test("a row is the name, a raced mark with its count only when the server gave one, and the last date", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(page.getByRole("heading", { name: "Setups", exact: true })).toBeVisible();
      const car = section(page, CAR);
      await expect(car).toBeVisible();
      await expect(car).toContainText("4 setups · 2 raced");
      // one row per name: v3 (two versions), v2, the garage capture, and the three files whose values are unknown
      await expect(rows(car)).toHaveCount(6);
      await expect(car.locator("[data-entry=setup]")).toHaveCount(2);
      const v3 = car.locator("[data-entry=setup]").filter({ hasText: "Bristol race v3.sto" });
      await expect(v3.getByRole("link", { name: "Bristol race v3.sto", exact: true })).toHaveAttribute("href", /#\/d\/\d+\/setups\/\d+$/);
      await expect(v3.locator(".raced")).toHaveText(/Raced 3×/);
      await expect(v3.locator(".date")).toHaveText(/^[A-Z][a-z]{2} \d+, 2026$/);
      await expect(car.locator("[data-entry=setup]").filter({ hasText: "Bristol race v2.sto" }).locator(".raced")).toHaveText(/Raced 1×/);
      // not raced draws nothing
      await expect(car.locator("[data-entry=captures] .raced")).toHaveCount(0);
      // Seen, Values known, Values unknown, Recording and file-name chips are gone
      for (const gone of ["Values known", "Values unknown", "Seen", "sightings", "Recording", "Export"]) await expect(page.getByText(gone, { exact: false })).toHaveCount(0);
      await expect(car.locator(".vk, .vu, .tag")).toHaveCount(0);
      // `No values` only on the files that have none
      await expect(car.locator("[data-entry=file]")).toHaveCount(3);
      await expect(car.locator("[data-entry=file]").first()).toContainText("Imported 1.sto");
      await expect(car.locator("[data-entry=file]").first()).toContainText("No values");
      await expect(car.getByText("No values")).toHaveCount(3);
      await expect(car.locator("[data-entry=captures]")).toContainText("Garage capture");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      await shot(page, "setups");
    });

    test("a raced setup the server gave no count for says Raced and no number", async ({ page }) => {
      await withManySetups(page, { count: 3, racedCount: false });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, MX5);
      await expect(car.locator("[data-entry=setup]").filter({ hasText: "Lime Rock 01.sto" }).locator(".raced")).toHaveText(/^\s*Raced\s*$/);
      await expect(car.locator("[data-entry=setup]").filter({ hasText: "Lime Rock 03.sto" }).locator(".raced")).toHaveAttribute("data-raced", "unknown");
      await expect(car.getByText("0×")).toHaveCount(0);
    });

    test("a car with more than ten setups shows ten rows and scrolls inside at that height; Show 10 more is at the bottom of the region and the page does not grow", async ({ page }) => {
      await withManySetups(page);
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, MX5);
      await expect(car).toContainText("25 setups · 13 raced");
      await expect(rows(car)).toHaveCount(10);
      const region = car.locator(".s-scroll");
      await expect(region).toHaveAttribute("data-scroll", "true");
      await expect(rows(car).filter({ hasText: "Lime Rock 11.sto" })).toHaveCount(0);
      // The ten-row height: the region is as tall as its first ten rows, and holds the button below them.
      const tenRows = () => region.evaluate((el) => [...el.querySelectorAll(":scope > .s-list > .s-entry")].slice(0, 10).reduce((n, r) => n + r.getBoundingClientRect().height, 0));
      const height = async () => (await region.boundingBox())!.height;
      await expect.poll(async () => Math.abs((await height()) - (await tenRows()))).toBeLessThanOrEqual(2);
      const more = region.getByRole("button", { name: "Show 10 more" });
      await expect(more).toHaveCount(1);
      expect(await region.evaluate((el) => el.scrollHeight > el.clientHeight + 1), "the button is below the ten rows, inside the region").toBe(true);
      await region.evaluate((el) => void (el.scrollTop = el.scrollHeight));
      await more.scrollIntoViewIfNeeded();
      await expect(more).toBeVisible();
      const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
      await more.click();
      await expect(rows(car)).toHaveCount(20);
      // The page did not grow and the region kept its height.
      expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(pageHeight);
      expect(Math.abs((await height()) - (await tenRows()))).toBeLessThanOrEqual(2);
      // Row eleven is a raced setup, with its count.
      const eleven = rows(car).nth(10);
      await eleven.scrollIntoViewIfNeeded();
      await expect(eleven).toContainText("Lime Rock 11.sto");
      await expect(eleven.locator(".raced")).toHaveText(/Raced 2×/);
      await expectInnerScrollOnlyInCarSections(page);
      await shot(page, "setups-inner-scroll");
      await region.evaluate((el) => void (el.scrollTop = el.scrollHeight));
      await region.getByRole("button", { name: "Show 10 more" }).click();
      await expect(rows(car)).toHaveCount(25);
      await expect(car.getByRole("button", { name: "Show 10 more" })).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollHeight)).toBe(pageHeight);
      // A car with ten rows or fewer does not scroll.
      const small = section(page, CAR).locator(".s-scroll");
      await expect(small).toHaveAttribute("data-scroll", "false");
      expect(await small.evaluate((el) => el.scrollHeight <= el.clientHeight + 1)).toBe(true);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("the inner scroll is native: it moves with the pointer over it and hands scrolling to the page at its end", async ({ page }) => {
      await withManySetups(page);
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, MX5);
      const region = car.locator(".s-scroll");
      await expect(region).toBeVisible();
      await page.evaluate(() => void ((document.querySelector(".setups-page") as HTMLElement).style.paddingBottom = "1500px"));
      expect(await region.evaluate((el) => getComputedStyle(el).overscrollBehaviorY), "no overscroll containment").toBe("auto");
      expect(await region.evaluate((el) => getComputedStyle(el).overflowY)).toBe("auto");
      await region.evaluate((el) => el.scrollIntoView({ block: "center" }));
      const box = (await region.boundingBox())!;
      const x = Math.min(box.x + box.width / 2, d.viewport.width - 8);
      const y = Math.max(box.y, 0) + Math.min(box.height, d.viewport.height - Math.max(box.y, 0)) / 2;
      await page.mouse.move(x, y);
      // Over the region, mid-way: the region moves, the page stays.
      const page0 = await page.evaluate(() => window.scrollY);
      await page.mouse.wheel(0, 120);
      await expect.poll(() => region.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      expect(await page.evaluate(() => window.scrollY)).toBe(page0);
      // At the region's end the same gesture moves the page.
      await region.evaluate((el) => void (el.scrollTop = el.scrollHeight));
      await page.mouse.wheel(0, 300);
      await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(page0);
    });

    test("a car section collapses and stays collapsed on this device; the revealed rows survive the collapse", async ({ page }) => {
      await withManySetups(page);
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, MX5);
      await car.getByRole("button", { name: "Show 10 more" }).click();
      await expect(rows(car)).toHaveCount(20);
      const toggle = car.getByRole("button", { name: new RegExp(MX5) });
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(rows(car).first()).toBeHidden();
      await expect(car.getByRole("button", { name: "Show 10 more" })).toBeHidden();
      // the header still names the car and counts it
      await expect(car).toContainText("25 setups · 13 raced");
      await shot(page, "setups-collapsed");
      await toggle.click();
      await expect(rows(car).first()).toBeVisible();
      await expect(rows(car)).toHaveCount(20);
      // remembered per device: collapse, leave and come back
      await toggle.click();
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      await page.goBack();
      await expect(section(page, MX5).getByRole("button", { name: new RegExp(MX5) })).toHaveAttribute("aria-expanded", "false");
      await page.reload();
      await expect(section(page, MX5).getByRole("button", { name: new RegExp(MX5) })).toHaveAttribute("aria-expanded", "false");
      await expect(section(page, CAR).getByRole("button", { name: new RegExp(CAR) })).toHaveAttribute("aria-expanded", "true");
      await expectNoHorizontalScroll(page);
    });

    test("the car header sticks under the app bar while its section is on screen and leaves with the section", async ({ page }) => {
      await withManySetups(page);
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, MX5);
      await car.getByRole("button", { name: "Show 10 more" }).click();
      await car.getByRole("button", { name: "Show 10 more" }).click();
      await expect(rows(car)).toHaveCount(25);
      const head = car.locator(".s-car-head");
      const bar = await page.evaluate(() => document.querySelector(".topbar")!.getBoundingClientRect().bottom);
      // mid-section: the header is pinned at the bar
      await page.evaluate(() => {
        const s = [...document.querySelectorAll<HTMLElement>(".s-car")].find((x) => x.getAttribute("aria-label") === "Global Mazda MX-5 Cup")!;
        window.scrollTo(0, s.getBoundingClientRect().top + window.scrollY + 250);
      });
      await expect.poll(async () => Math.round((await head.boundingBox())!.y), { message: "header pinned under the bar" }).toBeGreaterThanOrEqual(Math.floor(bar) - 1);
      const pinned = (await head.boundingBox())!;
      expect(pinned.y).toBeLessThanOrEqual(bar + 1);
      await shot(page, "setups-sticky");
      // a row inside the section moves under it
      // end of section: the header is carried off with it (room below the last section, so the page can scroll that far)
      await page.evaluate(() => void ((document.querySelector(".setups-page") as HTMLElement).style.paddingBottom = "1200px"));
      await page.evaluate((barBottom) => {
        const s = [...document.querySelectorAll<HTMLElement>(".s-car")].find((x) => x.getAttribute("aria-label") === "Global Mazda MX-5 Cup")!;
        window.scrollTo(0, s.getBoundingClientRect().bottom + window.scrollY - barBottom - 20);
      }, bar);
      const sec = (await car.boundingBox())!;
      await expect.poll(async () => (await head.boundingBox())!.y + (await head.boundingBox())!.height).toBeLessThanOrEqual(sec.y + sec.height + 1.5);
      expect((await head.boundingBox())!.y).toBeLessThan(bar - 1);
    });

    test("the toolbar is Car, Name and All, Raced, Not raced in the heading, Find a baseline stays, Progressive tuning is gone", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const bar = page.locator(".fb");
      await expect(bar).toHaveAttribute("data-layout", /^(inline|collapsed)$/);
      await expect(page.getByRole("link", { name: "Find a baseline" })).toHaveAttribute("href", new RegExp(`#/d/${DRIVER}/setups/baseline$`));
      await expect(page.getByText("Progressive tuning")).toHaveCount(0);
      if (wide) {
        await expect(bar).toHaveAttribute("data-layout", "inline");
        const controls = page.locator(".fb-controls");
        await expect(controls.getByRole("button", { name: /^Car/ })).toBeVisible();
        await expect(controls.getByPlaceholder("Name")).toBeVisible();
        await expect(controls.getByRole("group", { name: "Raced" }).getByRole("button")).toHaveText(["All", "Raced", "Not raced"]);
        // the controls sit at the heading's right, on the heading's row
        const h1 = (await bar.getByRole("heading", { name: "Setups" }).boundingBox())!;
        const pill = (await controls.getByRole("button", { name: /^Car/ }).boundingBox())!;
        expect(pill.x).toBeGreaterThan(h1.x + h1.width);
      }
      if (phone) {
        await expect(bar).toHaveAttribute("data-layout", "collapsed");
        await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
        const surface = await filterSurface(page);
        await expect(surface.getByRole("button", { name: /^Car/ })).toBeVisible();
        await expect(surface.getByPlaceholder("Name")).toBeVisible();
        await expect(surface.getByRole("group", { name: "Raced" })).toBeVisible();
        await shot(page, "setups-filters");
      }
      await expectNoHorizontalScroll(page);
    });

    test("filters by raced and by name through the toolbar, and the selection survives a visit to a setup and Back", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups`));
      const car = section(page, CAR);
      let surface = await filterSurface(page);
      await surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Raced", exact: true }).click();
      await closeFilters(page);
      await expect(rows(car)).toHaveCount(2);
      await expect(car.locator("[data-entry=file]")).toHaveCount(0);
      await expect(car).toContainText("2 setups · 2 raced");
      if ((await page.locator(".fb").getAttribute("data-layout")) === "collapsed") await expect(page.getByRole("button", { name: "Filters · 1" })).toBeVisible();
      await expect(page.locator(".fb-row").getByRole("button", { name: "Clear" })).toHaveCount(0);
      // the selection survives going to a setup and coming Back
      await car.getByRole("link", { name: "Bristol race v3.sto" }).click();
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      await page.goBack();
      await expect(rows(section(page, CAR))).toHaveCount(2);
      surface = await filterSurface(page);
      await expect(surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Raced", exact: true })).toHaveAttribute("aria-pressed", "true");
      await surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Not raced" }).click();
      await closeFilters(page);
      await expect(car.locator("[data-entry=setup]")).toHaveCount(1);
      await expect(car.locator("[data-entry=file]")).toHaveCount(3);
      surface = await filterSurface(page);
      await surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "All" }).click();
      await surface.getByPlaceholder("Name").fill("v2");
      await closeFilters(page);
      await expect(rows(car)).toHaveCount(1);
      await expect(car).toContainText("Bristol race v2.sto");
      // The name field clears itself; there is no Clear button beside the controls.
      surface = await filterSurface(page);
      await surface.getByPlaceholder("Name").fill("");
      await expect(page.getByRole("button", { name: "Clear", exact: true })).toHaveCount(0);
      await closeFilters(page);
      await expect(rows(car)).toHaveCount(6);
      await expectNoHorizontalScroll(page);
    });

    test("the car filter is a searchable multi-select; the button carries the car and clears from inside its list; no chips", async ({ page }) => {
      await withManySetups(page, { count: 3 });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, MX5)).toBeVisible();
      await expect(section(page, CAR)).toBeVisible();
      // colour is the car's: two sections, two stripe colours
      const colours = await page.locator(".s-car").evaluateAll((els) => els.map((e) => getComputedStyle(e).getPropertyValue("--car").trim()));
      expect(new Set(colours).size).toBe(2);
      let surface = await filterSurface(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
      const panel = page.getByRole("group", { name: "Car filter" });
      await expect(panel.getByRole("combobox", { name: "Search car" })).toBeFocused();
      await expect(panel).toContainText("0 of 2 cars");
      await panel.getByRole("combobox", { name: "Search car" }).fill("mazda");
      await expect(panel.getByRole("option")).toHaveCount(1);
      await panel.getByRole("option", { name: /Global Mazda/ }).click();
      await expect(panel).toContainText("1 of 2 cars");
      await expect(section(page, CAR)).toHaveCount(0);
      await page.keyboard.press("Escape");
      await expect(panel).toHaveCount(0);
      await expect(surface.getByRole("button", { name: `Car · ${MX5}` })).toBeVisible();
      await closeFilters(page);
      await expect(page.locator(".filter-chip, .fb-chips")).toHaveCount(0);
      await expect(page.locator(".fb-row").getByRole("button", { name: "Clear" })).toHaveCount(0);
      // Clear is the first row of the dropdown.
      surface = await filterSurface(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("group", { name: "Car filter" }).getByRole("button", { name: "Clear" }).click();
      await page.keyboard.press("Escape");
      await closeFilters(page);
      await expect(section(page, CAR)).toBeVisible();
      await expect(section(page, MX5)).toBeVisible();
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("the heading does not move when a filter is set or cleared", async ({ page }) => {
      await withManySetups(page, { count: 3 });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, MX5)).toBeVisible();
      const boxes = () =>
        page.evaluate(() =>
          [...document.querySelectorAll(".fb-row h1, .fb-row a, .fb-row .fb-controls > *, .fb-row .fb-trigger")].map((e) => {
            const r = e.getBoundingClientRect();
            return [r.x, r.y, r.width, r.height].map((n) => Math.round(n * 10) / 10);
          }),
        );
      const before = await boxes();
      let surface = await filterSurface(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("option", { name: /Global Mazda/ }).click();
      await page.keyboard.press("Escape");
      await surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "Raced", exact: true }).click();
      await closeFilters(page);
      expect(await boxes()).toEqual(before);
      surface = await filterSurface(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
      await page.getByRole("group", { name: "Car filter" }).getByRole("button", { name: "Clear" }).click();
      await page.keyboard.press("Escape");
      await surface.getByRole("group", { name: "Raced" }).getByRole("button", { name: "All", exact: true }).click();
      await closeFilters(page);
      expect(await boxes()).toEqual(before);
    });

    test("the Car dropdown lists the cars that have setups, not cars that only have files, and a name search does not shrink it", async ({ page }) => {
      await withManySetups(page, {
        count: 3,
        extraFiles: [{ sha256: "c".repeat(64), car_folder: "dallarair18", relative_path: "dallarair18/Loose.sto", format: "sto", present: true, linked_setup_id: null }],
      });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, MX5)).toBeVisible();
      await expect(section(page, "dallarair18")).toBeVisible();
      const surface = await filterSurface(page);
      await surface.getByRole("button", { name: /^Car/ }).click();
      const panel = page.getByRole("group", { name: "Car filter" });
      await expect(panel.getByRole("option")).toHaveCount(2);
      await expect(panel.getByRole("option", { name: /dallarair18/ })).toHaveCount(0);
      await expect(panel.getByRole("option", { name: new RegExp(MX5) })).toContainText("3");
      await expect(panel).toContainText("0 of 2 cars");
      await page.keyboard.press("Escape");
      // A name that only one car has leaves the other sections out but not the other cars' entries in the dropdown.
      await surface.getByPlaceholder("Name").fill("Lime Rock");
      await expect(section(page, CAR)).toHaveCount(0);
      await expect(section(page, MX5)).toBeVisible();
      await surface.getByRole("button", { name: /^Car/ }).click();
      await expect(page.getByRole("group", { name: "Car filter" }).getByRole("option")).toHaveCount(2);
    });

    test("no Library control; the lab library only by ?library=lab in the URL", async ({ page }) => {
      const asked: string[] = [];
      page.on("request", (r) => {
        if (/\/api\/v1\/drivers\/\d+\/setups(\?|$)/.test(r.url())) asked.push(new URL(r.url()).searchParams.get("library") ?? "(none)");
      });
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, CAR)).toBeVisible();
      await expect(page.getByText("Library")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Lab" })).toHaveCount(0);
      expect([...new Set(asked)]).toEqual(["(none)"]);
      await page.goto(hash(`/d/${DRIVER}/setups?library=lab`));
      await expect(page.getByText("bot sweep.sto")).toBeVisible();
      expect(asked).toContain("lab");
      await page.goto(hash(`/d/${DRIVER}/setups`));
      await expect(section(page, CAR)).toBeVisible();
      await expect(page.getByText("bot sweep.sto")).toHaveCount(0);
    });

    test("detail: the name once, raced count and first seen, no source or file-name chip, the file lower down, one tuning entry", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const head = page.locator("header.d-head");
      await expect(head.getByRole("heading", { name: "Bristol race v3.sto", level: 1 })).toBeVisible();
      expect(((await head.textContent())!.match(/Bristol race v3\.sto/gi) ?? []).length, "the name appears once in the header").toBe(1);
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      await expect(head.locator(".raced")).toHaveText(/Raced 3×/);
      await expect(head).toContainText(/First seen [A-Z][a-z]{2} \d+, 2026/);
      await expect(head).toContainText(CAR);
      await expect(head.locator(".tag, .vk, .vu, .chip")).toHaveCount(0);
      await expect(page.getByLabel("Sources")).toHaveCount(0);
      await expect(head.getByText("Recording")).toHaveCount(0);
      // the file name identifies the artifact lower down
      const file = page.getByRole("region", { name: "File" });
      await expect(file).toContainText("nascar-xfinity/Bristol race v3.sto");
      const order = await page.evaluate(() => {
        const y = (sel: string) => document.querySelector(sel)!.getBoundingClientRect().top + window.scrollY;
        return { head: y("header.d-head"), file: y("[aria-label=File]"), values: y("[aria-label='Setup values']") };
      });
      expect(order.file).toBeGreaterThan(order.values);
      // one tuning entry
      await expect(page.getByRole("button", { name: "Tune this setup" })).toHaveCount(1);
      await expect(page.getByText("Progressive tuning")).toHaveCount(0);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
      await shot(page, "setup-detail");
    });

    test("detail: a raced setup without a count says Raced alone; a setup with no decoded values says No values", async ({ page }) => {
      await page.route(/\/api\/v1\/setups\/1$/, async (route) => {
        const res = await route.fetch();
        const body = await res.json();
        delete body.setup.raced_count;
        body.values = {};
        await route.fulfill({ response: res, json: body });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const head = page.locator("header.d-head");
      await expect(head.locator(".raced")).toHaveText(/^\s*Raced\s*$/);
      await expect(page.getByRole("region", { name: "Setup values" })).toContainText("No values");
      await expect(page.getByText("Values unknown")).toHaveCount(0);
    });

    test("detail: Versions is collapsed, lists versions by date with changed counts, and a version opens to its changed keys and runs", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const compares: string[] = [];
      page.on("request", (r) => {
        if (r.method() === "POST" && /\/setups\/compare$/.test(new URL(r.url()).pathname)) compares.push(r.url());
      });
      const versions = page.getByTestId("versions");
      const toggle = versions.getByRole("button", { name: /^Versions/ });
      await expect(toggle).toHaveAttribute("aria-expanded", "false");
      await expect(versions.locator("[data-version]").first()).toBeHidden();
      await expect(versions).toContainText("2");
      expect(compares, "nothing is compared until the section opens").toEqual([]);
      await toggle.click();
      await expect(toggle).toHaveAttribute("aria-expanded", "true");
      const v2 = versions.locator("[data-version='2']");
      const v1 = versions.locator("[data-version='1']");
      await expect(v2).toBeVisible();
      await expect(v2).toHaveAttribute("aria-current", "true");
      await expect(v2).toContainText(/Sep \d+, 2026/);
      await expect(v2).toContainText("1 changed");
      await expect(v1).toContainText(/Sep \d+, 2026/);
      await expect(v1).not.toContainText("changed");
      expect(compares.length, "one comparison per version pair").toBe(1);
      // newest first
      const order = await versions.locator("[data-version]").evaluateAll((els) => els.map((e) => e.getAttribute("data-version")));
      expect(order).toEqual(["2", "1"]);
      await v2.getByRole("button").first().click();
      const changes = v2.getByRole("table", { name: "Changed settings" });
      await expect(changes.locator("tbody tr")).toHaveCount(1);
      const row = changes.locator("tbody tr").first();
      await expect(row.getByRole("rowheader")).toHaveText("Chassis · Left front · Camber");
      await expect(row.locator("td").nth(0)).toHaveText("-3.4");
      await expect(row.locator("td").nth(1)).toHaveText("-3.2");
      await expect(row.locator("td").nth(2)).toHaveText("deg");
      const runs = v2.getByRole("region", { name: "Recorded runs" });
      await expect(runs.getByRole("link", { name: "Session 103" })).toHaveAttribute("href", /#\/d\/\d+\/s\/103\/race/);
      // the viewed version has no Open link; the other has one
      await expect(v2.getByRole("link", { name: /^Open version/ })).toHaveCount(0);
      await v1.getByRole("button").first().click();
      await expect(v1.getByRole("region", { name: "Recorded runs" }).getByRole("link", { name: "Session 112" })).toBeVisible();
      await expect(v1.getByRole("link", { name: "Open version 1" })).toHaveAttribute("href", /#\/d\/\d+\/setups\/4$/);
      await expect(v1.getByRole("table")).toHaveCount(0);
      // no effect claim and no tire life
      await expect(versions).not.toContainText(/pace|tire life|faster|slower/i);
      await shot(page, "setup-versions");
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("detail groups values, keeps readouts and run readings apart, and compares with changed and unknown first", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await expect(page.getByRole("heading", { name: "Bristol race v3.sto" })).toBeVisible();
      const values = page.getByRole("region", { name: "Setup values" });
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).toContainText("-3.2 deg");
      await expect(values.getByRole("region", { name: "Chassis · Left front" }).first()).not.toContainText("Corner weight");
      const readouts = values.getByRole("region", { name: "Garage readouts" });
      await expect(readouts.getByRole("heading", { name: "Garage readouts" })).toBeVisible();
      await expect(readouts).toContainText("Corner weight");
      await expect(readouts).toContainText("Ride height");
      const readings = values.getByRole("region", { name: "Run readings" });
      await expect(readings).toContainText("Last hot pressure");
      await expect(readings).not.toContainText("Corner weight");
      await expect(values.getByRole("region", { name: "Tires aero · Left front tire" }).first()).not.toContainText("Last hot pressure");
      await expect(page.getByLabel("With").locator("option").filter({ hasText: "Bristol race v3.sto" })).toContainText(/Sep \d+, 2026/);
      await expect(page.getByRole("region", { name: "Sightings" }).getByText("Unmodified").first()).toBeVisible();
      await expect(page.getByRole("region", { name: "Sightings" }).getByRole("link", { name: "Session 103" })).toBeVisible();

      await page.getByLabel("With").selectOption("2");
      const summary = page.getByTestId("compare-summary");
      await expect(summary).toContainText("3 changed");
      await expect(summary).toContainText("1 unknown");
      await expect(summary).toContainText("1 readout moved");
      const heads = page.locator("table thead th");
      await expect(heads.nth(1)).toContainText("Bristol race v3.sto");
      await expect(heads.nth(1)).toContainText(/Sep \d+, 2026/);
      await expect(heads.nth(2)).toContainText("Bristol race v2.sto");
      const rowsOf = page.locator("table tbody tr");
      await expect(rowsOf.first()).toHaveAttribute("data-kind", "changed");
      await expect(page.locator("tr[data-kind=identical]")).toHaveCount(0);
      await expect(rowsOf.first().locator("th")).toHaveText(/^[A-Z][^.]* · /);
      await page.getByRole("button", { name: "Show all values" }).click();
      await expect(page.locator("tr[data-kind=identical]").first()).toBeVisible();
      await expect(page.locator("tr[data-kind=unknown]")).toContainText("Track bar height");
      const order = await page.locator("table tbody tr").evaluateAll((trs) => trs.map((tr) => (tr.classList.contains("group-head") ? "head" : `${tr.getAttribute("data-kind")}${tr.getAttribute("data-readout") ? "-readout" : ""}`)));
      const head = order.indexOf("head");
      expect(head).toBeGreaterThan(0);
      expect(order.slice(0, head)).not.toContain("identical-readout");
      expect(order.slice(head + 1).every((k) => k.endsWith("-readout"))).toBe(true);
      expect(order.slice(0, head).filter((k) => k === "identical").length).toBeGreaterThan(0);
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("compare offers only setups that compare, and gives the others a reason", async ({ page }) => {
      await page.route(/\/api\/v1\/drivers\/\d+\/setups\?.*car_path=/, async (route) => {
        const res = await route.fetch();
        const body = await res.json();
        body.setups.push({ ...body.setups[0], setup_id: 95, names: ["Imported sheet.htm"], sources: ["htm_export"], observation_count: 1, raced: false });
        await route.fulfill({ response: res, json: body });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const option = page.getByLabel("With").locator("option").filter({ hasText: "Imported sheet.htm" });
      await expect(option).toHaveAttribute("disabled", "");
      await expect(option).toContainText("Export only");
      await expect(page.getByLabel("With").locator("option").filter({ hasText: "Bristol race v2.sto" })).not.toHaveAttribute("disabled", "");
    });

    test("a note on a setup is queued, saved, and listed", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      await page.getByPlaceholder("Add a note").fill("Loose on exit at lap 40");
      await page.getByRole("button", { name: "Add note" }).click();
      await expect(page.getByRole("region", { name: "Notes" }).getByText("Loose on exit at lap 40")).toBeVisible();
      await expect(page.getByTestId("outbox")).toHaveCount(0);
      expect((await mockState(request)).notes).toHaveLength(1);
      await page.getByRole("button", { name: "Edit" }).click();
      await page.getByLabel("Edit note").fill("Loose on exit, laps 40 to 60");
      await page.getByRole("button", { name: "Save" }).click();
      await expect(page.getByText("Loose on exit, laps 40 to 60")).toBeVisible();
      await page.getByRole("button", { name: "Delete" }).click();
      await page.getByRole("button", { name: "Confirm delete" }).click();
      await expect(page.getByText("Loose on exit, laps 40 to 60")).toHaveCount(0);
    });

    test("tune this setup opens tuning for the recording that used it", async ({ page }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/1`));
      const sessionRequests: string[] = [];
      page.on("request", (r) => {
        if (/\/api\/v1\/sessions\/\d+$/.test(new URL(r.url()).pathname)) sessionRequests.push(r.url());
      });
      await page.getByRole("button", { name: "Tune this setup" }).click();
      await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/tuning/an-103$`));
      await expect(page.getByRole("heading", { name: "Bristol Motor Speedway - Oval" })).toBeVisible();
      expect(sessionRequests, "the setup's observation carries the analysis id").toEqual([]);
    });

    test("find a baseline: suggestions only, the car folder is sent, then a starting point with its cautions", async ({ page, request }) => {
      await page.goto(hash(`/d/${DRIVER}/setups/baseline`));
      const find = page.getByRole("button", { name: "Find starting point" });
      await expect(find).toBeDisabled();
      await page.getByLabel("Car", { exact: true }).fill("Ferrari");
      await page.getByLabel("Track and layout").fill("Martinsville Speedway");
      await expect(find).toBeEnabled();
      await expect(page.getByText("from your recordings")).toHaveCount(0);
      await page.getByLabel("Car", { exact: true }).fill(CAR);
      await page.getByLabel("Track and layout").fill("Bristol Motor Speedway - Oval");
      await page.getByLabel("Season").fill("26S4");
      await find.click();
      const result = page.getByTestId("baseline-result");
      await expect(result).toBeVisible();
      await expect(result).toContainText("Bristol race v3");
      await expect(result).toContainText("New Hampshire");
      await expect(result).toContainText("Source files unchanged");
      await expect(result.locator("[data-candidate=race]")).toContainText("Opaque");
      await expect(result.locator("[data-candidate=race]")).toContainText("bbbbbbbb");
      await expect(result.locator("[data-candidate=qualifying]")).toContainText("None");
      await expect(result.getByRole("list", { name: "Checks" }).getByRole("listitem")).toHaveCount(4);
      await expect(result.getByRole("list", { name: "Cautions" })).toContainText("Transfer the tuning logic");
      const writes = (await mockState(request)).writes;
      expect(writes.at(-1)!.body).toMatchObject({ car: "nascar-xfinity", track: "Bristol Motor Speedway - Oval", season: "2026S4" });
      await expectNoHorizontalScroll(page);
      if (phone) await expectTouchTargets(page);
    });

    test("find a baseline: no matching setup file is not a starting point", async ({ page }) => {
      await page.route(/\/api\/v1\/jobs\/\d+$/, async (route) => {
        const res = await route.fetch();
        const job = await res.json();
        if (job.state === "succeeded") job.result = { ok: false, status: "needs-baseline-export-or-research", package_id: "p", package_path: "p", context: {}, baseline: null, baseline_confirmation: { status: "none", confirmed: false, reason: "No match" }, qualifying: null, donor: { donor: "New Hampshire", reason: "flat" }, simulator_loadable_setup_produced: false, source_setup_files_modified: false };
        await route.fulfill({ response: res, json: job });
      });
      await page.goto(hash(`/d/${DRIVER}/setups/baseline`));
      await page.getByLabel("Car", { exact: true }).fill(CAR);
      await page.getByLabel("Track and layout").fill("Bristol Motor Speedway - Oval");
      await page.getByRole("button", { name: "Find starting point" }).click();
      await expect(page.getByText("No setup file for this car, track and season.")).toBeVisible();
      await expect(page.getByTestId("baseline-result")).toHaveCount(0);
      await expect(page.getByText("Source files unchanged")).toHaveCount(0);
      await expect(page.getByText("New Hampshire")).toHaveCount(0);
    });
  });
}
