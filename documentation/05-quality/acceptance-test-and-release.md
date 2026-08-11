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
| `QA-012` | A Race Analysis overhaul candidate MUST execute the changed-surface acceptance matrix at both 1280x720 and 1920x1080, maximized and nonmaximized. Every control and meaningful supported, missing, loading, error, empty, selected, and post-action state MUST be invoked in the exact executable and inspected for clipping, overflow, stale state, inaccessible controls, and truthful recovery. |
| `QA-013` | Visual motion acceptance MUST inspect intermediate frames as well as endpoints for section changes, Technical data drill-in/back, Track/Laps and toolbox reflow, map type/layer changes, replay playback/seek/speed, leader-lap auto-scroll, and synchronized running-order/continuous-flag-timeline updates across the checkered state. The same scenarios MUST be repeated with reduced motion. Automated source/state tests alone cannot pass this gate. |
| `QA-014` | Data acceptance MUST include at least one supported and one intentionally unsupported case for track geometry, replay participants/events, ABS, Technical data submetrics, tire prediction, Garage61 reference, and retained raw IBT. The exact source coverage and unavailable reason/action MUST be captured; a visually complete fixture cannot substitute for missing real evidence. |
| `QA-015` | A Progressive Tuning v2 candidate MUST execute the complete overhaul matrix against the exact development executable and MUST NOT be called accepted until one real clean compatible O'Reilly/Xfinity open-setup A/B cycle is linked. Browser/native fit, every turn/map/draft/result state, reduced motion, keyboard use, offline and invalid AI fallback, and all truthful blocked states remain separate acceptance evidence. |

## Release record

| ID | Requirement |
| --- | --- |
| `REL-001` | Each released artifact MUST have a version, immutable hash, build provenance, compatibility contract, and test summary. |
| `REL-002` | Release notes MUST distinguish new behavior, corrected defects, known limitations, acceptance pending, and data/schema changes. |
| `REL-003` | The repository commit used for a build MUST be identifiable from the release record. |
| `REL-004` | A release MUST NOT include secrets, personal telemetry, local settings, build credentials, or unapproved user data. |
| `REL-005` | Fixture-mode screenshots and tests MUST be labeled and MUST not imply real-service or real-telemetry acceptance. |
| `REL-006` | Acceptance evidence SHOULD be machine-readable enough for another agent to independently verify file hashes, counts, and assertions. |

## Development feedback loop

Normal user-feedback iterations produce a direct development executable, not an installer or portable archive. After the changed automated suites pass, `companion-app/tools/PublishDevelopment.ps1` publishes the WPF app into a new timestamped directory under `companion-app/artifacts/dev` and creates local junctions to the workspace backend plus the already installed Python and Coach Engine runtimes. The resulting executable is suitable for testing on this development PC; it is intentionally not portable and MUST NOT be treated as a release artifact.

The development loop MUST NOT run `BuildRelease.ps1`, create `installer-payload.zip`, compress a portable app, rerun unchanged upgrade/uninstall certification, or rewrite an earlier release record. Installer, portable-package, checksum, lifecycle, and clean-checkout gates resume only when Joshua asks for a packaged release candidate.

## Current 0.16.0 development evidence

The current source identifies 0.16.0. The integrated development gates passed 255/255 .NET tests, 247/247 Python tests, 9/9 first-party JavaScript syntax checks, and a Release solution build with zero warnings and zero errors. This iteration adds real Iowa/Daytona geometry repair, bounded 500-lap Race Analysis rendering, high-rate bounded replay capture plus long-race materialization guards, deeper Technical Data and planning decisions, native/full Live Telemetry parity, and a synchronized high-contrast graphite theme.

Direct browser checks used real supplied recordings as well as bounded synthetic stress data. The August 9 Iowa Open replay reconstructed 7,775 legacy frames and was exercised at 1280x720 and 1920x1080. A real 82-lap analysis retained all logical selections while bounding rendered paths and DOM work; actual pointer-tool round trips measured 62 ms average/113 ms maximum over charts and 66 ms average/70 ms maximum over the map. Fifty Customize open/close cycles produced no endpoint drift or overflow. The synthetic 500-lap case verifies bounded selection/render budgets, not real 500-lap source breadth or a display-refresh guarantee.

These results are current-source development acceptance only. They do not close every `QA-004` through `QA-015` matrix cell, certify real SDK/display cadence, or provide a 0.16.0 installer hash, source commit, clean-PC lifecycle result, or user acceptance. Exact details and known limits are recorded in the [0.16.0 development snapshot](../06-reality/implementation-snapshot-0.16.0-development.md); package evidence is added only after the frozen artifact passes its own gates.

## Prior 0.15.0 development evidence

The 0.15.0 source had integrated evidence from 232/232 .NET tests and 230/230 Python tests. Its verified handoff exposed 17 MCP tools across 114 manifested files. An explicitly requested 499,357,736-byte installer candidate was built from commit `9e4befbde2fea8c2d51e4bf40f1ed1586ed23187`; its SHA-256 is `9545666f9de5325158397a8a2dc1ccc92fd35c2b64a5f48993d412929dc6be7a`. The guarded lifecycle test passed install, rollback, replacement, preservation, uninstall, reinstall, and final uninstall. This remains historical development-feedback evidence rather than proof of the current source or a stable packaged-release record. Real simulator cadence, external Garage61 results, clean-PC behavior, and user acceptance remain separate claims unless explicitly recorded.

## Stable packaged 0.14.2 evidence

Integrated runs recorded 144/144 .NET tests, 187/187 Python tests, five JavaScript syntax checks, a passing handoff verifier, and a Release application build with zero warnings and zero errors. A fresh mouse-driven saved-race walkthrough confirmed wrapped name/unit labels, title-only guidance tooltips, full-card telemetry dragging, row-title reordering, direct chart pairing, toolbox reset, and the non-shrinking right drawer. The inherited 0.14.1 evidence still covers fastest-three defaults, bidirectional track/chart cursor synchronization, stable Telemetry/Review scrollbar width, and Home-matched green/caution styling. Historical privacy and installer-lifecycle evidence remains historical; unchanged upgrade/uninstall behavior is not rerun for this focused correction. No real-telemetry, source-cadence, 244 Hz presentation, or user acceptance is claimed here.
