# Implementation Snapshot: 0.13.0

Status: packaged 0.13.0 source baseline with integrated automated verification and measured artifact identity. The process-deadline path has runtime evidence, while a genuine mouse-driven tray-menu activation remains an explicit open acceptance boundary.

Evidence date: 2026-08-06.

## How to read this snapshot

This file records functional reality, not desired behavior. Normative intent remains in `00-governance` through `05-quality`. A statement under **Implemented reality** describes inspected production source. A statement under **Recorded evidence** identifies a completed check. A statement under **Open acceptance boundary** is not complete and must not be inferred from adjacent tests.

The executable identity matters. An installed 0.12.0 copy and screenshots from it are not evidence for 0.13.0. Development interaction must name the fresh 0.13.0 Release executable, and release claims must name the exact packaged artifact and hash.

## Version and data compatibility

- Application, installer, uninstaller, release script, coordinator client identity, and cache-download naming are set to `0.13.0` in current source.
- Portable settings remain schema 4. That schema stores named Live Telemetry layouts and Race Analysis trace layouts; external-service secrets remain machine-bound.
- The UI analysis cache is schema 5. This is separate from portable-settings schema 4 and durable-archive schema 1.
- The durable Documents archive remains schema 1. No statement in this snapshot authorizes mutation of raw iRacing telemetry, replay, setup, or purchased source files.

## Implemented reality

### Live Telemetry and native popout

- Dashboard rows and columns divide the available host evenly. Increasing row or column count reduces each track's share; the grid fills the usable page height rather than retaining the earlier fixed-height ceiling.
- The full page is the layout-authoring surface. Its right-side Toolbox overlays the unchanged dashboard width, transitions in and out, and can be hidden without ending edit mode.
- Library rows are draggable across their full item surface. An occupied drop target receives a yellow replacement preview naming both metrics, and a validated drop replaces the target atomically. Empty-cell moves and normal placement remain distinct operations.
- Default is the only immutable dashboard. Other dashboards can be renamed and deleted.
- Every real catalog metric supports the applicable number, bar, gauge, and chart forms. Boolean and categorical histories use stable encodings and step paths rather than invented numeric telemetry.
- Full-page trends retain real timestamps and nullable gaps, preserve brief extrema during bounded display reduction, and paint through `requestAnimationFrame`. Between-sample horizontal motion repositions recorded path data for visual continuity; it does not create a higher source sampling rate or new measured Y values.
- Live telemetry exposes a monotonic session epoch. Connect, disconnect, source-clock regression, and lap-counter regression advance it; both browser chart pipelines clear retained samples at that boundary and reject queued frames from an earlier epoch.
- The native popout is display-only: layout selection, window movement, machine-local physical scale, and close remain; layout creation and tile editing belong to the full page. Its logical rows and columns match the full-page layout and fit the current window.
- The popout scale control initializes from the saved physical scale as a percentage within its 70–200% range; opening the control does not silently coerce a normal 100% layout to 70%.
- The popout updates retained tile visuals and trend history incrementally rather than rebuilding its visual tree on the former low-rate timer.

### Home and analysis-cache preparation

- Home waits for initial discovery/capability completion before showing its settled workflow-card set, exposes one `Open live telemetry` action for the native popout, and makes each recent-race row the analysis action.
- Recent-race and Race-browser summaries use supported race-shape, pace, consistency, stop/run, fuel, and tire/control-load measurements. Unsupported measurements remain absent rather than seeded.
- Finalized Race recordings without a valid schema-5 UI cache enter a quiet sequential background queue. Connected live telemetry and interactive analysis prevent the next background item from starting.
- A failed identity receives one quiet retry. It then leaves the active set so a later refresh or application run can retry it instead of being suppressed for the rest of the process.
- A successful interactive or background analysis immediately updates the selected Race session and its immutable Home/Race event-group projections with `Analyzed`, status, cache path, and overview data. Race analysis does not incorrectly mark a paired Qualifying phase as analyzed.

