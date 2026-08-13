# Implementation Snapshot: Post-0.11.1 Race Analysis Round

Status: current-main focused Race Analysis development candidate. This snapshot does not claim a new packaged release and does not repeat installer or upgrade qualification.

## Material changes

- Home recent-race entries are whole-row controls. They open the selected race and begin loading Race telemetry immediately.
- Home rows use supported race-specific measurements: result/setup type, green and caution laps, longest green run, run/stop count, clean-lap consistency, pace trend when available, and measured tire remaining at a stop. Missing evidence stays missing.
- Race Analysis is a telemetry-first one-screen workspace. The old lower detail tabs and non-useful Setup panel are removed.
- Run, fuel, measured tire, race-shape, strategy, recorded track-area comparison, and pit/repair facts are integrated into the lap rail and compact insight rail.
- The map Y conversion preserves recorded driving direction. Brake map color is neutral at zero and becomes warmer with increasing input.
- The track map aggregates every selected lap at each aligned track position. Its header reports the selected-lap average scope, its cursor reports averaged speed/controls/time delta, and its metric modes use distinct low-to-high strength gradients.
- Trace strokes are thinner and use slight display-only smoothing. The raw analysis data and calculations remain unchanged.
- Steering values are presented as Left/Right degrees.
- Every chart row has a shared multicolor cursor readout with lap number, trace color, value, and unit. Cursor position is derived from rendered bounds.
- Lap rows now use one status icon, a brighter trace-color marker, magenta fastest time, and inline parenthesized pace delta. Pit rows expose a hover/focus service card backed by recorded tire-change, per-corner wear, fuel-added, service-duration, and estimated-range fields; missing fields remain explicit.
- Chart labels and cursor values are larger. The track map uses a tight geometry view box and more of its column height.
- Map pointer conversion now uses the SVG screen transform, then projects onto and interpolates along the rendered polyline. This removes both aspect-ratio offset and curve snapping while keeping the map and chart at one aligned-distance cursor.
- Successful analysis and cache work no longer leaves a completion toast or completed-job tray over the charts.
- Older analyses without detailed trace samples use a compact empty-telemetry state and still derive the fastest clean lap from supported recorded lap timing.
- Tray Exit now hides all product surfaces immediately, returns from the tray callback before disposal, closes WPF/WebView surfaces before backend services, and guarantees one final shutdown attempt even when an individual cleanup step fails.

## Verification at snapshot creation

- Release solution tests: 75 passed, 0 failed.
- Release application build: zero warnings and zero errors.
- Native Windows fixture review covered event opening, whole-row Home navigation, immediate telemetry display, aligned multicolor values, steering direction/units, brake map selection, integrated run detail, and removal of success overlays.
- Repository handoff verifier: passed.

## Known limits

- Sanitized fixture traces intentionally contain only a small selected-lap set; full real recordings are expected to populate the wider lap rail through the production trace payload.
- Some historical analyses do not contain pace slope, measured tire, or control-load fields. The UI shows no invented substitute.
- At narrower desktop widths the insight rail moves below telemetry to protect chart and lap-list readability; at wide desktop widths it remains alongside the telemetry workspace.
