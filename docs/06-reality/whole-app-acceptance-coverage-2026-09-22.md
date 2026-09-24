# Whole-app acceptance coverage — September 22, 2026

## What has and has not been evaluated

Every product screen and its source-declared control families were reviewed for purpose, handler behavior, useful output, missing prerequisites and broken handoffs. The baseline inventory has **257 declarations/gesture families**, including repeated conditional controls. The [usefulness audit](whole-app-usefulness-audit-2026-09-22.md) records twelve prioritized findings and repairs.

The [0.18.0 development snapshot](implementation-snapshot-0.18.0-development.md) is the integration record for application/profile versions, final suite results and transport/native identity. It distinguishes the native-tested development transport from the subsequently built full portable and installer packages. The full package hashes and clean source revision are recorded there. Exact full-portable native smoke and guarded installer lifecycle now pass within their separately stated scope.

This does **not** mean every control was clicked in every state. The following evidence categories remain separate:

- **Source review:** what the implementation actually connects and what the result can claim.
- **Automated tests:** the named deterministic behaviors and regressions exercised by tests.
- **Browser interaction:** DOM, keyboard, layout and presentation behavior in the preview; it cannot prove Windows popout/tray/installer behavior.
- **Native interaction:** the exact development executable, device, input, window size and real recording tested.
- **Real driving:** an active simulator session; recordings cannot establish cue timing, focus or sustained live performance.

## Coverage by app area

