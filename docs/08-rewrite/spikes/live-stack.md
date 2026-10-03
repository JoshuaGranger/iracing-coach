# Spike: C# live stack inventory (for the Rust collector port)

Paths are under `companion-app/src/` (CS = `iRacingCoach.Coordinator/`, APP = `iRacingCoach.App/`, CON = `iRacingCoach.Contracts/Models.cs`). Facts are from reading source. Anything labelled **inferred** was not observed; **UNKNOWN** could not be determined. No build or test was run.

## 1. SDK reading (`CS/IRacingSdkTelemetrySource.cs`, 484 lines)

**Mapping and events**
- Memory map `Local\IRSDKMemMapFileName`, read-only `MemoryMappedFile.OpenExisting` (:10, :191), whole-map view (:192), open retry at most once per second (:187).
- **No `IRSDKDataValidEvent` and no event wait.** Updates are found by polling the buffer tick.

**Header (Int32 offsets)**: status @4 (bit0 = connected), tickRate @8, sessionInfoUpdate @12, sessionInfoLength @16, sessionInfoOffset @20, numVars @24, varHeaderOffset @28, numBuf @32 (clamped 0..4), bufLen @36; buffers from @48, stride 16: tick @+0, offset @+4 (:35-54). Header 112 bytes, var header 144 (:11-12).
- Bounds: vars 1..16384, varOffset >= 112, bufLen > 0, else `InvalidDataException` -> Close -> disconnect (:42-43, :176).
- Buffer choice: highest tick among buffers with `offset >= 112 && offset+bufLen <= capacity` (:55); skip when tick equals last tick (:61). **No tick-before/after re-check (no torn-read guard).**
- Variable table re-read only when numVars or offset changes (:44). Entry: type @0, offset @+4, count @+8, name @+16 (32 bytes ASCII, NUL-trimmed) (:213-225). Types 0 char, 1 bool, 2 int, 3 bitfield, 4 float, 5 double; widths 1/1/4/4/4/8 (:462). Unit and description are not read.

**Poll rate and timers** (`CS/LiveTelemetry.cs`): connected 2 ms, disconnected 40 ms (:155-156); dedicated thread "iRacing Coach telemetry reader" on a high-resolution waitable timer (`CreateWaitableTimerEx` flag 0x2), falling back to normal timer then `Thread.Sleep` (:222-255, :603-651). Effective rate = SDK tick rate (carried as `TickRate`; docs say designed for up to 240 Hz, `docs/02-workflows/live-telemetry-and-monitor.md:68`). Dropped frames = tick deltas > 1, unsigned wrap-safe (:435-437). No new sample for > 1 s publishes a status update; snapshot older than 2 s -> "Live telemetry became stale." disconnect (:346-353).

**Disconnect detection**: status bit0 clear, map missing, or IO/Unauthorized/ArgumentOutOfRange/InvalidData exception -> `Close()` clears every cache (:176-179, :463-479); disconnected publishes rate-limited to 1/s (:200). **Session boundary** (`LiveTelemetry.cs:377-403`): was not connected, timestamp went backwards, lap decreased, SessionUniqueID/SubsessionId/SessionNum changed, or normalized session phase (qualifying/race/practice/warmup) changed. Each bumps `_sessionEpoch`, resets the engine and history.

**Session-info YAML**: re-read only when the header update counter differs (:232-234); that check runs before the tick check. Read as UTF-8 up to first NUL, max 16 MiB (:235-240). **Not a YAML parser; regexes only.** Fields parsed:
| Field | Rule | Line |
|---|---|---|
| `SubSessionID` | `^\s*SubSessionID:\s*(\d+)` | :241 |
| `Sessions[].SessionNum` -> `SessionType` | block regex; quotes stripped; empty and `~` ignored | :253-267 |
| `CarSetup` steering ratio | lines under top-level `CarSetup:`; every key whose letters/digits equal `SteeringRatio`; value `n:1` or bare number in [4,40]; null if absent, unreadable, or two different values | `CS/LiveSteering.cs:36-58` |
| `DriverInfo.Drivers[]`: `CarIdx`, `CarNumber`/`CarNumberRaw`, `CarClassID`, `CarClassShortName`, `CarScreenName`/`CarPath`, `UserName`/`AbbrevName`, `TeamName`, `IsSpectator` | per `- CarIdx:` block regex; quotes stripped; `~`/empty null | :269-305 |
Nothing else (no CarSetup values, no `DriverSetup*`, track, weather) is read live.

