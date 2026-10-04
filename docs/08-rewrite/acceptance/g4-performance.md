# Gate G4: rig performance with the installed collector (task rig-W9)

Run 2026-10-03 on the sim PC (DESKTOP-SIM) with the real simulator and the collector installed as the owner's pipeline (`collector-install.md`). This file has two parts: the thresholds and method below were written before the first measurement and are not adjusted afterwards; the results are appended after the run.

## Thresholds (written before any measurement; never adjusted afterwards)

Gate text (`workstreams.md`, G4): sim PC, solo session, the vJoy bot driving IRP; overlay visible, streaming at 60 Hz to two browsers; alternating 100 s windows, baseline and collector, four each, sim counters at 1 Hz (P3 recorder method). Pass: collector windows' `FrameRate` median within 1 % of the adjacent baselines' mean; p1 `FrameRate`, `CpuUsageFG` and `GpuUsage` medians inside the baselines' min-max range; collector CPU <= 1.5 % of one core mean and RSS <= 32 MB; collector disk writes <= 64 KB across on-track windows; 0 upload bytes and 0 hashing or scan reads inside on-track windows (each window starts at least 5 s after `IsOnTrack` became 1); the abort itself stops within 100 ms; stream >= 59 frames/s median at the server. After the run: the session's recording is analysed and visible within 2 min of iRacing finalizing it.

**Driving: a parked car, not the bot.** The vJoy bot is not runnable within this task's rules: it needs the agent control profile selected in `Documents\iRacing\app.ini` (`[ControlProfiles] Global=`) and the vJoy-bound control files swapped in and restored around the session (memory notes of 2026-09-25), which are writes to iRacing's own files, forbidden here; the bot code has also moved to the separate iRacing-utility repository (owner ruling 2026-10-03). So every window below is measured with **the car parked in the pit stall on track** (`IsOnTrack = 1`, speed 0), as in P3. This is labelled in every result table. Engine branches that react to motion are not exercised on the rig by this run.

Window plan: one solo offline Test Drive (the iRacing UI's remembered Test Drive settings; P3 and G3 used the Toyota Supra at Las Vegas), car put in the pit stall with **Test**. Nine 100 s windows in the order B1 C1 B2 C2 B3 C3 B4 C4 B5 (B = collector not running, C = installed collector running with its overlay and streaming), so every collector window has a baseline on each side. "Adjacent baselines" of Ck are Bk and Bk+1. Before a C window the collector is started (`coach-collector.exe run`, the Run value's command line) and 20 s pass before the window opens; before a B window it is asked to exit (its own quit event) and 10 s pass after it is gone. The recorder runs through every window. Two Chromium viewers on the dev PC hold the Live page open at desktop size (60 Hz) for the whole run.

| # | Criterion | Pass threshold |
|---|---|---|
| G4-1 | Frame rate | For each Ck: median `FrameRate` of Ck within +-1 % of the mean of the medians of Bk and Bk+1. 4 of 4. |
| G4-2 | Tail and load | For each of p1 `FrameRate` (1st percentile of the 1 Hz samples in a window), median `CpuUsageFG` and median `GpuUsage`: the median of the four C-window values lies inside [min, max] of the five B-window values. 3 of 3. (Each C window's own value against the range is reported too, for information.) |
| G4-3 | Collector CPU | Collector process CPU time across the four C windows divided by their wall time <= 1.5 % of one core (mean). |
| G4-4 | Collector RSS | Largest collector working set sampled in any C window <= 32 MB (33,554,432 bytes). |
| G4-5 | Disk writes on track | Sum over the four C windows of the collector's `WriteTransferCount` increase (Win32 `GetProcessIoCounters`) <= 65,536 bytes. |
| G4-6 | No file work on track | Sum over the four C windows of the collector's `ReadTransferCount` increase = 0 bytes (no hash, no chunk read), and the server's spool bytes and the offset of any upload interrupted at the transition (`HEAD /api/v1/uploads/{sha256}`) unchanged from the start of C1 to the end of C4 = 0 upload bytes. Every window has `IsOnTrack = 1` in all its samples and starts >= 5 s after `IsOnTrack` became 1. |
| G4-7 | Abort at the transition | With the collector uploading (its read counter rising) when the car goes on track, the time from the first sample with `IsOnTrack = 1` (fast sampler, 1 ms loop, collector I/O counters and the SDK flag read in the same pass) to the last increase of the collector's `ReadTransferCount` <= 100 ms; and the upload's server offset stops advancing (it may finish the 4 MiB request already sent, which is network, not file work: reported separately). |
| G4-8 | Stream rate | In each C window, the median per-second count of live (non-backlog) frames received by each of the two viewers from the server's `/ws/live` relay >= 59. 8 of 8 (4 windows x 2 viewers). |
| G4-9 | Time to visible | From iRacing finalizing the session's recording (the `.ibt`'s last-write time on the sim, after the sim exits; sim and dev-PC clocks compared in the same run) to the session appearing in `GET /api/v1/drivers/{id}/sessions` with its analysis current (`analysis_state` `ready` and `GET /api/v1/sessions/{id}/analysis` answering 200) <= 120 s. A parked-car recording may have no laps; if the worker cannot analyse it, that is reported as such and G4-9 is not passed by assumption. |
| G4-10 | Overlay visible | A screenshot of the sim during a C window shows the collector overlay over the running sim. |

