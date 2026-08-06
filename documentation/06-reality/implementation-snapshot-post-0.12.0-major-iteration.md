# Implementation Snapshot: Post-0.12.0 Live, Home, and Race Analysis Round

Status: superseded development snapshot between 0.12.0 and 0.13.0. It is preserved as historical evidence and is not the current implementation or a packaged-release claim. See `implementation-snapshot-0.13.0.md` for current reality.

Evidence date: 2026-08-05.

## Superseding requirements

The binding 2026-08-05 decision replaces the square-cell grid and editable native popout from 0.12.0:

- Live grid rows and columns divide their host equally. Cells may be rectangular.
- The full application is the only layout-authoring surface. Its Toolbox overlays the grid and may be hidden without ending edit mode.
- The native popout is a fitted, display-only named-layout selector with separate machine-local physical scale.
- Live charts may paint and move at display refresh cadence, but the SDK sampling rate remains the only telemetry source rate.
- Race Analysis uses one to ten configurable trace rows with one primary and at most one different secondary signal per row; each signal retains an independent Y scale.

The normative contracts are `LT-013` to `LT-020`, `LMG-001` to `LMG-014`, `HOME-009` to `HOME-010`, `RA-045A`, `RA-050A`, `RA-057`, `RA-059`, `SET-008` to `SET-009`, and `PERF-006` to `PERF-008`.

## Inspected current source reality

### Live Telemetry editor

- `LiveTelemetryLayoutGrid.razor` separates viewing/editing state from Toolbox visibility. Customize enters editing; the user can hide or reopen the Toolbox without changing the telemetry grid width or leaving editing.
- The CSS/JavaScript logical grid uses equal fractional row and column tracks and fills its available host. A larger row/column count reduces each track's share rather than growing a fixed cell footprint.
- Layout creation, duplication, rename, reset, delete, tile forms, units, precision, trend duration, drag/drop, replacement preview, keyboard movement/resizing, and undo remain in the full application.
- The default dashboard remains immutable; ordinary dashboards remain renameable and deleteable.
- The Toolbox is an overlay with a reduced-motion-safe visibility transition. Entering edit mode does not create a narrower dashboard column.

### Live tile rendering and source truth

- `live-telemetry-tile-charts.js` retains timestamp, lap number, and lap-distance percentage with each accepted value. `null`, `undefined`, and empty values are rejected instead of becoming zero.
- Seconds windows are positioned from recorded timestamps. Lap windows are positioned from recorded lap plus lap-distance progress.
- Canvas painting uses `requestAnimationFrame`. Between incoming samples, the renderer translates the already-recorded path horizontally for a short source-rate-bounded interval; it does not create intermediate Y measurements.
- Boolean and categorical metrics use stable scalar encodings and step paths. Reduced-motion mode stops between-sample movement while continuing to paint real incoming samples.
- Positioned missing samples remain nullable gap markers in both historical seeds and live append. They are excluded from domains and split path segments; material timestamp gaps also split a path.
- Point retention is bounded. Display paths retain first/minimum/maximum/last candidates per horizontal pixel, preserving brief extrema while bounding vertices. Domains expand immediately but shrink only after a delay and gradual decay, avoiding continuous Y-axis rescaling. Lap regressions clear lap-window charts so a new session cannot join an old one.
- The renderer caches paths until data or size changes and keeps text/layout updates outside the high-rate paint path.

This implementation is designed to look smooth on a high-refresh display. It does **not** prove that iRacing published 60 Hz data, that every frame reached the app, or that the display delivered 244 frames per second.

### Native telemetry popout

- `LiveMonitorWindow` builds equal Star-sized rows and columns from the selected portable dashboard.
- The popout exposes layout selection, movement, physical scale, and close. It does not expose layout creation, grid editing, tile editing, rename, reset, or delete.
- Physical scale remains machine-local. The chosen named layout and logical tile definitions remain shared with the full application.
- Responsive tile templates trim or scale content within their assigned cell instead of making the window an editor.
- Incoming readings update the existing native tile visuals incrementally. Trend history is retained and painted on the display dispatcher cadence; the popout no longer discards and rebuilds its complete visual tree on a 200 ms/5 Hz timer.
- Display-synchronized native painting does not change, infer, or claim the SDK source cadence. It repeats no measurement as a new sample and does not prove delivered refresh rate on a particular display.

### Home and cache preparation