### Race Analysis

- Selecting a Race opens its Race phase and begins telemetry loading without requiring a Qualifying/Race tab detour. Race and Qualifying keep intentionally different presentation contracts.
- Race uses a compact identity area followed by `Telemetry` and `Race review`. Telemetry contains the lap/run selector, aggregate track map, and aligned charts; text-oriented grades and findings live in Race review.
- The telemetry workspace retains the lap/map/chart row while each pane remains usable. Below a 1060-pixel telemetry container the lap rail moves above the still-side-by-side map and traces; below the application's supported window width the remaining pair may stack. SVG geometry follows every positive measured width instead of retaining a stale wide view box, and the page avoids a horizontal scrollbar.
- Trace configuration is portable, one-column, and capped at ten rows. Each row accepts one primary and at most one different secondary signal. Each signal retains its own selected-lap range and truthful unit even when two signals share a row.
- Available signals include Speed, Time Delta, Throttle, Brake, Calculated Tire Wear, Gear, RPM, Steering, derived Slip Angle, Yaw Rate, Lateral G, and Longitudinal G. An unavailable selected channel renders as not recorded rather than as zero.
- The cursor bridge performs frame-coalesced crosshair, marker, track-position, and value-card updates in JavaScript without a managed render per pointer frame. The marker/value-card pool is bounded by visible tooltip capacity; the mouse wheel pages that pool through selected laps.
- Track-map summaries explicitly average the selected laps. Heatmap domains use the selected-lap range and a separated multi-stop scale; missing samples remain neutral.
- Signed vehicle sideslip is derived only from paired finite `VelocityX`/`VelocityY` samples that pass the positive-forward-velocity and minimum planar-speed guards. It is not a native `SlipAngle` field, tire slip, or Yaw Rate.

### Settings and visual system

- Settings prioritizes Data, Backup or move PCs, App behavior, and Telemetry popout. Paths, Connections, and troubleshooting detail are subordinate disclosures.
- Garage61 and optional coaching controls remain functional within Settings/Connections. Service credentials remain machine-bound and are excluded from portable data.
- The dark charcoal visual system adds restrained translucent depth and mint, amber, coral, violet, and telemetry-specific accents without turning routine success into status noise.

### Tray Exit source behavior

- The explicit tray Exit path signals shutdown before hiding/disposal so its deadline is armed before the native tray callback can retain the host.
- Cleanup remains best-effort and ordered. A rooted five-second watchdog terminates the process tree, with direct process termination as a fallback, if normal WPF/WinForms/WebView shutdown does not complete.
- This is inspected source behavior plus focused automated coverage and an isolated runtime deadline probe. A genuine mouse-driven activation of the final tray `Exit` item remains open below and is not inferred from the probe.

## Recorded evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| Integrated .NET test run | 110 passed, 0 failed | Automated source/contract behavior; not installed-product acceptance |
| Integrated Python backend test run | 175 passed, 0 failed | Deterministic backend behavior; not real-session telemetry acceptance |
| JavaScript syntax checks | 4 passed: live page chart, tile chart, layout interaction, and Race cursor modules | Parseability only; not frame-rate or interaction acceptance |
| Release application build | 0 warnings, 0 errors | Fresh Release build used by the final package |
| Final artifact inspection | Installer file version `0.13.0.0`; both SHA-256 sidecars match; ZIP and staged payload each contain 9278 files; no unsafe paths, private-state roots, `.ibt`, or `.log` files | Static package identity and hygiene; not installation or runtime acceptance |
| Focused responsive/layout tests | Passed within the integrated .NET count | Encoded layout contract; does not replace DPI/device visual review |
| Focused cache-projection tests | Passed within the integrated .NET count | Correct in-memory/current-view updates for tested identities |
| Focused tray-exit test | Passed within the integrated .NET count | Confirms deadline arming occurs before cleanup; not a mouse-driven tray-menu test |
| Tray watchdog runtime probe | Process exited after 5.207 seconds; no probe process remained | Exact Release assembly deadline path; does not prove the native tray menu item was clicked |

