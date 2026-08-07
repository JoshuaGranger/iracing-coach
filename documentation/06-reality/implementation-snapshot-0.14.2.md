# Implementation Snapshot: 0.14.2

Status: focused Race Analysis trace-editor parity repair. Source and fresh Release executable are locally verified; final package measurements are recorded after packaging from the named source commit.

Evidence date: 2026-08-07.

## Scope and baseline

Version 0.14.2 inherits the 0.14.1 architecture, feature set, schema contracts, and open acceptance boundaries. It changes the Race Analysis trace-editor interaction and label presentation without changing telemetry, analysis, archive, cache, or portable-settings schemas.

- Application, installer, uninstaller, release script, backend client identity, repair-cache filename, and browser cache keys identify 0.14.2.
- Portable settings remain schema 4.
- Coordinator UI analysis caches remain schema 6.
- Durable archive manifest remains schema 1.
- Deterministic backend archive/index/cache and analysis payloads remain schema 2.

## Implemented reality

### Direct trace editing

Race Analysis uses a persistent right-side toolbox and the visible one-column chart as its editing canvas. Telemetry library cards are draggable across their full surface except for their add buttons. A card dropped between charts creates a row when capacity permits; a card dropped on a one-trace row becomes the second trace; a card dropped on a two-trace row replaces the second trace with explicit replacement feedback. Dragging a chart title reorders the rows by insertion.

The pointer engine has a five-pixel activation threshold, pointer capture, floating drag ghost, chart/insertion preview, auto-scroll, Escape/blur/lost-capture cancellation, mutation-on-release, commit locking, reduced-motion support, and post-commit reflow animation. Successful mutations persist through `CompanionSettings.RaceAnalysisTraces`.

### Compact trace labels

The SVG no longer renders trace names. An HTML label column overlays the existing chart gutter. Each bold title wraps naturally within `12ch`; the unit appears below it in normal weight. The title tooltip contains line-style identity, independent-scale guidance, and selected-lap missing-data notes. The chart surface therefore carries only race-specific data and compact labels.

## Recorded evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| Required handoff verifier | Passed | Repository contract/handoff consistency |
| Integrated .NET suite | 144 passed, 0 failed | Source and contract behavior |
| Integrated Python suite | 187 passed, 0 failed | Deterministic backend regression coverage; backend behavior unchanged |
| JavaScript syntax | 5 passed | Parseability, not measured pointer latency |
| Release application build | 0 warnings, 0 errors | Fresh source-tree executable |
| Mouse-driven saved-race walkthrough | Wrapped name/unit labels and title tooltip verified; toolbox opened without shrinking the chart; row-title drag reordered Speed below Time delta; full-card Brake drag paired it with Time delta; Reset restored all default rows/pairs | Local saved recording, not live simulator acceptance |

## Artifact record

- Source commit: `PENDING`
- Installer: `companion-app/artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Setup.exe`
- Installer bytes: `PENDING`
- Installer SHA-256: `PENDING`
- Portable package: `companion-app/artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Portable-win-x64.zip`
- Portable bytes: `PENDING`
- Portable SHA-256: `PENDING`
- Static payload identity and hygiene: `PENDING`.

## Open acceptance boundaries

- Saved-race interaction does not establish real iRacing capture cadence, reconnect behavior, latency, display delivery, or while-driving usefulness.
- The unchanged installer replacement, rollback, uninstall, reinstall, and durable-data-preservation matrix is deliberately not repeated.
- Broad DPI, accessibility, keyboard, multi-monitor, and representative-recording coverage remains open unless separately recorded against this exact artifact.
- Joshua has not accepted 0.14.2 merely because automated and local interaction checks pass.