**Variables read** (best buffer; missing -> null, never zero; array variables return empty unless the stated type):
| Variable | Type | Use |
|---|---|---|
| PlayerCarIdx | numeric | player index |
| CarIdxPosition, CarIdxClassPosition, CarIdxLap, CarIdxLapCompleted | int[] | positions, gaps, replay |
| CarIdxLapDistPct, CarIdxF2Time, CarIdxLastLapTime, CarIdxBestLapTime | float[] | field, gaps, leader last lap |
| CarIdxOnPitRoad | bool[] | replay |
| CarIdxTrackSurface, CarIdxPaceFlags | int[] | replay |
| PlayerCarPosition, PlayerCarClassPosition | numeric | position (fallback array at player index) |
| SessionFlags | int or bitfield only | flag, caution, black, repair |
| Lap | numeric | `Lap = raw+1`, min 1 (:118) |
| SessionLapsRemainEx, SessionLapsRemain | numeric | laps remaining (Ex first) |
| PlayerCarTowTime | numeric | tow = value > 0 |
| LapLastLapTime | numeric | last lap (> 0) |
| FuelLevel (L), FuelLevelPct (0..1) | numeric | fuel |
| TrackTempCrew else TrackTemp (C), AirTemp (C) | numeric | temps |
| dcBrakeBias | numeric | brake bias % |
| OnPitRoad | bool only | pit road |
| PitRepairLeft, PitOptRepairLeft (s) | numeric | repair timers |
| SteeringWheelAngle (rad), Throttle, Brake (0..1) | numeric | inputs |
| BrakeABSactive | bool, else numeric > 0.5 | ABS |
| BrakeABScutPct | numeric 0..1 | ABS cut |
| Gear, RPM, YawRate (rad/s), Speed (m/s) | numeric | |
| LatAccel, LongAccel (m/s^2) | numeric | / 9.80665 -> g (:149-150) |
| LapDistPct | numeric | kept only in [0,1] |
| Lat, Lon, SessionTime, SessionState, SessionUniqueID, SessionNum | numeric | position, identity |
| PlayerCarMyIncidentCount (fallback PlayerIncidents), PlayerCarDriverIncidentCount, PlayerCarTeamIncidentCount, PlayerTrackSurface | numeric | incidents, surface |
- Gap = `CarIdxF2Time` difference only when both cars have equal `CarIdxLap` and diff >= 0 (:429-434). Leader = overall position 1; ahead/behind = class position -1/+1.
- `LapsRemainingOrUnknown`: null for null, NaN, < 0, >= 32767 or >= 10000; else `ceil`, min 0 (:442-456).

## 2. Live calculations, tiles and cues

### 2.1 Truth policy (`CS/LiveTruthPolicy.cs`)
First match wins on SessionFlags: 0x00020000 disqualified; 0x00010000 black; 0x10 red; `CautionMask 0x0000C308` caution; 0x1 checkered; 0x2 white; (0x4 or 0x80000000) green; else racing; null = unknown. Repair = 0x00100000. Display: DISQUALIFIED, BLACK FLAG, RED, CAUTION, CHECKERED, WHITE, GREEN, RACING, UNKNOWN. Lap distance valid only in [0,1]. Python mirror `iracing-coach/skills/analyze-iracing-race/scripts/live_truth.py`; vectors `test-data/live-truth-conformance-v1.json` (53 flag vectors, 8 lap-distance vectors, plus `clean_lap_vectors`, `session_flag_bits`, `racing_state_precedence`, `clean_lap_thresholds`, `dotnet_legacy_caution_mask`, `caution_bits_missing_from_dotnet`).

