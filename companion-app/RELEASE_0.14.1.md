# iRacing Coach 0.14.1 Race Analysis regression repair

Date: 2026-08-07

## Artifact identity

- Source commit: `PENDING_SOURCE_COMMIT`
- Installer: `artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Setup.exe`
- Installer bytes: `PENDING`
- Installer SHA-256: `PENDING`
- Portable package: `artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Portable-win-x64.zip`
- Portable bytes: `PENDING`
- Portable SHA-256: `PENDING`

The measured values above are completed only after packaging from the named source commit. This release does not claim a repeated installer lifecycle matrix for unchanged installer behavior.

## Release focus

This is a narrow Race Analysis correction on top of 0.14.0. It repairs two interaction/default regressions and two presentation inconsistencies without changing portable-data, cache, analysis-payload, or backend schema versions.

## Corrected behavior

- Track-map and chart interaction now share one browser-side, animation-frame cursor owner. Chart movement updates the map marker, chart crosshair, tooltip values, and summary together; map movement updates the projected marker and summary without showing chart-only cursor popups.
- Removed the asynchronous Blazor map-move path that dropped intermediate pointer events and could disagree with the chart cursor.
- Automatic comparison now selects the fastest three complete, timed, non-pit, non-caution, non-incident laps. Coaching-reference exclusions such as traffic context no longer disqualify otherwise usable visual comparison laps.
- If no clean comparison laps exist, the fallback chooses the fastest usable timed laps instead of the first three recorded rows.
- The main workspace permanently reserves its vertical scrollbar gutter, so the Race/Qualifying selector does not shift when switching between Telemetry and shorter Race review content.
- Race Analysis event rows reuse Home's green/yellow status dots and neutral text treatment for green and caution lap counts.

## Regression causes

- The fastest-three regression was introduced in 0.14.0 when the visual lap picker reused the stricter coaching-reference `IsComparable` gate. For the Iowa recording, that rejected every candidate and activated a fallback that selected the first three recorded laps.
- The cursor regression originated in the 0.13 browser-cursor refactor. Chart movement moved to JavaScript and `requestAnimationFrame`, while the map retained an asynchronous Blazor `TrackMoved` handler guarded by `_trackMovePending`. The two paths owned separate cursor fractions and the guard discarded map events under normal movement.

## Verification completed before packaging

- Required handoff verifier: passed.
- .NET: 140 passed, 0 failed.
- Python: 187 passed, 0 failed.
- JavaScript syntax: 4 passed.
- Release application build: 0 warnings, 0 errors.
- Fresh Release UI walkthrough: Iowa opened with laps 10, 12, and 47 selected; separated chart/map positions moved the map marker and readout without exposing chart-only popups over the map; Telemetry and Race review switched without horizontal session-selector movement; Race Analysis green/caution styling matched Home.

## Known limits and acceptance pending

- The interaction walkthrough used saved local recordings, not a live iRacing session.
- Real shared-memory cadence, reconnect behavior, 244 Hz display delivery, combined-load performance, and while-driving usefulness remain separate acceptance work.
- Unchanged install replacement, rollback, uninstall, reinstall, and durable-data preservation behavior was not rerun for this focused UI correction.
- User acceptance remains pending.
