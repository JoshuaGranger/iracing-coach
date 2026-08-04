# iRacing Coach specification and reality documentation

This tree is the reviewable product contract for iRacing Coach. Its primary purpose is to let a human or an agent compare three different things without conflating them:

1. **Intent** — what the application is required to do.
2. **Reality** — what the current version actually implements.
3. **Evidence** — what tests, fixtures, screenshots, packages, or HOME_QA observations prove.

Passing tests does not make a requirement correct. Written requirements can be ambiguous, contradictory, unsafe, or impossible. Working code can satisfy the wrong requirement. This documentation is structured so an agent can criticize both directions.

## Reading path

1. [Authority and normative language](00-governance/authority-and-language.md)
2. [Source register](00-governance/source-register.md)
3. [Product mission, scope, and operating context](01-product/mission-scope-and-operating-context.md)
4. [Capability status](01-product/capability-status.md)
5. The applicable workflow under [`02-workflows`](02-workflows/home-and-navigation.md)
6. The applicable data or architecture contract
7. [Current implementation snapshot](06-reality/implementation-snapshot-0.11.1.md)
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
  - [Implementation snapshot: 0.11.1](06-reality/implementation-snapshot-0.11.1.md)
  - [Prior implementation snapshot: 0.11.0](06-reality/implementation-snapshot-0.11.0.md)
  - [Prior implementation snapshot: 0.10.0](06-reality/implementation-snapshot-0.10.0.md)
  - [Traceability matrix](06-reality/traceability-matrix.md)
  - [Gaps, contradictions, and open questions](06-reality/gaps-contradictions-and-open-questions.md)
- `07-agent-review`
  - [Review protocol](07-agent-review/review-protocol.md)
  - [Finding template](07-agent-review/finding-template.md)
  - [Change impact checklist](07-agent-review/change-impact-checklist.md)
  - [HOME_QA protocol](07-agent-review/home-qa-protocol.md)

## Stable requirement identifiers

Normative requirements use stable identifiers such as `RA-012` or `SEC-004`. An implementation, test, issue, or criticism should cite the identifier instead of paraphrasing the requirement. New requirements receive new identifiers; retired identifiers are not reused.

## Status vocabulary

- **Implemented:** source and local verification support the requirement.
- **Partial:** useful behavior exists, but a material clause or state is missing.
- **Conditional:** correct only when the required data, car, service, or session context exists.
- **Not implemented:** no complete production behavior exists.
- **Unsupported:** intentionally excluded because the source or permission contract cannot support it.
- **Fixture verified:** proven only with the isolated sanitized QA mode.
- **HOME_QA pending:** requires installed-app or real-simulator evidence from Joshua's racing PC.
- **Accepted:** may be assigned only from an explicit HOME_QA release decision.

## Current baseline

- Application version: `0.11.1` development candidate
- Desktop: C#/.NET 10, WPF host, Blazor Hybrid UI
- Deterministic backend: Python, MCP/CLI contract version 1
- Local verification: 74 .NET tests and 173 Python tests passed before packaging
- Installer lifecycle: local guarded install/upgrade/rollback/uninstall verification passed
- Final acceptance: **HOME_QA pending**

The 0.11.1 implementation evidence is summarized in `06-reality/implementation-snapshot-0.11.1.md`. The previous complete release certification remains in `companion-app/RELEASE_0.11.0.md`; unchanged installer lifecycle behavior was deliberately not re-certified for this rendering-only iteration. Build binaries and private user data are not part of the source repository.
