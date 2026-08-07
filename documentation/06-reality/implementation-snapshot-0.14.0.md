# Implementation Snapshot: 0.14.0

Status: versioned 0.14.0 corrective source candidate with integrated automated verification and fresh development-executable interaction. Final installer and portable-package identity remain explicit placeholders until the release build is produced and measured.

Evidence date: 2026-08-07.

## How to read this snapshot

This file records functional reality, not desired behavior. Normative intent remains in `00-governance` through `05-quality`. An **Implemented reality** statement describes inspected production source. A **Recorded evidence** statement identifies a completed check. An **Open acceptance boundary** is incomplete and may not be inferred from a neighboring test.

The executable identity matters. Screenshots or observations from an installed older version are not evidence for 0.14.0. The interaction evidence below names the fresh Release executable from this source tree; final release claims must name the exact packaged artifact, source commit, and hash.

## Version and schema compatibility

- Application, installer, uninstaller, release script, coordinator client identity, and cache-download naming are set to `0.14.0` in current source.
- Portable settings remain settings schema 4.
- Coordinator UI analysis caches are schema 6 and persist both selector and phase with the backend response.
- The top-level portable `archive-manifest.json`, owned by the C# `DurableArchiveService`, remains durable-archive manifest schema 1.
- The deterministic Python backend uses archive/index/cache schema 2. It adds session identity to its cache and history contracts and migrates the SQLite index. This is a separate schema namespace and does **not** change the top-level portable manifest to schema 2.
- Deterministic analysis payloads use analysis schema 2. That payload version is also distinct from the four storage/settings contracts above.

## Implemented reality

### Live reader, evidence, and backpressure

- Live SDK observation runs on a dedicated background thread rather than the former thread-pool timer. On Windows it requests a high-resolution waitable timer with a 2 ms interval while connected and a 40 ms discovery interval while disconnected; it falls back to a normal waitable timer or bounded thread sleep when necessary.
- Source tick deltas feed the dropped-frame count. Presentation frames do not masquerade as captured SDK samples.
- Connection changes, clock regression, and lap regression reset session-scoped evidence and history. Partial first laps, skipped intervals, caution, pit, repair, tow, black-flag, and missing-dynamics evidence are rejected for claims they cannot support.
- Fuel projection requires at least two complete clean observed burns. Run phase counts observed clean laps since pit road rather than claiming unrecorded service.
- Scalar reads avoid unnecessary history projection. Trend seeds, retained history, and UI pending work are bounded. When producers outrun presentation, compaction favors the newest frames while retaining semantic gaps and material minima/maxima instead of letting an unbounded queue grow.
- Detailed driving traces lazy-mount only when expanded. The full page and native popout retain timestamped rendering paths without inventing Y values between source samples.
- Automatic native-popout showing is non-activating. Manual hide remains respected for the connected session, ordinary popout Close hides rather than destroys it, and application shutdown explicitly destroys the popout.

### Exact Race and Qualifying identity

- Discovery `group_id` is the primary analysis selector. Numeric SubSessionID remains a backward-compatible fallback when exact group identity is unavailable.
- Interactive and background responses must match the requested phase and, when present, the exact group selector. Sibling-phase and same-phase/different-event responses are rejected rather than relabeled.
- Schema-6 UI cache keys and validation contain both selector and phase. Cached response metadata is checked again when read.
- The deterministic core cache identity contains the normalized selection identity. Emitted `analysis_id` values are phase/group-qualified from the base analysis fingerprint; unknown legacy/file-only inputs keep their base identity instead of receiving invented phase data.
- Backend history rows persist group ID, SubSessionID, simulator session number/type, and normalized phase. Reopening and historical comparisons prefer exact group, then SubSessionID plus phase, before narrower legacy fallbacks.
- Backend archive/index/cache schema 2 migrates legacy history rows by reading their saved `analysis.json` when available. SQLite initialization uses a 30-second busy timeout, retries transient busy/locked opens, enters `BEGIN IMMEDIATE`, and performs idempotent column, index, and backfill work before commit.
- The quiet Home queue processes finalized Race recordings sequentially, yields before another item while live telemetry or interactive analysis is active, and updates Home/Race projections only with the accepted exact-event result.

### Race Analysis truth and presentation

- Comparable-lap eligibility is mapped once and reused by fastest-lap, overview, run, track, and review calculations. Incidents and recorded exclusion reasons no longer contaminate those summaries.
- Race review corner summaries use comparable non-incident laps, weight laps equally, preserve missing speed/brake evidence, and include all detected track areas rather than truncating after four.
- Pace trend uses a comparable run while factual longest-run counts remain factual. Pit service evidence attaches to the service-ending run.
- Missing channels remain unavailable rather than becoming zero. Derived vehicle sideslip remains guarded, provenance-labeled, and distinct from Yaw Rate and tire slip.
- The `race-execution-v2` grade rubric uses configured weights of Pace 30%, Consistency 20%, Tire Management 20%, Strategy 15%, and Racecraft 15%, normalized only across categories with supported driver-attributable evidence. Raw position change, tow, and pit counts do not fabricate unavailable categories, and A+ remains gated without a comparable external cohort.
- Race and Qualifying open as distinct phase-qualified views. Race telemetry begins loading on open without requiring a phase-tab detour, and the UI cache cannot silently substitute its sibling phase.

