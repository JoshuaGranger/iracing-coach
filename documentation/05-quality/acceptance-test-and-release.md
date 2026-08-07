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

## Current 0.14.1 evidence

Integrated automated runs recorded 140/140 .NET tests, 187/187 Python tests, four JavaScript syntax checks, and a Release application build with zero warnings and zero errors. A fresh mouse-driven saved-race walkthrough confirmed fastest-three defaults, bidirectional track/chart cursor synchronization, stable Telemetry/Review scrollbar width, and Home-matched green/caution styling. The inherited 0.14.0 coverage still includes session-boundary resets, bounded telemetry projection/backpressure, exact Race/Qualifying identity and caches, durable-state serialization, comparable coaching evidence, missing-channel preservation, corner summaries, catalog parity, and versioned grading. Historical privacy and installer-lifecycle evidence remains historical; unchanged upgrade/uninstall behavior was not rerun for this focused correction. No real-telemetry, source-cadence, 244 Hz presentation, or user acceptance is claimed here.
