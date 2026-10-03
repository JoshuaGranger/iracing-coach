# Collector install on the sim PC (task rig-W9, D13/D14)

Run 2026-10-03 on DESKTOP-SIM through the remote tools. From this install on, the collector is the owner's real pipeline (D13: the desktop app is retired; D14: release at will).

## Preconditions

- `remote_status` (agent 2.0.4, session 1, `startupConfigured true`) and `remote_job processes` at 14:38Z: no `iRacingSim64DX11`, no `iRacingUI`. A hidden RDP session was connected and was left alone (only `remote_session status` would have been allowed; it was not needed).
- Gate G3 had passed (`g3-garage.md`), so the release collector was installed rather than the `--dry-run` build.
- Server before the install (`GET /api/v1/status`, 14:38Z): `server_version 0.19.0`, `collector_protocol 1`, `collectors []`, queues empty, spool 122 B in 2 files, `nas.state ok`. `GET /api/v1/drivers`: one driver, Joshua Granger (1507810), 6 sessions, `live false`.

## Install

| Step | Command and result |
|---|---|
| Build | `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` (`CARGO_TARGET_DIR=target/rig`; the tree with the three G3 fixes, unchanged since): `coach-collector.exe` 1,581,568 bytes, SHA-256 `2309c2a4a4e19b00e5500ed307ff517197f70831940b5594ded21b00d71a8c11` (limit 3 MB) |
| Copy | `remote_copy` upload to `C:\Users\joshu\AppData\Local\Programs\iRacingCoach\coach-collector.exe` (the location in `architecture.md` section 10). The first commit failed with `io_error` because the folder did not exist; the folder was created with a job and the same staged transfer committed: destination SHA-256 `2309C2A4...8C11`, 1,581,568 bytes |
| Install | `coach-collector.exe install --server http://192.168.1.81:8210` (remote job, exit 0, 14:42:57 to 14:43:00Z). Output: `installed ...\Programs\iRacingCoach\coach-collector.exe`, `server http://192.168.1.81:8210`, `id fc450983-d64c-4663-b314-320dfbdc9cca`, `state C:\Users\joshu\AppData\Local\iRacingCoach`, `started; it also starts at sign-in (HKCU Run value "iRacing Coach Collector")` |
| Autostart | HKCU `Software\Microsoft\Windows\CurrentVersion\Run` value `iRacing Coach Collector` (the collector's own definition: per-user Run value, no service, no admin, no firewall rule). The started process (pid 16188) outlived the remote job that started it |

No driver pairing step exists or is needed: the collector has one setting, the server address (D7). The driver is the one iRacing reports: the server attributes recordings from each IBT at ingest, and live presence from `DriverInfo` (collector-protocol section 2).

## Confirmed from the server (`http://192.168.1.81:8210`)

| Check | Evidence |
|---|---|
| Collector present | `GET /api/v1/status` 20 s after the install (14:43:20Z): `collectors[0]` = `{collector_id fc450983-..., machine DESKTOP-SIM, collector_version 0.19.0, protocol 1, compatible true, online true, status.sim not_running, queue {uploads_pending 1848, bytes_pending 16158111232}}` |
| Live indicator | with the sim in session (14:45:02Z): `GET /api/v1/drivers` gives Joshua Granger `live true`; the collector reports `sim in_session`, `connected true`, driver `{customer_id 1507810, name Joshua Granger}`, session `Offline Testing`, Las Vegas Motor Speedway Oval, `stockcars2 supra2019`, `streaming true`, `viewers 2`. The Live page in two desktop browsers shows the nav's live dot and 60 Hz tiles (screenshots `viewer-A-end.png`, `viewer-B-end.png` under `coach-migration\w9\g4\`) |
| On-track gate | in force: with the car on track the collector read 0 bytes and the server spool did not grow for 19 minutes; the abort at the transition took at most 31 ms (`g4-performance.md`, G4-6 and G4-7). Uploads resumed within 5 s of the car leaving the track |
| Upload spool | the server's spool limit is 21,474,836,480 bytes (`status.spool.limit_bytes`); the collector's own queue is its journal (`journal.jsonl`), which survived four stop/start cycles during G4 and the restart after it |
| Overlay | the "iRacing Coach monitor" window draws over the sim (540x360 at 716,24 on the 1280x800 session desktop; screenshots in `g4-performance.md`) |

State on the sim after the task: `C:\Users\joshu\AppData\Local\iRacingCoach\collector.json` (1,676 bytes: `collector_id fc450983-d64c-4663-b314-320dfbdc9cca`, `server http://192.168.1.81:8210`, `monitor_visible true`, `safe_glance true`, factory-default layout), `journal.jsonl`, `logs\collector.log`. Run value: `"C:\Users\joshu\AppData\Local\Programs\iRacingCoach\coach-collector.exe" run`. The collector was stopped and started four times by the G4 recorder and re-installed once afterwards with the same command (same collector id); it ran as pid 30576 when the task ended.

## Limits

- The Run value starts the collector at the owner's next sign-in; no sign-in or reboot was done here (forbidden), so autostart itself is configured but not exercised.
- The collector has no `backfill` subcommand (D12 step 3 expects one); its normal scan uploads every `.ibt` directly in `Documents\iRacing\telemetry` and every `.sto`/`.htm` under `setups`, which covers the live folder. The legacy raw archive was uploaded with `tools/migrate/Backfill-RawArchive.ps1` instead (`migration.md`).

## Update to the `ae65a0a` build (2026-10-03, 16:29Z to 16:32Z)

The collector side of `ae65a0a`: `coach-collector backfill`, the terminal `rejected` state for recordings that cannot become final, and the garage exit-name rule (`collector/README.md`). Run through the remote tools on DESKTOP-SIM; times are UTC as the machine that reported them gives them (sim, or the server for `last_seen_at`).

**Preconditions.** `remote_status`: agent 2.0.4, session 1, no lease held by anyone, `startupConfigured true`. `remote_job processes` (about 16:16Z) and a job at 16:29:39Z: no `iRacingSim64DX11`, no `iRacingUI`. No desktop lease was taken (files and jobs need none) and the hidden RDP session was not touched.

| Step | Command and result |
|---|---|
| Build | `cargo build --release -p coach-collector --target x86_64-pc-windows-msvc` in the W9 worktree at `ae65a0a` (`CARGO_TARGET_DIR=<repo>/target/W9`, 4 jobs, cargo and rustc 1.99.0): 83 s, exit 0; `coach-collector.exe` 1,679,872 B (limit 3 MB), SHA-256 `8f09df025cc03d9338f7d1d7c60215e6e0f86e7947419157014b864d582fd6af` |
| Before | pid 30576 (started 15:04:40Z, `coach-collector.exe run`), installed exe `2309c2a4...8c11` (1,581,568 B), `collector.json` id `fc450983-d64c-4663-b314-320dfbdc9cca`, `journal.jsonl` 1,765,443 B in 7,347 lines with no line naming the Vegas recording (the old build counted it pending without a journal entry); server status `queue {uploads_pending 1, bytes_pending 74231808}` |
| Stop | The collector's own quit event (`Local\iRacingCoach.Collector.Quit`, the event `install` and `uninstall` signal before they act), set from a Windows PowerShell job at 16:30:03.21Z; pid 30576 exited by 16:30:03.67Z (its log: `collector stopped` at 16:30:03.633Z). `uninstall` was not used: it deletes `collector.json` and `journal.jsonl`, which hold the collector id and the memo of the 1,850 files the server already holds |
| Copy | `remote_copy` upload to `C:\Users\joshu\AppData\Local\Programs\iRacingCoach\coach-collector.exe`, conditional on the destination still being the old build (`2309c2a4...`): completed and verified, destination `8F09DF02...D6AF`, 1,679,872 B |
| Install | `coach-collector.exe install --server http://192.168.1.81:8210` (remote job, exit 0, 16:30:22.8Z to 16:30:26.8Z): `installed C:\Users\joshu\AppData\Local\Programs\iRacingCoach\coach-collector.exe`, `server http://192.168.1.81:8210`, `id fc450983-d64c-4663-b314-320dfbdc9cca` (unchanged), `state C:\Users\joshu\AppData\Local\iRacingCoach`, `started; it also starts at sign-in (HKCU Run value "iRacing Coach Collector")`; the Run value is unchanged (`"...\coach-collector.exe" run`) |
| Running | pid 7388, started 16:30:22Z, `coach-collector.exe run`; working set 14.1 MB, private bytes 3.6 MB at 16:31Z; log `collector 0.19.0 starting: server http://192.168.1.81:8210` at 16:30:22.819Z. By 16:30:25Z the journal was 481,730 B in 1,851 lines (compacted at start; the rejected entry below is among them) |
| Online | `GET /api/v1/status` from 16:30:43Z: `collectors[0]` `fc450983-...`, `online true`, `compatible true`, `sim not_running`, **`queue {uploads_pending 0, bytes_pending 0}`**; the same at 16:32:33Z |

**The Vegas recording is in the rejected state.** `stockcars2 supra2019_vegas oval 2026-09-28 19-49-27.ibt` (74,231,808 B, last written 2026-09-29T01:08:43Z, unchanged) was given up on 2.3 s after start:

```
2026-10-03T16:30:25.161Z ERROR [uploader] giving up on C:\Users\joshu\Documents\iRacing\telemetry\stockcars2 supra2019_vegas oval 2026-09-28 19-49-27.ibt: the header says it holds 0 records
```

and `journal.jsonl` holds `{"rejected":{"path":"C:\\Users\\joshu\\Documents\\iRacing\\telemetry\\stockcars2 supra2019_vegas oval 2026-09-28 19-49-27.ibt","size":74231808,"mtime_ms":1790644123320,"kind":"ibt","reason":"the header says it holds 0 records","at_ms":1791045025161}}`. It is no longer pending: the server shows 0 uploads and 0 bytes pending, where the old build showed 1 and 74,231,808 for good.

**Backfill dry run.** `coach-collector.exe backfill --dry-run` (remote job, 16:31:16Z, exit 0 in 3.9 s; run again at 16:31:33Z with the `held` lines filtered out) printed `waiting for iRacing to be off track (or closed)`, the listing, and:

```
666 recordings, 20506458567 bytes: 36 to queue (4343199500 bytes), 630 already held, 0 refused
```

- `held` 630: every recording in `Documents\iRacing\telemetry` that the collector uploaded.
- `queue` 35 (4,268,967,692 B): the legacy raw archive (`Documents\iRacing Coach\data\telemetry-traces\raw\<sha256>\`), each listed `as` its original name. They were uploaded during the migration by `tools/migrate/Backfill-RawArchive.ps1`, not by this collector, so neither journal knows them, and a dry run sends nothing. The server holds all 35: `HEAD /api/v1/uploads/<folder hash>` answers 200 `upload-state: analyzed` for 35 of 35, and all 35 are in the library with `nas_state archived`. A real backfill would hash them and the server would answer that it holds the bytes; nothing would be transferred.
- `queue` 1: the Vegas recording above, although the running collector has given it up. The dry run takes refusals only from the backfill's own journal (`collector/src/backfill.rs` `plan`: `own.rejection`), and so does the backfill uploader's rescan (`hub.journal().is_rejected`), so a real run would read its header again and refuse it again.

Nothing is left to upload. Finding for W3 (minor): `backfill` should read the running collector's `rejected` entries as it already reads its `seen` memo, so that its dry-run total does not count a recording the collector has given up on as "to queue".

**Left as found.** The collector runs (pid 7388) as the owner's pipeline with the same id, server, settings and Run value; nothing under `Documents\iRacing` or `Documents\iRacing Coach` was written. The only writes on the sim: the replaced executable, `collector.json` saved again by `install` (1,676 B, same id and server), and the collector's own journal compaction and log lines. No lease was taken, so none is held. Autostart at sign-in is configured but, as before, not exercised.
