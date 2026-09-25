# iRacing Coach 0.18.0 development round 2: native findings on the sim, test days, setups from recordings

Date: September 24–25, 2026. Branch `main`, from the round-1 record `9077917` through `62b71bf` (16 first-parent commits, 93 in all) and the commit that adds this record. Application source version stays **0.18.0**. Analysis profile moves from **post-race-foundations-v19** to **v21** (v20 for the offline-flag rule, v21 for one early-versus-late figure in Details). The race investigation stays **schema 2**; its projection moves from `sections-trials-v1` to **`sections-trials-v6`**, and cached analyses rebuild each new projection without decoding the recording again. This follows the [round-1 snapshot](implementation-snapshot-0.18.0-development-round-1.md).

Claude drove this round with native desktop access to Joshua's sim PC (DESKTOP-SIM) through the Sabine remote tools, in a hidden RDP session at 1280×800. Development builds ran against **copies** of Joshua's real data (the QA profile), not his live archive. The builds were self-contained win-x64 publishes made on the main PC and copied to the sim, which has no .NET SDK. Findings below that cite the sim are session evidence from that work; the fix evidence is what each commit records (tests, browser preview, or a reproduction on the sim).

## What the driver can now do

| Workflow | Application behavior | Where |
|---|---|---|
| Review a test day | Consecutive practice and test recordings of one car at one layout form a **test day**. Opening one opens its investigation with a **Test day** tab: an overlay of every run by lap of run, one row per run (setup change since the previous run, early and late windows, fall-off, verdict, a compact tire cell) and **one next-change card** with its success rule. Missing values read `Not measured`. | `TestDayView.razor`, `CompanionState.TestDay.cs`, `TestDayMapper.cs`, `test_day.py` |
| Know whether a saved test can be checked | Each saveable test says how long a run it needs ("Needs a run of about 78 green laps without a pit stop; a 20-lap test run cannot check it"). When a late-run test needs a longer run than a test day, an early-run test of the same section is offered instead. A later recording that never reached the window reads "Not reachable in this recording (longest clean run 20 laps; needs ~78)". | `TrialAction.razor`, `TrialDetail.razor`, `race_investigation.py`, `driving_trials.py` |
| Compare setups | Setup files show garage values from the recordings that loaded them unchanged, with the recording and date named. Compare is keyed on the iRacing car path, done by the backend, and lists changed settings apart from computed garage readouts, which are never counted. Run readings (last-run temperatures, tread, hot pressures) sit in their own group. Search matches every word in any order. | `SetupPage.razor`, `CompanionState.Setups.cs`, `SetupComparability.cs`, `setup_recordings.py`, `setup_catalog.py` |
| Find the session | Older saved analyses with a bare `Infinity` stay in the library; a report that is still unreadable is counted once in a quiet notice. A test day's recordings group together as `Recording k`. A standalone practice opens the Race/Runs/Sections investigation; only qualifying keeps its one-screen review. | `SessionsPage.razor`, `NonFiniteJson.cs`, `RuntimeMapper.cs`, `ArchivedRaceCatalog.cs` |
| Trust a pace comparison | A comparison whose laps are all excluded, or whose windows span or are separated by a recorded caution, has **no pace result**. It reads "No pace result" in neutral styling, gives the backend's reason, offers no driving correction and is left out of the coach packet. The investigation opens on a usable comparison, preferring one that offers a saveable test. A lap-time gap reads in seconds; `s/lap` is kept for slopes. | `race_investigation.py`, `RaceInvestigationView.razor`, `RaceInvestigationMapper.cs`, `RaceCoachPacketBuilder.cs` |
| Inspect a section | IRP has fixed, named review sections A–G. A section's approach is its own length clamped to 3–8% of a lap, never past the previous section's start. | `review_sections.v1.json`, `race_inspection.py` |
| Read Details | Tire condition reads as tread remaining throughout. Bands show their own carcass/surface temperatures. The racecraft badge states the position change it rests on (`+4 positions`). Offline tests and practice leave out race answers (pit strategy, race fuel, net positions). A run's early-versus-late pace is the same figure Runs shows. | `RaceTechnicalData.razor`, `workflow.py` |
| Plan the next race | Plan builds from the reference recording as soon as it opens; an edit waits for **Build plan**. | `PlanningPage.razor`, `CompanionState.PlanDefaults.cs` |
| See what happened | Notifications take their own row below the top bar and never cover page controls. Information clears after 5 s (held while hovered); warnings and errors stay until dismissed and name the logged reason. A job's failure is keyed on its recording. | `AppToast.razor`, `CompanionState.Toasts.cs`, `ToastDismissTimer.cs`, `CompanionShell.razor` |
| Change settings | The save bar appears only for a real edit of anything persisted, including popout placement. | `SettingsPage.razor`, `CompanionState.SettingsEdits.cs` |
| Open the app | The main window opens inside a current monitor's work area and remembers its normal (not maximized) frame. | `MainWindowPlacement.cs`, `DisplayWorkAreas.cs`, `MainWindow.xaml.cs` |

