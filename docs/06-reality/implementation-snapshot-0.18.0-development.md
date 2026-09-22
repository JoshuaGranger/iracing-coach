# iRacing Coach 0.18.0 development: explain the race and preserve the driver's work

Date: September 22, 2026. Application source version: **0.18.0**. Analysis profile: **post-race-foundations-v19**. This follows the [0.17.7 snapshot](implementation-snapshot-0.17.7-development.md) and the [Bristol investigation](bristol-pace-investigation-2026-09-22.md). It records development implementation, not a released installer or whole-app acceptance.

The user needs the app to explain what changed in a race, show the relevant data, and identify what to try differently. This round therefore joins measured pace changes to lap comparisons and driving tests, and repairs workflow failures that lose context or make the displayed evidence contradictory.

## Race Analysis and Technical data

The backend now produces a structured race investigation containing pace/position progression, pit and incident events, compared lap cohorts, per-area control measurements, candidate explanations and conditional driving trials. Its comparisons distinguish usable reference laps from observed laps with traffic, damage or other exclusions. A large final fade remains visible even when the final laps cannot be used as clean references.

[RacePaceInvestigation](../../companion-app/src/iRacingCoach.UI/RacePaceInvestigation.razor) presents the measured change and the first suggested trial, then lets the driver choose a comparison and inspect its cohorts, inputs and uncertainties. Evidence buttons open the specified laps, signals and track region in Telemetry. Returning to the findings retains the investigation context. The workspace header includes the recording date to distinguish similar Bristol runs.

Track evolution separates per-lap numeric usage measurements from starting/carry-over session metadata. A starting rubber label is not turned into a time series. Missing control, traffic and condition measurements remain absent; they do not establish unchanged conditions or a ruled-out cause. The backend handles null optional trace/segment fields, withholds unsupported area measurements and bounds comparison output while retaining the finish and the largest pace change. The profile also includes the corrected green-exposure basis for pace slope; the [Bristol case study](bristol-pace-investigation-2026-09-22.md) preserves the earlier measured correction and its limits.

The native 0.17.7 baseline exposed a separate pit-detail defect in the September 20 3:09 PM Bristol recording: the overview reported a +12.2-lap post-stop margin, but the stop detail showed 55.4 laps of range against 56 remaining. **55.42 was the pre-service range.** The next run's actual projected range was **68.19**, yielding **+12.19** against 56 scheduled laps. [RuntimeMapper](../../companion-app/src/iRacingCoach.Coordinator/RuntimeMapper.cs) now reads the post-service field and never substitutes pre-service fuel when it is missing. The UI states the all-green, before-reserve basis and labels the result Projected surplus. The configured planning reserve is separate from this unreserved post-stop projection. Backend range wording now uses that actual configured reserve instead of hardcoding two laps, and pit-cycle advice distinguishes gained, unchanged and lost positions.

Technical overview cards no longer treat stops above the fuel minimum as inherently unnecessary or remaining tire tread as proven useful grip. Racecraft's overview prioritizes six outcome/pace slots; all phase and control measurements remain available in the drilldown. Browser findings at 1280×720 led to the density and comparison-layout repairs. Candidate 3 confirmed that all four overview cards fit and remain readable at exactly 1280×720 on the native host. The separately opened investigation still failed styling; its final repair has a separate acceptance requirement.

## Follow-up questions about the recording

[RaceCoachPanel](../../companion-app/src/iRacingCoach.UI/RaceCoachPanel.razor) exposes race-specific questions, cancellation, the current answer and earlier questions. The numerical packet includes exact lap identities, cohort exclusions, corner bounds, measured conditions and investigation limitations. It does not promote starting track usage to measured evolution. A late answer remains attached to the original race rather than appearing on a newly selected recording.

