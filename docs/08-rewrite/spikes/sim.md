# Spike: sim PC (DESKTOP-SIM) fact sheet

Collected 2026-10-02 ~20:57-20:59Z through `remote_job` (PowerShell 5.1, read-only commands, `PSModulePath` reset) on machineId `815240639f5a4f7683dad7be5c8e95ed`, session 1, user `DESKTOP-SIM\joshu`, non-elevated. No lease taken, nothing written/copied/started on the sim (two of my own read-only scan jobs were cancelled when too broad; no state change).

## 0. Simulator state

- `iRacingSim64DX11` is NOT running (`remote_job processes`, full list inspected). `iRacingService64` (session 0) is running. Safe to read.
- `remote_status`: `desktopAvailable=false`, `desktopIssue=desktop_unavailable`, `controlAvailable=true`, `startupConfigured=true`, agent 2.0.4, cert expires 2028-09-25. Files and jobs worked; desktop observation not tested (not needed).
- Other sessions on the sim at the time: `claude` x10, `codex`, `node`, `esbuild`, `cmd`/`powershell` in session 1 (another controller/agent is active on the sim; I did not touch the desktop). Session 2 exists with `LogonUI` (inferred: lock/logon screen of a second session). Session 1 also has `LogonUI` (inferred: console locked).

## 1. Hardware / OS

| Item | Value | Command |
|---|---|---|
| Windows | 11 Pro 10.0.26200 build 26200 | `Get-CimInstance Win32_OperatingSystem` |
| CPU | AMD Ryzen 7 9800X3D, 8 cores / 16 threads | `Win32_Processor` |
| RAM | 31.6 GB (2 x 16 GB DDR5 @ 6000 MHz) | `Win32_ComputerSystem`, `Win32_PhysicalMemory` |
| GPU | NVIDIA GeForce RTX 5080, driver 32.0.16.1062 (AdapterRAM field reads 4 GB, a 32-bit WMI cap; real VRAM UNKNOWN) | `Win32_VideoController` |
| Disk C: | 1862.4 GB total, 1242 GB free; Crucial CT2000T710 NVMe SSD 2 TB | `Win32_LogicalDisk`, `Get-PhysicalDisk` |
| Disk J: (network) | `\\192.168.1.82\Joshua`, 7133.8 GB, 4272.2 GB free | `Win32_LogicalDisk` |

No other local disks. `LongPathsEnabled=0` and no .NET SDK per memory (not re-verified).

## 2. Documents\iRacing (`C:\Users\joshu\Documents\iRacing`)

Top-level folders: airosters, aiseasons, lapfiles, logs, paint, profiles, replay, screenshots, scripts, sentry, setups, telemetry, videos. Four `iRacingSim64DX11__*.dmp/.log` crash dumps (about 1 MB each) sit in the root.

| Folder | Files | Size |
|---|---|---|
| telemetry | 617 `.ibt` | 14.37 GB |
| replay | 86 `.rpy` | 55.78 GB |
| paint | 1343 | 5.05 GB |
| setups | 1229 | about 3 MB |
| lapfiles | 132 | ~0 |
| logs / aiseasons / scripts / profiles | 40 / 22 / 7 / 6 | ~0 |

### Telemetry `.ibt` (flat folder, no subfolders; named `<car> <track> <date> <time>.ibt`)

Command: `Get-ChildItem Documents\iRacing\telemetry -Recurse -Filter *.ibt`, grouped by LastWriteTime month.

| Month | Files | GB |
|---|---|---|
| 2026-10 | 4 | 0.20 |
| 2026-09 | 613 | 14.17 |
| 2026-04 to 2026-08 | 0 | 0 |

Oldest 2026-09-25 15:32, newest 2026-10-02 10:39 (the folder was cleared or the machine is new/reset around 9/25; the sim holds only one week of telemetry). Largest five:

| MB | File |
|---|---|
| 1996.6 | stockcars2 supra2019_kansas oval 2026-09-27 23-53-21 |
| 222.6 | stockcars fordtaurus03_talladega 2026-09-29 21-39-30 |
| 180.9 | stockcars2 supra2019_irp 2026-09-27 01-05-54 |
| 110.0 | stockcars2 supra2019_vegas oval 2026-10-02 10-09-39 |
| 98.1 | stockcars2 supra2019_irp 2026-09-26 17-33-36 |

