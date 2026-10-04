# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: sessions.spec.ts >> tablet 1024x768 >> searchable multi-select filters; the button carries the choice and clears from inside its list
- Location: e2e\app\sessions.spec.ts:435:5

# Error details

```
Error: expect(locator).toHaveAttribute(expected) failed

Locator: getByRole('option', { name: /Global Mazda MX-5 Cup/ })
Expected: "true"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toHaveAttribute" getByRole('option', { name: /Global Mazda MX-5 Cup/ }) with timeout 5000ms
  - waiting for getByRole('option', { name: /Global Mazda MX-5 Cup/ })

```

```yaml
- banner:
  - link "iRacing Coach":
    - /url: "#/"
  - navigation "Main":
    - link "Sessions":
      - /url: "#/d/1507810/sessions"
    - link "Setups":
      - /url: "#/d/1507810/setups"
    - link "Live Live now":
      - /url: "#/d/1507810/live"
      - text: Live
      - img "Live now"
  - text: Joshua Granger
- main:
  - heading "Sessions" [level=1]
  - link "Live now":
    - /url: "#/d/1507810/live"
  - button "Filters" [expanded]
  - dialog "Filters":
    - region "Car":
      - heading "Car" [level=3]
      - button "Car"
    - region "Track":
      - heading "Track" [level=3]
      - button "Track"
    - region "Series":
      - heading "Series" [level=3]
      - button "Series"
    - region "Session":
      - heading "Session" [level=3]
      - button "Session"
    - region "Setup":
      - heading "Setup" [level=3]
      - button "Setup"
    - button "Done"
  - region "Bristol Motor Speedway - Oval":
    - heading "Bristol Motor Speedway" [level=2]
    - text: Oval · Mon Sep 28 Fixed NASCAR Xfinity Toyota Supra
    - list:
      - listitem:
        - link "Practice, Bristol Motor Speedway - Oval, Mon Sep 28, 1:00 pm":
          - /url: "#/d/1507810/s/101/race"
          - text: 1:00 pm Practice Pos P5 Best 15.500 Run 9 laps Fall-off +0.21 s
      - listitem:
        - link "Qualifying, Bristol Motor Speedway - Oval, Mon Sep 28, 1:30 pm":
          - /url: "#/d/1507810/s/102/race"
          - text: 1:30 pm Qualifying Pos P2 Lap 1 15.610 Lap 2 15.410
      - listitem:
        - link "Race, Bristol Motor Speedway - Oval, Mon Sep 28, 2:00 pm":
          - /url: "#/d/1507810/s/103/race"
          - text: 2:00 pm · 2 rec Race Finish P4 ▲8 Grn · Cau 60 · 20 Best 15.234 Pace 15.612 +0.021 Stops 3 Fuel 21.6 gal Run 41
  - region "Indianapolis Raceway Park - Oval":
    - heading "Indianapolis Raceway Park" [level=2]
    - text: Oval · Fri Sep 25 Open NASCAR Xfinity Toyota Supra
    - list:
      - listitem:
        - link "Practice, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:00 pm":
          - /url: "#/d/1507810/s/104/race"
          - text: 1:00 pm Practice Pos P6 Best 15.600 Run 10 laps Fall-off −0.08 s
      - listitem:
        - link "Qualifying, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:30 pm":
          - /url: "#/d/1507810/s/105/race"
          - text: 1:30 pm Qualifying Pos P3 Lap 1 15.710 Lap 2 15.510
      - listitem:
        - group "Race, Indianapolis Raceway Park - Oval, Fri Sep 25, 2:00 pm":
          - text: 2:00 pm Race Laps 83 Not analyzed
          - button "Analyze"
  - region "Lime Rock Park - Grand Prix":
    - heading "Lime Rock Park" [level=2]
    - text: Grand Prix · Tue Sep 22 Fixed Global Mazda MX-5 Cup
    - list:
      - listitem:
        - link "Practice, Lime Rock Park - Grand Prix, Tue Sep 22, 1:00 pm":
          - /url: "#/d/1507810/s/107/race"
          - text: 1:00 pm Practice Pos P7 Best 15.700 Run 11 laps Fall-off +0.34 s
  - region "New Hampshire Motor Speedway - Oval":
    - heading "New Hampshire Motor Speedway" [level=2]
    - text: Oval · Sat Sep 19 Fixed NASCAR Xfinity Toyota Supra
    - list:
      - listitem:
        - group "Practice, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:00 pm":
          - text: 1:00 pm Practice Open Laps 23 Failed · worker crash
          - button "Retry"
      - listitem:
        - link "Qualifying, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:30 pm":
          - /url: "#/d/1507810/s/109/race"
          - text: 1:30 pm Qualifying Pos P5 Lap 1 15.910 Lap 2 15.310
      - listitem:
        - link "Race, New Hampshire Motor Speedway - Oval, Sat Sep 19, 2:00 pm":
          - /url: "#/d/1507810/s/110/race"
          - text: 2:00 pm Race Finish P1 ▲2 Grn · Cau 66 · 0 Best 15.534 Pace 15.912 +0.031 Stops 3 Fuel 18.6 gal Run 38
  - region "Bristol Motor Speedway - Oval":
    - heading "Bristol Motor Speedway" [level=2]
    - text: Oval · Wed Sep 16 Open NASCAR Xfinity Toyota Supra
    - list:
      - listitem:
        - link "Practice, Bristol Motor Speedway - Oval, Wed Sep 16, 1:00 pm":
          - /url: "#/d/1507810/s/111/race"
          - text: 1:00 pm Practice Pos P9 Best 15.900 Run 13 laps Fall-off +0.12 s
      - listitem:
        - link "Qualifying, Bristol Motor Speedway - Oval, Wed Sep 16, 1:30 pm":
          - /url: "#/d/1507810/s/112/race"
          - text: 1:30 pm Qualifying Pos P6 Lap 1 16.010 Lap 2 —
      - listitem:
        - group "Race, Bristol Motor Speedway - Oval, Wed Sep 16, 2:00 pm": 2:00 pm Race Laps 86 Deferred · exceeds memory budget
  - region "Indianapolis Raceway Park - Oval":
    - heading "Indianapolis Raceway Park" [level=2]
    - text: Oval · Sun Sep 13 NASCAR Xfinity Toyota Supra
    - list:
      - listitem:
        - link "Practice, Indianapolis Raceway Park - Oval, Sun Sep 13, 1:00 pm":
          - /url: "#/d/1507810/s/114/race"
          - text: 1:00 pm Practice Pos P10 Best 16.000 Run 14 laps Fall-off —
```

