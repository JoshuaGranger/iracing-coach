# P2: TypeScript SPA versus Razor in WebAssembly (decides D3)

Run date: 2026-10-02. Status: complete (result below).

## Thresholds (written before any measurement; never adjusted)

PASS means reuse Razor in WebAssembly and drop the SPA rewrite. All three must hold:

1. At least 70% of the lines of the v1 UI and view-state code (the Razor components and the coordinator code behind the v1 screens, as listed in `spikes/ui-data.md`) are reusable in a browser without server coupling.
2. First interactive render of the Traces screen is 3 s or less at 6x CPU throttle.
3. Cursor loop is 55 fps or more under continuous pointer movement at 6x CPU throttle.

FAIL means the TypeScript SPA stands.

Measurement protocol fixed in advance: Chromium via Playwright; CDP `Emulation.setCPUThrottlingRate` 6; viewports phone 390x844 and tablet 1024x768; each measurement three times, median reported; dev PC CPU load below 50% before each run; first interactive = navigation start until the trace paths and cursor exist and respond to a pointer move; cursor fps = requestAnimationFrame count over 10 s of synthetic pointer movement across the chart. All render numbers are emulation (CPU throttle is not a phone). The threshold is judged on the worse of the two viewports.

## Method (commands)

Scratch root: `C:/Users/joshu/AppData/Local/Temp/claude/C--Users-joshu-source-repos-iRacing-Coach/478b474b-a1ef-4041-b6b1-2d2558dc1b5f/scratchpad/prove/P2/` (call it `$P2`). Scripts kept in `$P2/keep/` (`count.py`, `measure.js`, `server.js`, result JSON files). The only repository file written is this report. Building referenced projects wrote ignored `bin/` and `obj/` folders under `companion-app/src/*`; `git status` shows no tracked change.

Environment: dev PC DESKTOP-JOSHUA, .NET SDK 10.0.300, node 24.19.0, Playwright with its bundled Chromium 153.0.8010.12 (headless), PATH had `C:/Users/joshu/.cargo/bin` added (not needed). `dotnet workload list` printed an empty installed list: **wasm-tools is not installed** (not installed by this proof), so the publish is non-AOT, and the publish log says "Publishing without optimizations". The sim PC and the mini PC were not used.