## Native acceptance on DESKTOP-SIM

Native click-through at 1280×800 on copies of real data found these defects. Each row names the fix and the evidence the fixing commit records.

| What native acceptance showed | Fix | Evidence for the fix |
|---|---|---|
| The main window (fixed 1440×900, centred) opened at −80,−74 on the 1280×800 screen with its title bar and navigation above the screen, so it could not be moved or maximized with the mouse. | Placement is resolved against current monitor work areas and DPI, re-checked after first render and on restore from the tray; the remembered frame comes from `GetWindowPlacement`'s normal position. | `0d46b10`, `2b0bd2a`; pure-function placement tests |
| 77 of 158 older saved analyses contain a bare `"total_time_s": Infinity`. They were rejected as invalid JSON and vanished from the library and setup catalogs, each failure showed the generic "Something went wrong" toast, and every launch spent about 9 s rereading them. | Catalog, metadata, cached-reopen, tuning and missing-recording reads treat bare non-finite tokens as null without rewriting the report. Unreadable reports are remembered across launches in app-owned state, logged once, counted in one dismissible notice, and retried once after an upgrade. | `59a724e`, `5479225`, `086474f`, `f7ae785`; regression fixture minimised from a real report |
| With `LongPathsEnabled=0`, raw archives at 258–275 characters were reported absent, copied again, and the rename failed with WinError 3, stopping the analysis. | Raw-archive I/O uses extended-length paths; a destination-side copy failure is recorded and retried rather than fatal (`TD-008`). | `5dd6550`; on the sim a real 81.7 MB Bristol race at a 269-character archive path failed in 0.2 s before and analyzed in 6.8 s after, without rewriting the copy |
| Every Offline Testing lap was flagged caution: iRacing holds the one-lap-to-green bit for the whole session (almost every sample reads `0x10040200`). The IRP 9/20 16:16 test (23.44 s and 23.50 s laps) had no green laps, screened laps or sections. | Outside a Race session the bit counts only beside a real caution bit. Race and unknown sessions keep the full mask. Profile v20. | `dcb7bd6`; IRP 9/23 and Kansas 9/21 race analyses unchanged apart from `analysis_id` |
| Pace headlines claimed changes across cautions: Kansas 9/21 read "Laps 30-42 were 3.121 s/lap slower" across cautions 31–37 and 39–41 with all six laps excluded; Iowa 8/03 run 3 compared laps 23–24 with 32–33 across cautions 25–30. The app dropped the unusable flag and still showed a green "- s/lap". | Such comparisons carry no `delta_s`, state why, lose their hypotheses and driving corrections, and are withheld from the coach. | `6eb1585`, `cc12472`, `12890fd`; projections v2 and v3 |
| A standalone practice opened the old page. | Practice and offline testing open the investigation; a practice whose analysis holds two or more runs opens with a Test day of its runs. | `8520259` |
| Setups: every `.sto` said "0 values", Compare offered only "No comparison", and the IRP setup used by the 9/23 race and practice said no recording used it. | Values and links come from recordings (`SU-010`); Compare is keyed on car path (`SU-011`); run readings are listed apart; Tune starts from the recording the values came from. | `9b71a23`, `dbc1b98`, `25085a2`, `097915d`, `395631e`, `4987b1d`; on the sim's disposable profile the IRP setup links to the 9/23 race and practice with 71 garage values |
| Toasts never cleared ("Something went wrong, but the app is still running." stayed 47 minutes without saying what failed) and covered page controls and navigation. Follow-ups found the first fix's docked toast shrinking navigation in a crowded top bar (1024×720, 900×720, and 1280×800 with a failed-job chip), and its dismiss button taking clicks meant for page-header actions at 1280, 1024 and 900 px. | Notifications have their own grid row and an explicit tone at every call site; information clears itself, warnings and errors stay and name their reason; job failures key on the recording. | `6df8d9d`, `097f75c`, `25d25a6`, `d88927e`; preview `elementFromPoint` checks at 1280/1024/900 px |
| Plan showed nothing until **Build plan** was clicked, although every input came from the recording. | Plan builds once per complete context when it opens. | `52462f1`; preview: the Bristol plan appears 2.3 s after opening Plan with no click |
| Settings always showed the save bar, offering Save beside "Restored 2,606 Coach items." with nothing changed. A follow-up found that Restore defaults could be lost on the next launch. | The bar compares the whole persisted snapshot and appears only while it differs; quiet background saves no longer hide pending edits; trace-layout repair runs before the baseline. | `6565ae1`, `0965f45`, `84744d9` |
| Returning from a race left Sessions with a 10 px gutter instead of 28 px. | The edge-to-edge frame applies only while a race workspace is open. | `21528a5` |
| IRP 9/23 opened on "Run 1: earlier to final green laps" (laps 106–110, mixed, nothing to save), one row below "clean early versus late" (105–109, the same +0.730 s/lap, two saveable tests). | The default ranks usable, then saveable, then latest compared lap, then fully screened. | `af9897b`; projection v4 |
| IRP's detected section F (64.5–72.5%) timed its approach from 30.5%: "Entry" covered 34% of the lap and the section filled 19% of every chart. | The lead-in is the section's length clamped to 3–8% of a lap; F now starts at 56.5% and fills half the chart. | `af9897b` |
| Details contradicted itself: Kansas 9/21 read "Most wear: RF middle 9.8%" beside "90.2% remaining", and a bare "Gain" badge sat above "No clean run comparison". IRP 9/24 run D (offline) read "+0.18 s" early-to-late in Details beside "+0.211 s/lap" on Runs, and offline Details showed "Pit strategy · One stop" beside "No stop · Stops recorded 0". | Tread remaining throughout; the badge states the position change; one early-versus-late figure from the investigation; race-only answers left out of offline and practice Details. | `9c22c49`, `6725046`; profile v21, projection v6 |
| Kansas 9/21 run 2, which started on four tires fitted at the lap-17 stop, read "Tires and service: Unresolved". | The explanation cites the confirmed change that started each run and its own end reading, never a stop after the windows. | `af9897b` |
| Reports were duplicated: one full copy per open. The QA profile held 160 report directories (6.95 GB), 90 of them (5.35 GB) repeats, including 19 extra copies of the 9/21 Bristol race (0.80 GB). | One report per recording, keyed by `analysis_id` and replaced in place. A save waits up to 5 s for a reader holding the previous `analysis.json`, then publishes a complete sibling copy the index names. Duplicate and superseded copies are measured read-only; nothing is deleted. | `c75278e`, `48f58de`, `53c5e5c`, `992cf3b`, `a4805bc`, `c6f9ff2`; see Measured performance |

