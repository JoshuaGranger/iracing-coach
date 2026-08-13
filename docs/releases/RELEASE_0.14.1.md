# iRacing Coach 0.14.1 Race Analysis regression repair

Date: 2026-08-07

## Artifact identity

- Source commit: `301d7f4b1d5e81c8391b168a982034883aa4fb07`
- Installer: `artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Setup.exe`
- Installer bytes: `498956328`
- Installer SHA-256: `c643529b3896eae74a53171a87c370c033260406ec49d5df1ebe465005dfdde3`
- Portable package: `artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Portable-win-x64.zip`
- Portable bytes: `382961822`
- Portable SHA-256: `a4c9eeeea7cb478fd347005eb0920eded38c81142eda5cbe42f0753fcc02886f`

The package contains 9,278 payload files. The ZIP contains those 9,278 files plus 24 directory entries, with one root application executable and one coach manifest. The packaged runtime is `0.146.0-alpha.9.2`; its measured SHA-256 `ecd7a3eaff5e42723dbba03b5c91514b3986b5db5cbca8f34619620b5356f31f` matches the manifest. The installer reports file version `0.14.1.0` and product version `0.14.1+301d7f4b1d5e81c8391b168a982034883aa4fb07`. Static inspection found no unsafe paths, raw telemetry, secrets, private settings/auth files, or backend user-data directories.

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
