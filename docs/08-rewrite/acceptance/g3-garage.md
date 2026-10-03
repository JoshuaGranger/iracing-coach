# Gate G3: garage capture on the rig (W3 `--dry-run` build, task rig-W3)

Run 2026-10-03 on the sim PC (DESKTOP-SIM) with the real simulator. This file is written in two parts: the thresholds and method below were written before the first measurement and are not adjusted afterwards; the results section is appended after the run.

## Thresholds (written before any measurement; never adjusted afterwards)

Gate text (`workstreams.md`, G3): sim PC, solo Test Drive, Supra, copied data, collector `--dry-run`; cases P5 ran plus those it did not: leave without Apply, Revert, save under a new name, a second car, drive out of the stall. Pass: every committed visit carries the last-loaded file's name and its `.sto` hash; earlier loads appear only as name-unknown snapshots; committed values equal the next recording's `CarSetup` on all non-excluded keys; `Documents\iRacing\setups` hashes unchanged except iRacing's own `-Current-`.

Measured criteria. Every one must hold on every case that was run; a failed criterion is a G3 failure unless the failure is a defect fixed in `collector/` with a regression test and the affected case re-run.

| # | Criterion | Pass threshold |
|---|---|---|
| T1 | Snapshots complete | For each visit, the number of `garage.snapshot` lines in `dry-run.jsonl` equals the number of distinct `CarSetup.UpdateCount` values that an independent decoder of the raw SDK capture (`record-sdk`, run at the same time) sees while `IsInGarage = 1` or up to 5 s after it falls to 0 (collector-protocol section 8 edge rules), minus the count the visit entered with. 0 missing, 0 extra. |
| T2 | Name and hash | For every committed visit in which a file other than the one named at garage entry was loaded last: `setup_name` equals the file name I loaded last (from my action log), and `sto_sha256` equals the SHA-256 of that file computed independently (`Get-FileHash` on the sim before and after the session; both equal). 100 % of such visits. |
| T3 | Earlier loads stay unnamed | No snapshot carries a name, and no commit carries the name of any file loaded earlier in the same visit. 0 violations. |
| T4 | Values equal the next recording | For each visit followed by a recording, the commit's values equal the `CarSetup` of the next `.ibt` iRacing writes (read-only copy), key by key, on all keys except the derived and volatile ones named in P5: corner weights, ride heights, ARB and truck-arm preload, `LastHotPressure*`, `LastTempsOMI/IMO`, `TreadRemaining*`, `UpdateCount`. 0 differing keys. (Keys excluded are listed in the result; the P5 differences were 1 N, 1 mm, 0.2 Nm.) |
| T5 | Setups untouched | A SHA-256 of every file under `Documents\iRacing\setups` taken before iRacing starts and after it exits: only `-Current-` may differ. 0 other differences, 0 files added or removed. |
| T6 | Read-only collector | The collector writes only under its own `--state-dir` (`C:\Users\joshu\coach-rig\state`). `Documents\iRacing` contains no file newer than the start of the session other than those iRacing itself wrote (`telemetry\*.ibt`, `-Current-`, ini/log files). |
| T7 | Gate behaviour | A commit's hash is written only while the collector's gate allows file work: with the car parked in the pit stall (`IsOnTrack = 1`) the hash is withheld and appears within 10 s after the car is back at the session screen (`IsOnTrack = 0`). |
| T8 | Process health | The collector runs the whole session without a crash or restart; the exe is at most 3 MB (`workstreams.md` W3). |
| T9 | Replay agreement | `coach-collector --replay <raw capture> --dry-run` (virtual clock) on the real capture produces the same snapshots (visit-independent: update counts and values) and the same commit names as the live run. |

Expected outcomes defined by the contract (`collector-protocol.md` section 8), applied to the cases:

- A visit that loads a file different from the name at entry: one commit, `setup_name` = the file loaded last, hash resolved, earlier loads snapshots only.
- A visit that loads the file already named at entry, or only edits values: the SDK name does not change, so the contract commits values only, `setup_name: null`. This is not a pass of the gate's literal "every committed visit carries the last-loaded file's name"; it is reported as a limit of the contract and not counted for T2.
- A visit with no `UpdateCount` change and no name change: no commit.

