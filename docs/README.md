# iRacing Coach specification and reality documentation

> **The rewrite is delivered (decided 2026-10-02, built 2026-10-02 and 2026-10-03).** iRacing Coach is a Rust collector on the sim PC, a Rust service with the Python analysis worker (decode and kernels native, see the snapshot) in a container on the mini PC, raw recordings on the NAS, and a TypeScript web UI. The decisions in [`08-rewrite/owner-decisions-2026-10-02.md`](08-rewrite/owner-decisions-2026-10-02.md), as refined by [`08-rewrite/prove/verdicts.md`](08-rewrite/prove/verdicts.md), **supersede the desktop baseline** wherever they conflict (one-PC install, WPF/Blazor desktop, no local service, UNC rejection, win-x64 package rule). The desktop app is retired and its source (`companion-app/`) was deleted in rewrite wave 4; it lives in Git history (last present at `3ff5e87`). Current reality is the [rewrite snapshot](06-reality/implementation-snapshot-0.19.0-rewrite.md) (written at `ef98f0a`, 2026-10-03) plus the web rounds 1 to 6 recorded in [`08-rewrite/acceptance/round6-release.md`](08-rewrite/acceptance/round6-release.md) (the latest release record: live image `iracing-coach:0bf443a`, profile v26) and its predecessors `round1-release.md` and `round2-release.md` (which also holds rounds 3 to 5); no snapshot yet covers rounds 1 to 6. The product-intent sections below remain the contract for behaviour the rewrite keeps (evidence and truthfulness, investigation design, setups and tuning); the desktop snapshots are the historical record for the rest.

This tree is the reviewable product contract for iRacing Coach. Its primary purpose is to let a human or an agent compare three different things without conflating them:

1. **Intent** — what the application is required to do.
2. **Reality** — what the current version actually implements.
3. **Evidence** — what tests, fixtures, screenshots, packages, or direct simulator observations prove.

Passing tests does not make a requirement correct. Written requirements can be ambiguous, contradictory, unsafe, or impossible. Working code can satisfy the wrong requirement. This documentation is structured so an agent can criticize both directions.

## Reading path

Track geometry research: [Track extraction and telemetry registration](../parsers/TELEMETRY-REGISTRATION.md)
records the available corpus, SDK conversion, six-layout correlation and
four-layout fresh-file confirmation. It includes the evidence handoff and
remaining coverage, qualification and app-integration work; it is not a release
record or a claim of calibrated walls or absolute lateral accuracy.

1. [Authority and normative language](00-governance/authority-and-language.md)
2. [Source register](00-governance/source-register.md)
3. [Product mission, scope, and operating context](01-product/mission-scope-and-operating-context.md)
4. [Capability status](01-product/capability-status.md)
5. The applicable workflow under [`02-workflows`](02-workflows/home-and-navigation.md)
6. The applicable data or architecture contract
7. [Current implementation snapshot: the rewrite](06-reality/implementation-snapshot-0.19.0-rewrite.md), brought forward by the [round 6 release record](08-rewrite/acceptance/round6-release.md) (live: `0bf443a`, 2026-10-09) (the last desktop snapshot is [0.18.0 round 2](06-reality/implementation-snapshot-0.18.0-development-round-2.md))
8. [Traceability matrix](06-reality/traceability-matrix.md)
9. [Known gaps and contradictions](06-reality/gaps-contradictions-and-open-questions.md)
10. [Agent review protocol](07-agent-review/review-protocol.md)

## Documentation tree

- `00-governance`
  - [Authority and normative language](00-governance/authority-and-language.md)
  - [Source register](00-governance/source-register.md)
  - [Criticism model](00-governance/criticism-model.md)
  - [Decision log](00-governance/decision-log.md)