### Settings and shutdown

- Settings retains its compact task-first hierarchy with subordinate path, connection, and troubleshooting disclosures. External credentials remain machine-bound and outside portable data.
- Durable archive state writes targeting the same canonical path are serialized through a per-destination process gate. Each write uses a unique temporary file followed by an atomic replacement, and the temporary file is removed in `finally`, including after a failed write. This prevents concurrent `MarkActive` and related state updates from sharing or stranding temporary files within one app process.
- Main-window Close follows the configured close-to-tray preference. The explicit tray Exit path remains a separate operation that arms its shutdown deadline and is intended to terminate workers and process state completely.

## Recorded evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| Integrated .NET test run | 138 passed, 0 failed | Automated source/contract behavior, including catalog parity and concurrent same-path durable-state serialization/temp cleanup; not installed-product acceptance |
| Integrated Python backend test run | 187 passed, 0 failed | Deterministic backend behavior, including exact phase/group identity and concurrent migration; not real-session telemetry acceptance |
| JavaScript syntax checks | 4 passed: Race telemetry cursor, live page chart, live layout, and live tile-chart modules | Parseability only; not frame-rate or interaction acceptance |
| Release application build | 0 warnings, 0 errors | Fresh source-tree Release executable, not yet the final packaged artifact |
| Synthetic high-rate reader check | Approximately 170 frames captured in approximately 810 ms from a synthetic 240 Hz source | Demonstrates that the reader is not structurally capped near the former 125 Hz loop; not iRacing, display, latency, or 244 Hz acceptance |
| Fresh Release UI walkthrough | Home, Race browser, automatic Race/Qualifying telemetry loading, Race Telemetry/Review, trace Toolbox/fullscreen, Live Telemetry editor, native popout, Settings disclosures, maximized and normal layouts exercised | Development executable and available local recordings; not exhaustive DPI/accessibility or packaged-app acceptance |
| Packaged-payload smoke | Exact staged `iRacing Coach.exe` launched and rendered Home plus the enriched Race Analysis catalog | Startup/navigation evidence for the packaged payload; not an installer lifecycle, tray Exit, or real-session test |

## Final artifact record

The following values were measured from the final `BuildRelease.ps1` output built from the named source commit.

- Source commit: `43eb62b050961483a2603b0ac2b08b7a84cd8dd0`
- Installer: `companion-app/artifacts/dist/v0.14.0/iRacingCoach-0.14.0-Setup.exe`
- Installer bytes: `498952232`
- Installer SHA-256: `df41a55781c41b932a3ea849107e87fab56cd7b903481bf3bd8cb59377d50621`
- Portable package: `companion-app/artifacts/dist/v0.14.0/iRacingCoach-0.14.0-Portable-win-x64.zip`
- Portable bytes: `382959531`
- Portable SHA-256: `9ad2c85cf92d788c1f3acc8091a75c70deaef807a4d57244774d22ffa17250e2`
- Packaged payload count and manifest/runtime identity: `9278 files`; ZIP has 9278 file entries and 24 directory entries; one root app executable; one coach-engine manifest; manifest app `0.14.0`; runtime `0.146.0-alpha.9.2`; runtime SHA-256 `ecd7a3eaff5e42723dbba03b5c91514b3986b5db5cbca8f34619620b5356f31f` matches the embedded signed Codex executable
- Installer product revision: `0.14.0+43eb62b050961483a2603b0ac2b08b7a84cd8dd0`
- Static package hygiene: both sidecars match; installer file version is `0.14.0.0`; ZIP has no unsafe paths; payload contains no `.ibt`, `.log`, private settings, credentials, authentication state, or portable archive-state files

## Open acceptance boundaries

- Real iRacing shared-memory coverage, source cadence, reconnect behavior, and usefulness while driving remain unaccepted.
- The synthetic 240 Hz reader result is not acceptance of 60 Hz real-SDK capture or 244 Hz presentation. Display delivery requires the exact packaged executable, display mode, hardware, source, interval, delivered/dropped frames, and latency/stutter measurements.
- Representative-library background-analysis throughput and combined live/analysis load remain unmeasured on the racing computer.
- A genuine mouse-driven click of the final tray `Exit` item against the exact 0.14.0 package remains unobserved. Closing the main window under a configured close-to-tray preference is not the same test.
- Derived sideslip remains narrowly evidenced and requires broader car, track, and session validation.
- Exhaustive keyboard, screen-reader, high-DPI, narrow-window, and multi-monitor validation remain open unless separately recorded against the exact artifact.
- Installer replacement, rollback, uninstall, reinstall, and durable-data preservation were not rerun for this focused corrective iteration. Historical lifecycle evidence remains historical.
- Joshua has not accepted 0.14.0 merely because this snapshot exists or automated tests pass.