Note one 2 GB outlier (the next is 223 MB): a collector must stream and not assume file size. Mean is about 23 MB per file.

### Setups (`Documents\iRacing\setups`, 193 car folders, 1229 files)

Extensions: `.sto` 1204 (about 3 MB total), `.htm` 16 (0.1 MB), no extension 9 (files named `-Current-`, 1.4 to 9.9 KB, one per car folder: bmwm2g87, euronascar, legends ford34c, mx5 mx52016, stockcars fordtaurus03, stockcars2 arcatoyota25, stockcars2 supra2019, trucks silverado, trucks toyotatundra2022). Top 15 car folders by `.sto` count:

| Car folder | .sto | .htm |
|---|---|---|
| stockcars2 supra2019 | 67 | 16 |
| trucks toyotatundra2022 | 37 | 0 |
| stockcars fordmustang2022 | 36 | 0 |
| stockcars toyotacamry2022 | 36 | 0 |
| stockcars chevycamarozl12022 | 36 | 0 |
| trucks ram2026 | 35 | 0 |
| trucks silverado2019 | 35 | 0 |
| trucks fordf150 | 35 | 0 |
| stockcars2 mustang2019 | 34 | 0 |
| stockcars2 camaro2019 | 34 | 0 |
| latemodel2023 | 33 | 0 |
| acuransxevo22gt3 | 33 | 0 |
| amvantageevogt3 | 33 | 0 |
| mclaren720sgt3 | 33 | 0 |
| fordmustanggt3 | 33 | 0 |

