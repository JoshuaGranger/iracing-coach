# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: sessions.spec.ts >> phone 390x844 >> the selection is saved with the driver's prefs and comes back on another device
- Location: e2e\app\sessions.spec.ts:502:5

# Error details

```
Error: expect(received).toContainEqual(expected) // deep equality

Expected value: ["mx5-cup"]
Received array: []

Call Log:
- Timeout 5000ms exceeded while waiting on the predicate
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
  - main [ref=e13]:
    - generic [ref=e15]:
      - generic [ref=e17]:
        - generic [ref=e18]:
          - heading "Sessions" [level=1] [ref=e19]
          - link "Live now" [ref=e20] [cursor=pointer]:
            - /url: "#/d/1507810/live"
        - button "Filters" [active] [ref=e22] [cursor=pointer]
      - generic [ref=e26]:
        - region "Bristol Motor Speedway - Oval" [ref=e27]:
          - generic [ref=e28]:
            - generic [ref=e29]:
              - heading "Bristol Motor Speedway" [level=2] [ref=e35]
              - generic [ref=e36]:
                - generic [ref=e37]: Oval · Mon Sep 28
                - generic "Fixed setup" [ref=e38]: Fixed
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e44]: NASCAR Xfinity Toyota Supra
            - list [ref=e45]:
              - listitem [ref=e46]:
                - link "Practice, Bristol Motor Speedway - Oval, Mon Sep 28, 1:00 pm" [ref=e47] [cursor=pointer]:
                  - /url: "#/d/1507810/s/101/race"
                  - generic [ref=e48]:
                    - generic [ref=e49]:
                      - generic [ref=e50]: 1:00 pm
                      - generic [ref=e51]: Practice
                    - generic "Classified P5" [ref=e52]:
                      - generic [ref=e53]: Pos
                      - generic [ref=e54]: P5
                    - generic "Fastest clean lap" [ref=e56]:
                      - generic [ref=e57]: Best
                      - generic [ref=e58]: "15.500"
                    - generic "Longest run without a reset, laps" [ref=e60]:
                      - generic [ref=e61]: Run
                      - generic [ref=e62]:
                        - generic [ref=e63]: "9"
                        - generic [ref=e64]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e65]':
                      - generic [ref=e66]: Fall-off
                      - generic [ref=e67]:
                        - generic [ref=e68]: "+0.21"
                        - generic [ref=e69]: s
              - listitem [ref=e70]:
                - link "Qualifying, Bristol Motor Speedway - Oval, Mon Sep 28, 1:30 pm" [ref=e71] [cursor=pointer]:
                  - /url: "#/d/1507810/s/102/race"
                  - generic [ref=e72]:
                    - generic [ref=e73]:
                      - generic [ref=e74]: 1:30 pm
                      - generic [ref=e75]: Qualifying
                    - generic "Classified P2" [ref=e76]:
                      - generic [ref=e77]: Pos
                      - generic [ref=e78]: P2
                    - generic "First timed lap" [ref=e80]:
                      - generic [ref=e81]: Lap 1
                      - generic [ref=e82]: "15.610"
                    - generic "Second timed lap" [ref=e84]:
                      - generic [ref=e85]: Lap 2
                      - generic [ref=e86]: "15.410"
              - listitem [ref=e88]:
                - link "Race, Bristol Motor Speedway - Oval, Mon Sep 28, 2:00 pm" [ref=e89] [cursor=pointer]:
                  - /url: "#/d/1507810/s/103/race"
                  - generic [ref=e90]:
                    - generic [ref=e91]:
                      - generic [ref=e92]: 2:00 pm · 2 rec
                      - generic [ref=e93]: Race
                    - generic "Finished P4, started P12" [ref=e94]:
                      - generic [ref=e95]: Finish
                      - generic [ref=e96]:
                        - generic [ref=e97]: P4
                        - generic [ref=e98]: ▲8
                    - generic "Green · caution laps" [ref=e99]:
                      - generic [ref=e100]: Grn · Cau
                      - generic [ref=e101]:
                        - generic [ref=e102]: "60"
                        - generic [ref=e103]: ·
                        - generic [ref=e104]: "20"
                    - generic "Best clean lap, fastest of the event" [ref=e105]:
                      - generic [ref=e106]: Best
                      - generic [ref=e107]: "15.234"
                    - generic "Green pace and its trend, s per lap" [ref=e109]:
                      - generic [ref=e110]: Pace
                      - generic [ref=e111]:
                        - generic [ref=e112]: "15.612"
                        - generic [ref=e113]: "+0.021"
                    - generic "Pit stops" [ref=e114]:
                      - generic [ref=e115]: Stops
                      - generic [ref=e116]: "3"
                    - generic "Fuel used, gallons" [ref=e118]:
                      - generic [ref=e119]: Fuel
                      - generic [ref=e120]:
                        - generic [ref=e121]: "21.6"
                        - generic [ref=e122]: gal
                    - generic "Longest green run, laps" [ref=e123]:
                      - generic [ref=e124]: Run
                      - generic [ref=e125]: "41"
        - region "Indianapolis Raceway Park - Oval" [ref=e127]:
          - generic [ref=e128]:
            - generic [ref=e129]:
              - heading "Indianapolis Raceway Park" [level=2] [ref=e135]
              - generic [ref=e136]:
                - generic [ref=e137]: Oval · Fri Sep 25
                - generic "Open setup" [ref=e138]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e143]: NASCAR Xfinity Toyota Supra
            - list [ref=e144]:
              - listitem [ref=e145]:
                - link "Practice, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:00 pm" [ref=e146] [cursor=pointer]:
                  - /url: "#/d/1507810/s/104/race"
                  - generic [ref=e147]:
                    - generic [ref=e148]:
                      - generic [ref=e149]: 1:00 pm
                      - generic [ref=e150]: Practice
                    - generic "Classified P6" [ref=e151]:
                      - generic [ref=e152]: Pos
                      - generic [ref=e153]: P6
                    - generic "Fastest clean lap" [ref=e155]:
                      - generic [ref=e156]: Best
                      - generic [ref=e157]: "15.600"
                    - generic "Longest run without a reset, laps" [ref=e159]:
                      - generic [ref=e160]: Run
                      - generic [ref=e161]:
                        - generic [ref=e162]: "10"
                        - generic [ref=e163]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e164]':
                      - generic [ref=e165]: Fall-off
                      - generic [ref=e166]:
                        - generic [ref=e167]: −0.08
                        - generic [ref=e168]: s
              - listitem [ref=e169]:
                - link "Qualifying, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:30 pm" [ref=e170] [cursor=pointer]:
                  - /url: "#/d/1507810/s/105/race"
                  - generic [ref=e171]:
                    - generic [ref=e172]:
                      - generic [ref=e173]: 1:30 pm
                      - generic [ref=e174]: Qualifying
                    - generic "Classified P3" [ref=e175]:
                      - generic [ref=e176]: Pos
                      - generic [ref=e177]: P3
                    - generic "First timed lap" [ref=e179]:
                      - generic [ref=e180]: Lap 1
                      - generic [ref=e181]: "15.710"
                    - generic "Second timed lap, fastest of the event" [ref=e183]:
                      - generic [ref=e184]: Lap 2
                      - generic [ref=e185]: "15.510"
              - listitem [ref=e187]:
                - group "Race, Indianapolis Raceway Park - Oval, Fri Sep 25, 2:00 pm" [ref=e188]:
                  - generic [ref=e189]:
                    - generic [ref=e190]:
                      - generic [ref=e191]: 2:00 pm
                      - generic [ref=e192]: Race
                    - generic [ref=e193]:
                      - generic [ref=e194]: Laps
                      - generic [ref=e195]: "83"
                  - generic [ref=e198]:
                    - text: Not analyzed
                    - button "Analyze" [ref=e199] [cursor=pointer]
        - region "Lime Rock Park - Grand Prix" [ref=e200]:
          - generic [ref=e201]:
            - generic [ref=e202]:
              - heading "Lime Rock Park" [level=2] [ref=e205]
              - generic [ref=e206]:
                - generic [ref=e207]: Grand Prix · Tue Sep 22
                - generic "Fixed setup" [ref=e208]: Fixed
              - generic [ref=e212]: Global Mazda MX-5 Cup
            - list [ref=e215]:
              - listitem [ref=e216]:
                - link "Practice, Lime Rock Park - Grand Prix, Tue Sep 22, 1:00 pm" [ref=e217] [cursor=pointer]:
                  - /url: "#/d/1507810/s/107/race"
                  - generic [ref=e218]:
                    - generic [ref=e219]:
                      - generic [ref=e220]: 1:00 pm
                      - generic [ref=e221]: Practice
                    - generic "Classified P7" [ref=e222]:
                      - generic [ref=e223]: Pos
                      - generic [ref=e224]: P7
                    - generic "Fastest clean lap, fastest of the event" [ref=e226]:
                      - generic [ref=e227]: Best
                      - generic [ref=e228]: "15.700"
                    - generic "Longest run without a reset, laps" [ref=e230]:
                      - generic [ref=e231]: Run
                      - generic [ref=e232]:
                        - generic [ref=e233]: "11"
                        - generic [ref=e234]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e235]':
                      - generic [ref=e236]: Fall-off
                      - generic [ref=e237]:
                        - generic [ref=e238]: "+0.34"
                        - generic [ref=e239]: s
        - region "New Hampshire Motor Speedway - Oval" [ref=e240]:
          - generic [ref=e241]:
            - generic [ref=e242]:
              - heading "New Hampshire Motor Speedway" [level=2] [ref=e245]
              - generic [ref=e246]:
                - generic [ref=e247]: Oval · Sat Sep 19
                - generic "Fixed setup" [ref=e248]: Fixed
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e254]: NASCAR Xfinity Toyota Supra
            - list [ref=e255]:
              - listitem [ref=e256]:
                - group "Practice, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:00 pm" [ref=e257]:
                  - generic [ref=e258]:
                    - generic [ref=e259]:
                      - generic [ref=e260]: 1:00 pm
                      - generic [ref=e261]:
                        - text: Practice
                        - generic "Open setup" [ref=e262]: Open
                    - generic [ref=e265]:
                      - generic [ref=e266]: Laps
                      - generic [ref=e267]: "23"
                  - generic [ref=e270]:
                    - text: Failed · worker crash
                    - button "Retry" [ref=e271] [cursor=pointer]
              - listitem [ref=e272]:
                - link "Qualifying, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:30 pm" [ref=e273] [cursor=pointer]:
                  - /url: "#/d/1507810/s/109/race"
                  - generic [ref=e274]:
                    - generic [ref=e275]:
                      - generic [ref=e276]: 1:30 pm
                      - generic [ref=e277]: Qualifying
                    - generic "Classified P5" [ref=e278]:
                      - generic [ref=e279]: Pos
                      - generic [ref=e280]: P5
                    - generic "First timed lap" [ref=e282]:
                      - generic [ref=e283]: Lap 1
                      - generic [ref=e284]: "15.910"
                    - generic "Second timed lap, fastest of the event" [ref=e286]:
                      - generic [ref=e287]: Lap 2
                      - generic [ref=e288]: "15.310"
              - listitem [ref=e290]:
                - link "Race, New Hampshire Motor Speedway - Oval, Sat Sep 19, 2:00 pm" [ref=e291] [cursor=pointer]:
                  - /url: "#/d/1507810/s/110/race"
                  - generic [ref=e292]:
                    - generic [ref=e293]:
                      - generic [ref=e294]: 2:00 pm
                      - generic [ref=e295]: Race
                    - generic "Finished P1, started P3" [ref=e296]:
                      - generic [ref=e297]: Finish
                      - generic [ref=e298]:
                        - generic [ref=e299]: P1
                        - generic [ref=e300]: ▲2
                    - generic "Green · caution laps" [ref=e301]:
                      - generic [ref=e302]: Grn · Cau
                      - generic [ref=e303]:
                        - generic [ref=e304]: "66"
                        - generic [ref=e305]: ·
                        - generic [ref=e306]: "0"
                    - generic "Best clean lap" [ref=e307]:
                      - generic [ref=e308]: Best
                      - generic [ref=e309]: "15.534"
                    - generic "Green pace and its trend, s per lap" [ref=e311]:
                      - generic [ref=e312]: Pace
                      - generic [ref=e313]:
                        - generic [ref=e314]: "15.912"
                        - generic [ref=e315]: "+0.031"
                    - generic "Pit stops" [ref=e316]:
                      - generic [ref=e317]: Stops
                      - generic [ref=e318]: "3"
                    - generic "Fuel used, gallons" [ref=e320]:
                      - generic [ref=e321]: Fuel
                      - generic [ref=e322]:
                        - generic [ref=e323]: "18.6"
                        - generic [ref=e324]: gal
                    - generic "Longest green run, laps" [ref=e325]:
                      - generic [ref=e326]: Run
                      - generic [ref=e327]: "38"
        - region "Bristol Motor Speedway - Oval" [ref=e329]:
          - generic [ref=e330]:
            - generic [ref=e331]:
              - heading "Bristol Motor Speedway" [level=2] [ref=e334]
              - generic [ref=e335]:
                - generic [ref=e336]: Oval · Wed Sep 16
                - generic "Open setup" [ref=e337]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e342]: NASCAR Xfinity Toyota Supra
            - list [ref=e343]:
              - listitem [ref=e344]:
                - link "Practice, Bristol Motor Speedway - Oval, Wed Sep 16, 1:00 pm" [ref=e345] [cursor=pointer]:
                  - /url: "#/d/1507810/s/111/race"
                  - generic [ref=e346]:
                    - generic [ref=e347]:
                      - generic [ref=e348]: 1:00 pm
                      - generic [ref=e349]: Practice
                    - generic "Classified P9" [ref=e350]:
                      - generic [ref=e351]: Pos
                      - generic [ref=e352]: P9
                    - generic "Fastest clean lap" [ref=e354]:
                      - generic [ref=e355]: Best
                      - generic [ref=e356]: "15.900"
                    - generic "Longest run without a reset, laps" [ref=e358]:
                      - generic [ref=e359]: Run
                      - generic [ref=e360]:
                        - generic [ref=e361]: "13"
                        - generic [ref=e362]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e363]':
                      - generic [ref=e364]: Fall-off
                      - generic [ref=e365]:
                        - generic [ref=e366]: "+0.12"
                        - generic [ref=e367]: s
              - listitem [ref=e368]:
                - link "Qualifying, Bristol Motor Speedway - Oval, Wed Sep 16, 1:30 pm" [ref=e369] [cursor=pointer]:
                  - /url: "#/d/1507810/s/112/race"
                  - generic [ref=e370]:
                    - generic [ref=e371]:
                      - generic [ref=e372]: 1:30 pm
                      - generic [ref=e373]: Qualifying
                    - generic "Classified P6" [ref=e374]:
                      - generic [ref=e375]: Pos
                      - generic [ref=e376]: P6
                    - generic "First timed lap" [ref=e378]:
                      - generic [ref=e379]: Lap 1
                      - generic [ref=e380]: "16.010"
                    - generic "Second timed lap" [ref=e382]:
                      - generic [ref=e383]: Lap 2
                      - generic [ref=e384]: —
              - listitem [ref=e386]:
                - group "Race, Bristol Motor Speedway - Oval, Wed Sep 16, 2:00 pm" [ref=e387]:
                  - generic [ref=e388]:
                    - generic [ref=e389]:
                      - generic [ref=e390]: 2:00 pm
                      - generic [ref=e391]: Race
                    - generic [ref=e392]:
                      - generic [ref=e393]: Laps
                      - generic [ref=e394]: "86"
                  - generic [ref=e396]: Deferred · exceeds memory budget
        - region "Indianapolis Raceway Park - Oval" [ref=e398]:
          - generic [ref=e399]:
            - generic [ref=e400]:
              - heading "Indianapolis Raceway Park" [level=2] [ref=e403]
              - generic [ref=e404]: Oval · Sun Sep 13
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e408]: NASCAR Xfinity Toyota Supra
            - list [ref=e409]:
              - listitem [ref=e410]:
                - link "Practice, Indianapolis Raceway Park - Oval, Sun Sep 13, 1:00 pm" [ref=e411] [cursor=pointer]:
                  - /url: "#/d/1507810/s/114/race"
                  - generic [ref=e412]:
                    - generic [ref=e413]:
                      - generic [ref=e414]: 1:00 pm
                      - generic [ref=e415]: Practice
                    - generic "Classified P10" [ref=e416]:
                      - generic [ref=e417]: Pos
                      - generic [ref=e418]: P10
                    - generic "Fastest clean lap, fastest of the event" [ref=e420]:
                      - generic [ref=e421]: Best
                      - generic [ref=e422]: "16.000"
                    - generic "Longest run without a reset, laps" [ref=e424]:
                      - generic [ref=e425]: Run
                      - generic [ref=e426]:
                        - generic [ref=e427]: "14"
                        - generic [ref=e428]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e429]':
                      - generic [ref=e430]: Fall-off
                      - generic [ref=e431]: —
```