- `01-product`
  - [Mission, scope, and operating context](01-product/mission-scope-and-operating-context.md)
  - [Capability status](01-product/capability-status.md)
  - [Design around a racing question](01-product/companion-design-principles.md)
  - [Visual companion redesign and Opus 5.5 debate](01-product/visual-companion-redesign-2026-09-22.md)
- `02-workflows`
  - [Home and navigation](02-workflows/home-and-navigation.md)
  - [Live telemetry and monitor](02-workflows/live-telemetry-and-monitor.md)
  - [Race analysis](02-workflows/race-analysis.md)
  - [Race planning](02-workflows/race-planning.md)
  - [Setups and progressive tuning](02-workflows/setups-and-progressive-tuning.md)
  - [Connections, settings, and diagnostics](02-workflows/connections-settings-and-diagnostics.md)
  - [Simulator race development corpus agent prompt](02-workflows/simulator-race-development-corpus-agent-prompt.md)
  - [Simulator track-map capture agent prompt](02-workflows/simulator-track-map-capture-agent-prompt.md)
- `03-data`
  - [Evidence and truthfulness](03-data/evidence-and-truthfulness.md)
  - [Telemetry, sessions, and analysis](03-data/telemetry-session-and-analysis.md)
  - [Archive, portability, and security](03-data/archive-portability-and-security.md)
- `04-architecture`
  - [System boundaries and runtime](04-architecture/system-boundaries-and-runtime.md)
  - [Integrations and AI](04-architecture/integrations-and-ai.md)
  - [Progressive Tuning evidence contract v2](04-architecture/tuning-evidence-v2.md)
  - [Installation, upgrade, and uninstall](04-architecture/installation-upgrade-and-uninstall.md)
- `05-quality`
  - [Acceptance, test, and release](05-quality/acceptance-test-and-release.md)
  - [UI, accessibility, and performance](05-quality/ui-accessibility-and-performance.md)
  - [Race Analysis geometry and performance evidence](05-quality/race-analysis-geometry-and-performance.md)
  - [Technical strategy corpus evidence](05-quality/technical-strategy-corpus-check-2026-08-11.md)
  - [IRP review sections: derivation](05-quality/irp-review-sections-2026-09-24.md)