All 16 `.htm` are in `stockcars2 supra2019`, named `NOAPS_MaconiSetupShop <track> 26S3 Q/R.htm` (garage exports from a purchased shop, so treat as purchased source files: read-only). Most car folders hold about 33 stock/baseline `.sto` (inferred: iRacing's shipped baselines), so "setups run in a race" cannot be inferred from the setup folder alone.

### Replays

86 `.rpy`, 55.78 GB, all in `Documents\iRacing\replay` (about 650 MB average). D8 parks Replay, so these are not migration inputs.

## 3. Existing Coach installation and data

### Installation

- `C:\Program Files\iRacing Coach\`: 9383 files, 1009 MB; contains `iRacing Coach.exe` (FileVersion 0.9.0.0, 2026-08-02) and `Uninstall iRacing Coach.exe`, `coach-engine`, `iracing-coach`, `python` plus locale folders. Stale: `archive-manifest.json` says app 0.17.0. No Add/Remove Programs entry matched "Coach" (HKLM/HKCU Uninstall) and no Start Menu shortcut matched.
- Dev builds (self-contained, from main PC): `C:\Users\joshu\ClaudeDev\iRacingCoach-dev\<yyyymmdd-hhmmss>\` x7 (2026-09-24 21:05 through 2026-09-25 14:55), each contains `iRacing Coach.exe`, .NET runtime and WebView2 assets.
- Test-driver / bot work: `C:\Users\joshu\ClaudeDev\` holds about 90 `iracing-testdriver-<hash>` snapshots, `iracing-config-*` backups/prerestore sets, `Restore-iRacingConfig.ps1`, `drops`, `tools`. Not Coach, but they live on the sim and share its disk.
- No source checkout: `Documents\iRacing Coach` has `.git` and `iracing-coach\` (112 files, 3.5 MB) and `companion-app-handoff\` (114 files), so it is the data root, a git folder and an old handoff, all in one.

### Data root: `C:\Users\joshu\Documents\iRacing Coach` (the migration source)

Whole root: 9276 files, 12.99 GB; `data\`: 3531 files, 11.95 GB. `settings.json` keys: coachHome, iRacingRoot, iRacingInstallRoot, firstRunComplete, settingsSchemaVersion, coachThreadIds, launchAtSignIn, useReducedMotion, themeColor, customThemeColor, diagnosticIncludeConfounded, liveMonitor, raceAnalysisTraces, raceAnalysisTraceLayouts (values not read; 15 KB). `portable-state.json`: `safeToCopy=false` ("Coach folder is active"), `credentialsIncluded=false`. `archive-manifest.json`: archiveId `f65f85bf4522446d94dbdc6a2b0a218b`, appVersion 0.17.0, updated 2026-10-02 14:36Z.

| Store (under `data\` unless noted) | Files | Size |
|---|---|---|
| reports (156 .json = 6519 MB, 295 .md, 458 .svg; folders `season-<id>`) | 909 | 6528.6 MB |
| telemetry-traces\raw (35 `.ibt` + 35 `.json`, each in a sha256-named subfolder) | 70 | 4071.2 MB |
| telemetry-traces\live-replay (1554 `.ircr2` + 782 `.json`) | 2336 | 300.4 MB |
| analysis-cache | 63 | 1194.8 MB |
| ui-analysis-cache | 9 | 123.4 MB |
| test-artifacts (corner-tire-age, handoff-analyze: own history.sqlite3 + reports) | 29 | 7.0 MB |
| season-cache | 18 | 3.4 MB |
| tuning (`catalog\index.json` 1.26 MB, `inputs`, `packages\2026s3\<car>\<track>\setup-*.json`) | 7 | 1.3 MB |
| track-geometry | 10 | 1.0 MB |
| strategy-history | 5 | 0.3 MB |
| tire-models | 15 | 0.2 MB |
| telemetry-events | 10 | 0.1 MB |
| telemetry-profiles | 4 | ~0 |
| activity-history | 43 | ~0 |
| archive (1 file), auth (1 file: `garage61-api-request.json`, 366 bytes, credential-adjacent: do not read or copy) | 1 + 1 | ~0 |
| setup-history, target-laps, driver-models, garage61, race-index, ai-coaching, tuning-experiments | 0 | 0 |
| `data\history.sqlite3` (no -wal/-shm present) | 1 | 708,608 bytes, modified 2026-09-20 22:54 |

Outside `data\`: `exports` 84 files 559.7 MB; `research` 13 files 0.3 MB; `logs` 1 file; `portable-settings` 1 file; `backups`, `setups`, `user-library` empty (0 files); several `track-map-*` asset folders (not sized).

Findings relevant to D12:
- Only 35 raw `.ibt` are archived (4.07 GB) against 617 `.ibt` in iRacing's telemetry folder. Re-ingest source for the other 582 is only the iRacing folder itself, which holds just the last week (see section 2).
- 909 report files and 6.5 GB of `analysis.json`-class JSON exist, but `history.sqlite3` is only 708 KB and last written 2026-09-20 (older than the newest reports, whose dates were not checked: UNKNOWN whether the DB is current).
- User-authored data (notes, saved Tests, plans): no file name under `data\` matched note/saved/plan/test-day/library/annotation (excluding reports and caches); `setup-history`, `target-laps`, `user-library` are empty. The store that holds them is UNKNOWN (probably inside `history.sqlite3` or `activity-history`/`tuning`; not opened because opening SQLite could write a journal). The architect should have a worker read tables from a copy.
- `coachThreadIds` in settings.json suggests Codex thread IDs are stored; credentials are not (not read).

## 4. NAS

- `Test-Path \\192.168.1.82\apps` = True (session user joshu, non-interactive job). TCP 445 to 192.168.1.82 reachable (`Test-NetConnection`).
- `Test-Path \\192.168.1.82\apps\iracing-coach` = False (folder not created, as the brief says).
- Mapped drives (`net use`, `Get-SmbMapping`): only `J:` -> `\\192.168.1.82\Joshua`, status OK, remembered. No mapping for `apps`.
- The `apps` check worked without a mapped drive, so the logged-in user has an existing credential or guest access for UNC (mechanism UNKNOWN). Write permission on `apps` was NOT tested (read-only rule).
- Prior working route (memory): `J:\iRacing Temp\claude-drops\` for bulk drops.

## 5. Startup and running processes

HKCU Run: Trading Paints (`min`), Discord, SimHub (`windowsstartup`), Stream Deck, Volume Controller SD plugin. HKLM Run: SecurityHealth, RtkAudUService, Logitech Download Assistant x2. Startup folder: `Garage 61 Telemetry Agent.lnk` (currently running: `garage61-launcher`, `garage61-agent` from `%APPDATA%\garage61-install`). No iRacing Coach Run key, no Coach startup shortcut, no Coach scheduled task.

Non-Microsoft scheduled tasks: D3DGearRawFrameCaptureTask (iRacing's), NVIDIA App SelfUpdate, `RemoteEngineerHome-S-1-5-21-...-1001` (runs `Start-Target.ps1 -Managed` at logon: the remote agent), PowerToys autorun, `Simagic_simdaemon_AutoRun`, SoftLanding tasks.

Coach processes running now: none (no process matched coach/ibt/sabine; `RemoteEngineer` x2 = the agent). Related sim-side software already running, relevant to the collector's footprint: PowerToys (several processes), SimHub (autostart; not running at snapshot), Trading Paints, Discord, Stream Deck, Elgato, 1Password, Garage61 agent, `QtWebEngineProcess`, `GameInputSvc`, `hasplms`/`hasplmv` (HASP dongle licence services), `EasyTuneEngineService`.

`settings.json` has `launchAtSignIn` (value not read), but no Coach autostart artifact exists, so the old app is not configured to start with Windows.

## 6. Remote tool surface (remote-engineer plugin 2.0.4)

Exercised: `remote_machines`, `remote_status`, `remote_job` (processes, start, status, cancel). All else is from tool descriptions and marked (inferred).

| Tool | Does | Limits seen / documented |
|---|---|---|
| `remote_machines` | Lists enrolled targets and machineId | one target: DESKTOP-SIM, endpoint `https://192.168.1.21:47653/` |
| `remote_status` | Returns current sessionId, epoch, permissions, capabilities | sessionId/epoch must be passed on every call; desktop reported unavailable at snapshot while jobs/files worked |
| `remote_job` | Start/status/stdin/closeInput/resize/cancel/forget/processes; absolute exe path, arg array, no shell; `terminal:true` = ConPTY; `gui:true` = interactive session | observed; at most 64 running jobs, history 128 jobs / 24 h; jobs survive bridge disconnect but NOT agent restart/reboot; output is cursor-paged and a runaway job returned ~10 MB in one status reply (bound output by design); no desktop lease needed |
| `remote_file` | List/read/search/write/patch on the sim, absolute paths, sha256 checks (inferred) | read max 1 MiB chunks, write max 8 MiB, list max 500, search streams files up to 64 MiB; not exercised |
| `remote_copy` (+ `remote_transfer`, `remote_copy_recovery`) | Copy files parent<->sim, verified bytes (inferred) | up to 16 GiB, 600 s bulk timeout, needs fresh GUID copyId; memory: fails with `transfer_limit` after 8 staged transfers on the parent side, no close tool; prefer the NAS drop route for bulk |
| `remote_observe` / `remote_windows` / `remote_window` / `remote_monitors` | Screenshot/list/position windows (inferred) | needs desktop lease; GDI capture; fails if the Sabine viewer on the main PC is minimised (memory); not exercised |
| `remote_gesture` / `remote_control` / `remote_accessibility` / `remote_clipboard` | Mouse/keyboard input, UIA queries, clipboard (inferred) | lease and a fresh observation per gesture; max 256 steps / 10 s; not exercised |
| `remote_session` / `remote_connect_windows` | Hidden RDP connect/status/show/hide/disconnect; parent-side | never `hide` the viewer (OBS captures it, memory); not exercised, not needed this run |
| `remote_request_status` / `remote_request_cancel` | Recover or cancel a request by id | not exercised |

Job tool quirks to give workers: pass `-NoProfile`, reset `PSModulePath` to `C:\Windows\system32\WindowsPowerShell\v1.0\Modules`, set `workingDirectory` explicitly, always bound recursive scans (a scan of `C:\Users\joshu` was cancelled; `-Include` with `-Recurse -LiteralPath` ignored the filter and dumped the tree).

## Unknowns

- Real GPU VRAM; whether the 1 SATA/NVMe has other volumes (only C: and J: seen).
- Where notes/saved Tests/plans live (needs a read of a copy of `history.sqlite3`); whether `history.sqlite3` (last write 2026-09-20) is current.
- `launchAtSignIn` value; NAS `apps` write permission and credential mechanism.
- iRacing SDK shared-memory behaviour, vJoy/SimHub CPU cost at idle or on track, power plan, network link speed of the sim NIC (not measured).