| Area | Baseline declarations / families | Source usefulness review | Evidence and remaining acceptance |
|---|---:|---|---|
| Home | 7 | Complete baseline inventory | Browser empty/populated states and native dated race entry exercised. Candidate 4 repairs the Home/browser summary divergence: complete backend totals survive opening and cached reopening instead of being replaced by partial segmented-run sums. Two regressions pass; candidate-4 native retest passed before/open/return/hard reload for both Bristol races. Empty/error/pending states are not exhaustively accepted. |
| Navigation, shell and jobs | 14 | Complete baseline inventory | Job result identity repair needs exact result navigation, cancel/retry and failure recovery checks. Source/test coverage is not all-state native acceptance. |
| First run | 13 | Complete baseline inventory | New failed-detection→Settings→Continue recovery compiled; wrong path, correct path, save failure and restart must be exercised. |
| Live dashboard/editor/traces | 34 | Complete baseline inventory | Browser customize, layout/inspector persistence, search/add/remove, keyboard move/resize, undo/redo and factory guards exercised. Rejected grid-size selector restoration passed after repair. Pointer drag/edge resize and complete scaling matrix remain open. **Active live testing deferred by user.** |
| Native popout and tray | 14 | Complete baseline inventory | Candidate 2 opened the popout, hid the main window and returned on popout Close. Main Close left a responsive process without windows; tray policy versus cleanup remains unresolved. Multi-monitor/DPI, shortcut conflicts and driving focus remain unaccepted. |
| Race browser/workspace | 14 | Complete baseline inventory | Candidate 2 showed the correct dated Bristol, five comparisons and disconnected question drawer. Browser Connect ChatGPT handoff now expands Connections. Authenticated answers and practice/test discovery remain open. |
| Lap/trace workspace | 34 | Complete baseline inventory | Native candidate 2 selected six matched laps, highlighted 63–77%, and retained the comparison on return. Browser repaired handoff confirmed Speed/Brake/Steering/Time delta only. Candidates 3 and 4 verified the four-chart repair and synchronized scrolling across all ten selected lap values without clipping at exactly 1280×720. Candidate 4 also passed trace-layout restoration and Spotlight dismissal on return. |
| Technical data | 10 | Complete baseline inventory | **Candidate-2 native pass:** Bristol range 68.2 before reserve, remaining 56, margin +12.2 and gained-position advice agree. Missing/null branches remain automated evidence. Candidate 3's four overview cards fit exactly 1280×720, but its investigation remained unstyled after reload. Candidate 4 replaces scoped styles with an explicit shared stylesheet and passed native readable styling and four-card fit at exactly 1280×720. |
| Race replay | 10 | Complete baseline inventory | Candidate 2: 9:08 PM race seek/play/pause passed; primary 3:09 PM correctly unavailable without full-field positions. All phase/missing-field/keyboard cases and finding-to-event links remain unaccepted. |
| Planning | 12 | Complete baseline inventory | Candidate 2: manually selected 3:09 PM, 160→100 laps changed 1→0 stops and cleared stale output. Generic navigation retained latest 9:08 PM instead of the reviewed race; context gap remains open. Timed/manual/invalid/no-reference states are not exhaustive. |
| Setups and Starting Tune | 27 | Complete baseline inventory | Candidate-2 search and Tune this setup carried the linked 3:09 PM race/setup. Four Starting Tune pages, exact car/layout search, invalid-track guard and Review a recording handoff exercised. No real simulator setup change or complete A/B acceptance. |
| Tuning/map/feedback | 39 | Complete baseline inventory | Automated rapid-save regressions pass. Native 8.804-second phase switch and forced restart preserved the QA-only note; then cleared. Compact 1283×720 overlap was found, structurally repaired and passed candidate-3 native retest. Candidate 4 passed source-linked Tuning at 1280×720 and maximized 2560×1392, then restored 1280×720. The retained Early note survived the candidate-3→4 restart without leaking into Mid and was cleared. Subsecond native switching, graceful exit and experiment history remain open; exact 1920×1080 is unverified. |
| Settings/connections/diagnostics | 28 | Complete baseline inventory | Browser theme/hotkey persistence, theme-only Save without Working, backup preparation, diagnostics and isolated connection/repair rejection exercised. Expanded connection handoff retested. Real auth/reconnect, relocation and storage-failure states remain open. |
| Unmounted RaceCardPanel disclosure | 1 | Identified as not currently rendered | Its source presence does not count as a reachable feature or a native-tested control. |
| Installer / upgrade / uninstaller | Separate administrative surface, outside the 257 application baseline | Existing disabled-control painting and upgrade-backup/legacy-cleanup code reviewed | Exact full installer from clean `a7153b8082598586d2d9c9bbccf47efcf122d862` passed guarded install, manifest validation/tamper rejection, rollback, same-version running-app replacement, owned/unowned legacy handling, fake state/credential cleanup, preservation fixtures, uninstall/reinstall/uninstall and cleanup. Production registry/shortcut/task/service/firewall integration, visual contrast/progress and interactive prompts remain unverified. Historical passes retain their earlier scope. |
| **Total** | **257** | **Source audit complete for baseline** | **Whole-app native acceptance incomplete** |

## Required states for each applicable control family

| State | Pass condition |
|---|---|
| Ready | Action produces the named useful result for the exact selected recording/context. |
| Empty / no matching data | Explains the real missing prerequisite and offers the useful next action; does not hide available partial evidence. |
| Missing optional channels | Dependent values remain absent, useful measurements survive, and unavailable values are not zero. |
| Busy | Stage/context visible, navigation remains responsive, no duplicate work or stale result publication. |
| Cancel | Work ends without corrupting archive/source; later retry works and preserves identity. |
| Failure / retry | Error identifies recovery; retry performs that operation rather than redirecting to an unrelated view. |
| Race/turn/phase switch | Pending edits remain bound to their original context; obsolete responses cannot replace new selection. |
| Reopen / restart | Saved settings, notes, layouts and results restore; underlying original recordings are untouched. |
| Keyboard / focus / scaling | Every action operable and understandable; focus restored, no clipped controls, no unexpected simulator focus capture. |

## Six outcome tests that matter more than page count

