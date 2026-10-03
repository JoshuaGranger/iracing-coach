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