- [Race Analysis overhaul acceptance matrix](05-quality/race-analysis-overhaul-acceptance-matrix.md)
- [Progressive Tuning overhaul acceptance matrix](05-quality/progressive-tuning-overhaul-acceptance-matrix.md)
- `06-reality`
  - [Current snapshot: 0.19.0 rewrite (collector, server, web app, native analysis stages)](06-reality/implementation-snapshot-0.19.0-rewrite.md)
  - [Latest release record: web round 6 on the live service (0bf443a, profile v26)](08-rewrite/acceptance/round6-release.md)
  - [Last desktop development snapshot: 0.18.0 round 2](06-reality/implementation-snapshot-0.18.0-development-round-2.md)
  - [Previous development snapshot: 0.18.0 round 1](06-reality/implementation-snapshot-0.18.0-development-round-1.md)
  - [Prior development snapshot: 0.18.0](06-reality/implementation-snapshot-0.18.0-development.md)
  - [Whole-app usefulness audit: September 22](06-reality/whole-app-usefulness-audit-2026-09-22.md)
  - [Full control-by-control rationale](06-reality/whole-app-control-audit-2026-09-22.md) and [declaration inventory](06-reality/whole-app-control-inventory-2026-09-22.md)
  - [Whole-app acceptance coverage: September 22](06-reality/whole-app-acceptance-coverage-2026-09-22.md)
  - [Bristol pace-loss investigation and unreleased trend correction](06-reality/bristol-pace-investigation-2026-09-22.md)
  - [Prior development snapshot: 0.17.7](06-reality/implementation-snapshot-0.17.7-development.md)
  - [Prior development snapshot: 0.17.6](06-reality/implementation-snapshot-0.17.6-development.md)
  - [Prior development snapshot: 0.17.5](06-reality/implementation-snapshot-0.17.5-development.md)
  - [Prior development snapshot: 0.17.4](06-reality/implementation-snapshot-0.17.4-development.md)
  - [Prior development snapshot: 0.17.3](06-reality/implementation-snapshot-0.17.3-development.md)
  - [Prior development snapshot: 0.17.2](06-reality/implementation-snapshot-0.17.2-development.md)
  - [Prior development snapshot: 0.17.1](06-reality/implementation-snapshot-0.17.1-development.md)
  - [Prior development snapshot: 0.17.0](06-reality/implementation-snapshot-0.17.0-development.md)
  - [Prior development snapshot: 0.16.0](06-reality/implementation-snapshot-0.16.0-development.md)
  - [Prior development candidate: 0.15.0](06-reality/implementation-snapshot-0.15.0-development.md)
  - [Technical Data signal coverage: 0.15.0 development](06-reality/technical-data-signal-coverage-0.15.0-development.md)
  - [Stable packaged snapshot: 0.14.2](06-reality/implementation-snapshot-0.14.2.md)
  - [Prior implementation snapshot: 0.14.1](06-reality/implementation-snapshot-0.14.1.md)
  - [Prior implementation snapshot: 0.14.0](06-reality/implementation-snapshot-0.14.0.md)
  - [Prior implementation snapshot: 0.13.0](06-reality/implementation-snapshot-0.13.0.md)
  - [Superseded post-0.12.0 development snapshot](06-reality/implementation-snapshot-post-0.12.0-major-iteration.md)
  - [Prior current-main Race Analysis snapshot](06-reality/implementation-snapshot-post-0.11.1-race-analysis.md)
  - [Implementation snapshot: 0.11.1](06-reality/implementation-snapshot-0.11.1.md)
  - [Prior implementation snapshot: 0.11.0](06-reality/implementation-snapshot-0.11.0.md)
  - [Prior implementation snapshot: 0.10.0](06-reality/implementation-snapshot-0.10.0.md)
  - [Traceability matrix](06-reality/traceability-matrix.md)
  - [Gaps, contradictions, and open questions](06-reality/gaps-contradictions-and-open-questions.md)
- `07-agent-review`
  - [Review protocol](07-agent-review/review-protocol.md)
  - [Finding template](07-agent-review/finding-template.md)
  - [Change impact checklist](07-agent-review/change-impact-checklist.md)
- [`releases`](releases/) - immutable historical release, acceptance, audit, and development records.
- `08-rewrite` (authoritative for the rewrite)
  - [Owner decisions, October 2](08-rewrite/owner-decisions-2026-10-02.md)
  - Evidence: spikes ([infrastructure](08-rewrite/spikes/infra.md), [sim PC](08-rewrite/spikes/sim.md), [Python portability](08-rewrite/spikes/python-portability.md), [UI data](08-rewrite/spikes/ui-data.md), [live stack](08-rewrite/spikes/live-stack.md)) and proofs ([P1](08-rewrite/prove/P1-python-on-n100.md), [P2](08-rewrite/prove/P2-razor-wasm-vs-spa.md), [P3](08-rewrite/prove/P3-collector-language.md), [P4](08-rewrite/prove/P4-nas-transport.md), [P5](08-rewrite/prove/P5-garage-setup-publish.md), [verdicts](08-rewrite/prove/verdicts.md))
  - [Architecture](08-rewrite/architecture.md)
  - [Contracts](08-rewrite/contracts/README.md): HTTP API, collector protocol, live frame, NAS layout, database schema, worker interface
  - [Workstreams and waves](08-rewrite/workstreams.md)
  - [Risks, owner questions, scope](08-rewrite/risks.md)

## Stable requirement identifiers

Normative requirements use stable identifiers such as `RA-012` or `SEC-004`. An implementation, test, issue, or criticism should cite the identifier instead of paraphrasing the requirement. New requirements receive new identifiers; retired identifiers are not reused.

## Status vocabulary