The disconnected panel offers the connection path. Browser retest confirmed that Connect ChatGPT now opens Settings with Connections expanded. Local analysis and the deterministic investigation remain usable without a provider. Packet and race-switch behavior have automated coverage; **a real authenticated ChatGPT question/answer, cancellation and reconnect lifecycle remain pending**. Test doubles are not evidence of the external service working.

## Home, setup decisions, tuning and jobs

| Area | Concrete change |
|---|---|
| Home and routine copy | Actions describe the driver's task; greeting/workflow filler and repeated unavailable labels are reduced. Onboarding, connection and backup copy names the actual next step. |
| Starting Tune and setup library | Find a baseline describes the actual operation. An empty setup library can lead to recording review. Continuing into tuning requires the baseline's matching setup fingerprint, car and exact track context; an unrelated race is not silently substituted. Without matching evidence the review path retains the package. |
| Tuning notes | The last keystrokes survive immediate turn/phase/race changes and navigation. Saves retain their original draft/turn/phase while waiting for persistence; they cannot overwrite a newly selected race or a replacement map draft. |
| Race-analysis jobs | Open result and Retry use the job's recording identity, rather than the currently selected race. Missing recordings and duplicate running work receive explicit handling. This is not a claim that every non-analysis job has a new retry implementation. |
| Planning | Labels distinguish all-green fuel feasibility and minimum fuel stops from optimal tire/caution strategy. |
| First run and Settings | Failed detection offers Choose folders; Settings offers Continue setup until setup completes. Diagnostics and backup actions use concrete task labels. |
| Installer and older-version cleanup | Existing source uses custom disabled-control painting for readable installer buttons/checkboxes, plus upgrade backup and legacy-install cleanup. These predate this round; installer source changed only for versioning. The 0.18 development transport does not exercise install, upgrade, rollback or uninstall. |

The [whole-app usefulness audit](whole-app-usefulness-audit-2026-09-22.md) covers **257 baseline control declarations/gesture families in 25 product files** and twelve prioritized findings. Dynamic row controls are grouped by action; conditional declarations can be counted separately. This is a complete baseline **source inventory**, not proof that every button was clicked, every state passed, or all requested usefulness gaps are closed. The [acceptance coverage document](whole-app-acceptance-coverage-2026-09-22.md) records those distinctions.

## Windows host startup

A local 0.18 probe reproduced a startup exit before Blazor rendering: WebView2's WPF composition control failed to create a D3D9 device (`0x8876086A`). The host now uses the Windows Forms BlazorWebView inside the existing WPF window. Shared UI, native popout and confined browser/service configuration remain in place.

The probe published and remained responsive without the matching startup exception; affected host/isolation tests passed. Local input/capture failed before visual acceptance. The subsequent **DESKTOP-SIM candidate-2 run successfully launched, rendered, accepted input and supplied screenshots/accessibility data**, without the earlier D3D startup crash. This supersedes the local probe's input blocker for that remote run. The probe's original scope remains recorded in the ignored `companion-app/artifacts/bristol-investigation-20260922/NATIVE-HOST-ACCEPTANCE.md`.

## Candidate-2 native findings and follow-up repairs

DESKTOP-SIM tested `C:\Temp\ICQA0180-0922\app` in a disposable profile, verifying all **676 manifest files**. Candidate-2 transport SHA-256 was `612a2dae3bf6c60e412701029be525ec7cbe37e98044f3d4fab7248c7d8d18f2`. The private evidence is `QA-0180-NATIVE-20260922.md` and its JSON companion in the QA share. This identifies the tested candidate, not the final repaired transport.

