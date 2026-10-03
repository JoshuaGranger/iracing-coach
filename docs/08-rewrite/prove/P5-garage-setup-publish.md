# P5: does the SDK publish setup values when a setup is loaded in the garage?

Decides the D9 design (passive garage capture). Run 2026-10-02, 23:12 to 23:21 UTC, on the sim PC (DESKTOP-SIM) against the real iRacing simulator. All numbers below are measured, not emulated.

## Thresholds (written before any measurement; not adjusted afterwards)

- **PASS:** before the car leaves the garage, loading or applying a .sto changes the session-info string's CarSetup section (UpdateCount increments and the values match the loaded file's known values) and DriverSetupName reflects the loaded file, with DriverSetupIsModified reflecting unsaved changes.
- **FAIL:** nothing updates until the car leaves the garage or goes on track; then D9's passive capture must be redesigned around that moment, and the report must say so explicitly.

## Method (commands and steps)

Preconditions. `remote_job processes` at 23:12 and again at 23:13 listed no `iRacingSim64DX11` (only `iRacingService64` and `iRacingUI`), so the sim was free. One controller lease, label "P5 garage setup publish proof", released at 23:20.

Watcher (throwaway, kept only in the scratch folder `...\scratchpad\prove\P5\`, not in the repository). `Watch.cs`, about 150 lines of C# 5, compiled with `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /out:Watch.exe Watch.cs` (SHA-256 310864DE429C6DA1...). It did not reuse the product's SDK source file; it reads the memory map `Local\IRSDKMemMapFileName` read-only itself with the same header layout as `live-stack.md` section 1. Every 250 ms it reads the header (status, tickRate, sessionInfoUpdate), the latest telemetry buffer (`IsOnTrack`, `IsInGarage`, `IsOnTrackCar`, `OnPitRoad`, `PlayerCarInPitStall`, `SessionState`) and, when the header update counter changes, the whole session-info string. It logs every change in `CarSetup.UpdateCount`, `DriverSetupName`, `DriverSetupIsModified`, `DriverSetupLoadTypeName`, `DriverSetupPassedTech`, the full line diff of the CarSetup section, and writes each session-info string to a file (18 snapshots). Run on the sim as `Watch.exe <outdir> 3000`, started at 23:13:52 before iRacing, copied there with `remote_copy` (hash verified). It does not gate on status bit 0.

Sim steps (desktop through `remote_observe` and `remote_gesture`, observe after every gesture):

1. iRacing UI: Test Drive with the dialog's remembered settings: NASCAR O'Reilly Toyota Supra (`stockcars2 supra2019`) at Las Vegas Motor Speedway Oval (the car and track of the 9/28 and 10/2 test sessions; the IRP track named in the memory note was not selected). Solo offline Test session. Loaded setup at start: "identical to NOAPS_MaconiSetupShop LasVegas 26S4 R".
2. Session screen, Garage. My Setups, typed `NOAPS_MaconiSetupShop LasVegas 26S4 Q.sto` in the file dialog, Open (setup A). Apply. Wait.
3. My Setups, typed `claude LV R CAM.sto`, Enter (setup B). Apply. Wait.
4. Tires tab: left-front cold pressure `>` once (22.0 to 22.5 psi); garage title changed to "modified from claude LV R CAM". Nothing saved. Apply. Wait.
5. Apply & Close, then Test (car appears in its pit stall), 20 s wait. The car was not rolled out of the stall (see Notes).
6. Esc held (back to the session screen), Quit, Quit in the Save Replay dialog (nothing saved), UI back on its home page as found.

Cross-check of published values: a read-only PowerShell scan (`scan.ps1`, run on the sim) read the session-info block of every `*vegas*.ibt` in `Documents\iRacing\telemetry` (102 files; read-only open with `FileShare.ReadWrite`), kept the CarSetup block of the 26 recordings whose `DriverSetupName` was one of the three setups used, and `compare.py` (bundled Python) compared those with the live snapshots key by key, ignoring `UpdateCount`, `LastHotPressure*`, `LastTempsOMI/IMO`, `TreadRemaining*`. A hash of every file in the Supra setups folder was taken before and after (`Get-FileHash`).

Cleanup: watcher killed, `C:\Users\joshu\AppData\Local\Temp\p5-watch` and the result zip deleted (verified `Test-Path` False), one zip (93,512 bytes, SHA-256 C99C7C64...) copied to the main PC scratch first, no `iRacingSim64DX11` or `Watch` process left, lease released, `remote_status` shows lease null.

## Numbers

Times are UTC from the watcher log (`watch.log`, 250 ms resolution). Action times are estimates (+/- 3 s) from the gesture and observation timestamps; the watcher times are exact.

| UTC | Action (est.) | Header update | CarSetup.UpdateCount | DriverSetupName / IsModified | Telemetry | Other |
|---|---|---|---|---|---|---|
| 23:13:52 | watcher starts, sim not running | 10 (stale) | 2 | `...LasVegas 26S4 R.sto` / 0 | `status=0`, map present, `IsInGarage=0` | map persists from an earlier run |
| 23:14:52.99 | sim loading | status becomes 1, counter resets to 1 | CarSetup section absent | `INVALID_LOADTYPE`, load type `invalid`, tech 0 | | |
| 23:14:59.04 | tech inspection | 3 | first appears: 1 | | | |
| 23:15:00.08 | tech inspection | 4 | 2 | `NOAPS_MaconiSetupShop LasVegas 26S4 R.sto` / 0, `user`, tech 1 | | session-start publish |
| 23:15:17.67 | Garage clicked (about 23:15:17) | | | | `IsInGarage` 0 to 1 | |
| 23:15:21.08 | garage open | 5 | 3 | unchanged | | 1 value (RF corner weight 4039 to 4040 N) |
| 23:15:47.03 | load setup A (Q) | 6 | 4 | **unchanged (R)** | | 55 diff lines, 27 tuning keys differ from the previous state |
| 23:16:01.997 | Apply | 7 | 5 | unchanged | | 3 values, +/-1 N |
| 23:16:52.09 | load setup B (R CAM) | 8 | 6 | **unchanged (R)** | | 113 diff lines, 59 tuning keys differ from A |
| 23:17:02.835 | Apply | 9 | 7 | unchanged | | 3 values, 1 mm / 1 N |
| 23:17:17.834 | edit LF cold 22.0 to 22.5 psi (click about 23:17:11, `Apply` not yet pressed per my read of the log) | 10 | 8 | unchanged, `IsModified` 0 | | `ColdPressure` 152 to 155 kPa appears about 6.5 s after the click |
| 23:17:23.843 | Apply | 11 | 9 | unchanged, `IsModified` 0 | | 1 N |
| 23:17:43.73 / 43.997 | Apply & Close (click about 23:17:41) | 12 | 10 | **`claude LV R CAM.sto` / 1** | `IsInGarage` 1 to 0 at 23:17:43.73, 0.27 s before the name update | 10 diff lines (weights, preloads) |
| 23:18:03.18 | Test (car in stall) | 13 to 16 (4 updates in 4 s) | 10, text unchanged | unchanged | `IsOnTrack` 1, `PlayerCarInPitStall` 1, `OnPitRoad` 1 | CarSetup identical |
| 23:18:03 to 23:18:48 | 45 s in the car | 17 | 10 | unchanged | `IsOnTrack` 0 again at 23:18:48.5 (Esc held) | no CarSetup change |
| 23:19:21.56 | sim quit | 17 | 10 | | status 0, map still present | stale data stays readable |

`DriverSetupLoadTypeName` stayed `user` and `DriverSetupPassedTech` stayed 1 from 23:15:00 to the end (no change event), including while the garage showed "Tech Pending" after the edit.

Value check against the recordings' own session info (`compare.py`; 71 tuning keys per CarSetup):

| Live snapshot | Compared with | Keys differing |
|---|---|---|
| upd 3 (session start, R) | IBT 2026-09-29 21-04-05, setup R | 0 |
| upd 6 (A loaded, before Apply) | IBT 2026-09-28 18-13-59, setup Q | 1 (RR corner weight 2968 vs 2969 N) |
| upd 7 (A applied) | same IBT | 2 (corner weights 1 N) |
| upd 8 (B loaded, before Apply) | IBT 2026-09-28 15-23-02, `claude LV R CAM` | 4 (ARB preload 0.2 Nm, LF ride height 1 mm, RR corner weight 1 N, RR truck-arm preload 0.2 Nm) |
| upd 10 (B + edit, before Apply & Close) | IBT of this session (2026-10-02 18-18-02) | 3 (all derived: ARB preload 0.2 Nm, LR corner weight 1 N, truck-arm preload 0.2 Nm); LF `ColdPressure` 155 kPa matches |
| upd 12 (after Apply & Close) | same IBT | 0 |

Every user-set value (pressures, springs, dampers, camber, toe, track bar heights, tape, brake bias, steering offset) matched. The only differences are derived measurements the sim recomputes (corner weights, ride heights, preloads) and differ by 1 N, 1 mm or 0.2 Nm. The `.sto` payloads are encrypted, so the "loaded file's known values" are the CarSetup blocks of earlier recordings made with the same file name (and the garage's own on-screen values, e.g. LF cold 30.0 psi = 207 kPa for A, 22.0 psi = 152 kPa for B, 45.5 psi = 314 kPa).

Side facts:
- Setups folder hash comparison (84 files before and after): the only file that changed was `-Current-` (`85BAE950C62F...` to `6F7A683D8EAE...`), rewritten by iRacing itself during the garage session. No `.sto` was created or changed.
- iRacing wrote a new recording of this session: `Documents\iRacing\telemetry\stockcars2 supra2019_vegas oval 2026-10-02 18-18-02.ibt`, 2,960,161 bytes. Its session info has `DriverSetupName: claude LV R CAM.sto` and `DriverSetupIsModified: 1`.
- Of the 102 Las Vegas IBTs, 101 carry `DriverSetupIsModified: 0` and this session's carries 1, so the real IBT key is spelled `DriverSetupIsModified`. That supports `live-stack.md` surprise 8 (`ibt_reader.py:820` reads `DriverSetupModified`). The regex was applied to real recordings; `ibt_reader.py` itself was not run.
- The first read at 23:13:52 returned stale session info (`status=0`, update counter 10, a setup name) while no simulator process existed, and after quit the map kept the last data with `status=0`. A collector must not treat readable session info as live without status bit 0 or an advancing tick.

## Result: FAIL as written (the PASS conjunct on DriverSetupName is not met), and the FAIL definition is also not met. Mixed result.

- **Met in the garage:** the CarSetup section updates immediately on load (about 1 to 2 s after the click) and on Apply, `UpdateCount` increments on each (3 to 10 across the garage visit), and the values match the loaded setup (all user-set values equal; derived values within 1 unit). An unapplied edit also appeared, about 6.5 s after the click and before Apply (the log read just before the Apply click showed nothing; timing is +/- 3 s).
- **Not met in the garage:** `DriverSetupName`, `DriverSetupIsModified`, `DriverSetupLoadTypeName` and `DriverSetupPassedTech` did not change on any load, Apply or edit. After loading A and B and editing, `DriverSetupName` still read the session-start file (R). They updated once, at 23:17:43.997, when Apply & Close left the garage: name became `claude LV R CAM.sto` (the last file loaded), `IsModified` became 1 (the unsaved edit). Not tested: leaving the garage without Apply, `IsModified` going back to 0 after Revert, a setup saved under a new name, a different car.
- Not a total failure: values are available in the garage; the name that links them to a `.sto` is available only at garage exit.

## Decision impact

Confirms the core of **D9** (passive garage capture is possible, the SDK publishes setup values while the car is still in the garage) and changes how it must be built; no owner decision needs to change, so `contradictsOwnerDecision` is false.

Design consequences for D9's passive capture:
1. Capture on every `CarSetup.UpdateCount` change while `IsInGarage` is 1 (status bit 0 set), but commit the record only when `DriverSetupName` changes or `IsInGarage` returns to 0. Pairing a garage-time value snapshot with the name read at the same moment links values to the previous file (observed: Q and R CAM values published under the name `...R.sto`).
2. Only the last file loaded in a garage visit gets a name. If Joshua loads three files before leaving, the first two have values but no name. Treat them as "values seen, name unknown", match them to a known setup by values (the user-set keys), or drop them; do not attach the final name.
3. The name is the file name only (`claude LV R CAM.sto`, load type `user`). Link it to the file in `Documents\iRacing\setups\<car folder>\` and hash that file at the commit moment; two different files with the same name over time cannot be told apart otherwise.
4. Exclude the derived keys when comparing or hashing values (corner weights, ride heights, ARB/truck-arm preload, and `LastHotPressure*`, `LastTemps*`, `TreadRemaining*`, `UpdateCount`), or allow a 1 N / 1 mm / 0.2 Nm tolerance. The same file produced values differing by those amounts between sessions.
5. `DriverSetupIsModified` is only meaningful after garage exit; in the garage an edited, unsaved setup has `IsModified` 0 and the changed value with a new `UpdateCount`.
6. A setup loaded and applied but never taken onto the track is still captured at Apply & Close (no on-track time needed), so the collector can finish garage capture before the car moves.
7. The product's reader gates session-info reads on status bit 0 (`live-stack.md` section 6). Here bit 0 was 1 for the whole garage visit, so the gate does not hide garage updates for this path. It was 0 and the session info stale before the session loaded and after quit.
8. Separate defect to fix in the Python reader regardless of D9: use `DriverSetupIsModified` (real IBTs carry that key).

## Notes

- Limitations: one car (Supra), one track (Las Vegas, not IRP), one garage visit, two loads, one edit. Time resolution 250 ms for the watcher; action times +/- 3 s. The delayed appearance of the unapplied edit (about 6.5 s) rests on my reading of when the Apply click happened; I could not separate it from a periodic refresh.
- The car was not driven out of the stall (no engine start or throttle was scripted, the control profile is the owner's). `IsOnTrack` became 1 with the car in the stall and CarSetup did not change over the 45 s after that (including the 20 s wait). The step "drive out onto the track" is therefore only partly done; nothing in the data suggests a later change, but it was not observed with the car moving.
- Side effects on the sim from iRacing itself, left in place (they are raw telemetry or iRacing's own files, which the rules forbid me to modify): the new `.ibt` above, and the rewritten `-Current-` setup file. No replay was saved. Nothing else under `Documents\iRacing` changed (setups folder hash check; telemetry folder newest two entries listed).
- Another set of `claude` processes was running on the sim (started 13:52); I did not touch them. No other controller held the lease.
- `remote_copy` of the watcher failed once (uncertain) because the destination directory did not exist; nothing was written, the directory was created and a new copy ID used. Four uploads and one download were staged through the bridge (below the eight-transfer limit).
- Needs owner: nothing.
- Dev PC load was not checked; no timing was measured on the dev PC.
