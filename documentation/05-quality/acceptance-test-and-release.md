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
| `QA-009` | Real iRacing telemetry acceptance MUST be performed by HOME_QA on a real supported environment and MUST NOT be claimed from replay fixtures. |
| `QA-010` | A failed acceptance item MUST remain visible with evidence and ownership; it may not be rewritten as passed because adjacent tests succeeded. |

## Release record

| ID | Requirement |
| --- | --- |
| `REL-001` | Each released artifact MUST have a version, immutable hash, build provenance, compatibility contract, and test summary. |
| `REL-002` | Release notes MUST distinguish new behavior, corrected defects, known limitations, acceptance pending, and data/schema changes. |
| `REL-003` | The repository commit used for a build MUST be identifiable from the release record. |
| `REL-004` | A release MUST NOT include secrets, personal telemetry, local settings, build credentials, or unapproved user data. |
| `REL-005` | Fixture-mode screenshots and tests MUST be labeled and MUST not imply real-service or real-telemetry acceptance. |
| `REL-006` | Acceptance evidence SHOULD be machine-readable enough for another agent to independently verify file hashes, counts, and assertions. |

## Current 0.10.0 development evidence

The exact 0.10.0 candidate was built from source revision `d6a56146e544b5ea3e2108c8c67adf9d4dc5cc74`. The Release build completed with zero warnings and zero errors; 73 .NET tests and 173 Python tests passed. The packaged-app walkthrough, warm replay sample, privacy scan, artifact hashes, and guarded installer lifecycle results are recorded in `companion-app/RELEASE_0.10.0.md`.

HOME_QA real-telemetry acceptance remains pending and limits any claim of complete production acceptance. Evidence references: `companion-app/RELEASE_0.10.0.md`, `companion-app-handoff/ACCEPTANCE_CHECKLIST.md`, and the exact release artifact manifests.