# Test source

```ts
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
> 505 |       await expect.poll(async () => (await mockState(request)).writes.filter((w) => w.path.endsWith("/prefs")).map((w) => (w.body as { session_filters?: { cars: string[] } }).session_filters?.cars)).toContainEqual(["mx5-cup"]);
      |                                                                                                                                                                                                        ^ Error: expect(received).toContainEqual(expected) // deep equality
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
  592 |       await expect(page.getByRole("button", { name: "Older sessions" })).toHaveCount(0);
  593 |     });
  594 | 
  595 |     test("loaded rows stay on screen when the server drops, with a banner", async ({ page, context, request }) => {
  596 |       await page.goto(hash(`/d/${DRIVER}/sessions`));
  597 |       await expect(bristol(page)).toBeVisible();
  598 |       await context.route("**/api/v1/drivers/**", (r) => r.abort());
  599 |       await request.post("/__mock/event", { data: { type: "library_changed", customer_id: DRIVER, scope: "sessions" } });
  600 |       await expect(page.getByTestId("offline-banner")).toBeVisible();
  601 |       await expect(page.getByText(/Could not refresh/)).toBeVisible();
  602 |       await expect(bristol(page)).toBeVisible();
  603 |       await context.unroute("**/api/v1/drivers/**");
  604 |     });
  605 |   });
```