## Final artifact record

The following fields are deliberately machine-searchable. They must be replaced with measured values from the final `BuildRelease.ps1` output, not estimates or values copied from 0.12.0.

- Source commit: `1e7868c81236984c417d9606c6c8ecd5abdc4f6c`
- Installer: `companion-app/artifacts/dist/v0.13.0/iRacingCoach-0.13.0-Setup.exe`
- Installer bytes: `498935848`
- Installer SHA-256: `67c9043ea0339b00f4c1570a6b1bdfc887a1a7d38762f1006399ea19bdc42181`
- Portable package: `companion-app/artifacts/dist/v0.13.0/iRacingCoach-0.13.0-Portable-win-x64.zip`
- Portable bytes: `382942990`
- Portable SHA-256: `548c2cc251f80b3dd9de8d6f92408a69d47c94be7effc645f4b77d1ca6fec4`
- Packaged payload identity: `9278 files`; ZIP file count equals the staged payload count; one root `iRacing Coach.exe` and one `coach-engine/coach-engine-manifest.json` are present; manifest app version is `0.13.0`, runtime version is `0.146.0-alpha.9.2`, and its runtime SHA-256 matches the embedded signed Codex executable.

The installer product revision is `0.13.0+1e7868c81236984c417d9606c6c8ecd5abdc4f6c`, binding the package to the source commit above.

## Runtime evidence and remaining interaction boundary

- Exact executable: `companion-app/src/iRacingCoach.App/bin/Release/net10.0-windows10.0.17763.0/iRacing Coach.exe`
- Process-deadline result: the authoritative Release assembly armed the watchdog and the isolated probe process was forcibly gone after 5.207 seconds, with no matching probe process remaining.
- Genuine mouse-driven tray-menu `Exit`: unexecuted. Windows notification-area ownership prevented the automation tool from targeting the final menu item, so this snapshot does not claim a manual pass.
- Exact probe identity and command: `tray-watchdog-probe.exe` reflection-loaded `companion-app/src/iRacingCoach.App/bin/Release/net10.0-windows10.0.17763.0/iRacing Coach.dll`, invoked internal `App.ArmExitDeadline()`, printed `ARMED`, and then slept until the production watchdog ended it. A synchronous process stopwatch recorded exit code `-1`, 5.207 seconds elapsed, and zero remaining `tray-watchdog-probe` processes. PID and wall-clock time were not captured and are not inferred.

<!-- A hidden window, removed icon, or direct watchdog invocation is not a mouse-driven tray-menu pass. -->

## Open acceptance boundaries

- Real iRacing shared-memory coverage, capture cadence, reconnect behavior, and usefulness while driving remain unaccepted.
- Neither 60 Hz source capture nor 244 Hz display delivery is claimed. `requestAnimationFrame`, interpolation, unit tests, and fixture replay are not cadence measurements.
- Representative-library background-analysis throughput and combined-load behavior remain unmeasured on the racing computer.
- A genuine click of the final tray `Exit` item against the packaged 0.13.0 executable remains unobserved even though the underlying five-second process deadline has passed its isolated runtime probe.
- Derived sideslip remains narrowly evidenced and requires broader car/track/session validation.
- Exhaustive keyboard, screen-reader, high-DPI, narrow-window, and multi-monitor validation remain open unless separately recorded against the exact artifact.
- Installer replacement, rollback, uninstall, reinstall, and durable-data preservation were not rerun merely for every focused UI edit. Historical lifecycle evidence remains historical; any claim that 0.13.0 itself passed the complete lifecycle matrix requires an exact-artifact record.
- Joshua has not accepted 0.13.0 merely because this snapshot exists or automated tests pass.