### 2.2 Engine rules (`LiveTelemetryEngine`, `CS/LiveTelemetry.cs:654-995`)
- Session-confounded = flags unknown, caution, pit road, repair flag, tow or black flag. Input-confounded = session-confounded or gap ahead < 1.5 s or gap behind < 0.75 s (:857-859).
- Lap "fully observed" only after a sequential +1 lap from a lap observed from its start; the first lap after connect or reset is partial (:687-702). Clean lap = fully observed, never confounded, boundary sample not confounded (:871-873).
- Green-run counter +1 on clean lap not on pit road; caution-run counter +1 on observed caution lap; both reset on pit-road entry (:850-854, :876-883). Run phase: green laps < 5 "Early run", < 20 "Middle run", else "Late run" (:712).
- Clean lap times: bounded queue of 8, `LastLapSeconds` in (0,3600) (:885). Fuel per lap: queue of 8 of start-minus-end fuel in (0.05, 20) on clean laps (:888-893). Median averages the two middle values for even counts.
- Pace band: needs >= 3 clean laps; baseline = median; half band = max(0.15 s, 0.5% of baseline); Derived, confidence 0.72 (:791-798).
- Fuel: average needs >= 2 samples; `FuelLapsRemaining = max(0, fuel/avg)`; hard limit laps = `max(0, floor((fuel - min(0.5, 0.5*avg)) / avg))` (:800-818).
- Gap (`BuildGap` :771-789): unavailable if value missing/negative, flags unknown, caution or pit road (Stale when value present); otherwise 8 s history; trend needs >= 2 samples spanning >= 3 s: delta < -0.15 Closing, > +0.15 Growing, else Stable.
- Pace delta = LastLap - LeaderLastLap. Penalty string: "Unavailable" (flags unknown) / "Black flag" / "None recorded" (:751). Track-temperature delta vs first connected sample.
- **Braking cue** (:898-949): 20 zones, `zone = clamp(int(lapDist*20),0,19)`, peak Brake per zone per lap; only on fully observed, input-clean laps. Per-zone baseline = last 8 peaks. Zone counts when baseline >= 3 and median >= 0.35 x strongest zone median. Band = max(0.08 x median, 3 x median(|v - median|)). If peak - median > band: streak +1 and the peak is not added to baseline; else streak = 0 and the peak is added. Cue when streak >= 3 (largest excess/median wins): `Braking zone near {zone*5}% lap: peak {excess/median:P0} above your clean baseline on {streak} comparable laps`, Coaching, Derived, 0.72. The result replaces the persistent cue on every eligible lap (null when none).
- Reset (`ResetSessionState` :957-978) clears queues, baselines, cue, counters; on disconnect after connected, lap decrease, and service-level session boundary.

### 2.3 Cues (`BuildCue` :820-835, first match wins)
Priority (CON:968): Information 0, Environment 10, Coaching 20, Pace 30, Traffic 40, PitService 50, Strategy 60, Critical 100.
| # | Trigger | Message | Priority | Evidence |
|---|---|---|---|---|
| 1 | black flag | "Black flag: follow the recorded penalty instruction" | Critical | Measured |
| 2 | towing | "Tow active" | Critical | Measured |
| 3 | mandatory repair > 0 | "Mandatory repair: {ceil} s remaining" | Critical | Measured |
| 4 | fuel hard limit <= 1 | "Fuel hard limit: pit now if rules permit" | Critical | Derived |
| 5 | fuel hard limit <= 3 | "Fuel hard limit: {n} laps" | Strategy | Derived |
| 6 | flags unknown | "Race flag unavailable: flag-dependent coaching paused" | Information | Unavailable |
| 7 | caution | "Caution: competitive gap trends paused" | PitService | Measured |
| 8 | pit road | "Pit road: confirm service and speed" | PitService | Measured |
| 9 | ahead gap < 1.0 s and Closing | "Gap ahead closing: {x.xx} s" | Traffic | Derived |
| 10 | last lap > pace max + 0.3 | "Last lap outside the clean in-session target band" | Pace | Derived |
| 11 | persistent braking cue | see 2.2 | Coaching | Derived |
| 12 | no pace band | "Building your pace range · complete three clean laps" | Information | Unavailable |
| 13 | else | "No action: pace and race state within the supported band" | Information | Derived |
Confidence 0.8 (0 if Unavailable). Disconnected cue: "Waiting for iRacing". Basis string (e.g. "PlayerCarTowTime", "Fuel-use history", "8-second scoring trend") is the tile secondary text.

**Safe-glance hold** (:715-735): straight = Brake < 0.05 and `NearCenter` and |LatAccel g| < 0.35; missing inputs never count as straight. Opportunity = caution or pit road or lap just advanced or straight. Urgent = candidate priority >= Strategy (60). If safe-glance enabled, not paused, no opportunity, not urgent, and a cue was already displayed, the previous cue stays (suppression SafeGlanceDelay); otherwise the candidate becomes the displayed cue. Pause (tray) replaces cue with "Live coaching paused" (Information, Measured), toggle not persisted (`CompanionState.cs:3068-3072`). NearCenter: road-wheel abs < 0.12/12 rad; without ratio wheel abs < 0.12 rad (`LiveSteering.cs:67-71`). Road wheel = wheel / ratio when ratio > 0 (:60).