| Native check | Observed result |
|---|---|
| Bristol identity and comparisons | Correct 3:09 PM date; five comparisons; final 155–159 versus 116–120 at +1.496 s. Matched-age selection showed 35–37 versus 130–132 at +0.270 s. |
| Findings → traces → findings | Show evidence selected six matched-comparison laps and the 63–77% area. Back to findings retained the comparison. Compare traces selected the same six laps without an area restriction. |
| Post-stop fuel | **Passed:** 68.2 green laps before reserve, 56 remaining and +12.2 margin agree; +2-position pit-cycle advice is appropriate. Screenshot: `QA-0180-pit-range-fixed.png`. |
| Planning | After manually selecting 3:09 PM, 160 laps produced one stop at lap 80 and 139.4-lap range; changing to 100 cleared the stale result and produced zero stops with +39.4 laps. The first result was observed by 22.601 s, an upper bound including tool/input observation time. |
| Setup → tuning | Single Pit Road search returned three recorded baselines. Tune this setup carried the setup name and linked 3:09 PM race into Tuning. |
| Tuning notes | A QA-only Early/zone-1 note survived an 8.804-second phase switch and forced QA-process restart; the note was then cleared. This is **not** a subsecond typing/switch or graceful-shutdown stress pass. |
| Starting Tune | All four pages traversed; filtered car/exact layout selection worked; unresolved typed track disabled Find. Review a recording returned to the race browser. No simulator setup was loaded or modified. |
| Replay and native monitor | The separate **9:08 PM** recording's replay showed 28:29/lap 67/130 mph, advanced to 28:40 and paused. The primary 3:09 PM recording correctly reports replay unavailable because full-field positions are absent. Open live telemetry showed the native popout and hid the main window; popout Close returned to the main app. |
| Window sizes and exit | Compact 1272×720 and 1283×720 and maximized 2560×1392 were exercised. Exact native 1280×720 was not established. Main Close removed windows but left a responsive process; tray policy versus cleanup was not resolved, so graceful process exit was not passed or classified as a crash. |

The same test exposed three material presentation defects:

- **Missing investigation styles in the native publish:** black chart/axis text and default grey buttons, despite the shell being styled (`QA-0180-investigation-render.png`). Candidate 3 still failed after Ctrl+F5 despite the scoped entry and imported bundle both being present. Candidate 4 moves both new component styles into an explicitly namespaced shared static stylesheet, loads it directly in the native host, retains fingerprinted asset loading in Preview, and changes native asset cache keys to build 4. Readability and asset-cache regressions pass. Candidate-4 native retest at exactly 1280×720 confirmed readable styled controls and chart text (`QA-0180-c4-investigation-1280x720.png`).
- **Trace hover clipping and excess channels:** candidate 2 retained ten chart rows and clipped cohort hover values after two or three rows (`QA-0180-trace-tooltip-clipping.png`). The brake-release Show evidence repair now passes browser retest with exactly Speed, Brake, Steering and Time delta. Candidate 3 then passed native retest at exactly 1280×720: Speed, Brake, Steering and Time delta were the only four charts, the 63–77% region remained selected, and all ten selected laps were readable through synchronized tooltip scrolling without clipping (`QA-0180-c3-tooltip-final159-1280x720.png`).
- **Compact Tuning overlap:** the setup-origin strip inserted a third direct child into a two-row workbench, placing the race bar and map/toolbox in conflicting rows (`QA-0180-tuning-compact-overlap.png`). The origin and race selector now share an auto-height header; the body uses only the remaining height. Fourteen affected tests passed. Candidate-3 native retest subsequently confirmed the repaired compact Tuning layout.

Generic navigation to Planning still retained the latest 9:08 PM reference rather than the reviewed 3:09 PM race; manual dated selection worked. This context gap remains open.

The Home/browser total discrepancy was traced to a second UI projection after opening a race: it summed segmented runs, which omit a service-boundary lap, and replaced the backend complete-race summary. Candidate 4 retains that backend summary in the analysis workspace and republishes it unchanged to Home and grouped race entries. Missing whole-race fuel remains unknown even when partial run fuel exists. Two behavioral [regressions](../../companion-app/tests/iRacingCoach.Tests/RaceOverviewConsistencyTests.cs) cover initial open, persisted-cache reopen, service-boundary inclusion and missing fuel. Candidate-4 native comparison passed before opening, after opening each Bristol race, after returning to the browser, and after Ctrl+F5. The 3:09 PM race retained **128 green, 31 caution and 18.45 gal**; the 9:08 PM race retained **118 green, 34 caution and 17.36 gal**. The separate 18.31-gal measurement remains explicitly labeled Fuel used in runs.

