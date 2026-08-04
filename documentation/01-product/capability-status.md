# Capability status

This inventory describes the intended user-facing capability and the 0.10.0 development reality. “Implemented” does not mean HOME_QA accepted.

| Capability | Intended state | 0.10.0 reality | Acceptance boundary |
| --- | --- | --- | --- |
| Home | Compact race desk, recent races, live state, next actions | Implemented | Fixture/local QA; HOME_QA pending |
| Live Telemetry | Real-time map, metrics, traces, trends, fuel and safe cues | Implemented with SDK source and deterministic replay | Replay verified; real SDK HOME_QA pending |
| Live Monitor | Separate locked always-on-top grid with portable named layouts | Implemented | Automated layout/replay verified; packaged visual and HOME_QA real-race acceptance pending |
| Race browser | Newest-first friendly event rows; whole row opens | Implemented | Kentucky behavior locally/previous HOME_QA observed |
| Full Race Analysis | Exact seven-tab evidence workspace and multi-lap telemetry | Implemented | Fixture verified; final installed build pending |
| Qualifying phase | Present only when recorded and openable | Conditional | Fixture verified; real paired-event acceptance pending |
| Race Planning | Manual matching-history planner and briefing | Implemented for recorded-history path | Official upcoming-event discovery not implemented |
| Setup Library | Read-only discovery, readable fields, comparison | Implemented | Local/fixture verified |
| Starting Tune package | Guided open-setup package workflow | Implemented as Context → Source → Package → Baseline Run | Packaged UI and real baseline acceptance pending |
| Progressive Tuning | Map-based multiple feedback cards and reversible experiment | Implemented for supported open races | Fixture verified; real clean A/B acceptance pending |
| Connections | Garage61 and private Coach Engine service management | Implemented | Real Garage61/ChatGPT account acceptance pending |
| Settings and Diagnostics | Portable settings plus compact diagnostics and backup preparation | Implemented | Local lifecycle/tests verified |
| Garage61 own/team API | Protected machine credential, health, bounded sync | Conditional | Storage/status covered; real authorized sync pending |
| Garage61 global comparison | Only with separately approved scope | Unsupported now | MUST remain absent until permission exists |
| AI coaching | Optional bounded Coach Engine synthesis | Conditional | Runtime packaged; real account/schema interaction pending |
| Wet-weather analysis | Only when validated wet data exists | Not implemented | Hidden from production |
| Multiclass analysis | Class-aware scoring and context | Not implemented | Hidden from production |
| Target/reference trace | Evidence-gated aligned comparison | Partial/conditional | Actual recorded multi-lap trace works; validated external target remains limited |
| Tray behavior | Minimize/close options and monitor controls | Partial | Source exists; complete HOME_QA tray matrix not recorded |

## Capability registry alignment

The production `CapabilityRegistry` is the user-visible inventory. Version 0.10.0 aligns `TrackMap`, `SetupComparison`, and `SetupPackageBuilder` with their real conditional or supported implementations. Registry tests prove that incomplete and permanently unsupported capabilities remain hidden.

## Visibility rules

| ID | Requirement |
| --- | --- |
| `CAP-001` | A permanently unsupported or unimplemented capability MUST NOT appear as an actionable production control. |
| `CAP-002` | A conditionally applicable control MUST appear only when the current recorded/live context supplies its prerequisite. |
| `CAP-003` | A temporary outage MAY retain the control only with one concise state and recovery action. |
| `CAP-004` | Missing individual evidence MUST remove or weaken the dependent statement, not erase unrelated supported content. |
| `CAP-005` | Capability visibility decisions MUST be testable without making service requests. |
| `CAP-006` | The capability inventory MUST match the behaviors present in production source and the traceability matrix. |
| `CAP-007` | A permanently impossible or intentionally unsupported capability MUST have no actionable production shell. |
| `CAP-008` | A contextual capability MUST be hidden when its prerequisite is absent unless the workflow expects that measurement; an expected but absent measurement MAY use concise `Not measured` or `Insufficient evidence` wording. |
| `CAP-009` | Missing contextual evidence MUST NOT be converted to zero, neutral, average, false, or a fixture value. |