### 2.4 Tiles (`CS/LiveTelemetryCatalog.cs` `Build()` :222-260; 33 ids)
Src: R recorded, C calculated, Co coach. Unit list, default unit first. Precision clamp 0-3. All tiles allow Number/Gauge/Bar/Trend; coach-cue, flag, on-pit-road, tire-phase also allow Status.
| id | Label | Src | Formula | Units | Default style, prec | Range |
|---|---|---|---|---|---|---|
| air-temperature | Air temperature | R | AirTemp | °C, °F | Number, 1 | |
| ahead-gap | Gap ahead | C | same-lap F2Time delta to class P-1 | s | Number, 2 | min 0 |
| behind-gap | Gap behind | C | delta to class P+1 | s | Number, 2 | min 0 |
| brake | Brake | R | Brake x100 | % | Number, 0 | 0-100 |
| brake-bias | Brake bias | R | dcBrakeBias | % | Number, 1 | 40-70 |
| class-position | Class position | R | `P{n}` | | Number, 0 | min 1 |
| coach-cue | Coach cue | Co | cue message; secondary = basis; numeric = priority | | Status, 0 | 0-100 |
| flag | Flag state | R | display string; step ordinal Green0 Blue1 White2 Checkered3 Yellow4 Black5 Red6 Other7 (substring match) | | Status, 0 | 0-7 |
| fuel | Fuel remaining | C | FuelLiters (x0.2641720524 for US gal); secondary "{x.x} estimated laps" or "Lap estimate needs clean fuel-use evidence"; max = value / FuelLevelPct when pct > 0.0001 | US gal, L | Bar, 1 | min 0 |
| fuel-laps | Fuel laps remaining | C | median burn | laps | Number, 1 | min 0 |
| gear | Gear | R | Gear | | Number, 0 | -1..12 |
| lap | Lap | R | raw Lap+1 | | Number, 0 | min 0 |
| laps-remaining | Laps remaining | R | sentinel rule | laps | Number, 0 | min 0 |
| last-lap | Last lap | C | LapLastLapTime; secondary `{+0.000;-0.000} s vs leader last lap` | time | Number, 3 | min 0 |
| lateral-acceleration | Lateral acceleration | R | LatAccel/9.80665 | g, m/s² | Trend, 2 | |
| leader-gap | Physical gap to leader | C | overall P1 shows "Leader" (0); else same-lap delta | s | Number, 2 | min 0 |
| leader-last-lap | Leader last lap | R | CarIdxLastLapTime[P1] | time | Number, 3 | min 0 |
| longitudinal-acceleration | Longitudinal acceleration | R | LongAccel/9.80665 | g, m/s² | Trend, 2 | |
| mandatory-repair | Mandatory repair | R | PitRepairLeft | s | Number, 0 | min 0 |
| on-pit-road | Pit-road state | R | "On pit road"/"On track" | | Status, 0 | 0-1 |
| optional-repair | Optional repair | R | PitOptRepairLeft | s | Number, 0 | min 0 |
| pace-range | Personal pace range | Co | `{min}–{max}` clean band; trend = midpoint | time | Number, 3 | min 0 |
| pit-window | Laps to pit | Co | window opens–closes if known (never set today), else fuel hard limit | laps | Number, 0 | min 0 |
| position | Position | R | `P{n}` | | Number, 0 | min 1 |
| rpm | RPM | R | RPM | rpm | Trend, 0 | min 0 |
| speed | Speed | R | Speed m/s x 2.2369362920544 = mph | mph, km/h (x1.609344) | Trend, 1 | min 0 |
| steering | Steering (road wheel) | C | wheel / ratio; secondary "{ratio}:1 steering ratio"; no ratio -> "Ratio not recorded" (unavailable; wheel angle never shown under this name) | deg (x57.29577951308232), rad | Trend, 1 | |
| steering-wheel-angle | Steering wheel angle | R | SteeringWheelAngle | deg, rad | Number, 0 | |
| throttle | Throttle | R | Throttle x100 | % | Gauge, 0 | 0-100 |
| tire-phase | Run phase | Co | Early/Middle/Late run, step 0-2 | | Status, 0 | 0-2 |
| track-temperature | Track temperature | R | TrackTempCrew else TrackTemp | °C, °F | Number, 1 | |
| yaw-rate | Yaw rate | R | YawRate x57.29577951308232 | deg/s, rad/s | Trend, 1 | |
Rules: not connected -> "Waiting"; missing -> "Not measured"; reason starting "Insufficient" -> "Insufficient evidence". Dynamic range = semantic range else observed min/max with 8% padding (:375-393). Lap format `m:ss.fff` (>= 60 s) else `s.fff`. Trend windows 15/30/60 s, 1 lap, 3 laps (default 30 s). Step shape: class-position, coach-cue, flag, gear, lap, laps-remaining, last-lap, leader-last-lap, on-pit-road, pit-window, position, tire-phase.
History: one `LiveTracePoint` per sample; queue max 36,000 points and 10 min; UI snapshot copy at most every 100 ms (`LiveTelemetry.cs:157-158, :332-336`). Seed projection above 4096 points keeps first/last, min, max and first missing per bucket (`maximumPoints/5` buckets) (`Catalog:114-176`).
`HighlightAbsIntervention` exists in CON:1021 but is **unused by the WPF monitor** (grep); only `UI/LiveTelemetryLayoutGrid.razor` uses it.

