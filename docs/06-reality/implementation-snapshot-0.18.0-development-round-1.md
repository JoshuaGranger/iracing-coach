# iRacing Coach 0.18.0 development round 1: one investigation, one saved test, one plan

Date: September 23, 2026. Branch `codex/development-round-1`, from handoff baseline `4c98a46` through the commit that adds this record. Application source version stays **0.18.0**; analysis profile stays **post-race-foundations-v19**. The race investigation moves to **schema 2**, projection `sections-trials-v1`. This follows the [0.18.0 development snapshot](implementation-snapshot-0.18.0-development.md) and turns the September 22 [compact study](../01-product/visual-companion-redesign-2026-09-22.md) from a prototype into application behavior.

The governing test is Joshua's: five features that win races beat a thousand that do not. This round therefore added one connected loop (find the loss, try one change, check it on a later recording) and removed or folded surfaces that did not serve it.

## What the driver can now do

| Workflow | Application behavior | Where |
|---|---|---|
| Find the session | One **Sessions** library replaces Home and the recording browser. Races, qualifying, practice and offline testing are grouped by event; filters are All / Races / Practice & tests. Archived analyses whose recording is no longer in the telemetry folder stay listed. Offline sessions no longer collapse into one event. | `SessionsPage.razor`, `DashboardMapper.RealIdentifier` |
| Locate the fade | **Race** view: lap progression with screened/excluded laps, run trend, caution, service and incident marks, plus position; beside it every backend comparison with exact laps, fuel, exposure, track temperature, changed measurements and explanation states. | `RaceInvestigationView.razor`, `RaceProgressionChart.razor` |
| Compare runs at the same age | **Runs** view overlays runs on green laps into the run, with the five-lap observed median (traffic included, broken at gaps) and an optional screened fit confined to its domain. | `RunOverlayChart.razor` |
| Inspect the track section | **Sections** view: recorded map with the approach (dashed) and section, five review sections with later-minus-earlier medians, and six synchronized channels (speed, time gap, throttle, brake, steering, body slip) for the fastest and slowest real passes. One cursor drives the charts and the map. | `SectionInspectionPanel.razor`, `race-inspection.js` |
| Try one change | A comparison that passes every screen offers one conditional test with its metric, section bounds and version, exposure window, laps to beat and success rule. **Save test** writes it to the archive through the backend. Finish comparisons with traffic are never offered as a baseline. | `TrialAction.razor`, `driving_trials.py` |
| Check it later | Saved tests appear at the top of Sessions. Compatible later recordings are listed; **Check** analyzes if needed, evaluates, and opens a before/after section view. Every check is kept; checking the same recording again replaces its entry. Verdicts are Improved, Worse, Not established or Not comparable; there is no "unchanged". | `TrialDetail.razor`, `CompanionState.Investigation.cs` |
| Plan the next race | Plan follows the race being reviewed, not the newest recording. One decision (for example "1 stop · between laps 21 and 139"), one strip showing full-tank reach, the fuel window and the equal split, then four figures. The copy states that tires, cautions and track position choose the point inside the window. Plans build from the saved analysis when the recording itself is gone. | `PlanningPage.razor`, `race_plan_decision.stop_windows` |
| Compare setups | Changed and unknown values lead; identical values sit behind **Show all values** with a count. Missing or unreadable on either side is unknown, never equal. File actions are links. | `SetupPage.razor` |
| Check the installation | Settings opens with one line: ready, or each warning with the action that clears it (repair, correct folder, check copy, check again, open logs). ChatGPT and Garage61 status sit in the Connections row and never count as problems for local analysis. | `SettingsPage.razor`, `CompanionState.Settings.cs` |

Navigation is a labeled top bar: Sessions, Plan, Setups, Live, and Settings. Running jobs, pending analysis and failures appear as chips; the jobs tray shows only while work runs or has failed, or after the driver opens it. The last investigation, view and selection are restored on restart when the cached investigation is still current; otherwise Sessions opens.