# Test source

```ts
  348 |       // The silhouette is about 1.5 rem tall.
  349 |     });
  350 | 
  351 |     test("Fixed and Open are the one constraint chip; a phase that differs carries its own; no status-normal chip", async ({ page }) => {
  352 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  353 |       await expect(bristol(page).locator(".sx-meta .chip-constraint")).toHaveText("Fixed");
  354 |       await expect(page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).first().locator(".sx-meta .chip-constraint")).toHaveText("Open");
  355 |       const hampshire = page.getByRole("region", { name: "New Hampshire Motor Speedway - Oval" });
  356 |       await expect(hampshire.locator(".sx-meta .chip-constraint")).toHaveText("Fixed");
  357 |       await expect(cell(page, 108).locator(".sx-head .chip-constraint")).toHaveText("Open");
  358 |       await expect(cell(page, 109).locator(".sx-head .chip")).toHaveCount(0);
  359 |       // An event with no recorded setup type shows none. The series is not drawn; it is in the car's tooltip.
  360 |       await expect(bristol(page).locator(".sx-car span[title]")).toHaveAttribute("title", "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series");
  361 |       await expect(bristol(page).getByText("NASCAR Xfinity Series")).toHaveCount(0);
  362 |       const none = page.getByRole("region", { name: "Indianapolis Raceway Park - Oval" }).nth(1);
  363 |       await expect(none.locator(".sx-meta .chip")).toHaveCount(0);
  364 |       await expect(page.getByRole("region", { name: "Lime Rock Park - Grand Prix" }).getByText("Global Mazda MX-5 Cup")).toHaveCount(1);
  365 |       // Only constraints and exceptions exist; none of the retired status chips.
  366 |       const kinds = await page.locator(".sx-events .chip").evaluateAll((els) => [...new Set(els.map((e) => [...e.classList].find((c) => /^chip-(constraint|exception|action)$/.test(c)) ?? "other"))].sort());
  367 |       expect(kinds).toEqual(["chip-constraint", "chip-exception"]);
  368 |       for (const word of ["Analyzed", "Values known", "Recording"]) await expect(page.getByText(word, { exact: true })).toHaveCount(0);
  369 |       expect(await page.locator(".sx-events .chip").evaluateAll((els) => els.map((e) => getComputedStyle(e).borderRadius))).not.toContain("999px");
  370 |     });
  371 | 
  372 |     test("no Resume banner; only the exceptions are named, each with its verb", async ({ page }) => {
  373 |       await page.addInitScript((c) => localStorage.setItem(`coach.last-investigation.${c}`, JSON.stringify({ session_id: 103, analysis_id: "an-103", view: "traces", comparison_id: "c1" })), DRIVER);
  374 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  375 |       await expect(bristol(page)).toBeVisible();
  376 |       await expect(page.getByText("Resume")).toHaveCount(0);
  377 |       const notAnalyzed = cell(page, 106);
  378 |       await expect(notAnalyzed.locator(".chip-exception")).toContainText("Not analyzed");
  379 |       await expect(notAnalyzed.getByRole("button", { name: "Analyze" })).toBeVisible();
  380 |       await expect(value(notAnalyzed, "recorded")).toHaveText("83");
  381 |       await expect(notAnalyzed.locator(".sx-slot[data-slot=pos]")).toHaveCount(0);
  382 |       const failed = cell(page, 108);
  383 |       await expect(failed.locator(".chip-exception")).toContainText("Failed · worker crash");
  384 |       await expect(failed.getByRole("button", { name: "Retry" })).toBeVisible();
  385 |       await expect(cell(page, 113).locator(".chip-exception")).toHaveText("Deferred · exceeds memory budget");
  386 |       // A ready phase shows no state at all and is the only kind that opens.
  387 |       await expect(cell(page, 103).locator(".chip-exception")).toHaveCount(0);
  388 |       await expect(cell(page, 103).getByRole("link")).toHaveCount(1);
  389 |       await expect(notAnalyzed.getByRole("link")).toHaveCount(0);
  390 |       await expect(page.getByRole("link", { name: /^Race, Bristol Motor Speedway - Oval/ }).first()).toBeVisible();
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
> 448 |       await expect(page.getByRole("option", { name: /Global Mazda MX-5 Cup/ })).toHaveAttribute("aria-selected", "true");
      |                                                                                 ^ Error: expect(locator).toHaveAttribute(expected) failed
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
  491 |       await expect(page.locator("li.session-row")).toHaveCount(2);
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
```