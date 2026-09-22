# Whole-app acceptance coverage — September 22, 2026

## What has and has not been evaluated

Every product screen and its source-declared control families were reviewed for purpose, handler behavior, useful output, missing prerequisites and broken handoffs. The baseline inventory has **257 declarations/gesture families**, including repeated conditional controls. The [usefulness audit](whole-app-usefulness-audit-2026-09-22.md) records twelve prioritized findings and repairs.

The [0.18.0 development snapshot](implementation-snapshot-0.18.0-development.md) is the integration record for application/profile versions, final suite results and transport/native identity. It explicitly distinguishes the development transport from a complete portable release or installer.

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
| Tuning/map/feedback | 39 | Complete baseline inventory | Automated rapid-save regressions pass. Native 8.804-second phase switch and forced restart preserved the QA-only note; then cleared. Compact 1283×720 overlap was found, structurally repaired and passed candidate-3 native retest. Subsecond native switching, graceful exit and experiment history remain open. |
| Settings/connections/diagnostics | 28 | Complete baseline inventory | Browser theme/hotkey persistence, theme-only Save without Working, backup preparation, diagnostics and isolated connection/repair rejection exercised. Expanded connection handoff retested. Real auth/reconnect, relocation and storage-failure states remain open. |
| Unmounted RaceCardPanel disclosure | 1 | Identified as not currently rendered | Its source presence does not count as a reachable feature or a native-tested control. |
| Installer / upgrade / uninstaller | Separate administrative surface, outside the 257 application baseline | Existing disabled-control painting and upgrade-backup/legacy-cleanup code reviewed | These changes predate 0.18; current installer source changed only for versioning. The development transport reuses installed runtimes and does **not** test install/upgrade/rollback/uninstall. Historical lifecycle passes retain their earlier version scope. |
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

Candidate 2 completed the primary native fuel regression: September 20, 3:09 PM Bristol, lap 102 shows 68.2 green laps before reserve against 56.0 scheduled laps left and +12.2 margin, with the race date visible. Missing-range recordings and the complete 1280×720/1920×1080 matrix still need separate checks. Candidate 3 proved the overview at exact native 1280×720 but failed investigation styling after reload. Candidate 3 also proved four focused charts and unclipped, scrollable values for all ten selected laps. Candidate 3 also passed the repaired compact Tuning layout. Candidate 4 then passed readable investigation styling, all ten lap values in four focused charts, restored custom traces and consistent complete-race totals before/open/return/hard reload. The 3:09 PM race retained 128 green / 31 caution / 18.45 gal; 9:08 PM retained 118 green / 34 caution / 17.36 gal. The exact ZIP hash and 675 manifest-listed files were verified. Its native report remains in progress; this is bounded acceptance rather than an all-controls pass.

New native note regression: type a distinctive final word and immediately switch phase, turn, race or page; reopen and confirm the word appears only in its original context. Repeat with an earlier save delayed and after restart. Type then immediately begin analysis; the submitted packet must include the final word.

Cold analysis remains open performance work: the fresh native-rate backend run measured 18.858 s overall and 17.678 s in a combined decoding/analysis stage, including loading, hashing, cache serialization and enrichment. There is no decoder-only duration. Decoded data is not reused across profile upgrades, and no seconds-at-most promise is established.

Do not mark this document complete from seven reachable pages, endpoint screenshots or aggregate unit-test counts. Update individual cases with the executable identity, real data selection, observed output and unresolved failures.
