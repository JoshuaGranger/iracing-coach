# Acceptance, Test, and Release

Quality claims must identify the environment, artifact, fixture or real source, test command, and result. A passing unit test is not interchangeable with installed-product, visual, performance, or real-telemetry acceptance.

## Quality gates

| ID | Requirement |
| --- | --- |
| `QA-001` | A release candidate MUST build from a clean checkout with no unexplained warnings. |
| `QA-002` | All .NET and Python automated tests in the release contract MUST pass. |
| `QA-003` | Contract and packet verifiers MUST pass against the exact candidate artifacts. |
| `QA-004` | Core workflows MUST be exercised in the packaged app, including keyboard operation, error states, empty states, and representative data density. |
| `QA-005` | Every interactive control in release scope MUST be opened or invoked and checked for a visible, correct result; disabled controls require a truthful reason. |
| `QA-006` | Visual review MUST cover supported window sizes, scaling factors, long values, truncation, scroll behavior, focus, contrast, and title/icon consistency. |
| `QA-007` | Installer, prior-version replacement, rollback, uninstall, reinstall, and data preservation MUST be tested on the exact package. |
| `QA-008` | Secret/privacy scanning MUST cover source, staged repository content, release payload, logs, screenshots, and support bundles. |
| `QA-009` | Real iRacing telemetry claims MUST be supported by direct observation on a real supported environment and MUST NOT be claimed from replay fixtures alone. The evidence MUST identify the executable/commit, environment, source scenario, date, and observed result. |
| `QA-010` | A failed acceptance item MUST remain visible with evidence and ownership; it may not be rewritten as passed because adjacent tests succeeded. |
| `QA-011` | A live-cadence or high-refresh claim MUST report source-frame cadence separately from paint cadence. Evidence MUST include the exact executable/commit, real or replay source, display refresh mode, observation interval, delivered/dropped frames, latency or stutter metric, and hardware. A `requestAnimationFrame` implementation is not by itself an acceptance result. |

## Release record

| ID | Requirement |
| --- | --- |
| `REL-001` | Each released artifact MUST have a version, immutable hash, build provenance, compatibility contract, and test summary. |
| `REL-002` | Release notes MUST distinguish new behavior, corrected defects, known limitations, acceptance pending, and data/schema changes. |
| `REL-003` | The repository commit used for a build MUST be identifiable from the release record. |
| `REL-004` | A release MUST NOT include secrets, personal telemetry, local settings, build credentials, or unapproved user data. |
| `REL-005` | Fixture-mode screenshots and tests MUST be labeled and MUST not imply real-service or real-telemetry acceptance. |
| `REL-006` | Acceptance evidence SHOULD be machine-readable enough for another agent to independently verify file hashes, counts, and assertions. |

## Current 0.13.0 evidence

Integrated automated runs recorded 108/108 .NET tests, 175/175 Python tests, four JavaScript syntax checks, and a Release application build with zero warnings and zero errors. Those checks cover the changed Home/cache projections, equal-share Live Telemetry layout and replacement path, display-only popout, configurable responsive Race Analysis traces, cursor boundary, compact Settings structure, and tray-shutdown contract. The exact post-fix tray-runtime observation and final 0.13.0 installer/portable hashes remain explicitly pending in `06-reality/implementation-snapshot-0.13.0.md`; neither is implied by the automated counts. Exact historical artifact hashes, privacy scans, packaged-app checks, and guarded installer lifecycle results remain recorded in the applicable `companion-app/RELEASE_*.md` files. Focused UI iterations do not repeat unchanged installer/upgrade checks; an actual packaged release still must satisfy every claimed release gate. No real-telemetry, 60 Hz source-capture, or 244 Hz presentation acceptance is claimed here.