The gate passes when G4-1 to G4-10 all hold. A failure is recorded as a failure with its numbers; it is not re-measured into a pass.

Instruments (written before the run, kept in the repository):

- `tools/rig/g4-perf.ps1` (new): Windows PowerShell 5.1 with an inline C# sampler; reads the iRacing SDK map read-only (`Local\IRSDKMemMapFileName`: `FrameRate`, `CpuUsageFG`, `CpuUsageBG`, `GpuUsage`, `IsOnTrack`, `Speed`, `SessionTick`) and the collector process's CPU time, working set, private bytes and I/O counters. `-Mode windows` runs the window plan above at 1 Hz (the P3 recorder method: one row per second, the same recorder in every window, CSV); `-Mode fast` samples in a 1 ms loop for the abort check. The P3 recorder (Rust, scratch) was not kept, so this one is rebuilt from the P3 report with the same variables and rate.
- `tools/rig/g4_viewers.mjs` (new): two Playwright Chromium viewers (1280x720) on `http://192.168.1.81:8210/#/d/<id>/live`, per-second counts of live frame headers (`live-frame.md`), plus a `GET /api/v1/status` poll each second for spool bytes and collector presence.
- `tools/rig/g4_analyze.py` (new): window statistics and the threshold table from the recorder CSV and the viewer log.


---

# Results (appended after the run; the thresholds above are unchanged)

## Verdict

**G4 passes on a parked car, with three deviations from my own pre-written method, stated below (G4-9's endpoint name, G4-10's screenshot time, the rebuilt recorder); none changes a number.** Every window was measured with the Toyota Supra parked in the pit stall at Las Vegas (`IsOnTrack 1` in 900 of 900 samples, `Speed` at most 0.001 m/s). The vJoy bot did not drive (see the thresholds), so motion-dependent engine branches were not exercised on the rig.

## Run

