# Implementation Snapshot: 0.14.1

Status: focused Race Analysis regression repair, source and fresh Release executable locally verified. Final package measurements are recorded after packaging from the named source commit.

Evidence date: 2026-08-07.

## Scope and baseline

Version 0.14.1 inherits the 0.14.0 architecture, feature set, schema contracts, and open acceptance boundaries. This snapshot records only the functional differences and new evidence. The 0.14.0 snapshot remains the detailed baseline.

- Application, installer, uninstaller, release script, backend client identity, repair-cache filename, and browser cache keys identify 0.14.1.
- Portable settings remain schema 4.
- Coordinator UI analysis caches remain schema 6.
- Durable archive manifest remains schema 1.
- Deterministic backend archive/index/cache and analysis payloads remain schema 2.

## Implemented reality

### Fastest-three comparison default

The visual lap picker uses display-specific clean-lap rules: a candidate must be complete, have a finite positive lap time, contain no pit time, contain no caution/yellow state, and have no recorded incident points. Candidates are ordered by lap time and then lap number. The first three are selected automatically. When there are no clean candidates, fallback selection is still time-ordered and never silently becomes the first three recorded rows.

This is intentionally different from coaching-reference comparability. Traffic, restart, repair-context, and other reason-code exclusions can make a lap unsuitable as coaching evidence without making its recorded trace unsuitable for direct visual comparison.

### One cursor owner

The browser cursor module owns pointer enter, move, and leave for both the trace SVG and track SVG. Track coordinates are mapped through the SVG screen transform and projected to the nearest track segment. The resulting fraction drives the map marker and aggregate readout in the same `requestAnimationFrame` render; when the chart itself is hovered it also drives the chart crosshair and per-lap markers/tooltips. Chart-only popups remain hidden while the pointer is over the map. Track listeners are rebound when the rendered SVG changes and removed during disposal.

The former Blazor `TrackMoved` handler, `_trackMovePending` event-dropping guard, and duplicate C# projection method are absent.

### Stable layout and condition styling

The shared scrollable workspace uses `scrollbar-gutter: stable`, reserving vertical scrollbar width on both long and short Race Analysis sections. Race Analysis event rows reuse Home's green/yellow metric-dot classes, glow, and neutral primary/muted text colors.

## Recorded evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| Required handoff verifier | Passed | Repository contract/handoff consistency |
| Integrated .NET suite | 140 passed, 0 failed | Source and contract behavior |
| Integrated Python suite | 187 passed, 0 failed | Deterministic backend regression coverage; backend behavior was unchanged |
| JavaScript syntax | 4 passed | Parseability, not measured pointer latency |
| Release application build | 0 warnings, 0 errors | Fresh source-tree executable |
| Mouse-driven saved-race walkthrough | Iowa automatically selected laps 10, 12, and 47; separated chart/map positions synchronized the marker and readout while chart-only popups stayed off the map; Telemetry/Review switch did not move Race/Qualifying; event-list status colors matched Home | Local saved recording, not live simulator acceptance |

## Artifact record

- Source commit: `301d7f4b1d5e81c8391b168a982034883aa4fb07`
- Installer: `companion-app/artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Setup.exe`
- Installer bytes: `498956328`
- Installer SHA-256: `c643529b3896eae74a53171a87c370c033260406ec49d5df1ebe465005dfdde3`
- Portable package: `companion-app/artifacts/dist/v0.14.1/iRacingCoach-0.14.1-Portable-win-x64.zip`
- Portable bytes: `382961822`
- Portable SHA-256: `a4c9eeeea7cb478fd347005eb0920eded38c81142eda5cbe42f0753fcc02886f`
- Static payload identity and hygiene: 9,278 payload files; ZIP contains 9,278 files plus 24 directory entries; one root application executable; one coach manifest identifying application `0.14.1`; runtime `0.146.0-alpha.9.2`; measured and manifested runtime SHA-256 both `ecd7a3eaff5e42723dbba03b5c91514b3986b5db5cbca8f34619620b5356f31f`; installer file version `0.14.1.0`; installer product version `0.14.1+301d7f4b1d5e81c8391b168a982034883aa4fb07`; no unsafe paths, raw telemetry, secrets, private settings/auth files, or backend user-data directories found.

## Open acceptance boundaries

- Saved-race interaction does not establish real iRacing capture cadence, reconnect behavior, latency, display delivery, or while-driving usefulness.
- The unchanged installer replacement, rollback, uninstall, reinstall, and durable-data-preservation matrix was deliberately not repeated.
- Broad DPI, accessibility, keyboard, multi-monitor, and representative-recording coverage remains open unless separately recorded against this exact artifact.
- Joshua has not accepted 0.14.1 merely because automated and local interaction checks pass.
