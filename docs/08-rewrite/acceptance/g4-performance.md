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