1. Static count: `python.exe $P2/count.py "<repo>/companion-app/src" > count.json`. The script splits each C# file into members by brace depth and marks a member server-coupled when it matches a coupling pattern (Process/ProcessStartInfo, explorer.exe, powershell.exe, File./Directory./FileInfo/FileStream/FileSystemWatcher, Registry/Microsoft.Win32, DllImport, RegisterHotKey, OpenFileDialog, MemoryMappedFile, IRacingSdk, Mutex/NamedPipe, temp paths). The second column set also marks members that call a uniquely named coupled member (to a fixed point, 4 rounds); settings-store and SDK classes are excluded from that propagation because they sit behind injected interfaces. Razor files: `@code` members by the same rule, plus any line that matches a pattern or calls a coupled `State.<member>`. `IRacingSdkTelemetrySource.cs` is counted fully coupled by hand (shared-memory reader gated on `OperatingSystem.IsWindows()`). Scope is the file list in `spikes/ui-data.md` sections 1 and 4 for the v1 screens (Sessions, Race/Runs/Sections, Traces, Setups, Find a baseline, Progressive Tuning, Live grid, shell), with the parked/Drop list excluded. Contracts models are not counted (data only). Backend calls are not counted as coupling because they go through `IBackendClient`, an interface a browser build can implement over HTTP.
2. Throwaway host: `dotnet new blazorwasm -n WasmHost`, then `ProjectReference` to `companion-app/src/iRacingCoach.UI` and `iRacingCoach.Contracts`; services stubbed in `Stubs.cs` (no-op `IBackendClient`, in-memory `ISettingsStore`, `DisconnectedLiveTelemetrySource`, `DisabledCoachEngineSupervisor`, no-op credential store, a browser path provider) and passed to the same `CompanionState` constructor the Preview host uses. `App.razor` fetches a copy of a real saved analysis (`reports/season-6361/subsession-87526384-2/.../analysis.json`, 6.7 MB, 43 laps, 36 trace paths rendered, older analysis profile, no `race_investigation`; copied into the scratch folder and deleted afterwards), maps it with `RuntimeMapper.ArchivedAnalysis`, and mounts `<TelemetryWorkspace IsRaceWorkspace="true">` inside the same `.app-shell/.workspace/.analysis-primary-column` wrappers with the same CSS and ten JS modules as `iRacingCoach.Preview/Components/App.razor`. No test-data fixture contains a full set of lap traces (the `analysis-view-*.json` fixtures are validator cases), so a real copy was the only source.
3. Publish: `dotnet publish -c Release -o $P2/publish` in `$P2/WasmHost`; sizes by `find publish/wwwroot -name "*.br" -printf "%s %p\n"`.
4. Serve and measure: `node server.js publish/wwwroot 5290` (Node static server, serves `.br` with `Content-Encoding: br` when accepted, `.wasm` as `application/wasm`, no caching); `node measure.js http://127.0.0.1:5290/ wasm6x 3` (Playwright, new browser context per run so nothing is cached, CDP `Emulation.setCPUThrottlingRate {rate: 6}`, viewports 390x844 and 1024x768, 3 runs each). Before every run the script samples `\Processor(_Total)\% Processor Time` for 3 s and waits until it is below 50 (all runs started at 30-48%, no timeouts; other proofs were running). Time to first interactive is `performance.now()` (navigation start origin) at the moment a MutationObserver sees the shared cursor line's `x1` change after a real mouse move, which can only happen after `[data-analysis-trace-path]` and `[data-analysis-cursor-layer] .shared-cursor` exist. Cursor loop: rAF counted for 10 s while Playwright mouse moves sweep the chart continuously. 6x runs set `THROTTLE=6` (default); reference runs use `THROTTLE=1 node measure.js ... wasm1x 1`.
5. Comparison: the real Preview host (`dotnet build -c Release` then `dotnet bin/Release/net10.0/iRacingCoach.Preview.dll --urls http://127.0.0.1:5292 --isolated-profile <temp>/iracing-coach-host-<32 hex> --isolated-no-webview`) starts without the Windows app and lists the copied archived race, but opening it shows "Telemetry could not be loaded ... older analysis format ... recording is missing" and never renders Traces (no analysis with the current profile and `race_investigation` exists on this PC; the first-run wizard also had to be bypassed through the isolated profile's `settings.json`). So the comparison number comes from a scratch Blazor Server host built from the Preview project's own files (`Program.cs`, `App.razor`, interactive-server render mode, `UseStaticWebAssets`) with the same stub services and the same fixture page: `dotnet build -c Release` in `$P2/ServerHost`, `dotnet bin/Release/net10.0/ServerHost.dll --urls http://127.0.0.1:5291`, `node measure.js http://127.0.0.1:5291/ server6x 3`. This is Preview-equivalent, not the Preview host itself. The server is not throttled; only the browser is.

## Numbers

### Step 1: static coupling count (v1 UI and view-state code, 63 files)

Totals: 23,691 lines. Coupled members, direct match: 2,038 lines (8.6%). Coupled including callers of coupled members: 3,725 lines (15.7%). **Reusable in a browser: 91.4% (direct) and 84.3% (conservative).** By layer, conservative: UI project 99.5% reusable (9,234 lines, 42 coupled), Coordinator 74.5% reusable (14,457 lines, 3,683 coupled). Sensitivity: adding the Windows host files the spike lists (`LiveMonitorWindow.xaml.cs`, `MainWindow.xaml.cs`, `App.xaml.cs`, `MainWindowPlacement.cs`, 2,137 lines, all coupled) to the scope gives 77.3%.

Where the coupling is: `CompanionState.cs` 2,412 of 4,771 lines (inventory refresh, analysis cache reads, `LoadArchivedRaces`, report/setup file copy, explorer.exe, powershell, FileSystemWatcher, settings writes), `SettingsStore.cs` 214, `IRacingSdkTelemetrySource.cs` 484 (all), `ArchivedRaceCatalog.cs` 171, `ProgressiveTuningCoordinator.cs` 88 (draft files), `CompanionState.Investigation.cs` 111, `CapabilityRegistry.cs` 51, `LiveTelemetry.cs` 49, `SetupLibrary.cs` 49, `CompanionState.Setups.cs` 42. The Razor components themselves are almost free of coupling; they depend on the `CompanionState` object, whose file and process members would have to be stubbed or moved.

Per-file table (lines from `wc -l` semantics; "with callers" is the conservative column):

| File | Layer | Lines | Coupled (direct) | Coupled (with callers) | Reusable % |
|---|---|---:|---:|---:|---:|
| CompanionState.cs | Coordinator | 4771 | 941 | 2412 | 49% |
| CompanionState.Investigation.cs | Coordinator | 265 | 0 | 111 | 58% |
| CompanionState.Setups.cs | Coordinator | 157 | 0 | 42 | 73% |
| CompanionState.Toasts.cs | Coordinator | 159 | 0 | 12 | 92% |
| CompanionState.Settings.cs | Coordinator | 39 | 0 | 0 | 100% |
| CompanionState.SettingsEdits.cs | Coordinator | 106 | 0 | 0 | 100% |
| RuntimeMapper.cs | Coordinator | 2637 | 0 | 0 | 100% |
| RaceInvestigationMapper.cs | Coordinator | 299 | 0 | 0 | 100% |
| DashboardMapper.cs | Coordinator | 288 | 0 | 0 | 100% |
| SteeringBasisMapper.cs | Coordinator | 60 | 0 | 0 | 100% |
| RuntimeEnvelopeValidator.cs | Coordinator | 144 | 0 | 0 | 100% |
| NonFiniteJson.cs | Coordinator | 93 | 0 | 0 | 100% |
| ArchivedRaceCatalog.cs | Coordinator | 244 | 152 | 171 | 30% |
| SetupLibrary.cs | Coordinator | 84 | 49 | 49 | 42% |
| SetupComparability.cs | Coordinator | 155 | 0 | 0 | 100% |
| SetupReadings.cs | Coordinator | 34 | 0 | 0 | 100% |
| ProgressiveTuningCoordinator.cs | Coordinator | 602 | 88 | 88 | 85% |
| RaceReviewMetrics.cs | Coordinator | 75 | 0 | 0 | 100% |
| CalculatedTireWear.cs | Coordinator | 100 | 0 | 0 | 100% |
| AnalysisTraceLayouts.cs | Coordinator | 376 | 0 | 0 | 100% |
| AnalysisTraceLayoutSets.cs | Coordinator | 226 | 0 | 0 | 100% |
| ThemeColors.cs | Coordinator | 124 | 0 | 0 | 100% |
| CapabilityRegistry.cs | Coordinator | 252 | 50 | 51 | 80% |
| SettingsStore.cs | Coordinator | 476 | 214 | 214 | 55% |
| LiveTelemetryCatalog.cs | Coordinator | 525 | 0 | 0 | 100% |
| LiveMonitorLayouts.cs | Coordinator | 468 | 0 | 0 | 100% |
| LiveSteering.cs | Coordinator | 81 | 0 | 0 | 100% |
| LiveTelemetry.cs | Coordinator | 1008 | 49 | 49 | 95% |
| IRacingSdkTelemetrySource.cs | Coordinator | 484 | 484 | 484 | 0% |
| LiveTruthPolicy.cs | Coordinator | 48 | 0 | 0 | 100% |
| LiveTelemetryFrameCompaction.cs | Coordinator | 77 | 0 | 0 | 100% |
| MeasuredChart.cs | UI | 60 | 0 | 0 | 100% |
| InvestigationPresentation.cs | UI | 170 | 0 | 0 | 100% |
| IncidentPresentation.cs | UI | 100 | 0 | 0 | 100% |
| CornerEvidencePresentation.cs | UI | 30 | 0 | 0 | 100% |
| AnalysisTrackMapGeometry.cs | UI | 162 | 0 | 0 | 100% |
| ProgressiveTrackProjection.cs | UI | 216 | 0 | 0 | 100% |
| ProgressiveTurnCatalog.cs | UI | 253 | 11 | 12 | 95% |
| SessionsPage.razor | UI | 222 | 0 | 3 | 99% |
| ProductIcon.razor | UI | 176 | 0 | 0 | 100% |
| AnalysisPage.razor | UI | 42 | 0 | 1 | 98% |
| AnalysisWorkspacePage.razor | UI | 314 | 0 | 2 | 99% |
| RaceInvestigationView.razor | UI | 250 | 0 | 0 | 100% |
| RaceProgressionChart.razor | UI | 176 | 0 | 0 | 100% |
| RunOverlayChart.razor | UI | 210 | 0 | 0 | 100% |
| SectionInspectionPanel.razor | UI | 405 | 0 | 0 | 100% |
| EvidenceBadge.razor | UI | 22 | 0 | 0 | 100% |
| MetricTile.razor | UI | 14 | 0 | 0 | 100% |
| StatusChip.razor | UI | 8 | 0 | 0 | 100% |
| GapCell.razor | UI | 10 | 0 | 0 | 100% |
| TimelineLane.razor | UI | 12 | 0 | 0 | 100% |
| TelemetryWorkspace.razor | UI | 2604 | 0 | 0 | 100% |
| SetupPage.razor | UI | 243 | 0 | 11 | 95% |
| StartingTunePage.razor | UI | 154 | 0 | 2 | 99% |
| TuningPage.razor | UI | 903 | 0 | 4 | 100% |
| TuningTrackSelector.razor | UI | 437 | 0 | 0 | 100% |
| ProgressiveTuningFeedbackEditor.razor | UI | 419 | 0 | 0 | 100% |
| TrialAction.razor | UI | 74 | 0 | 0 | 100% |
| TrialDetail.razor | UI | 78 | 0 | 1 | 99% |
| LiveTelemetryPage.razor | UI | 59 | 0 | 0 | 100% |
| LiveTelemetryLayoutGrid.razor | UI | 860 | 0 | 0 | 100% |
| LiveTelemetryVisuals.razor | UI | 258 | 0 | 0 | 100% |
| CompanionShell.razor | UI | 106 | 0 | 1 | 99% |
| AppTopBar.razor | UI | 61 | 0 | 0 | 100% |
| AppToast.razor | UI | 85 | 0 | 3 | 96% |
| JobTray.razor | UI | 41 | 0 | 2 | 95% |
| **Total** | | **23691** | **2038** | **3725** | **84.3%** |

### Step 2: can the UI library be referenced from a browser target?

Yes, with nothing blocked. `iRacingCoach.UI` targets `net10.0` (it already declares `SupportedPlatform Include="browser"`), and `Coordinator`, `BackendClient` and `Contracts` are plain `net10.0` libraries with no Windows-only target and no Windows package references. `dotnet build -c Release` of the WASM host: 0 warnings, 0 errors, 31 s. Windows behaviour is guarded at run time (`OperatingSystem.IsWindows()`), not by the target framework. Lines that would have to move to make that safe, per step 1: 3,725 of 23,691 (conservative), mostly inside `CompanionState`. The host needed no shim beyond the stub services; `TelemetryWorkspace` rendered the real chart and map (36 trace paths, shared cursor).

### Step 3: published payload (`dotnet publish -c Release`, no wasm-tools, no AOT)

| Item | Bytes |
|---|---:|
| All `.br` files in `publish/wwwroot` (80 files) | 4,270,556 |
| of which fixture analysis (`fixture/analysis.json.br`, stands in for the API response) | 367,741 |
| of which `_content` CSS/JS from the UI library (17 files) | 92,658 |
| framework + app assemblies + ICU + runtime, the rest | 3,810,157 |
| `dotnet.native.wasm.br` | 977,044 |
| `System.Private.CoreLib.wasm.br` | 555,876 |
| `iRacingCoach.Coordinator.wasm.br` | 340,151 |
| `iRacingCoach.UI.wasm.br` | 299,813 |
| `iRacingCoach.Contracts.wasm.br` | 147,180 |
| three ICU data files (only one is fetched per run) | 248,840 / 222,250 / 143,983 |
| Bytes actually transferred on one first load in Chromium (78 requests, median of 3, identical every run) | 3,815,717 |
| Uncompressed `publish/wwwroot` total | 30,396,605 |

The three app assemblies the SPA would not need are 787,144 bytes br; the rest is the .NET runtime and base library. For comparison the Blazor Server host moved 1,018,439 bytes (22 requests) for the same page, which is the CSS, JS and the SignalR traffic, since the fixture stays on the server.

### Step 4: Chromium at 6x CPU throttle (emulation), Traces screen, WebAssembly host, median of 3

| Viewport | First interactive, runs (ms) | Median | Cursor loop, runs (fps) | Median |
|---|---|---:|---|---:|
| Phone 390x844 | 73,979 / 76,835 / 74,563 | **74,563 ms** | 9.4 / 9.2 / 9.1 | **9.2 fps** |
| Tablet 1024x768 | 77,463 / 74,229 / 79,974 | **77,463 ms** | 8.6 / 9.3 / 9.1 | **9.1 fps** |

CPU load before each run: 29.9% to 35.3%, none waited beyond the 3 s sample. Each fps run had 86-94 rAF frames in 10 s with 84-92 gaps over 25 ms (longest 167-300 ms).

Where the first-interactive time goes at 6x (browser marks from the page, phone run 1): runtime booted and app initialised at 7.1 s, fixture fetched 8.95 s, JSON parsed 11.4 s, `RuntimeMapper.ArchivedAnalysis` finished 19.8 s, trace paths and cursor present at 73.5 s. The remaining 54 s is Blazor rendering `TelemetryWorkspace` (cursor-config build, 36 trace paths, map projection) in the interpreted, non-AOT runtime.

Reference only (single unthrottled run, `THROTTLE=1`, not a threshold measurement): WebAssembly first interactive 7,035 ms phone and 7,620 ms tablet, 60 fps both; mapping done at 2.1 s, so about 5 s is render time even without throttle.

### Step 5: comparison on a Blazor Server host (Preview-equivalent, same page, same JS), median of 3 at 6x

| Viewport | First interactive, runs (ms) | Median | Cursor loop, runs (fps) | Median |
|---|---|---:|---|---:|
| Phone | 4,789 / 3,688 / 3,838 | 3,838 ms | 9.9 / 9.5 / 9.6 | 9.6 fps |
| Tablet | 3,758 / 3,858 / 3,849 | 3,849 ms | 8.9 / 9.2 / 9.2 | 9.2 fps |

CPU load before runs: 31.6% to 47.6%. Unthrottled reference (single run): 730 ms phone, 765 ms tablet, 60 fps.

## Result

**FAIL.** The TypeScript SPA stands.

- Threshold 1 (at least 70% reusable): met, 84.3% conservative and 91.4% direct (77.3% with the Windows host counted).
- Threshold 2 (first interactive 3 s or less at 6x): failed, 74.6 s phone and 77.5 s tablet, 25 times the limit. Even unthrottled the page takes 7.0 to 7.6 s because the non-AOT WebAssembly runtime interprets the Razor render. AOT needs the wasm-tools workload, which is not installed and which this proof does not install, so its effect is UNKNOWN; a 5x speedup would still leave about 9 s at 6x.
- Threshold 3 (cursor loop 55 fps or more at 6x): failed, 9.2 and 9.1 fps. All three had to hold.

## Decision impact

Confirms D3 (TypeScript single-page app): the "reuse Razor in WebAssembly" alternative is rejected on measured first-render cost and payload (3.8 MB of brotli to first paint, almost all of it the .NET runtime), not on code reuse, which is high. No owner decision needs to change; `contradictsOwnerDecision` is false.

Three points for the architect:

1. The 55 fps threshold at 6x did not separate the options. The cursor loop is plain JavaScript (`analysis-telemetry-cursor.js`), identical in every host. The Blazor Server host, which runs no .NET on pointer move, also gives 9.6 and 9.2 fps at 6x and 60 fps at 1x. A TypeScript SPA that reuses this JS should be expected to land near 9 fps at 6x too, so the SPA needs its own P-level measurement against this threshold, and the cursor loop (not the framework) is where the 6x gap lives. This proof makes no claim that an SPA meets it.
2. The "84% reusable" figure overstates what is worth reusing. Of the reusable lines, `RuntimeMapper` and the other mappers (3,521 lines) exist because backend JSON is untyped and would be redundant if the Rust API emits view types, and the WebAssembly host had to ship them plus the 340 KB Coordinator assembly just to map one analysis (8.4 s of the 6x timeline).
3. Blazor Server renders the Traces screen interactively in 0.73 s unthrottled and 3.8 s at 6x, but it needs .NET on the host, which D3 excludes (no .NET on the mini PC).

## Notes

- Fixture: one real saved analysis (older profile, 43 laps, no `race_investigation`), because no test-data fixture carries lap traces. A full-size current-profile analysis (up to 82 laps) would take longer, not less. The fixture fetch and mapping run inside the WebAssembly host's timeline; in a real rewrite the server would deliver bounded series, which would remove at most the 19.8 - 7.1 = 12.7 s of fetch, parse and map at 6x, leaving the render (about 54 s) untouched.
- The numbers are emulation: a 6x CPU throttle in headless desktop Chromium on a dev PC that was 30-48% loaded by other proofs, no GPU, a localhost server with no network latency or bandwidth limit. They are not phone measurements. The pointer moves come from Playwright over CDP, which is also throttled; the mouse-move count per run (86-100) tracks the frame count, so part of the 9 fps may be input delivery. That affects the SPA the same way.
- The run did not use the sim PC or the mini PC. Nothing needs the owner.
- Cleanup: Preview, ServerHost and the static server were stopped; the publish output, build output, fixtures and the isolated Preview profile were deleted from the scratch folder. The Preview host start used an isolated profile under the temp directory and left the live Coach archive untouched. The repository's ignored `companion-app/src/*/bin|obj` folders contain the Release builds made for this proof.
- Not measured: AOT, Brotli level, service-worker caching on repeat loads (a repeat load would skip the 3.8 MB), and a TypeScript SPA baseline.
