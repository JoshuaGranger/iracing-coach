# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: sessions.spec.ts >> tablet 1024x768 >> the selection is saved with the driver's prefs and comes back on another device
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
    - generic [ref=e13]: Joshua Granger
  - main [ref=e14]:
    - generic [ref=e16]:
      - generic [ref=e18]:
        - generic [ref=e19]:
          - heading "Sessions" [level=1] [ref=e20]
          - link "Live now" [ref=e21] [cursor=pointer]:
            - /url: "#/d/1507810/live"
        - button "Filters" [active] [ref=e23] [cursor=pointer]
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
                - link "Practice, Bristol Motor Speedway - Oval, Mon Sep 28, 1:00 pm" [ref=e48] [cursor=pointer]:
                  - /url: "#/d/1507810/s/101/race"
                  - generic [ref=e49]:
                    - generic [ref=e50]:
                      - generic [ref=e51]: 1:00 pm
                      - generic [ref=e52]: Practice
                    - generic "Classified P5" [ref=e53]:
                      - generic [ref=e54]: Pos
                      - generic [ref=e55]: P5
                    - generic "Fastest clean lap" [ref=e57]:
                      - generic [ref=e58]: Best
                      - generic [ref=e59]: "15.500"
                    - generic "Longest run without a reset, laps" [ref=e61]:
                      - generic [ref=e62]: Run
                      - generic [ref=e63]:
                        - generic [ref=e64]: "9"
                        - generic [ref=e65]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e66]':
                      - generic [ref=e67]: Fall-off
                      - generic [ref=e68]:
                        - generic [ref=e69]: "+0.21"
                        - generic [ref=e70]: s
              - listitem [ref=e71]:
                - link "Qualifying, Bristol Motor Speedway - Oval, Mon Sep 28, 1:30 pm" [ref=e72] [cursor=pointer]:
                  - /url: "#/d/1507810/s/102/race"
                  - generic [ref=e73]:
                    - generic [ref=e74]:
                      - generic [ref=e75]: 1:30 pm
                      - generic [ref=e76]: Qualifying
                    - generic "Classified P2" [ref=e77]:
                      - generic [ref=e78]: Pos
                      - generic [ref=e79]: P2
                    - generic "First timed lap" [ref=e81]:
                      - generic [ref=e82]: Lap 1
                      - generic [ref=e83]: "15.610"
                    - generic "Second timed lap" [ref=e85]:
                      - generic [ref=e86]: Lap 2
                      - generic [ref=e87]: "15.410"
              - listitem [ref=e89]:
                - link "Race, Bristol Motor Speedway - Oval, Mon Sep 28, 2:00 pm" [ref=e90] [cursor=pointer]:
                  - /url: "#/d/1507810/s/103/race"
                  - generic [ref=e91]:
                    - generic [ref=e92]:
                      - generic [ref=e93]: 2:00 pm · 2 rec
                      - generic [ref=e94]: Race
                    - generic "Finished P4, started P12" [ref=e95]:
                      - generic [ref=e96]: Finish
                      - generic [ref=e97]:
                        - generic [ref=e98]: P4
                        - generic [ref=e99]: ▲8
                    - generic "Green · caution laps" [ref=e100]:
                      - generic [ref=e101]: Grn · Cau
                      - generic [ref=e102]:
                        - generic [ref=e103]: "60"
                        - generic [ref=e104]: ·
                        - generic [ref=e105]: "20"
                    - generic "Best clean lap, fastest of the event" [ref=e106]:
                      - generic [ref=e107]: Best
                      - generic [ref=e108]: "15.234"
                    - generic "Green pace and its trend, s per lap" [ref=e110]:
                      - generic [ref=e111]: Pace
                      - generic [ref=e112]:
                        - generic [ref=e113]: "15.612"
                        - generic [ref=e114]: "+0.021"
                    - generic "Pit stops" [ref=e115]:
                      - generic [ref=e116]: Stops
                      - generic [ref=e117]: "3"
                    - generic "Fuel used, gallons" [ref=e119]:
                      - generic [ref=e120]: Fuel
                      - generic [ref=e121]:
                        - generic [ref=e122]: "21.6"
                        - generic [ref=e123]: gal
                    - generic "Longest green run, laps" [ref=e124]:
                      - generic [ref=e125]: Run
                      - generic [ref=e126]: "41"
        - region "Indianapolis Raceway Park - Oval" [ref=e128]:
          - generic [ref=e129]:
            - generic [ref=e130]:
              - heading "Indianapolis Raceway Park" [level=2] [ref=e136]
              - generic [ref=e137]:
                - generic [ref=e138]: Oval · Fri Sep 25
                - generic "Open setup" [ref=e139]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e144]: NASCAR Xfinity Toyota Supra
            - list [ref=e145]:
              - listitem [ref=e146]:
                - link "Practice, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:00 pm" [ref=e147] [cursor=pointer]:
                  - /url: "#/d/1507810/s/104/race"
                  - generic [ref=e148]:
                    - generic [ref=e149]:
                      - generic [ref=e150]: 1:00 pm
                      - generic [ref=e151]: Practice
                    - generic "Classified P6" [ref=e152]:
                      - generic [ref=e153]: Pos
                      - generic [ref=e154]: P6
                    - generic "Fastest clean lap" [ref=e156]:
                      - generic [ref=e157]: Best
                      - generic [ref=e158]: "15.600"
                    - generic "Longest run without a reset, laps" [ref=e160]:
                      - generic [ref=e161]: Run
                      - generic [ref=e162]:
                        - generic [ref=e163]: "10"
                        - generic [ref=e164]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e165]':
                      - generic [ref=e166]: Fall-off
                      - generic [ref=e167]:
                        - generic [ref=e168]: −0.08
                        - generic [ref=e169]: s
              - listitem [ref=e170]:
                - link "Qualifying, Indianapolis Raceway Park - Oval, Fri Sep 25, 1:30 pm" [ref=e171] [cursor=pointer]:
                  - /url: "#/d/1507810/s/105/race"
                  - generic [ref=e172]:
                    - generic [ref=e173]:
                      - generic [ref=e174]: 1:30 pm
                      - generic [ref=e175]: Qualifying
                    - generic "Classified P3" [ref=e176]:
                      - generic [ref=e177]: Pos
                      - generic [ref=e178]: P3
                    - generic "First timed lap" [ref=e180]:
                      - generic [ref=e181]: Lap 1
                      - generic [ref=e182]: "15.710"
                    - generic "Second timed lap, fastest of the event" [ref=e184]:
                      - generic [ref=e185]: Lap 2
                      - generic [ref=e186]: "15.510"
              - listitem [ref=e188]:
                - group "Race, Indianapolis Raceway Park - Oval, Fri Sep 25, 2:00 pm" [ref=e189]:
                  - generic [ref=e190]:
                    - generic [ref=e191]:
                      - generic [ref=e192]: 2:00 pm
                      - generic [ref=e193]: Race
                    - generic [ref=e194]:
                      - generic [ref=e195]: Laps
                      - generic [ref=e196]: "83"
                  - generic [ref=e199]:
                    - text: Not analyzed
                    - button "Analyze" [ref=e200] [cursor=pointer]
        - region "Lime Rock Park - Grand Prix" [ref=e201]:
          - generic [ref=e202]:
            - generic [ref=e203]:
              - heading "Lime Rock Park" [level=2] [ref=e209]
              - generic [ref=e210]:
                - generic [ref=e211]: Grand Prix · Tue Sep 22
                - generic "Fixed setup" [ref=e212]: Fixed
              - generic [ref=e216]: Global Mazda MX-5 Cup
            - list [ref=e219]:
              - listitem [ref=e220]:
                - link "Practice, Lime Rock Park - Grand Prix, Tue Sep 22, 1:00 pm" [ref=e221] [cursor=pointer]:
                  - /url: "#/d/1507810/s/107/race"
                  - generic [ref=e222]:
                    - generic [ref=e223]:
                      - generic [ref=e224]: 1:00 pm
                      - generic [ref=e225]: Practice
                    - generic "Classified P7" [ref=e226]:
                      - generic [ref=e227]: Pos
                      - generic [ref=e228]: P7
                    - generic "Fastest clean lap, fastest of the event" [ref=e230]:
                      - generic [ref=e231]: Best
                      - generic [ref=e232]: "15.700"
                    - generic "Longest run without a reset, laps" [ref=e234]:
                      - generic [ref=e235]: Run
                      - generic [ref=e236]:
                        - generic [ref=e237]: "11"
                        - generic [ref=e238]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e239]':
                      - generic [ref=e240]: Fall-off
                      - generic [ref=e241]:
                        - generic [ref=e242]: "+0.34"
                        - generic [ref=e243]: s
        - region "New Hampshire Motor Speedway - Oval" [ref=e244]:
          - generic [ref=e245]:
            - generic [ref=e246]:
              - heading "New Hampshire Motor Speedway" [level=2] [ref=e252]
              - generic [ref=e253]:
                - generic [ref=e254]: Oval · Sat Sep 19
                - generic "Fixed setup" [ref=e255]: Fixed
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e261]: NASCAR Xfinity Toyota Supra
            - list [ref=e262]:
              - listitem [ref=e263]:
                - group "Practice, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:00 pm" [ref=e264]:
                  - generic [ref=e265]:
                    - generic [ref=e266]:
                      - generic [ref=e267]: 1:00 pm
                      - generic [ref=e268]:
                        - text: Practice
                        - generic "Open setup" [ref=e269]: Open
                    - generic [ref=e272]:
                      - generic [ref=e273]: Laps
                      - generic [ref=e274]: "23"
                  - generic [ref=e277]:
                    - text: Failed · worker crash
                    - button "Retry" [ref=e278] [cursor=pointer]
              - listitem [ref=e279]:
                - link "Qualifying, New Hampshire Motor Speedway - Oval, Sat Sep 19, 1:30 pm" [ref=e280] [cursor=pointer]:
                  - /url: "#/d/1507810/s/109/race"
                  - generic [ref=e281]:
                    - generic [ref=e282]:
                      - generic [ref=e283]: 1:30 pm
                      - generic [ref=e284]: Qualifying
                    - generic "Classified P5" [ref=e285]:
                      - generic [ref=e286]: Pos
                      - generic [ref=e287]: P5
                    - generic "First timed lap" [ref=e289]:
                      - generic [ref=e290]: Lap 1
                      - generic [ref=e291]: "15.910"
                    - generic "Second timed lap, fastest of the event" [ref=e293]:
                      - generic [ref=e294]: Lap 2
                      - generic [ref=e295]: "15.310"
              - listitem [ref=e297]:
                - link "Race, New Hampshire Motor Speedway - Oval, Sat Sep 19, 2:00 pm" [ref=e298] [cursor=pointer]:
                  - /url: "#/d/1507810/s/110/race"
                  - generic [ref=e299]:
                    - generic [ref=e300]:
                      - generic [ref=e301]: 2:00 pm
                      - generic [ref=e302]: Race
                    - generic "Finished P1, started P3" [ref=e303]:
                      - generic [ref=e304]: Finish
                      - generic [ref=e305]:
                        - generic [ref=e306]: P1
                        - generic [ref=e307]: ▲2
                    - generic "Green · caution laps" [ref=e308]:
                      - generic [ref=e309]: Grn · Cau
                      - generic [ref=e310]:
                        - generic [ref=e311]: "66"
                        - generic [ref=e312]: ·
                        - generic [ref=e313]: "0"
                    - generic "Best clean lap" [ref=e314]:
                      - generic [ref=e315]: Best
                      - generic [ref=e316]: "15.534"
                    - generic "Green pace and its trend, s per lap" [ref=e318]:
                      - generic [ref=e319]: Pace
                      - generic [ref=e320]:
                        - generic [ref=e321]: "15.912"
                        - generic [ref=e322]: "+0.031"
                    - generic "Pit stops" [ref=e323]:
                      - generic [ref=e324]: Stops
                      - generic [ref=e325]: "3"
                    - generic "Fuel used, gallons" [ref=e327]:
                      - generic [ref=e328]: Fuel
                      - generic [ref=e329]:
                        - generic [ref=e330]: "18.6"
                        - generic [ref=e331]: gal
                    - generic "Longest green run, laps" [ref=e332]:
                      - generic [ref=e333]: Run
                      - generic [ref=e334]: "38"
        - region "Bristol Motor Speedway - Oval" [ref=e336]:
          - generic [ref=e337]:
            - generic [ref=e338]:
              - heading "Bristol Motor Speedway" [level=2] [ref=e344]
              - generic [ref=e345]:
                - generic [ref=e346]: Oval · Wed Sep 16
                - generic "Open setup" [ref=e347]: Open
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e352]: NASCAR Xfinity Toyota Supra
            - list [ref=e353]:
              - listitem [ref=e354]:
                - link "Practice, Bristol Motor Speedway - Oval, Wed Sep 16, 1:00 pm" [ref=e355] [cursor=pointer]:
                  - /url: "#/d/1507810/s/111/race"
                  - generic [ref=e356]:
                    - generic [ref=e357]:
                      - generic [ref=e358]: 1:00 pm
                      - generic [ref=e359]: Practice
                    - generic "Classified P9" [ref=e360]:
                      - generic [ref=e361]: Pos
                      - generic [ref=e362]: P9
                    - generic "Fastest clean lap" [ref=e364]:
                      - generic [ref=e365]: Best
                      - generic [ref=e366]: "15.900"
                    - generic "Longest run without a reset, laps" [ref=e368]:
                      - generic [ref=e369]: Run
                      - generic [ref=e370]:
                        - generic [ref=e371]: "13"
                        - generic [ref=e372]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e373]':
                      - generic [ref=e374]: Fall-off
                      - generic [ref=e375]:
                        - generic [ref=e376]: "+0.12"
                        - generic [ref=e377]: s
              - listitem [ref=e378]:
                - link "Qualifying, Bristol Motor Speedway - Oval, Wed Sep 16, 1:30 pm" [ref=e379] [cursor=pointer]:
                  - /url: "#/d/1507810/s/112/race"
                  - generic [ref=e380]:
                    - generic [ref=e381]:
                      - generic [ref=e382]: 1:30 pm
                      - generic [ref=e383]: Qualifying
                    - generic "Classified P6" [ref=e384]:
                      - generic [ref=e385]: Pos
                      - generic [ref=e386]: P6
                    - generic "First timed lap" [ref=e388]:
                      - generic [ref=e389]: Lap 1
                      - generic [ref=e390]: "16.010"
                    - generic "Second timed lap" [ref=e392]:
                      - generic [ref=e393]: Lap 2
                      - generic [ref=e394]: —
              - listitem [ref=e396]:
                - group "Race, Bristol Motor Speedway - Oval, Wed Sep 16, 2:00 pm" [ref=e397]:
                  - generic [ref=e398]:
                    - generic [ref=e399]:
                      - generic [ref=e400]: 2:00 pm
                      - generic [ref=e401]: Race
                    - generic [ref=e402]:
                      - generic [ref=e403]: Laps
                      - generic [ref=e404]: "86"
                  - generic [ref=e406]: Deferred · exceeds memory budget
        - region "Indianapolis Raceway Park - Oval" [ref=e408]:
          - generic [ref=e409]:
            - generic [ref=e410]:
              - heading "Indianapolis Raceway Park" [level=2] [ref=e413]
              - generic [ref=e414]: Oval · Sun Sep 13
              - generic "NASCAR Xfinity Toyota Supra · NASCAR Xfinity Series" [ref=e418]: NASCAR Xfinity Toyota Supra
            - list [ref=e419]:
              - listitem [ref=e420]:
                - link "Practice, Indianapolis Raceway Park - Oval, Sun Sep 13, 1:00 pm" [ref=e421] [cursor=pointer]:
                  - /url: "#/d/1507810/s/114/race"
                  - generic [ref=e422]:
                    - generic [ref=e423]:
                      - generic [ref=e424]: 1:00 pm
                      - generic [ref=e425]: Practice
                    - generic "Classified P10" [ref=e426]:
                      - generic [ref=e427]: Pos
                      - generic [ref=e428]: P10
                    - generic "Fastest clean lap, fastest of the event" [ref=e430]:
                      - generic [ref=e431]: Best
                      - generic [ref=e432]: "16.000"
                    - generic "Longest run without a reset, laps" [ref=e434]:
                      - generic [ref=e435]: Run
                      - generic [ref=e436]:
                        - generic [ref=e437]: "14"
                        - generic [ref=e438]: laps
                    - 'generic "Pace lost over the longest run: its last clean lap minus its first, s" [ref=e439]':
                      - generic [ref=e440]: Fall-off
                      - generic [ref=e441]: —
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