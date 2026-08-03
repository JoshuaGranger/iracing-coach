# iRacing Coach companion app — historical v0.3 development report

> This file is retained as the historical v0.3.0 milestone record. It no longer describes current storage or packaging. The current portable archive, migration, and uninstall release is documented in [PORTABLE_ARCHIVE_ACCEPTANCE_0.6.0.md](PORTABLE_ARCHIVE_ACCEPTANCE_0.6.0.md); Coach Engine v0.5 and Live Monitor milestone reports remain historical records.

Date: 2026-08-02  
App version: 0.3.0  
Backend: `iracing-coach-local` 0.3.0  
MCP protocol: 2025-06-18  
Contract surface: MCP v1, 16 tools

## What works now

- A packaged .NET 10 Windows application using the existing WPF + Blazor Hybrid architecture.
- A quieter black/gray UI, themed native title bar, matching application icon, correct time-based greeting, no manual dashboard refresh controls, and header status only for actionable errors.
- Six personal-tool navigation areas: Home, Race Analysis, Race Planning, Setups & Packages, Progressive Tuning, and Settings. Diagnostics is part of Settings.
- Automatic local discovery backed only by real iRacing/backend data. No sample data is inserted into production state.
- An event-based Race Analysis browser with search and truthful filters, Qualify/Race grouping, reconnect-file grouping, single-click preview, and explicit deep analysis.
- A deterministic Race Card that preserves derived, inferred, external, user-reported, and unavailable evidence labels.
- Race Planning defaults to the latest raced car and combines real recorded, installed, and setup-linked cars found on the PC.
- Setups & Packages lists only real local `.sto` artifacts and treats them as read-only.
- Progressive Tuning starts from an analyzed open-setup race, uses its embedded setup/telemetry context, recommends one controlled change, preserves rollback identity, and records the outcome.
- Portable settings at `Documents\iRacing Coach\settings.json`, including the Garage61 key by explicit product decision. The key is masked after saving and excluded from diagnostics/log output.
- The backend Garage61 bridge reads that portable setting without putting the key in command-line arguments, environment variables, or logs.
- A v0.3.0 self-contained installer with staged/rollback-safe replacement of Program Files app files while leaving the portable Documents repository untouched.

## Visual QA

- [Populated Race Analysis and Race Card](artifacts/qa/race-card-populated.png)
- [Final Settings screen](artifacts/qa/settings-final.png)

The populated screenshots use only the sanitized handoff fixtures behind a preview-only environment switch. Production runtime state remains unseeded.

## Build and package

- Installer: `artifacts/dist/iRacingCoach-0.3.0-Setup.exe`
- SHA-256 file: `artifacts/dist/iRacingCoach-0.3.0-Setup.exe.sha256`
- Installer size: 368,224,294 bytes
- SHA-256: `90be9ca214183841760a182e97b427eb07cefdf8002387ae46d3249e12a6ea27`

The installer was test-extracted into an isolated Windows temporary folder. Required app, uninstaller, Python runtime, MCP configuration, backend, and skill files were present. The installed application stayed running for a five-second startup smoke test. The temporary installation was then removed.

## Verification

- Full solution build: succeeded with 0 warnings and 0 errors.
- Companion app tests: 18 passed, 0 failed.
- Backend tests: 173 passed.
- Handoff verifier: passed.
- Verified handoff contents: 109 files, 1,946,977 bytes, 17 contracts, 14 fixtures, 16 MCP tools.
- Synthetic MCP end-to-end check: passed with high data quality and the expected subsession.
- Browser QA covered every primary navigation area plus event filters/search, race selection, Analyze/Open behavior, the full Race Card, planning lookup, setup empty state, tuning recommendation/feedback, settings save/reset, diagnostics health/installation checks, navigation collapse, reduced-motion toggle, notifications, and the background-job tray.

## Measured performance

- Uncached sanitized synthetic IBT analysis: 41.43 ms backend time.
- Immediate repeat of the same synthetic analysis: 41.98 ms and the same analysis ID. The current analysis tool still performs work on repeat; this is not claimed as a cache-speedup.
- Cached 24,290-byte Race Card JSON parse + UI-model mapping: 0.1424 ms median, 0.2137 ms p95, 0.4798 ms max over 2,000 iterations.

These are synthetic-fixture measurements, not a claim about a long real-world telemetry file. Representative real-file timing remains a next-round benchmark.

## Remaining limitations

### Product

- Deep Race Analysis still needs strict grading, run/lap/flag/sector/pit navigation, synchronized vector track maps and traces, Target Lap, tire-phase calibration, and full interruption/damage views.
- Race Planning currently uses comparable local history; Upcoming Event selection, full manual planning, qualifying, fuel/pit/caution outputs, brake bias, and car-specific in-car adjustments remain.
- Setups & Packages currently enables Library only. Compare, editable package worksheets, and package assembly remain disabled rather than pretending to work.
- Progressive Tuning uses typed feedback today. The clickable car/track graphical feedback builder and conflict/trade-off reasoning remain.

### Data

- Official/hosted/league/AI labels and related filters are shown only when the backend has recorded evidence. Unknown event scope stays `[U] Not recorded`.
- The car selector uses recorded, installed, and setup-linked cars. Exact iRacing entitlement/ownership discovery still needs a reliable local or approved external source.
- iRacing `.sto` files remain opaque and read-only. Embedded IBT setup data is authoritative for what was driven.
- Real-file cached/uncached benchmarks and broad state screenshots still need a representative set of the user's races.

### External access

- Garage61 authentication/status is connected through portable settings, but Garage61 Pro indexing, exact-layout matching, and lap/corner comparison await the approved API surface and real response-contract validation.
- Codex coaching is optional and is not required for deterministic local analysis. Deeper AI orchestration and provenance remain a later milestone.

## Non-destructive install or upgrade on the racing PC

1. Back up or copy the entire `Documents\iRacing Coach` folder. It contains portable settings, analysis/history, logs, setup copies, and source/build artifacts. Keep it private because `settings.json` contains the portable Garage61 key.
2. Close iRacing Coach.
3. Copy `iRacingCoach-0.3.0-Setup.exe` and its `.sha256` file to the racing PC and verify the SHA-256 value above.
4. Run the installer. It stages the new Program Files payload, keeps the previous payload until replacement succeeds, and does not remove or overwrite the Documents repository.
5. Open iRacing Coach, then use Settings → Run health test and Verify installation. Confirm the portable repository, iRacing Documents, and iRacing installation paths.

## Prioritized next round

1. Implement Milestone 2 deep Race Analysis: grades, runs/laps, flag and pit timelines, synchronized traces, vector track map, Target Lap, tire phases, and damage/repair evidence.
2. Add representative real-IBT performance benchmarks and the remaining populated/empty/loading/unavailable/warning/repair/long-content visual-state matrix.
3. Complete Upcoming Event and Manual Plan, including qualifying and car-appropriate strategy/in-car guidance.
4. Build Setup Compare and editable package worksheets, then package assembly.
5. Keep supported-STO-writing research isolated: document formats and simulator-supported creation/import paths, validate checksums/versioning, and do not ship a writer until it is independently proven safe.
6. Add the graphical Progressive Tuning feedback builder and explicit conflict/trade-off reasoning.
7. Add Garage61 Pro cache/index workflows and then AI refinement with source provenance.
8. Add tire calibration and the semi-live local sidecar only after offline analysis remains complete and reliable.
