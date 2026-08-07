# iRacing Coach specification and reality documentation

This tree is the reviewable product contract for iRacing Coach. Its primary purpose is to let a human or an agent compare three different things without conflating them:

1. **Intent** — what the application is required to do.
2. **Reality** — what the current version actually implements.
3. **Evidence** — what tests, fixtures, screenshots, packages, or direct simulator observations prove.

Passing tests does not make a requirement correct. Written requirements can be ambiguous, contradictory, unsafe, or impossible. Working code can satisfy the wrong requirement. This documentation is structured so an agent can criticize both directions.

## Reading path

1. [Authority and normative language](00-governance/authority-and-language.md)
2. [Source register](00-governance/source-register.md)
3. [Product mission, scope, and operating context](01-product/mission-scope-and-operating-context.md)
4. [Capability status](01-product/capability-status.md)
5. The applicable workflow under [`02-workflows`](02-workflows/home-and-navigation.md)
6. The applicable data or architecture contract
7. [Current implementation snapshot](06-reality/implementation-snapshot-0.14.1.md)
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
- `02-workflows`
  - [Home and navigation](02-workflows/home-and-navigation.md)
  - [Live telemetry and monitor](02-workflows/live-telemetry-and-monitor.md)
  - [Race analysis](02-workflows/race-analysis.md)
  - [Race planning](02-workflows/race-planning.md)
  - [Setups and progressive tuning](02-workflows/setups-and-progressive-tuning.md)
  - [Connections, settings, and diagnostics](02-workflows/connections-settings-and-diagnostics.md)
- `03-data`
  - [Evidence and truthfulness](03-data/evidence-and-truthfulness.md)
  - [Telemetry, sessions, and analysis](03-data/telemetry-session-and-analysis.md)
  - [Archive, portability, and security](03-data/archive-portability-and-security.md)
- `04-architecture`
  - [System boundaries and runtime](04-architecture/system-boundaries-and-runtime.md)
  - [Integrations and AI](04-architecture/integrations-and-ai.md)
  - [Installation, upgrade, and uninstall](04-architecture/installation-upgrade-and-uninstall.md)
- `05-quality`
  - [Acceptance, test, and release](05-quality/acceptance-test-and-release.md)
  - [UI, accessibility, and performance](05-quality/ui-accessibility-and-performance.md)
- `06-reality`
  - [Current implementation snapshot: 0.14.1](06-reality/implementation-snapshot-0.14.1.md)
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

- Application version: `0.14.1` focused Race Analysis regression repair
- Desktop: C#/.NET 10, WPF host, Blazor Hybrid UI
- Deterministic backend: Python, MCP/CLI contract version 1
- Local verification: integrated runs recorded 140/140 .NET tests, 187/187 Python tests, four JavaScript syntax checks, a Release application build with zero warnings and zero errors, and a mouse-driven saved-race regression walkthrough
- Installer lifecycle: prior guarded install/upgrade/rollback/uninstall evidence is unchanged and was not rerun for every focused UI adjustment
- Current focused evidence: session-reset live coaching, conservative complete-lap fuel/pace evidence, a dedicated high-resolution live reader, bounded latest-value telemetry backpressure, lazy detailed traces, non-activating automatic popout behavior, phase-qualified Race/Qualifying identity, schema-6 phase-safe UI caches, schema-2 deterministic backend indexing, comparable-lap filtering, and versioned evidence-weighted grading have local evidence. A synthetic 240 Hz source exercises the reader above its former polling ceiling, but real-session cadence, combined-load performance, 244 Hz presentation, broad derived-sideslip validation, a genuine tray-menu Exit click, and user acceptance remain open.

The current reality is summarized in `06-reality/implementation-snapshot-0.14.1.md`. Earlier Race Analysis and artifact evidence remain in their immutable prior snapshots and release records. Unchanged installer lifecycle behavior was deliberately not re-certified for this corrective iteration. Build binaries and private user data are not part of the source repository.