- **Implemented:** source and local verification support the requirement.
- **Partial:** useful behavior exists, but a material clause or state is missing.
- **Conditional:** correct only when the required data, car, service, or session context exists.
- **Not implemented:** no complete production behavior exists.
- **Unsupported:** intentionally excluded because the source or permission contract cannot support it.
- **Locally verified:** proven through automated tests or direct local interaction with non-production test data.
- **Real-system verified:** directly observed with the named executable, hardware, simulator/source, scenario, and date.
- **Accepted:** explicitly accepted by Joshua for the named scope or release artifact.

## Current baseline

The authoritative description is the [0.19.0 rewrite snapshot](06-reality/implementation-snapshot-0.19.0-rewrite.md); each claim there names its evidence file under [`08-rewrite/acceptance`](08-rewrite/acceptance/), [`08-rewrite/prove`](08-rewrite/prove/verdicts.md), [`08-rewrite/port`](08-rewrite/port/phase3-writer.md) or [`08-rewrite/reviews`](08-rewrite/reviews/port-phase3-review-codex-sol.md).

- Retired baseline: the desktop app (C#/.NET WPF host, Blazor Hybrid UI, one-PC install), retired by owner ruling D13 and deleted from the source in wave 4 (`f2d35fd`; last present at `3ff5e87`). Nothing in the current tree builds it.
- Source now: a Rust collector (`collector/`, no .NET, no WebView) on the sim PC; a Rust server (`server/`, `crates/`) with a Python 3.12 worker (`iracing-coach/`) in one container on the mini PC; a TypeScript web app (`web/`) served by the server; raw recordings on the NAS. Version `0.19.0`, worker profile `post-race-foundations-v23`, API version 1, collector protocol 1. Analysis stages are native (`crates/coach-analysis`) behind byte-identical parity gates: decode and kernels are in the last released image (`dccfadf`), and the writer stage and the server's `COACH_NATIVE_STAGES` setting are in source, not yet recorded as released.
- Gates, as recorded: **G1** passes twice on the N100 (203 MB race: 15.3 s with the Python worker, 8.50 s and 8.40 s with native decode and kernels, worker peak 2.29 to 0.25 GiB, byte parity); **G2** passes at 6x CPU throttle (phone 2.07 s, tablet 1.54 s, 60 fps, zero requests); **G3** passes after three collector fixes; **G4** passes with a parked car. Parity corpus of every port phase: the two fixtures and 50 unique private recordings, identical raw and parsed.
- Real-system evidence: the collector is installed on DESKTOP-SIM and is the owner's pipeline; the library holds 657 sessions (655 ready, 2 deferred by the memory ceiling) migrated from the old app and the live folder.
- Not verified: real phone and tablet devices (checks are emulated), tailnet access from outside, the rig's own screen, the 2 GB Kansas recording, a bot-driven G4, autostart at sign-in. Parked by owner decision: Plan, saved Tests, Test day, Replay, Details, Ask, crew-chief calls and the `.sto` harvest (which moves to `iRacing-utility`). The known follow-ups are listed in the snapshot. Nothing is Accepted yet.

## Desktop baseline at retirement (historical)

The desktop source this section describes was deleted in rewrite wave 4 and lives in Git history; the paths and builds it names do not exist in the current tree. It stays as the record of what the retired app did; current reality is above.

- Stable packaged version: `0.14.2`; its immutable package hashes and evidence remain in the 0.14.2 snapshot
- Current source version: `0.18.0` development round 2, analysis profile `post-race-foundations-v23`, race investigation schema 2, projection `sections-trials-v7`. Since round 2, steering everywhere is the steering applied at the front wheels (steering-wheel angle ÷ the recording's own steering ratio), never guessed when the ratio is unknown; see [`TD-037`](03-data/telemetry-session-and-analysis.md). Round 2 adds the Test day review, saved-test reachability, setup values and links from recordings with car-keyed Compare, and fixes for the defects native acceptance on DESKTOP-SIM found; see the [current snapshot](06-reality/implementation-snapshot-0.18.0-development-round-2.md). The round-1 investigation, saved tests, Sessions library, race-linked planning and consolidated Settings are described in the [round-1 snapshot](06-reality/implementation-snapshot-0.18.0-development-round-1.md). Development implementation remains pending Joshua's acceptance.
- Desktop: C#/.NET 10, WPF host, Blazor Hybrid UI
- Deterministic backend: Python, MCP/CLI contract version 1
- Current source verification at `62b71bf`: **581/581 application tests**, a Release build with 0 warnings, and **1,276/1,276 backend tests, 25 MCP tools, 22 contracts and 29 fixtures passed** repository verification. The current snapshot separates these checks from bounded interaction evidence.
- Round 2 interaction evidence: native click-through on DESKTOP-SIM at 1280×800 in a hidden RDP session, on copies of Joshua's real data, including the Test day tab on 13 real, bot-driven IRP Offline Testing recordings. Cold analysis of the 203 MB New Hampshire race is 10.3 s (was 23.7 s), with byte-identical output. No installer or portable package was built; 150% scaling, multi-monitor, Live with a live session and authenticated services were not verified natively.
- Round 1 interaction evidence: browser-preview checks on an isolated profile reproduce the Bristol anchors, the save-and-check loop (a truthful Not comparable), race-linked planning, setup comparison and Settings. The round-1 development executable started natively on an isolated profile; no native click-through was performed. Measured cold analysis of a 203 MB race is 21.9 s; cached open 0.3 s.
- Earlier interaction evidence: the completed native 0.18 candidate-4 report records readable investigation styling and four-chart inspection at exactly 1280×720, trace-layout restoration, complete-race totals before/open/return/hard reload, compact/maximized Tuning, retained phase-specific notes and read-only Settings checks. The subsequent exact full portable passed native startup and Bristol smoke using its bundled runtimes. These are bounded passes; the [coverage document](06-reality/whole-app-acceptance-coverage-2026-09-22.md) separates source, automated, browser and native evidence. Authenticated AI and active live telemetry remain unverified; live testing is deferred at Joshua's request.
- Current package boundary: full 0.18 installer and portable packages were built from clean commit `a7153b8082598586d2d9c9bbccf47efcf122d862`, with all **9,333 manifest-listed payload hashes verified**. They include the self-contained win-x64 application, backend, Python and Coach Engine. Exact hashes, bundled versions and DESKTOP-SIM portable checks are recorded in the [snapshot](06-reality/implementation-snapshot-0.18.0-development.md). Earlier candidate transports reused installed runtimes; the full packages carry their own. The production installation and original racing files remained unchanged during testing.
- Current installer lifecycle evidence: the exact 0.18 Setup executable passed guarded install, manifest validation/tamper rejection, rollback, running-app replacement, owned-legacy cleanup, fixture preservation, uninstall and reinstall checks. Replacement used the same payload plus an old-version marker, not migration from every prior release. Production Windows integration and visual installer progress/disabled-text contrast remain unverified; see the snapshot for exclusions.
- Development focus: explain pace changes with the relevant laps, measured conditions and controls; connect findings to trace inspection and a useful next driving test; preserve exact recording/setup context and pending notes. The usefulness audit covers 257 baseline control declarations/gesture families, **not 257 accepted native controls**. Practice-baseline discovery, useful experiment history, a real setup A/B cycle and broader native/live acceptance remain open.

The last desktop source reality is summarized in `06-reality/implementation-snapshot-0.18.0-development-round-2.md`, and the last stable desktop package in `06-reality/implementation-snapshot-0.14.2.md`; earlier snapshots and release records are immutable historical evidence. The current reality is `06-reality/implementation-snapshot-0.19.0-rewrite.md`. Build binaries and private user data are not part of the source repository.