| Item | Value |
|---|---|
| Sim PC | DESKTOP-SIM, 2026-10-03; `remote_job processes` at 14:38Z: no `iRacingSim64DX11`, no `iRacingUI`; one desktop lease ("rig-W9 G4 collector install and perf gate"), re-acquired once after it lapsed while idle, released 15:07:35Z |
| Collector | the installed release build (`collector-install.md`, SHA-256 `2309c2a4...8c11`), `coach-collector.exe run` with its overlay, server `http://192.168.1.81:8210`; a backlog of 1,848 files (16.16 GB) was uploading when the car went on track |
| Session | iRacing UI Test Drive with its remembered settings (NASCAR O'Reilly Toyota Supra, Las Vegas Motor Speedway oval, 2:15 PM, realistic weather), offline, solo; **Test** at 14:45:56Z put the car in the pit stall; Escape held 2.8 s at about 15:05:33Z, Quit, Yes Quit, Save Replay: Quit (nothing saved); iRacing UI closed |
| Recorder | `powershell -NoProfile -ExecutionPolicy Bypass -File g4-perf.ps1 -Mode windows -Out g4.csv -Status g4.status` (14:47:13 to 15:04:22Z; output `B1:stopped C1:started B2:stopped C2:started B3:stopped C3:started B4:stopped C4:started B5:stopped`) and, before it, `-Mode fast -Seconds 75 -Out abort.csv` (14:45:47 to 14:47:03Z, 5,570 loops, 1,519 rows, median row spacing 16 ms) |
| Viewers | `node tools/rig/g4_viewers.mjs --out <scratch>/g4 --seconds 3000 --stop-file <scratch>/g4/stop` on the dev PC from 14:44:33Z: two Chromium browsers, each on `ws://192.168.1.81:8210/ws/live/1507810?rate=60`, one socket each, 0 page errors |
| Clocks | against NTP (time.windows.com): dev PC 1.70 s fast (`w32tm /stripchart` -1.70 s; a direct SNTP query -1.69 to -1.70 s), sim PC 0.074 s slow; sim minus dev = -1.774 s, applied to every cross-machine comparison |
| Analysis | `python tools/rig/g4_analyze.py --windows g4.csv --viewers viewers.jsonl --fast abort.csv --clock-offset-s -1.774` |

## Windows (100 samples each, 1 Hz, car parked on track)

| Window | UTC (sim) | FrameRate median | p1 | CpuUsageFG | CpuUsageBG | GpuUsage | Collector CPU (% of a core) | Working set max | Read / write bytes | Viewer A / B fps median (lowest second) |
|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 14:47:23-14:49:02 | 180.571 | 180.327 | 0.1670 | 0.1340 | 0.2640 | | | | |
| C1 | 14:49:23-14:51:02 | 180.578 | 180.440 | 0.1659 | 0.1340 | 0.2639 | 0.796 | 14.05 MiB | 0 / 0 | 60 / 60 (57 / 56) |
| B2 | 14:51:13-14:52:52 | 180.578 | 180.265 | 0.1646 | 0.1330 | 0.2636 | | | | |
| C2 | 14:53:13-14:54:52 | 180.566 | 178.196 | 0.1673 | 0.1330 | 0.2637 | 0.875 | 14.04 MiB | 0 / 0 | 60 / 60 (57 / 58) |
| B3 | 14:55:03-14:56:42 | 180.588 | 180.263 | 0.1817 | 0.1545 | 0.2648 | | | | |
| C3 | 14:57:03-14:58:42 | 180.593 | 180.262 | 0.1849 | 0.1550 | 0.2646 | 1.781 | 14.04 MiB | 0 / 0 | 60 / 60 (56 / 57) |
| B4 | 14:58:53-15:00:32 | 180.607 | 180.191 | 0.1854 | 0.1560 | 0.2647 | | | | |
| C4 | 15:00:53-15:02:32 | 180.604 | 180.227 | 0.1868 | 0.1600 | 0.2649 | 1.172 | 14.02 MiB | 0 / 0 | 60 / 60 (56 / 56) |
| B5 | 15:02:43-15:04:22 | 180.588 | 176.027 | 0.1845 | 0.1580 | 0.2646 | | | | |

Each C window ran a fresh collector process (pids 15712, 16820, 15392, 17884), started 20 s before the window; private bytes at most 2.89 MiB. The sim sat at its 180 FPS cap throughout.

## Criteria

| # | Result | Numbers |
|---|---|---|
| G4-1 frame rate | PASS (4 of 4) | deviation of each C median from the mean of its two neighbouring B medians: C1 +0.002 %, C2 -0.009 %, C3 -0.002 %, C4 +0.004 % (limit +-1 %) |
| G4-2 tail and load | PASS (3 of 3) | median of the four C values against the five-B range: p1 FrameRate 180.245 in [176.027, 180.327]; CpuUsageFG 0.1761 in [0.1646, 0.1854]; GpuUsage 0.26427 in [0.26359, 0.26479]. Per window (information only): p1 outside the range in C1 (180.440, above the top, so better than every baseline); FG and GPU outside in C4 (0.1868 and 0.26494, above the top by 0.0014 and 0.00015). FG and BG stepped up by about 0.02 between B2 and B3 with the collector stopped and stayed there, so the late windows sit on a higher background |
| G4-3 collector CPU | PASS | 1.156 % of one core averaged over the four C windows (limit 1.5 %). Per window 0.80, 0.88, 1.78, 1.17 %: C3 alone is above 1.5 %; the criterion is the mean |
| G4-4 collector RSS | PASS | largest working set 14,737,408 bytes (14.05 MiB; limit 32 MB) |
| G4-5 disk writes on track | PASS | collector `WriteTransferCount` increase over the four C windows: 0 bytes, 0 write operations (limit 65,536 bytes) |
| G4-6 no file work on track | PASS | collector `ReadTransferCount` increase over the four C windows: 0 bytes; `IsOnTrack 1` in all 900 window samples; B1 opened 86 s after the car went on track (first `IsOnTrack 1` sample 14:45:56.672Z). Server spool constant at 33,554,554 bytes in 3 files from 14:46:28Z (sim clock) until the car left at about 15:05:33Z, so 0 upload bytes reached the server on track (the decrease from 183,879,954 before that is the archiver moving finished files to the NAS). Collector-reported `bytes_pending` constant at 14,730,522,885 through the on-track period of the first process |
| G4-7 abort at the transition | PASS | fast capture: the collector read 150,211,182 bytes in the 8.8 s before the transition (62 counter increases in the last second). Last `IsOnTrack 0` sample at 8,796 ms, first `IsOnTrack 1` at 8,827 ms (14:45:56.672Z); the last increase of `ReadTransferCount` is in that same first on-track sample and there is none in the following 66 s, so the file reads stopped at most 31 ms after the flag changed (the sample gap is the resolution; limit 100 ms). The server's last 4 MiB chunk landed in the second ending 14:45:56.2Z (sim clock), before the flag changed, and none after it |
| G4-8 stream rate | PASS (8 of 8) | median live frames per second: 60 for both viewers in all four C windows (98 whole seconds each; lowest single second 56). Measured at the viewers, which receive every frame the server relays; the API has no server-side frame counter |
| G4-9 time to visible | PASS | `stockcars2 supra2019_vegas oval 2026-10-03 09-45-56.ibt` (75,857,115 bytes, SHA-256 `937d4e30...968e`) was finalized when the driver left the car: last write 15:05:37.479Z (sim). The collector uploaded it first (newest first, ahead of 1,817 backlog files); `python tools/rig/g4_time_to_visible.py --server http://192.168.1.81:8210 --timeout 1200 --start-after 2026-10-03T14:40:00Z` saw session 37 `running` at 15:05:51.24Z and `ready` at 15:05:56.45Z (dev-PC times converted to the sim clock); its projection job finished at 15:05:56Z (server). **18.97 s** from finalization to ready (limit 120 s). `GET /api/v1/analyses/d8b6091d8a4e7a7d929466a6/race` answered 200 (21,266 bytes) |
| G4-10 overlay visible | PASS, timing deviation | the "iRacing Coach monitor" window (540x360 at 716,24, process `coach-collector`) drawn over the sim on the session screen at 14:45:06Z and in the car at 14:46:00Z (installed collector, car on track, streaming to both viewers), and in the car again at 15:05:04Z (collector restarted after B5). No capture was taken inside a C window, to keep the GDI capture out of the measured samples |

Deviations from the method as written: (1) G4-9 names `GET /api/v1/sessions/{id}/analysis`; that path is POST-only in the contract (it answered 405), so "visible" was taken as `analysis_state ready` plus the race view answering 200. (2) G4-10's screenshots are from just before B1 and just after B5, not from inside a C window. (3) The P3 per-second recorder was rebuilt (it had never been committed).

## Observations

- A restarted collector reports only its journal's pending uploads (2) until it may scan again; the 1,817 files reappeared in `bytes_pending` only after the car left the track, so the backlog shown on track can be understated after a restart. Consistent with the protocol (a scan is file work); noted for W3.
- The server analysed the parked recording (`lap_count 1`, `recorded_laps 0`) in about 5 s.

## Left as found

`iRacingSim64DX11` and `iRacingUI` not running (only the `iRacingService64` service); the sim window was moved for clicks and quit from there (its position is not stored); no replay saved; Discord in the foreground as before; lease released. The collector stays installed and running. The recorder scripts and CSVs on the sim were deleted with `C:\Users\joshu\coach-rig\` at the end of the task; copies of the CSVs and screenshots are on the dev PC under `C:\Users\joshu\source\coach-migration\w9\g4\`.


---

# Bot-driven run (task G4-bot, 2026-10-04)

The owner has permitted the bot-driven version (2026-10-04, relayed: "use the driver to do whatever driver things you needed my permission for"). This part re-runs G4 as `workstreams.md` defines it, with the vJoy bot driving IRP. As above, the thresholds and method in this section were written before any measurement of this run (2026-10-04 13:02Z, before the sim was touched) and are not adjusted afterwards; the results are appended below them. The parked-car results above stand as their own record.

## Thresholds for the bot-driven run (written before measuring; never adjusted afterwards)

The ten criteria G4-1 to G4-10 and their pass thresholds are those of the table above, unchanged, with these clarifications that follow from the car moving:

- **Workload validity (W).** A window counts as bot-driven only if all its 100 samples have `IsOnTrack 1`, `OnPitRoad 0` and `Speed >= 15 m/s` (the bot lapping, not stopped, towed or in the pits). The run is a bot-driven G4 only if all nine windows meet W. A window that fails W is reported with its numbers and the gate is not passed for the bot-driven run by assumption; it is not re-measured into a pass.
- **G4-6.** "Starts >= 5 s after `IsOnTrack` became 1" is checked against the first `IsOnTrack 1` sample of the stint (the car is on track from the stall before the launch).
- **G4-7.** The transition is the car going back on track (the session screen's **Test**) while the collector is reading the bot run's finalized recording for upload (that recording is finalized when the driver leaves the car after the windows). Same limit: last increase of the collector's `ReadTransferCount` <= 100 ms after the first `IsOnTrack 1` sample (fast sampler). If the collector has finished reading before the transition, G4-7 is reported as not exercised, not as passed.
- **G4-9.** "Visible" as `workstreams.md` now defines it: the session reports `analysis_state` `ready` (`GET /api/v1/drivers/{id}/sessions`, the session's `analysis_id` set) and `GET /api/v1/analyses/{analysis_id}/race` answers 200, with every recording of this sim session in the session (`recording_count` equal to the recordings iRacing wrote). Measured from the finalization of the session's last recording (its `.ibt` last-write time on the sim) to that state; limit 120 s.
- **G4-10.** A screenshot of the sim with the collector overlay drawn over the moving car, taken while the collector runs and streams, in a C window's 20 s settle period (outside the measured 100 s, so the GDI capture is not in any sample).

Method: as above (window plan B1 C1 B2 C2 B3 C3 B4 C4 B5, 100 s windows, 20 s settle before C, 10 s after the collector exits before B, the same 1 Hz recorder through every window, two Chromium viewers on the dev PC holding the Live page at 1280x720 for the whole run), with:

- **Driver.** The vJoy bot of iRacing-utility (`testdriver/`, a bundle of `main` at its current commit), with the arguments of `capture/g4-workload.json` (plan L at a fixed 0.95 pace, `--guard-log-only`, `--steer vjoy --pedals vjoy`, default `--inputs human`, `--max-resets 0`) plus `--fresh-tyres`; `--laps 61` (out-lap + 60, the orchestrator's default) instead of the pointer's 41, because nine windows with their settle periods need about 17 minutes of lapping after the out-lap (41 laps is about 16 minutes). Started directly with `drive.py` (one run, no `--end-in-stall`, so the run ends with the car stopped on the backstretch and the recording is finalized by the driver leaving the car).
- **Session.** Solo offline Test Drive, NASCAR O'Reilly Toyota Supra at Lucas Oil Indianapolis Raceway Park (oval), `NOAPS_MaconiSetupShop IRP 26S4 R.sto` loaded unmodified; the bot's controls profile `agent` selected through iRacing's own Control Profile list, Joshua's files backed up with hashes before and verified after the switch back to `Baseline` (iRacing-utility `AGENTS.md`). The collector is stopped during the garage visits so they leave no garage snapshots in Coach's library.
- **Recorder.** `tools/rig/g4-perf.ps1` also records `Lap`, `LapDistPct`, `OnPitRoad` and `PlayerTrackSurface` (appended columns, read the same way) for W; started once the out-lap is done and the car is lapping.
- **Live-engine observations (information, no threshold).** What the P3 report left open (its "Measurement limits": engine arithmetic under motion not exercised; overlay focus with a driver not checked): collector CPU per C window with the car moving against the parked run; the bot's own focus-loss record (`drive.py` summary `focus_graces_ms`, failsafe trips) across the four collector starts and stops; the collector's status at the server during the run (`on_track`, `streaming`, viewers); screenshots of the Live page in a viewer during C windows; the overlay's content on the sim.

## Results of the bot-driven run (appended after the run; the thresholds above are unchanged)

### Verdict

**G4 fails with the bot driving.** Seven criteria pass (G4-1, G4-2, G4-4, G4-5, G4-6, G4-8, G4-10) and the workload condition W holds in all nine windows. **G4-3 fails**: the collector used 2.34 % of one core on average across the four C windows against the 1.5 % limit (the parked run measured 1.16 %). **G4-7 was not exercised**: the collector had already finished reading the bot run's recording 1.07 s before the car went back on track, so there was no file work to abort; it is not counted as a pass. **G4-9 is not met as written**: the two recordings of the one sim session became two library sessions (658 and 659), so no session ever held both; each became visible well inside 120 s (22.7 s and 4.6 s). Recorded as measured; not re-run.

### Run

| Item | Value |
|---|---|
| Sim PC | DESKTOP-SIM, 2026-10-04; at 12:58Z no `iRacingSim64DX11`, no `iRacingUI`, no lease (sim free); lease "Coach G4-bot" taken 13:05Z, lapsed once while idle during the windows and re-taken 13:39Z, released 13:51Z |
| Driver | iRacing-utility `testdriver/` bundle of `11c0c27` (`iracing-testdriver-11c0c27.zip`, SHA-256 `487725e0...fb9b`); `python -m unittest` on the sim: 448 tests, 1 failure in the offline Kansas tool `tools/ksfit.py` (`ModuleNotFoundError: ibt` when run from `tools/`; not on the live path), 22 skipped. vJoy 2.2.2.0 device 1 free (`vjoyfeed.py probe`); `calibration.json` from build `5f109bf` (vJoy calibrated 2026-09-25 18:12, after the agent profile's last change); a fresh `drive.py --calibrate-vjoy` in the stall (written to a separate file) reproduced it point for point (X 0.5598 -> -1.1395 rad, throttle 0.5 -> 0.500, brake 0.5 -> 0.289, cross-talk 0), so the device was bound |
| Session | iRacing UI Test Drive: Supra, Lucas Oil IRP **Oval**, 2:15 PM, realistic weather, offline, solo (guard: `EventType Test`, `Offline Testing`, 1 driver). `NOAPS_MaconiSetupShop IRP 26S4 R.sto` loaded unmodified with `setups/garage_harvest.py restore` (collector stopped), passes tech. Controls profile `agent` selected in the sim (Settings > Driving > Control Profile, Save) |
| Bot command | `drive.py` with `capture/g4-workload.json`'s arguments + `--laps 61 --fresh-tyres --no-status`; four new tyres in 17.5 s, fuel 70.68 L, launch 13:13:34Z; 50 laps, 22.883 to 24.500 s (median 23.675), every lap "inputs: valid", 0 resets, `focus_graces_ms` empty (no focus loss in the whole run). The push logic lowered the pace from 0.95 to as low as 0.880 on slide calls from lap 13 (`--guard-log-only` keeps only the guard from lowering it), so laps were not near-identical as `capture/README.md` expects. At the start of lap 51 (13:38:40Z, 7 min after the plan ended) the failsafe stopped the bot (lateral error 6.0 m) and the car stopped against the wall |
| Recorder | `powershell -NoProfile -ExecutionPolicy Bypass -File g4-perf.ps1 -Mode windows -Out g4.csv -Status g4.status` from 13:14:18 to 13:31:27Z (output `B1:stopped C1:started B2:stopped C2:started B3:stopped C3:started B4:stopped C4:started B5:stopped`; collector stopped since 13:07Z; car on track since 13:11Z); then `-Mode fast -Seconds 200 -Out abort.csv` 13:38:54 to 13:42:14Z (2,505 rows) |
| Viewers | `node tools/rig/g4_viewers.mjs --base http://192.168.1.81:8210 --out <scratch>/viewers --seconds 4500 --stop-file <scratch>/viewers.stop --shot-every 30`, 13:07:52 to 13:43:04Z: two Chromium browsers, one socket each on `ws://192.168.1.81:8210/ws/live/1507810?rate=60`, 0 page errors, 0 skipped sequence numbers, 0 backlog frames, 40 screenshots of the Live page |
| Clocks | `w32tm /stripchart` against time.windows.com: dev PC 0.37 s fast, sim 0.33 s slow at 13:05Z (sim minus dev -0.695 s); 0.39 s fast and 0.34 s slow at 13:51Z (-0.730 s); -0.71 s used for G4-9 |
| Analysis | `python tools/rig/g4_analyze.py --windows g4.csv --viewers viewers.jsonl --clock-offset-s -0.695`; the abort capture split by hand around its `IsOnTrack` changes |

### Windows (100 samples each, 1 Hz, the bot lapping)

| Window | UTC (sim) | Laps | Speed min / median (m/s) | FrameRate median | p1 | CpuUsageFG | CpuUsageBG | GpuUsage | Collector CPU (% of a core) | Working set max | Read / write bytes | Viewer A / B fps median (lowest second) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 | 13:14:28-13:16:07 | 1-6 | 38.5 / 45.9 | 180.581 | 180.442 | 0.1304 | 0.0900 | 0.21495 | | | | |
| C1 | 13:16:28-13:18:07 | 7-11 | 38.4 / 45.1 | 180.579 | 180.391 | 0.1285 | 0.0890 | 0.21551 | 1.937 | 15.79 MiB | 0 / 0 | 60 / 60 (57 / 57) |
| B2 | 13:18:18-13:19:57 | 11-16 | 37.1 / 45.5 | 180.578 | 180.445 | 0.1260 | 0.0880 | 0.21573 | | | | |
| C2 | 13:20:18-13:21:57 | 17-21 | 36.3 / 44.3 | 180.577 | 180.469 | 0.1259 | 0.0870 | 0.21644 | 1.921 | 15.90 MiB | 0 / 0 | 60 / 60 (57 / 57) |
| B3 | 13:22:08-13:23:47 | 21-25 | 36.9 / 44.6 | 180.575 | 178.894 | 0.1261 | 0.0870 | 0.21628 | | | | |
| C3 | 13:24:08-13:25:47 | 26-30 | 36.6 / 44.6 | 180.583 | 180.298 | 0.1276 | 0.0870 | 0.21600 | 2.907 | 15.77 MiB | 0 / 0 | 60 / 60 (58 / 58) |
| B4 | 13:25:58-13:27:37 | 31-35 | 35.4 / 44.7 | 180.577 | 180.448 | 0.1274 | 0.0867 | 0.21524 | | | | |
| C4 | 13:27:58-13:29:37 | 36-40 | 37.2 / 45.6 | 180.580 | 180.418 | 0.1271 | 0.0860 | 0.21432 | 2.610 | 15.73 MiB | 0 / 0 | 60 / 60 (57 / 57) |
| B5 | 13:29:48-13:31:27 | 41-45 | 35.9 / 44.2 | 180.571 | 179.572 | 0.1246 | 0.0900 | 0.21351 | | | | |

Each C window ran a fresh collector (pids 28396, 16544, 7224, 18960); private bytes at most 4.29 MiB. The sim held its 180 FPS cap with the car moving, as it did parked.

### Criteria

| # | Result | Numbers |
|---|---|---|
| W bot-driven | PASS (9 of 9) | every sample `IsOnTrack 1`, `OnPitRoad 0`, `Speed` >= 35.4 m/s (limit 15); laps 1 to 45 covered |
| G4-1 frame rate | PASS (4 of 4) | deviation from the mean of the neighbouring B medians: C1 +0.00002 %, C2 +0.0007 %, C3 +0.0039 %, C4 +0.0030 % (limit +-1 %) |
| G4-2 tail and load | PASS (3 of 3) | median of the four C values against the five-B range: p1 FrameRate 180.404 in [178.894, 180.448]; CpuUsageFG 0.12736 in [0.12457, 0.13038]; GpuUsage 0.21576 in [0.21351, 0.21628]. Per window (information): C2's p1 (180.469) and GPU (0.21644) above the top of the range, the rest inside |
| G4-3 collector CPU | **FAIL** | 2.344 % of one core averaged over the four C windows (limit 1.5 %); per window 1.94, 1.92, 2.91, 2.61 % (CPU time 1,937 / 1,921 / 2,907 / 2,610 ms over 100.0 s each). Parked run: 1.156 % |
| G4-4 collector RSS | PASS | largest working set 16,670,720 bytes (15.90 MiB; limit 32 MB) |
| G4-5 disk writes on track | PASS | `WriteTransferCount` increase over the four C windows: 0 bytes (limit 65,536) |
| G4-6 no file work on track | PASS | `ReadTransferCount` increase over the four C windows: 0 bytes; server spool 2,170,087 bytes in every viewer poll of the plan (13:14:28 to 13:31:28Z), so 0 upload bytes; nothing was pending (collector queue 0 bytes before the run); B1 opened about 3 min after the car went on track (13:11Z) |
| G4-7 abort at the transition | **NOT EXERCISED** | fast capture: driver out of the car at 42.206 s (13:39:37.02Z); the collector read 218,033,684 bytes (the 108,964,466-byte recording twice: hash, then upload) from 42.363 s to 56.798 s; the car went back on track (Test) at 57.867 s (13:39:52.68Z), 1.069 s after the last read. 0 bytes read in the 37.8 s on track. The server had the whole file (spool 111,134,553 bytes at 13:39:57Z dev clock, queue 0) before the transition, so no upload was interrupted. The upload ran at about 7.5 MB/s, faster than I allowed for when I timed the transition |
| G4-8 stream rate | PASS (8 of 8) | median live frames per second: 60 for both viewers in all four C windows (98 whole seconds each, lowest single second 57) |
| G4-9 time to visible | **NOT MET as written** | iRacing wrote two recordings in this sim session: `stockcars2 supra2019_irp 2026-10-04 08-11-20.ibt` (108,964,466 bytes, finalized 13:39:37.058Z sim, the bot run) and `... 08-39-52.ibt` (2,481,579 bytes, finalized 13:40:30.528Z, the in-stall stint of the G4-7 attempt). The server made them two sessions, 658 (start 13:11:20Z) and 659 (start 13:39:52Z), one recording each, so the pre-written condition "every recording of this sim session in the session" never held. Each was visible fast: 658 `ready` with `analysis_id` `15ed4fc1...` at 13:40:00.48Z dev (13:39:59.77Z sim) = **22.7 s** after finalization, race view 200 (166,248 bytes); 659 `ready` at 13:40:35.78Z dev = **4.6 s**, race view 200 (22,616 bytes). `python tools/rig/g4_time_to_visible.py --server http://192.168.1.81:8210 --timeout 1800 --start-after 2026-10-04T13:00:00Z --min-recordings 2` (1 s polling) |
| G4-10 overlay visible | PASS | "iRacing Coach monitor" (540x360 at 716,24, pid 16544) drawn over the moving car at 13:20:06.67Z, in C2's settle period, collector streaming to both viewers; again at 13:38:46Z (pid 3540) |

### Live-engine observations on track (what P3 left open)

- **Engine cost under motion.** The collector's CPU doubled from the parked run (1.16 % to 2.34 % of a core) with the same overlay, the same 60 Hz stream and two viewers; frame rate, FG CPU and GPU stayed inside the baseline range, so the cost shows in the collector process, not in the sim's own counters at this resolution. C3 and C4 (laps 26-40, more slides and traction cuts) cost more than C1 and C2. For W3/W2: per-tick engine or overlay redraw work while values change.
- **Focus.** Four collector starts and four stops while the bot drove: no focus loss (`focus_graces_ms` empty, no failsafe trip until the unrelated lateral-error stop after the plan). The overlay did not take focus from the driver.
- **Overlay and Live page content (solo test).** Position P1, "Physical gap to leader: Leader", last lap updating each lap (23.408, 23.437, 24.042 s), fuel falling (16.3 US gal at about lap 9, 11.4 at lap 50). Two tiles look wrong for a solo offline test: the Coach cue showed "Caution: competitive gap trends paused" (source "iRacing race status") for the whole run with no caution in a test session, and "Laps to pit" stayed "Insufficient evidence" ("Lap estimate needs clean fuel-use laps") after 50 green laps. Possibly one cause (laps treated as caution laps); not verified against `SessionFlags`, which the recorder does not read. For W2.
- **Server.** Collector `online`, `on_track` true, 2 viewers (a third viewer was on the Live page at 13:29:51-52Z and 13:38:46-13:39:20Z, not mine); 6 status-poll timeouts during the plan, all in B windows. The two stints of one offline test session became two library sessions (start times 13:11:20Z and 13:39:52Z). For W4: an offline test has no subsession id to group by.
- **Upload after the run.** Hash and upload of the 109 MB recording took 14.4 s from the driver leaving the car (about 7.5 MB/s); the session was analysed and visible 22.7 s after finalization.

### Deviations

(1) `--laps 61` instead of the pointer's 41, and `drive.py` started directly instead of the orchestrator (both written in the thresholds). (2) The bot's pace was not fixed (0.95 down to 0.880), so laps varied by 1.6 s; W held. (3) The G4-7 transition came too late to catch the upload; it was not repeated. (4) The desktop lease lapsed once while idle (the first Escape after it was refused with no input sent). (5) At 13:46Z the hidden RDP host stopped drawing (capture `Win32Exception`, main PC session 1 `Disc`); I disconnected and reconnected my own hidden host and drawing resumed, as in the harvest of 2026-10-04.

### Left as found, and what changed

- No `iRacingSim64DX11` or `iRacingUI` running at 13:51Z; collector running again (pid 29288; it was running before, pid 7388); lease released; scratch folder `C:\Users\joshu\ClaudeDev\g4bot` removed after copying the CSVs, logs and screenshots to the dev PC (`C:\Users\joshu\source\coach-migration\w9\g4-bot\`).
- Sim quit through `garage_harvest.py quit`: no replay saved, window stored at (-2560, 0). The Supra's current setup is `claude LV R CAM.sto` again (unmodified, passes tech; `garage_harvest.py restore` with the collector stopped, so no garage snapshots reached Coach).
- **Joshua's control files.** Backup `C:\Users\joshu\ClaudeDev\iracing-config-backup-20261004-080447` (10 files, SHA-256). `Baseline` selected again through iRacing. iRacing then changed three of his files itself and could not undo it: on Save it wrote its device calibration, now including the vJoy device, into `Baseline\joyCalib.yaml` (his wheel and pedal values unchanged, a vJoy section added); at exit it wrote the RDP session's sound devices into `app.ini` (`devSpeakerId`, `devLFEId`, `devSPCCId`, `devVoiceChatId`, `devMicrophone*`) and the bot's IRP fuel-per-lap history into `fueldata.ini`. With iRacing closed I copied those three back from the backup (the iRacing-utility `AGENTS.md` recovery path; iRacing's versions kept in `iracing-config-prerestore-20261004-084959`). All eight of his files now equal the backup by hash; `app.ini` has `[ControlProfiles] Global=Baseline` and `drivingCamFOV=179`. Only the bot's own `agent\joyCalib.yaml` differs (re-ordered by iRacing). Every step is in `C:\Users\joshu\ClaudeDev\iracing-config-changes.md`. **Joshua: these three files were copied back, not restored by iRacing.**
- The iRacing UI's Test Drive dialog now remembers IRP Oval (it remembered Las Vegas).
- Coach: two new sessions under Joshua's account, 658 (the bot run, 50 laps) and 659 (a 38 s in-stall stint), both rig tests to remove like the five of 2026-10-03 (backlog #14).
