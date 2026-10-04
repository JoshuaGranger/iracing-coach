# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: sessions.spec.ts >> tablet 1024x768 >> filters combine across and within; the Session filter offers practice, qualifying and race
- Location: e2e\app\sessions.spec.ts:468:5

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('li.session-row')
Expected: 2
Received: 4
Timeout:  5000ms

Call log:
  - Expect "toHaveCount" locator('li.session-row') with timeout 5000ms
  - waiting for locator('li.session-row')
    14 × locator resolved to 4 elements
       - unexpected value "4"

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - banner [ref=e4]:
    - link "iRacing Coach" [ref=e5] [cursor=pointer]:
      - /url: "#/"
    - navigation "Main" [ref=e8]:
      - link "Sessions" [ref=e9] [cursor=pointer]:
        - /url: "#/d/1507810/sessions"
      - link "Setups" [ref=e10] [cursor=pointer]:
        - /url: "#/d/1507810/setups"
      - link [ref=e11] [cursor=pointer]:
        - /url: "#/d/1507810/live"
        - text: Live
        - img "Live now" [ref=e12]
    - generic [ref=e13]: Joshua Granger
  - main [ref=e14]:
    - generic [ref=e16]:
      - generic [ref=e18]:
        - generic [ref=e19]:
          - heading "Sessions" [level=1] [ref=e20]
          - link "Live now" [ref=e21] [cursor=pointer]:
            - /url: "#/d/1507810/live"
        - button "Filters · 1" [active] [ref=e23] [cursor=pointer]
      - generic [ref=e27]:
        - region "Bristol Motor Speedway - Oval" [ref=e28]:
          - generic [ref=e29]:
            - generic [ref=e30]:
              - heading "Bristol Motor Speedway" [level=2] [ref=e36]
              - generic [ref=e37]:
                - generic [ref=e38]: Oval · Mon Sep 28
                - generic "Fixed setup" [ref=e39]: Fixed
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e45]: NASCAR Xfinity Toyota Supra
            - list [ref=e46]:
              - listitem [ref=e47]:
                - link "Race, Bristol Motor Speedway - Oval, Mon Sep 28, 2:00 pm" [ref=e48] [cursor=pointer]:
                  - /url: "#/d/1507810/s/103/race"
                  - generic [ref=e49]:
                    - generic [ref=e50]:
                      - generic [ref=e51]: 2:00 pm · 2 rec
                      - generic [ref=e52]: Race
                    - generic "Finished P4, started P12" [ref=e53]:
                      - generic [ref=e54]: Finish
                      - generic [ref=e55]:
                        - generic [ref=e56]: P4
                        - generic [ref=e57]: ▲8
                    - generic "Green · caution laps" [ref=e58]:
                      - generic [ref=e59]: Grn · Cau
                      - generic [ref=e60]:
                        - generic [ref=e61]: "60"
                        - generic [ref=e62]: ·
                        - generic [ref=e63]: "20"
                    - generic "Best clean lap, fastest of the event" [ref=e64]:
                      - generic [ref=e65]: Best
                      - generic [ref=e66]: "15.234"
                    - generic "Green pace and its trend, s per lap" [ref=e68]:
                      - generic [ref=e69]: Pace
                      - generic [ref=e70]:
                        - generic [ref=e71]: "15.612"
                        - generic [ref=e72]: "+0.021"
                    - generic "Pit stops" [ref=e73]:
                      - generic [ref=e74]: Stops
                      - generic [ref=e75]: "3"
                    - generic "Fuel used, gallons" [ref=e77]:
                      - generic [ref=e78]: Fuel
                      - generic [ref=e79]:
                        - generic [ref=e80]: "21.6"
                        - generic [ref=e81]: gal
                    - generic "Longest green run, laps" [ref=e82]:
                      - generic [ref=e83]: Run
                      - generic [ref=e84]: "41"
        - region "Indianapolis Raceway Park - Oval" [ref=e86]:
          - generic [ref=e87]:
            - generic [ref=e88]:
              - heading "Indianapolis Raceway Park" [level=2] [ref=e94]
              - generic [ref=e95]:
                - generic [ref=e96]: Oval · Fri Sep 25
                - generic "Open setup" [ref=e97]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e102]: NASCAR Xfinity Toyota Supra
            - list [ref=e103]:
              - listitem [ref=e104]:
                - group "Race, Indianapolis Raceway Park - Oval, Fri Sep 25, 2:00 pm" [ref=e105]:
                  - generic [ref=e106]:
                    - generic [ref=e107]:
                      - generic [ref=e108]: 2:00 pm
                      - generic [ref=e109]: Race
                    - generic [ref=e110]:
                      - generic [ref=e111]: Laps
                      - generic [ref=e112]: "83"
                  - generic [ref=e115]:
                    - text: Not analyzed
                    - button "Analyze" [ref=e116] [cursor=pointer]
        - region "New Hampshire Motor Speedway - Oval" [ref=e117]:
          - generic [ref=e118]:
            - generic [ref=e119]:
              - heading "New Hampshire Motor Speedway" [level=2] [ref=e125]
              - generic [ref=e126]:
                - generic [ref=e127]: Oval · Sat Sep 19
                - generic "Fixed setup" [ref=e128]: Fixed
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e134]: NASCAR Xfinity Toyota Supra
            - list [ref=e135]:
              - listitem [ref=e136]:
                - link "Race, New Hampshire Motor Speedway - Oval, Sat Sep 19, 2:00 pm" [ref=e137] [cursor=pointer]:
                  - /url: "#/d/1507810/s/110/race"
                  - generic [ref=e138]:
                    - generic [ref=e139]:
                      - generic [ref=e140]: 2:00 pm
                      - generic [ref=e141]: Race
                    - generic "Finished P1, started P3" [ref=e142]:
                      - generic [ref=e143]: Finish
                      - generic [ref=e144]:
                        - generic [ref=e145]: P1
                        - generic [ref=e146]: ▲2
                    - generic "Green · caution laps" [ref=e147]:
                      - generic [ref=e148]: Grn · Cau
                      - generic [ref=e149]:
                        - generic [ref=e150]: "66"
                        - generic [ref=e151]: ·
                        - generic [ref=e152]: "0"
                    - generic "Best clean lap" [ref=e153]:
                      - generic [ref=e154]: Best
                      - generic [ref=e155]: "15.534"
                    - generic "Green pace and its trend, s per lap" [ref=e157]:
                      - generic [ref=e158]: Pace
                      - generic [ref=e159]:
                        - generic [ref=e160]: "15.912"
                        - generic [ref=e161]: "+0.031"
                    - generic "Pit stops" [ref=e162]:
                      - generic [ref=e163]: Stops
                      - generic [ref=e164]: "3"
                    - generic "Fuel used, gallons" [ref=e166]:
                      - generic [ref=e167]: Fuel
                      - generic [ref=e168]:
                        - generic [ref=e169]: "18.6"
                        - generic [ref=e170]: gal
                    - generic "Longest green run, laps" [ref=e171]:
                      - generic [ref=e172]: Run
                      - generic [ref=e173]: "38"
        - region "Bristol Motor Speedway - Oval" [ref=e175]:
          - generic [ref=e176]:
            - generic [ref=e177]:
              - heading "Bristol Motor Speedway" [level=2] [ref=e183]
              - generic [ref=e184]:
                - generic [ref=e185]: Oval · Wed Sep 16
                - generic "Open setup" [ref=e186]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e191]: NASCAR Xfinity Toyota Supra
            - list [ref=e192]:
              - listitem [ref=e193]:
                - group "Race, Bristol Motor Speedway - Oval, Wed Sep 16, 2:00 pm" [ref=e194]:
                  - generic [ref=e195]:
                    - generic [ref=e196]:
                      - generic [ref=e197]: 2:00 pm
                      - generic [ref=e198]: Race
                    - generic [ref=e199]:
                      - generic [ref=e200]: Laps
                      - generic [ref=e201]: "86"
                  - generic [ref=e203]: Deferred · exceeds memory budget