## Candidate-4 native confirmation

DESKTOP-SIM verified the exact candidate-4 archive hash and all **675 manifest-listed files** (the ZIP has 676 entries including its manifest), then ran `C:\Temp\ICQA0180-c4-0922\app` in the reused disposable profile. `QA-0180-CANDIDATE4-NATIVE-20260922.md` remains an in-progress report; the following are its completed checks, not whole-app acceptance.

- The investigation is styled and readable at exactly **1280×720**, and all four Technical overview cards fit. Screenshots: `QA-0180-c4-investigation-1280x720.png` and `QA-0180-c4-overview-1280x720.png`.
- Show evidence displays **Speed, Brake, Steering and Time delta**. All ten selected laps, 116–120 and 155–159, remain readable through synchronized tooltip scrolling without overlap. Back to findings dismisses the open Spotlight menu, reopening evidence keeps it dismissed, and Restore my traces restores the earlier ten-row custom layout. Screenshot: `QA-0180-c4-tooltip-final159-1280x720.png`.
- Complete-race green/caution/fuel totals remain consistent before opening, after each Bristol race, on return and after hard reload. Screenshots: `QA-0180-c4-home-before.png`, `QA-0180-c4-totals-after-primary.png`, `QA-0180-c4-totals-after-both.png` and `QA-0180-c4-totals-reloaded.png`.

The 9:08 PM recording was fully rendered by a **13.235-second observation upper bound**, including tool/observer latency. This is not a precise app-duration benchmark. A retained candidate-3 QA note followed a 9.990-second phase switch; that timing does not establish subsecond save behavior. The report had not yet completed candidate-4 note restart acceptance at this snapshot.

## Verification status

| Evidence | Current result and scope |
|---|---|
| Final repository/backend verification | **Passed: 1,067 backend tests, 21 contracts and 28 fixtures.** This is deterministic/backend evidence, not native UI acceptance. |
| Final full application suite | **Passed: 434/434, zero skipped.** Run record: `development-round2-final5.trx`. |
| Focused tuning/workflow verification | **56/56 passed**, including five note-persistence regressions covering immediate switches, disposal, delayed writes and stale-map rejection. |
| Focused fuel/overview verification | **28/28 passed**, including five new cases for pre/post-service range, absent/null/zero values and retention of detailed metrics. |
| Local UI compilation after copy repairs | Passed with zero warnings and errors. |
| Self-contained Windows host probe | Published and stayed responsive; affected host checks passed. Visual/input acceptance was blocked in that probe. |
| Browser review | At 1280×720, bounded investigation, Settings and disconnected dashboard-editor checks passed. Exact four-channel inspection and rejected grid-size selector restoration passed after repairs. Detailed steps/limits: ignored `companion-app/artifacts/product-audit-20260922/ROOT-BROWSER-QA-20260922.md`. |
| DESKTOP-SIM 0.18 candidate 2 | Bounded checks completed above, including coherent fuel and comparison/trace handoffs. Missing scoped styles, hover clipping and compact Tuning overlap were found. Later scoped retests are recorded separately below. |
| DESKTOP-SIM 0.18 candidate 3 | Exact native 1280×720 overview, four-channel hover and compact Tuning checks passed; investigation styling still failed after reload. Candidate 4 replaces that scoped styling path and its visible native styling now passes. |
| DESKTOP-SIM 0.18 candidate 4 | Exact hash and 675 manifest files verified; readable investigation and four overview cards at 1280×720, four-channel scrolling through all ten lap values, trace restoration/menu dismissal, and complete-race totals through open/return/reload passed. The native report remains in progress. |
| Authenticated AI / Garage61 | Not established by isolated-profile tests; pending authorized real-service interaction. |
| Active simulator telemetry | Deferred at the user's request. Recording review does not validate live cue timing, focus or sustained telemetry performance. |