Cases planned (each in the garage of a solo offline Test Drive, Toyota Supra at Las Vegas, the car and track of P3 and P5; a visit ends with Apply & Close or the garage's other exit):

1. Two different `.sto` files loaded and applied, one value edited without saving, Apply & Close (P5 repeated with the collector watching).
2. Edit one value and leave without Apply.
3. Load a file, change a value, Revert, Apply & Close.
4. Re-load the file named at entry (the contract's name-less case).
5. Drive out of the stall (only if the remote input tools can do it with the owner's control profile; otherwise the car stays parked and this is reported as not done).
6. A second car if a second Test Drive with another car is cheap.
7. Not run by design: save under a new name (a write into `Documents\iRacing\setups`, forbidden for this task).

Method: the release collector (`target/rig/x86_64-pc-windows-msvc/release/coach-collector.exe`, SHA-256 `874f7253f4bba0049a82048e251f8ad60b738f39d13e68898fab74bfb8040e61`, 1,578,496 bytes) is copied to `C:\Users\joshu\coach-rig\` and run as `coach-collector run --dry-run --no-ui --state-dir C:\Users\joshu\coach-rig\state`; `record-sdk` runs next to it on the same map (both read-only). The desktop is driven with `remote_observe` and `remote_gesture`, observing after every gesture, under one lease.


---

# Results (appended after the run; the thresholds above are unchanged)

## Verdict

**G3 passes after three collector fixes, with one recorded limit and four cases not run.** The live dry run on the rig (build `874f7253...`) failed the visit-integrity part of T1: it recorded all 20 `UpdateCount` changes but split the 5 garage visits into 19 (defect 1) and never logged the 3 values-only commits (defect 2). Both are fixed in `collector/` with regression tests; a third defect (the load placeholder taken as a file name) was found while replaying. The fixed build (`2309c2a4...`) was run against the bytes of the live session (`--replay` of the raw `record-sdk` capture), not against a second live session. Everything else held live: names, hashes, values and the setups folder. Not run: second car, save under a new name (forbidden here), drive out of the stall, the on-track gate for a named commit.

## Run

Sim PC DESKTOP-SIM, 2026-10-03 13:54 to 14:11 UTC. `remote_job processes` at the start showed no `iRacingSim64DX11`; one lease, label "rig-W3 G3 garage capture", released 14:10:40; `remote_status` afterwards shows `lease: null`.

| Item | Value |
|---|---|
| Build | `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc`: 1,578,496 bytes (limit 3 MB), SHA-256 `874f7253f4bba0049a82048e251f8ad60b738f39d13e68898fab74bfb8040e61`; copied with `remote_copy` (verified) to `C:\Users\joshu\coach-rig\` |
| Collector | `coach-collector.exe run --dry-run --no-ui --state-dir C:\Users\joshu\coach-rig\state`, started 13:54:35, stopped 14:10:57, no crash, empty log |
| Recorder | `coach-collector.exe record-sdk 1500 ...\g3-session.sdkfix` started 13:55:19: 52,502 ticks, 474,923,725 bytes, SHA-256 `24C9D515AFE13BF5301240AB6C15F411B79B1DCD8C7D1A3E9392F0768B1DBB5B`, stored at `C:\Users\joshu\source\coach-migration\fixtures\g3-rig-2026-10-03.sdkfix` |
| Session | iRacing UI Test Drive (remembered settings): NASCAR O'Reilly Toyota Supra, Las Vegas Motor Speedway Oval, offline, 2:15 PM, session-start setup `NOAPS_MaconiSetupShop LasVegas 26S4 R.sto` |
| Driving the garage | `remote_observe` before every gesture. The sim window is 7680x1440 across three monitors and the session desktop is 1280x800, so the window was moved with `remote_window setBounds` (x -2560, -3840 or -3000; y 0 or -640) to bring each part of the center screen into view, and put back at (-2560, 0) before the sim quit. Window position is not stored in `app.ini`. |

Actions (UTC, +/- 3 s; the SDK times in the raw capture are exact):

| UTC | Action | Visit |
|---|---|---|
| 13:55:29 | sim loads; tech inspection shows `IsInGarage` for 5 s | V1 |
| 13:56:35 | Garage; 13:57:27 load `NOAPS_MaconiSetupShop LasVegas 26S4 Q.sto` (A); 13:58:50 Apply; 13:59:22 load `claude LV R CAM.sto` (B); 13:59:35 Apply; 13:59:47 LF cold 22.0 to 22.5 psi, not saved; 14:00:05 Apply & Close | V2 |
| 14:01:50 | Test: car in the pit stall on track. Three attempts (14:02:20 to 14:02:50) to start and roll with the sim PC's keys (NUMPAD9 gear, NUMPAD8 throttle, NUMPAD2 brake, I ignition): the car did not move (max `Speed` in the whole capture 0.08 m/s, max `Throttle` 0.0). 14:03:04 Esc held, back to the session screen | |
| 14:03:20 | Garage; 14:03:30 RF cold 45.5 to 46.0 psi; 14:03:51 close button, Discard (leave without Apply) | V3 |
| 14:04:15 | Garage; load `claude LV Q CAM.sto` (C): iRacing asked about the modified setup, Yes opened a Save Car Setup dialog, closed with Back (nothing saved); 14:05:25 load C; 14:05:42 Apply; 14:05:53 RF 46.0; 14:06:15 Revert, Discard; 14:06:46 Apply & Close | V4 |
| 14:07:20 | Garage; load `claude LV R CAM.sto` again (the file already named at entry), No at the prompt, load; 14:08:10 Apply & Close | V5 |
| 14:08:35 | Test again (second recording), 14:09:05 Esc; 14:09:25 Quit, Yes, Quit; Save Replay dialog: Quit (nothing saved); iRacing UI closed | |

## Criteria

| # | Result | Numbers |
|---|---|---|
| T1 snapshots complete | Live: count PASS, visit grouping FAIL. Fixed build on the live bytes: PASS | Independent decoder of the raw capture (`IsInGarage` per tick, session-info `UpdateCount` per version, dips shorter than 1.5 s joined): 5 visits with counts [1], [2..8], [10..12], [13..18], [19..21] = 20 snapshots (count 9 was published at the session screen, 0.1 s before `IsInGarage` rose, so it belongs to no visit). Live dry run: 20 snapshot lines (0 missing, 0 extra) in 19 visit ids. Fixed `--replay --dry-run` of the raw capture: 20 snapshots in exactly those 5 visits. |
| T2 name and hash | PASS | V1 commit `NOAPS_MaconiSetupShop LasVegas 26S4 R.sto`, sha `6bb6a8db...a2af4d`; V2 commit `claude LV R CAM.sto`, sha `8683a3b4...e7042`. `Get-FileHash` of both files before and after the session gives the same two values. Both hashed at once (car off track, gate open). |
| T3 earlier loads unnamed | PASS | A (snapshots 3 and 4, LF 207 kPa) and C never appear as a name anywhere; V2's commit names B only; no snapshot has a name field. |
| T4 values equal the next recording | PASS | V2 commit values (`UpdateCount` 8) against `stockcars2 supra2019_vegas oval 2026-10-03 09-01-49.ibt` (`DriverSetupName claude LV R CAM.sto`, `IsModified 1`): 84 of 84 `CarSetup` keys identical, derived ones included. V5's last snapshot (`UpdateCount` 21, the values-only commit) against `...09-08-36.ibt` (`IsModified 0`): 84 of 84 identical. The excluded keys were needed on 0 of these comparisons; intermediate snapshots (e.g. upd 7, 10, 13) differ from the first recording only on them (1 N, 1 mm, 0.2 Nm), as in P5. |
| T5 setups untouched | PASS | 1,229 files under `Documents\iRacing\setups` hashed before and after: 1,229 and 1,229, one difference: `stockcars2 supra2019\-Current-` (`940D17FB...` to `E49B4181...`). The save dialog was opened once and closed; no `.sto` was created. |
| T6 read-only collector | PASS | Collector state only under `C:\Users\joshu\coach-rig\state` (`%LOCALAPPDATA%\iRacingCoach` was not created). Files in `Documents\iRacing` newer than the start are iRacing's own: `app.ini`, `camera.ini`, `core.ini`, `fueldata.ini`, `rendererDX11Monitor.ini`, `profiles\hud\Baseline.ini`, `logs\main-ui.log`, `aiseasons\*.json`, `paint\stockcars2 supra2019\car_*.tga`, `setups\...\-Current-` and two recordings (4,768,467 and 1,986,468 bytes, left in place). |
| T7 gate behaviour | NOT EXERCISED | Both named commits resolved while `IsOnTrack` was 0, so no hash was ever pending with the car on track. Covered by `the_sto_is_hashed_only_while_the_car_is_off_track` and `a_commit_waits_for_the_gate_...` only. A Test Drive "Test" puts the car in the pit stall with `IsOnTrack` 1 (as P5 found) and the garage is reachable only after Esc (`IsOnTrack` 0). |
| T8 process health | PASS | 16 min 22 s without a crash; exe 1,578,496 bytes (fixed build 1,581,568). |
| T9 replay agreement | PASS | Fixed build, `--replay <raw capture> --speed 0 --dry-run --no-ui --exit-at-end`: 20 snapshots, 5 commits (names `NOAPS...R.sto`, `claude LV R CAM.sto`, then three `null`), the same counts per visit as T1. The sanitized fixture gives the same 5 visits, 20 snapshots and 5 commits with the neutral names and the hashes of synthetic files (`collector/tests/rig_g3.rs`). |

## What the rig showed that P5 could not (comparison with the P5 watcher)

Overlap, confirmed: `UpdateCount` increments on every load, apply and edit (2 to 8 over V2; 3 to 10 in P5); `DriverSetupName` and `DriverSetupIsModified` stay on the session-start values until the exit and change once, for the last file loaded (here 50 ms after `IsInGarage` fell, 270 ms in P5); the unapplied edit is published about 6 s after the click (13:59:47 to 13:59:53); in the pit stall `IsOnTrack`, `PlayerCarInPitStall` and `OnPitRoad` are all 1 and `CarSetup.UpdateCount` stays 8 while the header counter advances 11 to 19 (P5: 13 to 16 with `CarSetup` unchanged).

New, from tick-level data (P5's watcher polled every 250 ms):

1. `IsInGarage` falls to 0 for 40 to 83 ms (14 dips, 1 to 2 ticks) just before every load and every Apply, and the new `UpdateCount` is published 0.2 to 1.3 s later with the car back in the garage. This split visits (defect 1).
2. The placeholder `DriverSetupName INVALID_LOADTYPE` (load type `invalid`) is published while the session loads; `IsInGarage` is 1 for about 5 s then (tech inspection) and the first real name and `UpdateCount` 1 follow 0.3 s after it falls. That start-of-session visit is a valid commit of the setup the car started with (named, hashed).
3. Leave without Apply (close button, Discard): the unapplied edit and then its removal are both published as `UpdateCount` changes (11 with RF 317 kPa, 12 with RF 314 kPa); the name does not change; V3 is a values-only commit.
4. Revert returns to the setup the garage had at entry, not to the last applied file: after loading C and editing, Revert restored B plus the edit (LF 155 kPa, snapshot 17); the name never changed; V4 is a values-only commit.
5. Loading the file that is already named (V5): the name cannot change, so the visit commits values only. The values are exactly the file's (`IsModified 0`, 84 of 84 keys equal to the second recording, whose `DriverSetupName` is `claude LV R CAM.sto`) but nothing in the stream says which file it was.

## Defects found in `collector/` (fixed; each regression test fails without its fix)

1. **Visits split at every Apply or load** (`garage.rs`). `IsInGarage` dips for 40 to 83 ms before each; the state machine treated each dip as an exit and the re-entry as a new visit, so the live run produced 19 visit ids for 5 visits, no snapshot could be tied to its commit, and 14 more visits than there were would have been committed as values-only visits of their own. Fix: a re-entry within `REENTRY_JOIN_MS` (1,500 ms) of the exit continues the visit (a name change within the window still commits at once). Tests: `garage::tests::a_dip_of_in_garage_while_a_setup_is_applied_does_not_split_the_visit`, `garage::tests::a_dip_that_lasts_longer_than_the_join_window_is_an_exit_and_a_new_visit`, `tests/garage.rs::a_dip_in_in_garage_before_every_apply_keeps_one_visit_with_one_named_commit`, and `tests/rig_g3.rs` on the real bytes (with the constant at 0 it sees 19 visits). Two existing re-entry tests had their gaps moved from 0.8 s to 1.8 s because 0.8 s is now a dip. This changes the edge rule in `collector-protocol.md` section 8 ("re-entering the garage inside the 5 s window closes the open visit first") for gaps up to 1.5 s: **the contract text needs a one-line amendment (owner: contracts)**.
2. **Values-only commits never reached the dry-run log** (`garage_io.rs`): only commits that needed a hash were written, so a dry run showed snapshots with no visit around them and G3 could not see 3 of the 5 commits. Test: `garage_io::tests::a_commit_without_a_name_is_written_to_the_dry_log_with_its_values`.
3. **`INVALID_LOADTYPE` accepted as a setup name** (`sdk.rs`): replaying the capture committed `INVALID_LOADTYPE` when the first session-info version arrived after the garage entry. It is now not a name. Tests: `tests/garage.rs::the_load_placeholder_is_not_a_setup_name` and the first visit of `tests/rig_g3.rs`.

Checks on the final tree: `cargo test -p coach-collector` (90 unit tests, e2e 2, garage 7, link 11, replay_pipeline 8 and 1 ignored, rig_g3 2, uploader 15, win_render 3; 0 failures), `cargo clippy -p coach-collector --all-targets -- -D warnings` clean, `cargo fmt -p coach-collector -- --check` clean, release exe 1,581,568 bytes.

## Limits and cases not run

- **Same-file reload (V5) and revert (V4) commit values only**, as `collector-protocol.md` section 8 specifies, so the gate sentence "every committed visit carries the last-loaded file's name" is not true for them. The SDK gives no name change to detect; this is a limit of the passive design (D9), not a bug. A candidate for the owner: attach the exit name to a values-only commit when `DriverSetupIsModified` is 0 and the visit changed values, accepting that it can name the previous file after a Revert.
- **Drive out of the stall: not done.** Keys sent with `remote_gesture` (Numpad9/8/2, I) did not reach the sim as driving input (Esc and clicks did). The car never moved, so "garage, pit stall and on track" is garage plus pit stall on track.
- **Second car: not run** (needs quitting and a new Test Drive plus a second recording; not cheap here).
- **Save under a new name: skipped by instruction** (a write into `Documents\iRacing\setups`).
- **Fixed build not run live.** The three fixes were verified by replaying the live session's own bytes and by the tests, not by a second sim session.
- `tools/rig/g3-garage.ps1` (named in `workstreams.md`) does not exist; the comparison used throwaway scripts kept with the raw files in `coach-migration/rig-W3-2026-10-03/` (`g3_compare.py` for T4, `t1.py` for T1, `fixdec.py` raw-capture decoder, `timeline.txt`).

## Left as found

`C:\Users\joshu\coach-rig\` deleted (collector, state, 475 MB capture, copies); no `coach-collector`, `iRacingSim64DX11` or `iRacingUI` process running; iRacing UI closed as it was at the start; Discord foreground as before; lease released. Raw files are on the dev PC only, under `C:\Users\joshu\source\coach-migration\`: `fixtures\g3-rig-2026-10-03.sdkfix` and `rig-W3-2026-10-03\` (live `dry-run.jsonl`, copies of the two recordings, before/after setups hashes, replay output, scripts). The sanitized fixture is `test-data/sdk/g3-garage-pit-2026-10-03.sdkfix` (2,186,962 bytes) with its sanitizer and README.