### 2.5 Layouts (`CS/LiveMonitorLayouts.cs`)
- Grid 1-8 x 1-8; default 2 rows x 3 columns.
- **Default** (immutable `factory-default`): position(Number), last-lap(Number), leader-gap(Number), fuel(Bar), pit-window(Number), coach-cue(Status).
- Seeded once as user layouts (flag `BuiltInDashboardsInitialized`): **Race** `factory-race`: position, ahead-gap, behind-gap, fuel(Bar), pit-window, coach-cue(Status). **Qualifying** `factory-qualifying`: last-lap, pace-range, speed(Trend), brake(Trend), rpm(Trend), coach-cue(Status).
- Deterministic packing `TryPack` (order: placed tile, row, column, id; first fit; atomic rollback). `ValidateAndRepair` falls back to Default and repairs duplicate ids, invalid style/unit/precision/trend/accent. Accents: default, blue, green, amber, coral, violet. Editing happens only in the full app; the monitor is display-only (LMG-014).

### 2.6 Settings affecting live behaviour
`CON:976-995`: Visible, ActiveLayoutId, IsLocked (true), UserLayouts, BuiltInDashboardsInitialized, **SafeGlanceEnabled (true)**, **ReopenOnConnect (false)**, HistoryLaps (3; no consumer found, UNKNOWN use), GlobalHotkey (empty). `CompanionSettings` (CON:852-): LaunchAtSignIn, UseReducedMotion, ThemeColor "mint", CustomThemeColor "#5CE8C3", IRacingRoot `Documents\iRacing`, CoachHome `Documents\iRacing Coach`.
- Logical/portable (in `<CoachHome>\settings.json`): layouts, SafeGlance, ReopenOnConnect, HistoryLaps, GlobalHotkey text, theme, reduced motion, LaunchAtSignIn.
- Machine-local (`%LOCALAPPDATA%\iRacingCoach\machine-settings.json`, `CS/SettingsStore.cs:43-44, :228-287`): Left, Top, OverallScale (0.7-2), MonitorDeviceName, PlacementRecoveredAt. Legacy migration of left/top/width (scale = width/560) at :372-376. Main window placement in `<LocalStateRoot>\main-window-placement.json`.
- Auto-reopen when connected, `ReopenOnConnect`, monitor hidden and not suppressed; shows without activation. A user hide while connected suppresses reopening until the next disconnect (`CompanionState.cs:3060-3061, :4544-4562`).

### 2.7 Golden-vector candidates (tests in `companion-app/tests/iRacingCoach.Tests/`, MSTest; counts from grep of `[TestMethod]`)
| File | Methods | Value |
|---|---|---|
| `LiveTruthPolicyConsumerTests.cs` | 2 | Fixture-driven (`test-data/live-truth-conformance-v1.json`) plus missing-flag engine case. Best ready golden file. |
| `CoordinatorTests.cs` :2421-2808 | 12 `LiveTelemetry_*` engine tests (critical priority and urgent override; rolling gap trend and caution; hard limit vs window; three clean laps; safe-glance delay; missing dynamics not a straight; fully observed clean laps; run-phase reset on pit road; run-phase green laps; braking cue baseline and persistence; repair-confounded laps; disconnect clears baselines) | Behaviour vectors for the engine (sample sequence -> snapshot/cue). |
| `LiveMonitorTests.cs` | 32 | Sentinel vectors (:11-25), layout ops, catalog (6), trend projection, settings round trip, repair. Many are source-text or markup assertions about WPF/Blazor; not portable. |
| `SteeringBasisTests.cs` | 17 (5 live: ratio parsing :221, CarSetup-only :233, road wheel :246, tiles :271, engine :344) | Ratio vectors; Python mirror `iracing-coach/tests/test_steering_basis.py` (13). |
| `LiveReplayCaptureOverhaulTests.cs` | 13 (+7 DataRow) | `.ircr2` codec; not v1. |
No test drives `IRacingSdkTelemetrySource` through a mock memory map; only `LapsRemainingOrUnknown` is tested. **No SDK byte fixture exists** (UNKNOWN whether any recorded buffer is in `test-data/`).