Removed: `NavRail.razor`, `HomePage.razor`, `RacePaceInvestigation.razor` and its embed in Technical data. Plan no longer repeats race priorities, corner evidence or in-race responses; those belong to the investigation.

## Backend

- `race_inspection.py`: review sections from a configuration catalog, run views (sampled green-lap exposure, observed trend, screened fit), section metrics, and pass inspection pairs with entry/inside/exit gaps measured from an explicit context origin.
- `review_sections.v1.json`: Bristol `365-single-pit-road` version `2026-09-23.1`, sections A–E. E (77.5–86%) is the driver-confirmed grouping of the former sections 5–7, recomputed over the whole merged interval. These are neutral review sections, **not** verified turn numbers.
- `driving_trials.py`: save (idempotent), list, load and evaluate tools. Tests live in `<archive>/trials/`, written atomically. Threshold is max(0.02 s, half the measured loss); at least three screened laps within ±2 green laps of the source exposure.
- Investigations from older schemas are rebuilt from the cached analysis without decoding the recording again.
- `race_plan_decision.stop_windows`: per stop k, [max(0, D − (S − k)R), min(D, kR)].
- MCP tools: 23. Contracts and affected fixtures regenerated.
- Cold analysis is faster without changing results: an exact-float fast path through the per-sample finite check, unit conversions resolved once per channel, and one bounds check per trace bucket. Bit-exact regression tests pin the conversion arithmetic.

## Bristol anchors reproduced in the application

Browser preview of the application on an isolated profile holding the archived v19 Bristol analysis (September 20, 3:09 PM, Toyota Supra Class B, 159 complete laps; lap 160 partial):

- Run 2 earlier to final green laps: laps 116–120 against 155–159, **+1.496 s/lap**, with 5 of 10 laps excluded for close traffic or not racing.
- Runs 1 and 2 at similar green exposure: laps 35, 36, 37 against 130, 131, 132, **+0.270 s/lap**; fuel 15.2 against 5.7 gal; track 78 °F both.
- Section D (62.5–77.0%): lap 36 against lap 132, entry **+0.121**, inside **+0.096**, exit **+0.217**, entry speed **−6.0 mph**.
- Section E (77.5–86.0%, grouped): group median **−0.008 s**; fastest and slowest passes are laps 35 and 36.
- Service at laps 102–103 appears as a gap in the run trend.

## Bristol "Fullpit" versus "Single Pit Road"

The recording's own session information reports track ID **365**, `TrackName` **"bristol fullpit"**, `TrackDisplayName` "Bristol Motor Speedway" and `TrackConfigName` **"Single Pit Road"**. "bristol fullpit" is iRacing's internal package name for track 365, not a second layout, and the two labels do not describe different configurations. Configuration identity is track ID plus configuration name: session grouping, track geometry and the review-section catalog (`365-single-pit-road`) already key on it, and every displayed track name uses the display name (since 0.17.1). The remaining place the internal name appears is iRacing's own recording file name. No string replacement was added. The production library currently holds no Bristol recording, so the original contradictory screen could not be reproduced; the investigation used the archived analysis.

## Settings and installer

- Fixed: an iRacing folder without a `setups` subfolder (nothing saved yet) reported a failed setup refresh. It is now an empty library; a wrong iRacing folder still fails visibly.
- Fixed: inventory diagnostics read "Local local setups".
- Installer (source review only, not rebuilt this round): disabled buttons and checkboxes paint their text in the normal foreground colour, and upgrades swap atomically, remove app-owned legacy installs and delete the `.previous` copy while leaving the Coach folder alone. Visual installer contrast and production Windows integration remain unverified.

## Measured performance

The development machine, Python 3.12, and a copied 203 MB New Hampshire race recording (August 1, 3:09 PM) in a fresh archive:

| Measure | Result |
|---|---|
| Cold backend analysis, before this round's change | 24.8 s (decode and analysis 23.5 s) |
| Cold backend analysis, after | **21.9 s** (decode and analysis 20.6 s); analysis output identical apart from timestamps and source-hash IDs |
| Where cold time goes (after, wall-clock stage timers) | lap trace payload 8.7 s (of which additional channels 5.8 s), lap summaries 2.8 s, IBT load 2.2 s, setup series and telemetry 3.6 s, track profile 1.7 s |
| Cached backend reopen of the same recording | 1.7 s (selection check 0.6 s, cache read 0.7 s, persist 0.5 s) |
| One backend process round trip (start, initialize, list tools) | 0.12 s |
| Click a never-analyzed race to investigation, through the app | **24.1 s** |
| Click a race analyzed earlier (app disk cache) | 0.31–0.36 s |
| Reopen the investigation already in memory | 0.05–0.06 s |
| Switch Race/Runs/Sections, select a comparison or section | 29–108 ms |
| Cursor move to next painted frame | 18 ms (one display frame) |

The interaction figures are browser-preview measurements (Blazor Server over a local socket), not native WebView2. Ordinary work (open, reopen, switch, inspect) meets "seconds". A cold analysis of a long race does not; it still runs in the background after a session ends. Decoding is 2.2 s of the 21.9 s, so avoiding re-decode is not the main opportunity; per-sample Python work in the trace payload and lap summaries is. The historical Bristol figure (18.858 s) cannot be re-measured because the Bristol recording is not on this machine.

## Verification

| Check | Result |
|---|---|
| `tools/verify-repository.ps1` | pass: **1,108/1,108** backend tests, 23 MCP tools, 21 contracts, 28 fixtures, MCP end to end |
| Application tests (Release) | **453/453**, zero skipped |
| `dotnet build iRacingCoach.sln -c Release` | pass, 0 warnings |
| JavaScript syntax (`node --check`) | 10 files pass |
| Development executable | `companion-app/artifacts/dev/v0.18.0-dev-20260923-222439/iRacing Coach.exe`, framework-dependent win-x64, backend junctioned to this worktree, Python and Coach Engine junctioned to the installed app |
| Native start of that executable | started on an isolated profile, window responsive after 15 s; only log entry is the expected "Garage61 disabled in the isolated profile". **No native screenshots or clicks**: desktop control was declined |

Browser-preview screenshots at 1280×720, all labeled `browser-…`, are kept outside the repository in `companion-app/artifacts/claude-round-20260923/screens-browser-preview/`: Sessions, Bristol Race (matched comparison), Runs, Section D, Section E grouped, Plan, test detail, setup comparison, Live waiting, Settings. **None is a native screenshot.**

## Not verified

- Native click-through at 1280×720 and 1920×1080, maximized and restored, at 100% and 150% scaling, with reduced motion.
- Improved and Worse verdicts on real recordings. They are covered only by backend tests; the real saved test (New Hampshire, section A) was checked against the August 2 offline test and correctly returned **Not comparable** (no screened laps at 25.1–35.1 green laps into a run). Most local races only yield traffic-affected finish comparisons, which are never saveable.
- Live cues, timing and focus during driving; authenticated ChatGPT and Garage61.
- Four-turn labels for Bristol. Sections A–E remain neutral review sections.
- Installer and portable packages: none were built; no release was requested.

## Next work, in order

1. **Native acceptance of the loop** on the development executable: Sessions → Bristol → matched comparison → Section D → Save test → later practice → Check, at 1280×720 and 1920×1080, maximized and restored, 100% and 150%.
2. **A real Improved/Worse check.** Drive three clean laps at the saved exposure window after a Bristol or New Hampshire test and confirm the verdict and before/after view.
3. **Cold analysis time.** Profile `_lap_trace_payload` and `_lap_summaries`; the per-bucket Python loops over 50+ additional channels dominate. Consider building additional-channel traces on demand rather than for every lap.
4. **Offer saveable tests more often.** When a race has only finish comparisons, find an earlier-run pair at similar exposure automatically rather than offering none.
5. **Verified turn labels** for Bristol and New Hampshire from a sourced track map, versioned in `review_sections.v1.json`.
6. **Live** priorities once active simulator testing is authorized.