Other fixes from the same work: the Sections test strip clipped its success rule at 1280 px (`255cd7f`); the isolated profile logged a Garage61 error on every inventory refresh and called itself "Not connected" (`e051689`, `8e7d710`); one invalid UTF-8 byte in a report aborted the backup check (`bcdbe89`); tread tiles were coloured from wear while the scale read remaining (`7daf3d6`); archived offline recordings were keyed by subsession 0, so one stint's analysis was stamped onto every other (`9e1fec6`); a test-day review and a parked background retry could wait on each other indefinitely (`034f60b`).

## Test day

The racing question is "did my last setup change make the car faster at the same laps into a run, and what do I change next?".

Backend:

- Discovery tags consecutive practice/test recordings of one exact car path at one track configuration, each starting no more than two hours after the previous one ended, with optional `test_day_id`, `test_day_run` and `test_day_run_count`. Offline stints qualify, and so does a hosted or official practice whose event has no recorded race or qualifying. Races and qualifying are never tagged, and no recordings are merged.
- `review_test_day` (contract `test-day-review-v1`) reads 1–12 archived analyses and returns one row per run (the laps between pit visits), keeping the latest 24 runs. Windows are laps 2–5 and the last five laps up to the shortest run of at least eight laps, starting no earlier than lap 6. Fall-off is a least-squares fit over laps 2..N with its residual scatter.
- The verdict is Faster, Slower, Within scatter or Not comparable, against max(0.03 s, 2 × combined median standard error), on the laps of the run clean in both runs. Nothing is "unchanged". Comparability requires the exact car path and configuration, an open setup, track temperature and starting fuel within the driving-trial limits, and the same compound when recorded.
- The setup change counts garage adjustments only. Computed readouts (corner, cross and nose weight, ride heights, truck-arm preload) are listed apart and never counted; the garage fingerprint leaves them and tire readings out, while the original `setup_fingerprint` stays byte-identical.
- Tires at the stop show the pit-stall telemetry and the garage sheet separately, oriented inside/middle/outside. The sheet's timing is marked unverified, because in the checked Iowa and New Hampshire recordings it never matched the stint's own final stall reading.
- Exactly one next-change card: revert a slower change, return to an earlier best late pace, keep one faster change (quoting the O'Reilly ruleset step only when the ruleset names that direction), isolate several changes, or repeat. No card is derived from tire temperatures.
- For Offline Testing runs the review ignores only the caution and restart reasons and says so, and also excludes lap 1 of each run and laps where the car stopped on track.

Application: the Test day tab is the first view of a day's investigation; Runs, Sections, Traces, Replay and Details stay per recording. One job per day analyzes any recording without an archived analysis and then reviews the day; returning to a day waits for its running job. Background maintenance prepares tagged recordings and yields to live telemetry, interactive analysis and a running review. The mapper reads the contract strictly, refuses other versions, keeps null as `Not measured` and preserves unknown optional fields. Selecting a row rings exactly the compared laps and lists the verdict summary, cautions and setup change as text; lap dots are not tab stops (96 Tab presses to the first row became 9).

### Real test-day data and native acceptance

An automated solo Offline Testing session of the O'Reilly Toyota Supra (`stockcars2 supra2019`) at IRP produced **13 recordings on September 24–25** with five setups: baseline, v2 LF camber, v3 steering 10:1, v4 wedge and v5 RR track bar. The runs were driven by a separate tool outside this repository (`iracing-testdriver`); it is only the source of these recordings and is not part of the application.

Native acceptance on that data:

- Test day tab: tire temperatures and inside/outside orientation matched ground truth, and laps 2–5 medians matched within 2 ms.
- Setups page (fixed in `a67a3ee`, `6f296a0`): every file-selected Offline Testing analysis had superseded the other offline rows in the history index (ten real recordings kept one row), which unlinked their setup files. The page listed one setup five times, found nothing for "supra IRP", badged Offline Testing setups "Recorded race" and counted readouts as changes. v3 against baseline now reads "1 changed · 2 garage readouts moved" instead of "3 changed". A personal file name ("claude IRP test v2 LFcamber") no longer becomes a track.

### Test day real-data fixes (in progress at time of writing)

The same acceptance found further defects in the Test day review. A separate branch fixing them was being finished when this record was written; it is **not** part of `62b71bf`, and this subsection will be updated when it merges. The areas:

- the verdict's noise threshold;
- late windows over the common run length;
- an offline tow not excluded;
- hot versus cold tire pressures;
- readout headlines;
- chart scaling;
- layout.

## Setups

- `SU-010`: a `.sto` is encrypted, so a setup file shows garage values only from an analyzed recording whose session info names the same file for the same car folder, loaded from the user's library and unchanged. Values name the recording and date, exclude the run's tire readings, and are never presented as decoded from the file. A name that exists in more than one folder for the car supplies no values. On the sim, `NOAPS_MaconiSetupShop IRP 26S4 R` links to the 9/23 race and practice with 71 garage values; the same file name under `camaro2019` and `mustang2019` does not.
- `SU-011`: same car means the same iRacing car path, never a display name. A linked `camaro2019` `.sto` ("Camaro 2019") had never matched its own recording ("NASCAR Xfinity Chevrolet Camaro"). Each same-car setup not offered is named with its reason.
- `SU-012`: `compare_iracing_setups` compares displayed values. Same-unit values are equal only when the numbers are (`-0 mm` equals `0 mm`; a one-newton corner-weight difference is still shown); a value in another unit is equal within half the coarser display step. Readouts use the same set as the Test day.
- `SU-013`: one Compare entry per set of garage adjustments, grouped same track then other tracks, newest first; token search across name, file, car, car path, track, season and session; the library relinks once background analysis of new recordings finishes.

## Backend

- Analysis profile v20 (`dcb7bd6`, offline one-lap-to-green) and v21 (`6725046`, Details early-versus-late from the investigation). Projection `sections-trials-v2` to `v6` (`6eb1585`, `cc12472`, `af9897b`, `8f2fcd4`, `6725046`); each rebuilds from the cached analysis without decoding.
- IRP review sections `232-irp/2026-09-24.1`: seven contiguous sections A–G (Frontstretch from the line, Turns 1-2 entry and exit, Backstretch, Turns 3-4 entry and exit, Frontstretch to the line) derived from the racing-line geometry of 62 clean laps. Race and practice now report identical sections. They are review sections, not official turn boundaries. Derivation: [IRP review sections](../05-quality/irp-review-sections-2026-09-24.md). A cached investigation also rebuilds when its section version differs from the catalog's.
- Saved tests store reachability: the run length without a pit stop their window needs, and whether a test-day run of up to 20 laps reaches it. IRP 9/23's race test needed three clear laps at 77.5–81.5 green laps into a run. A run whose clean early-versus-late window needs a longer run adds a `Run N: first 20 green laps` comparison through the unchanged screens.
- Raw archive and report storage: extended-length paths for raw-archive I/O; reports keyed by `analysis_id`; `coach_cli.py report-storage` measures duplicate and superseded copies read-only and creates nothing (the WAL-mode index is read with `immutable=1` or from a private copy). The history index no longer treats `SubSessionID` 0 as one session.
- Cold analysis a column at a time, typed-memoryview IBT decoding, and automatic garbage collection paused for one workflow call, with byte-identical output (`tools/analysis_parity.py` on four recordings; `test_analysis_columns.py` against verbatim pre-change copies). Details are in the round-1 snapshot's "Cold analysis follow-up".
- MCP tools: **25** (`review_test_day`, `compare_iracing_setups` added). Contracts: **22** (`test-day-review-v1` added). Fixtures: **29** (`test-day-review.json` built through the backend from three synthetic Offline Testing stints). The analyzer bundle digest is re-recorded at each analyzer change.

## Measured performance

| Measure | Result | Where |
|---|---|---|
| Cold backend analysis, 203 MB New Hampshire race | 23.7 s → **10.3 s** (−56%), output byte-identical | main PC, median of three quiet interleaved runs |
| Cold backend analysis, 146 MB Iowa race | 15.3 s → **7.1 s** (−54%) | main PC, same method |
| Click an uncached IRP practice to its view, through the app | about **10–13 s**, on an older build | DESKTOP-SIM, native |
| Long-path Bristol race (81.7 MB, 269-character archive path) | previous backend failed in 0.2 s; fixed backend analyzed it in 6.8 s | DESKTOP-SIM |
| Report save while a .NET reader holds the previous 40 MB report | holds of 0.1, 0.4 and 2 s: replaced in place in 0.25, 0.53 and 2.31 s; a 30 s hold: complete sibling copy after 5.27 s, one copy after the next save | probe; the previous backend failed the 2 s and 30 s holds |
| Read-only report-storage measure, main-PC archive | 189 directories (2.60 GB): 160 superseded (2.40 GB) and 14 duplicate (0.10 GB) reclaimable; 2.0 s, nothing created or changed | main PC |
| Superseded report copies, sim QA profile | 2.14 GB | DESKTOP-SIM |
| Plan appears after opening Plan (Bristol) | 2.3 s, no click | browser preview |

## Verification

| Check | Result |
|---|---|
| `tools/verify-repository.ps1` | pass at `62b71bf` and on this record: **1,276/1,276** backend tests, 25 MCP tools, 22 contracts, 29 fixtures, MCP end to end |
| Application tests (Release) | **581/581** at `62b71bf` |
| `dotnet build iRacingCoach.sln -c Release` | pass, 0 warnings, at `62b71bf` |
| Native acceptance | DESKTOP-SIM, hidden RDP session, 1280×800, copies of real data; findings and fixes above |
| Browser preview | commit-recorded checks at 1280×720, 1280×800, 1024×720, 900×720 and 900×640 (notifications, Settings save bar, Plan, Sections strip, Details tire cards) |

## Not verified

- Installer and portable packages: none were built this round.
- Authenticated ChatGPT and Garage61.
- The Live page with a live session: not exercised.
- Multi-monitor and 150% scaling natively, and 1920×1080. `MainWindowPlacementTests` cover 150% scaling and a negative-origin secondary monitor through the placement function only.
- A human-driven test day. The Test day acceptance used bot-driven recordings; nothing here shows how the review reads a human's runs.
- The Test day real-data fixes: not merged at the time of writing.
- A real Improved or Worse verdict for a saved test: none is recorded in this round's commits.
- Long paths outside the raw archive: the history database, report listing and application readers still assume paths under 260 characters (`GAP-020`).
- IRP sections A–G come from one car and one driver's racing line; they are not verified turn boundaries.

## Round-1 next work, where it stands

| Round-1 item | Status after round 2 |
|---|---|
| 1. Native acceptance of the loop | Native acceptance ran on DESKTOP-SIM at 1280×800 and found the defects above. 1920×1080, 150% scaling and multi-monitor remain unverified natively. |
| 2. A real Improved/Worse check | Not recorded. The IRP race test needed a run of about 78 green laps, which a 20-lap test run cannot reach; reachability and the early-run test now say so. |
| 3. Cold analysis time | Done: 23.7 s → 10.3 s on the 203 MB race, byte-identical. |
| 4. Offer saveable tests more often | Partly: races open on a saveable comparison when one exists, and a run whose late window a test day cannot reach adds a first-20-green-laps comparison. |
| 5. Verified turn labels | Not done for Bristol or New Hampshire. IRP gained named, geometry-derived review sections, not verified turns. |
| 6. Live | Not exercised. |

## Next work, in order

1. **Merge the Test day real-data fixes** and repeat native acceptance of the Test day tab on the 13 IRP recordings at 1280×800: verdicts against the noise threshold, late windows, tow exclusion, hot and cold pressures, readouts, chart and layout.
2. **A human-driven test day.** Joshua drives two or three runs of about 20 laps with one setup change between them; confirm the verdict, the next-change card and the tire cards against what he felt and what the stall showed.
3. **A real saved-test check.** Save an early-run test that a 20-lap run reaches, drive it on a later test day, and confirm the Improved or Worse verdict and the before/after section view.
4. **Build a self-contained win-x64 installer and portable package with checksums** from `main`, then run the native states this round did not cover: 1920×1080, 150% scaling and multi-monitor. Re-measure click-to-view of an uncached practice on that build.
5. **Reclaim duplicate and superseded report copies** through a Settings action with an explicit user choice (`GAP-025`, `PORT-007`) that re-checks references and drops app caches naming a removed copy.
6. **Long paths beyond the raw archive:** history database, report listing and application readers (`GAP-020`).
7. **Live** priorities once active simulator testing is authorized, then authenticated ChatGPT and Garage61.