1. **Explain the Bristol fade.** Find the correct open race, distinguish both fades and pit recovery, compare sensible lap windows, show track usage/conditions and driving traces, and distinguish observed late loss from eligible reference evidence.
2. **Tell the driver what to try.** Name the measured problem and a concrete technique or controlled setup test, state expected change in the traces, and define a comparison that can disprove the explanation.
3. **Prepare the next race.** Show fuel feasibility and assumptions, distinguish equal fuel stints from optimal tire/pit strategy, and carry a useful driving focus into practice/review.
4. **Evaluate a setup change.** Use the exact baseline/setup lineage, preserve driver feedback, propose an allowed manual change, and compare a compatible later recording while retaining failed history.
5. **Help while driving.** Deliver timely short, correct cues from a valid baseline without stale data, invented opponent telemetry or focus interruption. This remains deferred until active simulator testing is authorized.
6. **Keep recordings useful.** Discovery, analysis, cached reopen, error recovery, retained source, relocation and backup must work on a representative real library without unexplained stalls.

## Current focused automated evidence

The audit repair workstream ran **56/56 affected application tests successfully**, including five new tuning-note regressions. These cover behavior rather than merely asserting button text. Final application verification passed **434/434 with zero skips** (`development-round2-final5.trx`); backend verification passed **1,067 tests, 21 contracts and 28 fixtures**. The snapshot records the exact development transport separately.

After the native fuel contradiction and browser density finding, a separate affected run passed **28/28** tests (`PitFuelPresentationTests`, `RaceFoundationMapperTests`, `IterationUxTests`, `RacePlanDecisionConsumerTests`). The five new cases verify the correct pre/post-service field distinction, missing/null/zero values and retention of all detailed metrics behind the concise overview. These suite totals overlap existing integration tests and should not be added together as a whole-app coverage count.

Candidate 2 completed the primary native fuel regression: September 20, 3:09 PM Bristol, lap 102 shows 68.2 green laps before reserve against 56.0 scheduled laps left and +12.2 margin, with the race date visible. Missing-range recordings and the complete 1280×720/1920×1080 matrix still need separate checks. Candidate 3 proved the overview at exact native 1280×720 but failed investigation styling after reload. Candidate 3 also proved four focused charts and unclipped, scrollable values for all ten selected laps. Candidate 3 also passed the repaired compact Tuning layout. Candidate 4 then passed readable investigation styling, all ten lap values in four focused charts, restored custom traces and consistent complete-race totals before/open/return/hard reload. The 3:09 PM race retained 128 green / 31 caution / 18.45 gal; 9:08 PM retained 118 green / 34 caution / 17.36 gal. The exact ZIP hash and 675 manifest-listed files were verified. Its native report is now complete for the bounded scope, including compact/maximized Tuning and the retained note after candidate restart. Exact 1920×1080 remains unverified because the supported native resize action was refused; no app defect is inferred from that tool limit. This is bounded acceptance rather than an all-controls pass.

**Final full-package verification:** the exact portable SHA `0ff9d701fe86f0fbf5e3bdcaabce45bc1a4be711a188efe56b6a7ffaeec6035b` passed all 9,333 payload hash/size checks with no extras, ordinary bundled runtime-directory verification, packaged Python 3.12.13 AMD64 and signed Coach Engine 0.146.0-alpha.9.2 checks, native startup and the primary Bristol flow. At 1426×893 it showed 128 green / 31 caution / 18.45 gal, a styled +1.496 s/lap investigation and four-signal/ten-lap evidence at 63–77%. This reused the isolated cache, did not reanalyze raw telemetry or authenticate AI, and did not capture the short-lived backend executable path. Original telemetry and production executable hashes were unchanged. Evidence: `QA-0180-FULL-PORTABLE-SMOKE-20260922.md`/JSON.