```

# Test source

```ts
  391 |       await expect(page.getByRole("link", { name: /^Race, Indianapolis Raceway Park - Oval/ })).toHaveCount(0);
  392 |       if (d.touch) await expectTouchTargets(page);
  393 |     });
  394 | 
  395 |     test("analyze queues the job and the phase opens when the server says so", async ({ page }) => {
  396 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  397 |       const waiting = page.locator("li.session-row[data-state=none]");
  398 |       await expect(page.locator("li.session-row").first()).toBeVisible();
  399 |       const before = await waiting.count();
  400 |       expect(before).toBeGreaterThan(0);
  401 |       await waiting.first().getByRole("button", { name: "Analyze" }).click();
  402 |       await expect(waiting).toHaveCount(before - 1, { timeout: 6000 });
  403 |       await expect(page.locator("li.session-row[data-state=ready]").first()).toBeVisible();
  404 |     });
  405 | 
  406 |     test("the filters sit in the heading at 1440 and collapse to one button below that; no Library control, no Clear beside the heading", async ({ page }) => {
  407 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  408 |       await expect(bristol(page)).toBeVisible();
  409 |       await expect(page.locator(".filter-bar.fb-chips")).toHaveCount(0);
  410 |       if (wide) {
  411 |         await expect(bar(page)).toHaveAttribute("data-layout", "inline");
  412 |         const head = (await bar(page).locator(".fb-row").boundingBox())!;
  413 |         const h1 = (await bar(page).getByRole("heading", { name: "Sessions" }).boundingBox())!;
  414 |         for (const name of ["Car", "Track", "Series", "Session", "Setup"]) {
  415 |           const b = (await bar(page).locator(".fb-row").getByRole("button", { name: new RegExp(`^${name}`) }).boundingBox())!;
  416 |           expect(b.x, `${name} is right of the title`).toBeGreaterThan(h1.x + h1.width);
  417 |           expect(b.y + b.height, `${name} is on the heading row`).toBeLessThanOrEqual(head.y + head.height + 1);
  418 |         }
  419 |         await expect(page.getByRole("button", { name: /^Filters/ })).toHaveCount(0);
  420 |       } else {
  421 |         await expect(bar(page)).toHaveAttribute("data-layout", "collapsed");
  422 |         await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
  423 |         await expect(page.getByRole("button", { name: /^Car/ })).toHaveCount(0);
  424 |         await page.getByRole("button", { name: "Filters", exact: true }).click();
  425 |         const surface = page.getByRole("dialog", { name: "Filters" });
  426 |         await expect(surface).toHaveClass(d.touch ? /fb-sheet/ : /fb-pop/);
  427 |         for (const name of ["Car", "Track", "Series", "Session", "Setup"]) await expect(surface.getByRole("button", { name: new RegExp(`^${name}`) })).toBeVisible();
  428 |       }
  429 |       await expect(page.locator(".lib-ctl")).toHaveCount(0);
  430 |       await expect(page.getByRole("button", { name: "Lab", exact: true })).toHaveCount(0);
  431 |       await expect(page.locator(".filter-clear")).toHaveCount(0);
  432 |       await expectNoHorizontalScroll(page);
  433 |     });
  434 | 
  435 |     test("searchable multi-select filters; the button carries the choice and clears from inside its list", async ({ page }) => {
  436 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  437 |       await expect(bristol(page)).toBeVisible();
  438 |       await expect(page.getByRole("button", { name: "Tests" })).toHaveCount(0);
  439 |       const surface = await openFilters(page);
  440 |       await surface.getByRole("button", { name: /^Car/ }).click();
  441 |       const search = page.getByRole("combobox", { name: "Search car" });
  442 |       await expect(search).toBeFocused();
  443 |       await expect(page.getByRole("option")).toHaveCount(2);
  444 |       await search.fill("mazda");
  445 |       await expect(page.getByRole("option")).toHaveCount(1);
  446 |       await expect(page.locator(".ms-opt-label u")).toHaveText("Mazda");
  447 |       await page.getByRole("option", { name: /Global Mazda MX-5 Cup/ }).click();
  448 |       await expect(page.getByRole("option", { name: /Global Mazda MX-5 Cup/ })).toHaveAttribute("aria-selected", "true");
  449 |       await expect(page.locator(".ms-foot")).toContainText("1 of 2 cars");
  450 |       await page.keyboard.press("Escape");
  451 |       await closeFilters(page);
  452 |       await expect(page.getByRole("button", { name: /^Remove/ })).toHaveCount(0);
  453 |       await expect(page.locator("section.sx-event")).toHaveCount(1);
  454 |       await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" })).toBeVisible();
  455 |       if (!phone && !wide) await expect(page.getByRole("button", { name: "Filters · 1" })).toBeVisible();
  456 | 
  457 |       // Clear lives inside the list.
  458 |       const again = await openFilters(page);
  459 |       await again.getByRole("button", { name: /^Car · Global Mazda/ }).click();
  460 |       await page.locator(".ms-clear-row").click();
  461 |       await page.keyboard.press("Escape");
  462 |       await closeFilters(page);
  463 |       expect(await page.locator("section.sx-event").count()).toBeGreaterThan(3);
  464 |       await expectNoHorizontalScroll(page);
  465 |       if (d.touch) await expectTouchTargets(page);
  466 |     });
  467 | 
  468 |     test("filters combine across and within; the Session filter offers practice, qualifying and race", async ({ page }) => {
  469 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  470 |       let surface = await openFilters(page);
  471 |       await surface.getByRole("button", { name: /^Session/ }).click();
  472 |       await expect(page.locator(".ms-opt-label")).toHaveText(["Practice", "Qualifying", "Race"]);
  473 |       await expect(page.getByRole("combobox", { name: "Search session" })).toBeFocused();
  474 |       await page.keyboard.press("ArrowDown");
  475 |       await page.keyboard.press("ArrowDown");
  476 |       await page.keyboard.press("Enter");
  477 |       await expect(page.getByRole("option", { name: /Race/ })).toHaveAttribute("aria-selected", "true");
  478 |       await page.keyboard.press("Escape");
  479 |       await closeFilters(page);
  480 |       await expect(page.locator("li.session-row")).toHaveCount(4);
  481 |       await expect(page.locator("li.session-row .sx-phase")).toHaveText(["Race", "Race", "Race", "Race"]);
  482 |       // Races only: one column, the race panel as wide as the phases area inside its gutter.
  483 |       const only = (await page.locator("li.session-row").first().boundingBox())!;
  484 |       const inner = await page.locator(".sx-cells").first().evaluate((el) => {
  485 |         const cs = getComputedStyle(el);
  486 |         return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  487 |       });
  488 |       expect(Math.abs(only.width - inner)).toBeLessThan(2);
  489 | 
  490 |       await choose(page, "Setup", /Fixed/);
> 491 |       await expect(page.locator("li.session-row")).toHaveCount(2);
      |                                                    ^ Error: expect(locator).toHaveCount(expected) failed
  492 |       await choose(page, "Track", /New Hampshire/);
  493 |       await expect(page.locator("li.session-row")).toHaveCount(1);
  494 |       await choose(page, "Series", /Global Mazda/);
  495 |       await expect(page.getByRole("heading", { name: "No matching sessions" })).toBeVisible();
  496 |       await page.getByRole("button", { name: "Clear filters" }).click();
  497 |       expect(await page.locator("section.sx-event").count()).toBeGreaterThan(3);
  498 |       surface = await openFilters(page);
  499 |       await closeFilters(page);
  500 |     });
  501 | 
  502 |     test("the selection is saved with the driver's prefs and comes back on another device", async ({ page, request }) => {
  503 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  504 |       await choose(page, "Car", /Global Mazda/);
  505 |       await expect.poll(async () => (await mockState(request)).writes.filter((w) => w.path.endsWith("/prefs")).map((w) => (w.body as { session_filters?: { cars: string[] } }).session_filters?.cars)).toContainEqual(["mx5-cup"]);
  506 |       // The device's own copy is only a hint for the first paint: without it the prefs still restore the chips.
  507 |       await page.evaluate((c) => localStorage.removeItem(`coach.sessions.filters.${c}`), DRIVER);
  508 |       await page.reload();
  509 |       await expect(page.locator("section.sx-event")).toHaveCount(1);
  510 |       const surface = await openFilters(page);
  511 |       await expect(surface.getByRole("button", { name: /^Car · Global Mazda MX-5 Cup/ })).toBeVisible();
  512 |     });
  513 | 
  514 |     test("Library: the driver's own by default; the lab only by ?library=lab in the URL, never saved", async ({ page }) => {
  515 |       const requested: string[] = [];
  516 |       page.on("request", (r) => {
  517 |         if (/\/api\/v1\/drivers\/\d+\/sessions(\?|$)/.test(r.url())) requested.push(new URL(r.url()).searchParams.get("library") ?? "(none)");
  518 |       });
  519 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  520 |       await expect(bristol(page)).toBeVisible();
  521 |       await expect(cell(page, 900)).toHaveCount(0);
  522 |       expect([...new Set(requested)]).toEqual(["(none)"]);
  523 |       await page.goto(hash(`/d/${DRIVER}/sessions?library=lab`));
  524 |       await expect(cell(page, 900)).toBeVisible();
  525 |       await expect(cell(page, 901)).toBeVisible();
  526 |       expect(requested).toContain("lab");
  527 |       await expect(page.getByRole("region", { name: BRISTOL })).toHaveCount(0);
  528 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  529 |       await expect(bristol(page)).toBeVisible();
  530 |       await expect(cell(page, 900)).toHaveCount(0);
  531 |       expect(await page.evaluate(() => JSON.stringify({ ...localStorage }).toLowerCase())).not.toContain("lab");
  532 |     });
  533 | 
  534 |     test("a session opens on the tab it was left on, without being asked", async ({ page }) => {
  535 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  536 |       await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
  537 |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/race$`));
  538 |       await page.getByRole("tab", { name: "Traces" }).click();
  539 |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces$`));
  540 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  541 |       await expect(bristol(page)).toBeVisible();
  542 |       await expect(page.getByText("Resume")).toHaveCount(0);
  543 |       await page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first().click();
  544 |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/103/traces`));
  545 |       // A session never opened opens on Race.
  546 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  547 |       await expect(bristol(page)).toBeVisible();
  548 |       await page.getByRole("link", { name: /^Qualifying, Bristol Motor Speedway - Oval/ }).first().click();
  549 |       await expect(page).toHaveURL(new RegExp(`#/d/${DRIVER}/s/102/race$`));
  550 |     });
  551 | 
  552 |     test("the silhouette is about 1.5 rem and comes from the track's geometry, once per layout", async ({ page }) => {
  553 |       const asked = await serveGeometry(page);
  554 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  555 |       const sil = bristol(page).getByTestId("silhouette");
  556 |       await expect(sil).toBeVisible();
  557 |       await expect(sil.locator("path")).toHaveAttribute("d", /^M[\d.,L-]+Z$/);
  558 |       const box = (await sil.boundingBox())!;
  559 |       expect(box.height).toBeGreaterThanOrEqual(22);
  560 |       expect(box.height).toBeLessThanOrEqual(26);
  561 |       for (const row of await page.locator("section.sx-event").all()) await row.scrollIntoViewIfNeeded();
  562 |       await expect.poll(() => asked.length).toBe(4);
  563 |       await page.waitForLoadState("networkidle");
  564 |       // Bristol, Indianapolis, Lime Rock and New Hampshire: four layouts across six events.
  565 |       expect(new Set(asked).size).toBe(asked.length);
  566 |       expect(asked.length).toBeLessThanOrEqual(4);
  567 |       await expectNoHorizontalScroll(page);
  568 |     });
  569 | 
  570 |     test("a track without usable geometry draws no silhouette and the row still reads", async ({ page }) => {
  571 |       await page.route("**/api/v1/analyses/*/track", (route) =>
  572 |         route.fulfill({ json: { analysis_id: "x", projection_version: 1, schema_version: 1, profile_version: "", coordinate_space: "normalized_local_vector", status: "usable", main_path: { pct: [0, 0.5], x: [0.1, 0.9], y: [0.5, 0.5] }, provenance: {} } }),
  573 |       );
  574 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  575 |       await expect(bristol(page).getByRole("heading", { name: "Bristol Motor Speedway" })).toBeVisible();
  576 |       await expect(page.getByTestId("silhouette")).toHaveCount(0);
  577 |     });
  578 | 
  579 |     test("older events load in batches", async ({ page }) => {
  580 |       await editLibrary(page, (rows) => {
  581 |         const out: LibraryRow[] = [];
  582 |         for (let k = 0; k < 60; k++)
  583 |           for (const r of rows.slice(0, 3)) out.push({ ...r, session_id: r.session_id + 1000 * (k + 1), event_key: `${r.event_key}-${k}`, ...({ analysis_id: null, analysis_state: "none" } as object) });
  584 |         return out;
  585 |       });
  586 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  587 |       await expect(page.locator("section.sx-event")).toHaveCount(24);
  588 |       await page.getByRole("button", { name: "Older sessions" }).click();
  589 |       await expect(page.locator("section.sx-event")).toHaveCount(48);
  590 |       await page.getByRole("button", { name: "Older sessions" }).click();
  591 |       await expect(page.locator("section.sx-event")).toHaveCount(60);
```