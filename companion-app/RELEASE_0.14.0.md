# iRacing Coach 0.14.0 corrective robustness release

Date: 2026-08-07

## Artifact identity

- Source commit: `PENDING_FINAL_SOURCE_COMMIT`
- Executable: `src/iRacingCoach.App/bin/Release/net10.0-windows10.0.17763.0/iRacing Coach.exe`
- Installer: `artifacts/dist/v0.14.0/iRacingCoach-0.14.0-Setup.exe`
- Installer bytes: `PENDING_FINAL_INSTALLER_BYTES`
- Installer SHA-256: `PENDING_FINAL_INSTALLER_SHA256`
- Portable package: `artifacts/dist/v0.14.0/iRacingCoach-0.14.0-Portable-win-x64.zip`
- Portable bytes: `PENDING_FINAL_PORTABLE_BYTES`
- Portable SHA-256: `PENDING_FINAL_PORTABLE_SHA256`
- Payload and embedded-runtime identity: `PENDING_FINAL_PAYLOAD_IDENTITY`
- Installer product revision: `PENDING_FINAL_PRODUCT_REVISION`

These placeholders are release blockers. They must be filled from the final measured package and must not reuse values from 0.13.0.

## Release focus

This is a corrective robustness and performance release for the existing feature set. It does not broaden product scope. The main changes make Live Telemetry capture/presentation more bounded and responsive, make Race/Qualifying identity fail closed, harden concurrent durable migration, and make Race Analysis conclusions consistently use comparable evidence.

## Corrected behavior

### Live Telemetry and popout

- Replaced the coarse high-rate thread-pool polling path with a dedicated Windows waitable-timer reader: 2 ms connected observation and 40 ms disconnected discovery, with safe fallback and bounded disposal.
- Counted skipped source ticks separately from display frames.
- Bounded history projection and pending UI work. Latest-value compaction preserves newest frames, missing-data gaps, and material extrema under backpressure.
- Avoided trend-history work for scalar-only reads, cached/bounded trend seeds, and lazy-mounted detailed traces.
- Reset coaching evidence at session boundaries and rejected partial, skipped, caution, pit, repair, tow, black-flag, or missing-dynamics evidence for claims it cannot support.
- Required two complete clean burns before projecting fuel and defined run phase as observed clean laps since pit road.
- Kept automatic popout showing non-activating, preserved manual hide until reconnect, treated normal popout Close as hide, and explicitly destroyed the popout during app exit.

### Event identity, caches, and durable history

- Made discovery `group_id` the primary selector while retaining numeric SubSessionID fallback.
- Rejected wrong-phase and same-phase/wrong-group backend responses and caches instead of relabeling them.
- Advanced coordinator UI analysis cache to schema 6 with selector-plus-phase keying and validation.
- Qualified deterministic core cache identity and `analysis_id` with group/SubSession/session-number/phase identity while preserving base IDs for unknown legacy inputs.
- Advanced deterministic backend archive/index/cache to schema 2 with persisted session identity and exact-group, then SubSession-plus-phase, history joins.
- Hardened legacy SQLite migration with a 30-second busy timeout, retry of transient busy/locked startup, `BEGIN IMMEDIATE`, idempotent column/index creation, and report-backed backfill.
- Serialized durable archive writes that target the same canonical path, gave every write a unique temporary file, and removed that temporary file after success or failure so concurrent state updates cannot share or strand it.

### Race Analysis

- Centralized comparable-lap eligibility across fastest lap, overview, run, track, and review calculations.
- Excluded incidents and recorded reason-code-confounded laps from comparisons.
- Made corner review equal-weight per comparable lap, preserved missing channels, and included all detected track areas.
- Attached pit service evidence to the service-ending run and kept factual longest-run counts distinct from comparable pace trend.
- Added the versioned `race-execution-v2` evidence-weighted rubric without fabricating Strategy or Racecraft from pit, tow, or position counts.

## Data and compatibility

- Top-level portable archive manifest: schema 1, unchanged.
- Portable settings: schema 4, unchanged.
- Coordinator UI analysis cache: schema 6.
- Deterministic backend archive/index/cache: schema 2.
- Deterministic analysis payload: schema 2.

These numbers belong to separate schema namespaces. Backend schema 2 does not mean that `Documents\iRacing Coach\archive-manifest.json` changed from schema 1.

## Verification completed before packaging

- .NET: 138 passed, 0 failed, including catalog parity and concurrent same-path durable-state write/temp-cleanup regressions.
- Python: 187 passed, 0 failed.
- JavaScript syntax: 4 passed.
- Release application build: zero warnings, zero errors.
- Synthetic high-rate reader: approximately 170 frames captured in approximately 810 ms from a synthetic 240 Hz source, demonstrating removal of the former approximately 125 Hz structural polling ceiling.
- Fresh source-tree Release UI walkthrough covered Home, Race browser, automatic Race/Qualifying telemetry loading, Race Telemetry/Review, trace configuration/fullscreen, Live Telemetry customization, native popout, Settings, and maximized/normal layouts.

The synthetic cadence result is not a real iRacing SDK measurement and is not acceptance of 244 Hz presentation. Final package hashes, payload inspection, and source revision remain pending above.

## Known limits and acceptance pending

- Real iRacing cadence, reconnect coverage, high-refresh display delivery, combined-load performance, and while-driving usefulness require measurement on the racing PC with the exact packaged artifact.
- A genuine mouse-driven tray-menu Exit against the exact package remains pending; a normal main-window close configured to minimize to tray is not equivalent.
- Derived vehicle sideslip remains narrowly validated rather than proven across all cars and sessions.
- Exhaustive DPI, screen-reader, keyboard, narrow-window, and multi-monitor coverage remains pending.
- The unchanged full installer upgrade/rollback/uninstall lifecycle matrix was not repeated for this focused release. Prior evidence is not relabeled as 0.14.0 evidence.
- User acceptance remains pending.