The exact installer SHA `0f68a702795fd26421bacf3e3590398374e58a10f662cf6eedbbe330b7cc2fad` separately passed the guarded lifecycle described in the table. Its old-version-marker replacement used the same payload and disposable preservation/credential fixtures; Windows integration and visual installer rendering were deliberately excluded. Evidence: ignored `companion-app/artifacts/product-audit-20260922/installer-lifecycle-0180-report.md` and results JSON. These exact-artifact checks add bounded proof without reclassifying every candidate-4 scenario as a full-package test.

New native note regression: type a distinctive final word and immediately switch phase, turn, race or page; reopen and confirm the word appears only in its original context. Repeat with an earlier save delayed and after restart. Type then immediately begin analysis; the submitted packet must include the final word.

Cold analysis remains open performance work: the fresh native-rate backend run measured 18.858 s overall and 17.678 s in a combined decoding/analysis stage, including loading, hashing, cache serialization and enrichment. There is no decoder-only duration. Decoded data is not reused across profile upgrades, and no seconds-at-most promise is established.

## September 23 round-1 cases

Executable: round-1 development build `companion-app/artifacts/dev/v0.18.0-dev-20260923-222439` (source through the round-1 snapshot commit). Interaction evidence below is **browser preview** of the same source (Blazor Server, isolated profile, 1280×720, Edge headless) unless marked native. Real data: archived v19 Bristol analysis (September 20, 3:09 PM) and copied New Hampshire/Iowa recordings.

| Case | Action | Observed result | Evidence type |
|---|---|---|---|
| Find the correct Bristol | Sessions → Bristol, Sep 20 3:09 PM | Opens Race view for that recording; restores on restart | Browser |
| Locate the fade | Race → "Run 2: earlier to final green laps" | 116–120 vs 155–159, +1.496 s/lap, 5 of 10 laps excluded (close traffic, not racing) | Browser |
| Comparable laps, exposure, fuel | Race → "Runs 1 and 2: similar green exposure" | 35–37 vs 130–132, +0.270 s/lap, 21.7–23.7 vs 21.8–23.8 green, 15.2 vs 5.7 gal, 78 °F both | Browser |
| Inspect the section with upstream | Sections → D | Lap 36 vs 132, entry +0.121, inside +0.096, exit +0.217, −6.0 mph; approach dashed on the map from C | Browser |
| Merged section | Sections → E | Grouped 77.5–86.0%, median −0.008 s, laps 35 vs 36 | Browser |
| Save one test | New Hampshire Aug 1 9:09 PM → clean comparison → Save test | Saved "Test a smoother steering arc"; listed under Tests | Browser |
| Later-recording check | Tests → Check the Aug 2 1:30 PM offline test | Not comparable: no screened laps at 25.1–35.1 green laps into a run; check kept | Browser |
| Return from a test | Top-bar Sessions while a test is open | Returns to the list (regression test added) | Browser + automated |
| Plan from the reviewed race | Review Bristol → Plan → Build plan | 1 stop between laps 21 and 139, split lap 80, 139.4-lap range from 18.6 gal | Browser |
| Setup comparison | Setups → compare two recorded NH setups | 41 changed, 0 unknown, 43 identical hidden until Show all values | Browser |
| Empty setups folder | Isolated profile with no `setups` folder | Empty library, no failure (previously "Update failed") | Browser + automated |
| Settings | Settings | "Local analysis is ready"; Connections row shows ChatGPT and Garage61 status | Browser + automated |
| Cold click-to-result | Open a race with no cache | 24.1 s | Browser timing |
| Native start | Development executable, isolated profile | Responsive window; only the expected isolated Garage61 log entry | **Native (no clicks)** |

Not exercised this round: native clicks and screenshots (desktop control declined), 1920×1080, 150% scaling, reduced motion, cancel/retry of a trial check, active live telemetry, authenticated providers, installer.

Do not mark this document complete from seven reachable pages, endpoint screenshots or aggregate unit-test counts. Update individual cases with the executable identity, real data selection, observed output and unresolved failures.