## 3. Monitor window and shell

- **Rendering** (`APP/LiveMonitorWindow.xaml`, 151 lines; `.xaml.cs`, 1252 lines): WPF element tree, retained visuals updated in place. A dirty flag is set on each live update; repaint happens in `CompositionTarget.Rendering` (display refresh), only when visible and the size popup is closed (`.cs:122-149`). Trend tiles keep their own buffers (gap threshold `max(40 ms, 3500/tickRate)`, :1030) and animate between samples unless `UseReducedMotion`.
- **Geometry**: grid viewport fixed 540 x 360 (:28-29, xaml:133); strip 56 px; `SizeToContent=WidthAndHeight`; scale via `LayoutTransform`, presets Compact 0.8, Standard 1.0, Expanded 1.25 (xaml:141-143). Tile is compact below 132x116, dense below 88x64 (header hidden); min font 11.67; styles fall back to Number below 62x46. Number font `min(32,max(17,h*.43))`; Status `min(18,max(12,h*.23))`; bar track 9 px; gauge diameter `clamp(min(w,h)-2,34,108)`, stroke `clamp(size*.085,4,8)`, arc fraction up to 0.9999; bar and gauge ranges only widen until reset.
- **Window flags**: `WindowStyle=None`, `ResizeMode=NoResize`, `Topmost=True`, `ShowInTaskbar=True`, rounded corners via DWM attribute 33 (xaml:6, .cs:948-953). `ShowActivated=false` for auto-open (:77). **Not click-through, not a tool window, no WS_EX_NOACTIVATE or layered style** (only `SetWindowPos`, `GetWindowRect`, `DwmSetWindowAttribute` P/Invokes). Drag via strip (`DragMove`, :848-855). Close hides, not destroys (:97-102). Docs: works over borderless-windowed iRacing; exclusive fullscreen may cover it (`CompanionState.cs:3551`).
- **Placement**: restore saved Left/Top on Loaded, clamp into the work area; else 24 px inside the top-right of the saved monitor (by `MonitorDeviceName`), then nearest screen, then primary (:877-926). Save debounced 500 ms after `LocationChanged` (:57, :930-946).
- **Global hotkey** (`APP/MainWindow.xaml.cs:414-449`): `RegisterHotKey` on the main window, id 0x4937, `WM_HOTKEY` 0x0312, MOD_NOREPEAT 0x4000 always; text `+`-joined, modifiers `ctrl|control`, `shift`, `alt` only, at least 2 parts; key = one letter/digit or F1-F24. Action = toggle monitor. Re-registered on settings save; failure toast.
- **Tray** (`MainWindow.xaml.cs:67-83, :340-344`): WinForms `NotifyIcon`; menu: Open iRacing Coach, Show telemetry popout, status line (disabled), Pause live coaching, Settings, Exit. Left click opens main window. Tooltip "iRacing Coach · Connected · Race L{n}" (or flag) / "... Waiting for iRacing", max 127 chars. Tray tooltip update interval not read (UNKNOWN). Exit runs a single guarded shutdown with a process-level deadline (`RequestExit`/`DisposeApplication` :346-388).
- **Single instance**: mutex `Local\iRacingCoach.SingleInstance` (+ suffix) (`App.xaml.cs:10, :37`).
- **Autostart** (`APP/StartupRegistration.cs`): `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `iRacing Coach`, data `"<exe>" --minimized`; removed when off; only when host profile allows machine integration. `--minimized` hides the main window on load (:118-119).

## 4. File discovery

- **Watcher** (`CS/CompanionState.cs:3491-3526`): one `FileSystemWatcher` on `Settings.IRacingRoot` (`Documents\iRacing`), `IncludeSubdirectories`, filter `FileName | LastWrite | DirectoryName`, handlers Created/Changed/Renamed/Deleted. Paths with an extension other than `.ibt/.sto/.htm/.html` are ignored (extensionless paths pass). A 2 s reset timer then runs `RefreshDataAsync(false)` (full inventory refresh through the backend, :575-809). Reconfigured at the end of each refresh when the root changes (:787). **No finalization logic, no per-file handling, no copy, no hash, no share-mode handling in C#.**
- **Finalization rule** (`iracing-coach/skills/analyze-iracing-race/scripts/workflow.py:1254-1295`, `_assert_finalized_ibt`): reject if `record_count <= 0`; reject if `file_size != buffer_offset + record_count * buffer_length` (a larger file means iRacing appended a partial record before updating the disk subheader); reject if `now - mtime < 2.0 s` (`_IBT_QUIET_SECONDS`). Skipped when metadata lacks integer fields. Called at :1387, :2067, :2483, :2726. Also a source-changed guard: `(size, mtime_ns)` must be unchanged between read and persist (:2204-2211).
- **Inventory suffixes** (`workflow.py:661-699`): `.ibt`; replays `.rpy`; setups `.sto/.htm/.html`; lap files `.olap/.blap/.lap` (or any path under `lapfiles`); configs `.ini/.cfg`. Records: path, relative path, size, mtime. `.sto` root = `<documents>/setups` (rglob `*.sto`, :1836-1857). `.sto` and `.htm` are inventoried and name-linked; **never copied or decrypted**. Library `.sto` values are unknown except via an exported `.htm` or a recording that loaded the file unchanged (`setup_recordings.py:11-16`, `NO_VALUES_REASON`).
- **Hashing**: SHA-256 of an IBT only when the user locates a file by hand (`CompanionState.cs:3226-3257`). Backend identifies files by size + `mtime_ns`; derived-data hashes are content hashes.
- **Pause while live**: `WaitForHomeAnalysisWindowAsync` polls every 250 ms while `AnalysisLoading || TestDayLoading || ReportStorageBusy || LiveState.Snapshot.Connected` (`CompanionState.cs:4269-4283`); top bar text "Analysis paused while iRacing runs · {n} left" (`UI/AppTopBar.razor:22`); test `HomeRefresh_PausesBackgroundAnalysisWhileLiveTelemetryIsConnected` (`CoordinatorTests.cs:1764`). Only background analysis pauses. The watcher-triggered inventory refresh does not check live state (:3516-3526), so it still runs while connected (by code, not observed).
- Backend also lists iRacing `app.ini` SDK keys as safe to report (`irsdkEnableMem`, `irsdkEnableDisk`, `irsdkLogSetup`, `irsdkUtf8SessionStr`, ...), `workflow.py:702-714` (partial read).

## 5. Live replay capture (`.ircr2`)

- **Trigger**: each connected sample on the reader thread, throttled to about 60 Hz (`ceil(tickRate/60)` ticks per capture, `LiveTelemetry.cs:457-466`): every tick at 60 Hz, every 4th at 240 Hz. Frame = session identity, 18 player fields, 10 fields for each car, coverage, participants, derived events (incidents, track surface, pit road, tow, repair, session flags) (:472-585).
- **Writer**: bounded channel of 2,048 frames; a failed `TryWrite` drops and counts; one writer task (`LiveReplayCaptureStore.cs:16, :56-63, :118-129`). Chunk flush at 1,200 frames or 10 s (:20, :202).
- **Format**: magic `IRCRPLY2`, 56-byte header, gzip (`Optimal`) of delta-binary frames with bitmask field presence; every chunk starts with full state; limits 64 MiB compressed, 128 MiB raw (`LiveReplayChunkCodec.cs:13-52, :86-90`). Python decoder `live_replay_v2.py:189`.
- **Disk**: `<CoachHome>\data\telemetry-traces\live-replay\<sessionKey>\chunk-NNNNNN.ircr2` + `manifest.json` (SHA-256 per chunk), atomic temp+rename with `stream.Flush(true)` (`:208-215, :331-366, :556-568`). **While on track that is a forced-flush chunk write at least every 10 s plus a manifest rewrite.**
- **Sizes: UNKNOWN.** `Documents\iRacing Coach\data\telemetry-traces\live-replay` is absent on the main PC (`Test-Path`). Sim PC not checked.
- **Consumers**: Python `storage.py:3479-3560` loads SHA-verified chunks matching subsession, session number and phase and merges them as `race_replay` when the IBT has none or fewer frames (`workflow.py:2213-2220`); C# `ReplayManifestConsumer.cs`, `RuntimeMapper.cs`, `UI/RaceReplayWorkspace.razor`, `UI/AnalysisWorkspacePage.razor` (Replay tab, not v1). Its unique value is rival positions on track; none of Race/Runs/Sections/Traces, Live monitor or Setups reads it (inferred from grep).

## 6. Garage capture feasibility

**Where setup fields are read today**
| Field | C# | Python |
|---|---|---|
| `CarSetup` | only `SteeringRatio` via `LiveSteering.RatioFromSessionInfo` (`CS/LiveSteering.cs:36`); `SetupReadings.cs`, `SetupLibrary.cs` consume backend JSON | `analysis_engine.py:1309`, `setup_recordings.py:146`, `setup_catalog.py:774-782`, `steering_basis.py:174` (from IBT session info) |
| `DriverSetupName` | none (comment `SetupLibrary.cs:9`) | `analysis_engine.py:1376`, `setup_recordings.py:149`, `ibt_reader.py:819` |
| `DriverSetupIsModified` | none | `setup_recordings.py:143`, `analysis_engine.py:1378`; **`ibt_reader.py:820` reads `DriverSetupModified`**, with `setup_recordings.py:144` keeping that spelling only as a fallback for older synthetic fixtures. Likely a latent defect leaving `driver_setup_modified` null for real recordings (inferred, not checked against a real IBT). |
| `DriverSetupLoadTypeName` | none | `setup_recordings.py:150` (`user`/`iracing`/`fixed`) |
| `UpdateCount` | none | treated as a revision counter, not a setting (`setup_recordings.py:33`, `setup_catalog.py:955`, `workflow.py:1666`) |
Excluded from garage values as run readings: `LastHotPressure*`, `LastTemps*`, `TreadRemaining*`, `UpdateCount` (`setup_recordings.py:30-33, :80-104`).

**Are garage session-info updates observed today?** The reader re-reads the whole string whenever the header update counter changes (`IRacingSdkTelemetrySource.cs:46, :232`) but retains only the items in section 1; nothing stores the string, a hash, the counter, or `DriverSetup*`. It reads session info only after the status bit0 check passes (:36), so if iRacing does not set bit0 in the garage nothing is observed. Whether bit0 is set in the garage is UNKNOWN.

**Web research**
- The SDK header carries a `sessionInfoUpdate` counter tracking session-string changes ([irsdkSharp](https://github.com/mherbold/irsdkSharp), [pyirsdk docs](https://docsearch.algolia.com/mcp/docs/repo/kutu/pyirsdk)). pyirsdk records, per top-level YAML key, the counter value at its last parse and exposes `get_session_info_update_by_key(key)` ([irsdk.py](https://raw.githubusercontent.com/kutu/pyirsdk/master/irsdk.py)). A search summary of the pyirsdk docs describes comparing the `CarSetup` key's update tick with a stored value to detect "setup changed and applied in the garage" (second-hand; the page itself was not retrievable).
- pyirsdk treats a session as connected when status is connected, with a workaround state machine that accepts a readable `SessionNum` as connected ([irsdk.py](https://raw.githubusercontent.com/kutu/pyirsdk/master/irsdk.py)). That suggests iRacing sometimes publishes data while bit0 is clear, which our bit0-only gate would miss (inferred).
- I did not find an official iRacing statement (SDK docs or forums) that `CarSetup`/`DriverSetupName` refresh on load or Apply in the garage before going on track. Searches returned nothing specific.
- Local evidence: the product's live steering depends on the live string containing `CarSetup` (`LiveSteering.cs:7-8`, Supra), and IBT session info carries `DriverSetup*` (`setup_recordings.py:5-9`). That shows presence, not garage-time updates.
- **Confidence**: live `CarSetup` and `DriverInfo.DriverSetupName` exist in the live string: high. The counter increments and the YAML reflects the new values when a setup is loaded or applied in the garage before going on track: **medium-low (about 50-60%)**. Whether `DriverSetupIsModified` flips on edit, and whether bit0 is set in the garage, are UNKNOWN. A rig test is required.
- Suggested rig test (solo practice on the sim, copied data): log `(status, sessionInfoUpdate, tick)` at 60 Hz plus hashes of `DriverInfo.DriverSetup*` and `CarSetup` while (a) loading a library `.sto` in the garage, (b) editing a value and pressing Apply, (c) loading an iRacing baseline, (d) leaving to track, (e) returning to the garage after a tow; also log whether the map exists and bit0 on the iRacing menus. Compare the hash with the exported `.htm`.

## Surprises
1. No `IRSDKDataValidEvent`: 2 ms polling (about 500 wakeups/s while connected, inferred) and no torn-read guard.
2. Bit0 gating may hide session-info updates between sessions.
3. The monitor is an ordinary topmost, taskbar-visible, activatable window; click-through and no-activate are new requirements.
4. Live cue state is memory-only; baselines rebuild every session.
5. Finalization lives only in Python and runs at analysis time; the Rust collector needs its own copy gate (exact size, record_count > 0, 2 s quiet).
6. The conformance JSON has `dotnet_legacy_caution_mask` and `caution_bits_missing_from_dotnet`; confirm current C# `CautionMask` (0x0000C308) equals the backend value before treating the vectors as golden (not checked).
7. `.ircr2` forces a disk flush at least every 10 s while on track; v1 does not need it.
8. `ibt_reader.py:820` `DriverSetupModified` vs `DriverSetupIsModified` (section 6).