A fresh DESKTOP-SIM backend run used the normal workflow at native sample rate (`target_hz=null`) and an empty disposable archive: **18.858 s total, 17.678 s in the decoding and analysis stage, cache miss**. That stage counter includes loading, analysis, hashing, cache serialization and enrichment; it does not isolate decoder time. The five comparison cohorts, complete lap data and track provenance matched the earlier fresh v19 result. Completed lap 159 remained an observed finish; partial lap 160 stayed outside comparison windows. Original and copied source hashes were unchanged. This measures the backend workflow, not click-to-result UI latency or a cache hit. Evidence: `BRISTOL-V19-NATIVE-validation.md`/JSON; analyzer digest `260b76154736ac7041d6bef5808e9f319f54451496e0daae6bbe5d0486317ef4`; redacted analysis SHA-256 `0dec80f67e055ee12af03133471a9ab488bc97dcc5d4770480e78d773d0c7b8d`. Intermediate v19 development caches share a pipeline key; the fresh archive was needed to establish the latest wording rather than reuse an earlier development result.

The cold path remains a measured performance gap against the requested seconds-at-most experience. Decoded telemetry is not reused across analysis-profile upgrades, so an upgrade can repeat loading and analysis even for an existing recording. A reusable decode cache with correct source invalidation and separate stage timings remains work to do. Neither this measurement nor cached reopen evidence establishes a speed promise for every recording or library.

Focused suites overlap the full suite; their totals must not be added to claim wider test coverage. Existing 0.17.7 native results retain their original scope and executable identity. They do not become 0.18 passes because the source has been repaired.

## Development transport boundary

The 0.18 candidate is a **.NET self-contained win-x64 development transport**. It includes the development application/backend for isolated evaluation but **reuses the already installed Python and Coach Engine runtimes**. It is **not a full portable package or installer**, and does not establish a new install/upgrade/uninstall lifecycle pass. Original racing files and production settings are not test fixtures to modify.

| Final transport identity | Value |
|---|---|
| Source revision | Candidate 4 was built from `7e9a356dec0fe659b04ff93b81c6c24d36f8dbd3` plus this round's uncommitted changes. Final release provenance belongs in the release manifest. |
| Candidate directory / archive | `iRacingCoach-0.18.0-development-transfer-candidate4.zip` on the private QA share; **82,970,844 bytes**, **676 ZIP entries**. |
| SHA-256 / manifest verification | Archive SHA-256: `71cad6530282f25cf63f389227ec5d7a00a275779ae970b39dc571215c550f3c`. DESKTOP-SIM verified the ZIP hash and all 675 manifest-listed files. Completed native checks are recorded above. |
| Native report | Candidate-2/3 findings and completed candidate-4 retests recorded above. `QA-0180-CANDIDATE4-NATIVE-20260922.md` is still in progress; no exhaustive native acceptance claim. |

Private recordings, redacted derived investigations, screenshots and candidate binaries remain in ignored artifacts or the private QA share. The source repository retains sanitized fixtures, tests and these reviewable conclusions.

## Remaining work

This round does not close useful practice/test-baseline discovery, a browsable experiment history, a complete real matched setup A/B cycle, authenticated service failures/recovery, sustained active live driving, the complete native state/viewport/DPI matrix, or broad interrupted/disk-full archive recovery. Initial cold analysis remains a distinct performance workload from cached reopen; no blanket claim that every race now finishes in seconds is supported here.

The principal acceptance case remains the identified Bristol race: see both fades and pit recovery, distinguish screened comparisons from the final observed loss, show track-state limitations, open the supporting laps, and produce a useful test for the next session. A coherent racing explanation and successful follow-through matter more than reaching all seven pages.