- Home withholds its workflow-card region until initial discovery/capability data is ready, avoiding the temporary incomplete card set.
- `Open live telemetry` opens the native popout. Home no longer presents a second competing Live Monitor action.
- After discovery, `CompanionState` examines every finalized Race. A valid schema-5 UI-analysis cache is reused when its source-write time matches an existing source (or when the source is no longer present); every other eligible Race is queued for sequential background analysis.
- Before beginning each queued item, the background scheduler waits while live telemetry is connected or an interactive analysis owns the analysis path. It resumes only after those higher-priority activities release the gate; this prevents background catch-up from intentionally competing for the same analysis capacity.
- A successful result is stored in the portable `ui-analysis-cache` and updates race summaries. Failure is contained to that recording and produces no routine success/failure tray noise. The final 0.13.0 behavior gives a failed identity one quiet retry and permits a later refresh or application run to attempt it again.
- Home and Race Analysis rows expose supported green/caution totals, longest green run, run/stop counts, pace/consistency, and measured tire or control-load context. Missing measurements remain unavailable.

### Race Analysis

- Race uses a compact identity bar followed by a `Telemetry` / `Race review` selector. Telemetry is the default visual workbench; grades and text-oriented findings live in Race review. Qualifying retains its separate combined layout.
- `AnalysisTraceLayouts` persists one to ten trace rows in portable settings. Rows support add/remove/reorder, one primary signal, and any one different optional secondary signal.
- The trace Toolbox exposes a compact draggable signal library and explicit primary/secondary drop targets for every row. Only attempting to duplicate the primary in the secondary slot is rejected; the selects remain available as the keyboard/accessibility path.
- Fewer configured rows share the fixed vertical trace area; selected unavailable channels receive `Not recorded for this race` rather than a zero line.
- The trace catalog includes Speed, Time Delta, Throttle, Brake, Calculated Tire Wear, Gear, RPM, Steering, Slip Angle, Yaw Rate, Lateral G, and Longitudinal G.
- The deterministic engine emits Yaw Rate independently and derives signed vehicle sideslip in degrees as `atan2(VelocityY, VelocityX)` only for paired finite velocity samples with positive/usable forward velocity and at least 5 m/s planar speed. Failed guards remain gaps. This is a derived vehicle-sideslip channel, not a native `SlipAngle` field or tire-slip measurement.
- Speed map color now spans a materially separated cool-blue, cyan, green, yellow, and warm-coral scale across the selected-lap minimum and maximum. Missing samples remain neutral, and effectively zero control/load values retain the dark neutral treatment.
- `TelemetryWorkspace` serializes selected trace values, each signal's independent selected-lap range, colors, map geometry, and cursor configuration when component state changes. `analysis-telemetry-cursor.js` then uses animation frames, cached binary point lookup, and direct SVG/DOM updates for crosshair, markers, cards, track cursor, and aggregate readout. Marker/card DOM is pooled to tooltip capacity and rebound to the current wheel-visible selected-lap slice; the aggregate track readout continues to cover every selected lap. Pointer movement and wheel paging do not call .NET or render the Blazor component per frame.

### Settings and visual system

- Settings leads with compact cards for Data, Backup or move PCs, App behavior, and Telemetry popout. Folder paths, Connections, and troubleshooting detail are disclosures; the Garage61 token and optional coaching controls remain available inside Connections.
- The app-wide theme retains a dark charcoal foundation while adding translucent depth and a restrained mint/amber/coral/violet telemetry palette. Add and destructive icons receive semantic color without relying on color alone.

## Verification boundary

### Locally verified in this development round

- Source inspection confirms the state, persistence, data-lineage, and rendering paths above.
- Focused .NET tests cover the encoded Home priority gate, grid/popout contract, telemetry value conversion/missing-value behavior, native incremental render contract, Race Analysis trace-layout/drop rules, and the DOM-owned cursor boundary.
- Derived vehicle sideslip has 19/19 focused Python checks, a 1/1 coordinator mapper check, and scoped recorded-file evidence from one Iowa IBT: 542 valid derived samples and 9 guarded gaps across four laps. This validates the implemented path for that recording, not a native field or broad fleet claim.
- The fresh raw `companion-app/src/iRacingCoach.App/bin/Release/net10.0-windows10.0.17763.0/iRacing Coach.exe`—not the older installed copy—was used for focused Home and native-popout interaction and for Race Analysis presentation review at 1424×900. An installed executable from an earlier release is not evidence for this snapshot.

### Not verified or accepted by this snapshot

- Real iRacing shared-memory field coverage, capture cadence, reconnect behavior, or usefulness while driving.
- Sustained 60 Hz source capture or 244 Hz display delivery on Joshua's racing hardware.
- Broad derived-sideslip validation across representative cars, tracks, session states, and velocity conventions beyond the named Iowa recording; native `SlipAngle` and tire-slip measurements are not claimed.
- Representative private-library background-analysis success across every historical recording.
- Representative-library throughput and contention measurements while background cache work yields to a real live session or a long interactive analysis.
- Exhaustive keyboard, screen-reader, high-DPI, narrow-window, or multi-monitor behavior.
- A new installer, upgrade/uninstall lifecycle, release hash, or product acceptance.

Only an exact packaged artifact plus scenario-specific evidence can close those boundaries. `requestAnimationFrame`, a fixture replay, unit tests, and screenshots must not be promoted into real-system acceptance.
