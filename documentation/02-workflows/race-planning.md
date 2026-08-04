# Race Planning

Race Planning converts a selected future context plus comparable recorded evidence into one internally consistent briefing. It is not a blank coaching prompt.

## Entry paths

| ID | Requirement |
| --- | --- |
| `RP-001` | The planner MUST support a manual path with friendly car, exact layout, laps/minutes, and fixed/open inputs. |
| `RP-002` | The most recently raced car SHOULD be selected automatically. |
| `RP-003` | Car choices MUST come from real indexed/owned/downloaded content; production code MUST NOT seed fake options. |
| `RP-004` | Track choices and comparable races MUST remain aligned with the selected car, layout, and setup type. |
| `RP-005` | The planner SHOULD support a supported official upcoming-event provider with a manual fallback. |
| `RP-006` | A schedule provider MUST NOT inspect browser cookies, passwords, or unrelated local state. |

Version 0.11.0 implements the compact manual recorded-history path. Official upcoming-event discovery is not a complete production capability.

## Deterministic calculation contract

| ID | Requirement |
| --- | --- |
| `RP-020` | Stop count and pit targets MUST use the user's requested distance, not the source race's scheduled distance. |
| `RP-021` | A timed plan MAY convert duration through a supported recorded lap-time estimate and MUST disclose that assumption. |
| `RP-022` | Fuel deltas MUST exclude refuel jumps and distinguish green and caution burn. |
| `RP-023` | Requested service MUST remain distinct from confirmed service. |
| `RP-024` | Identical plan requests SHOULD reuse the exact context cache and MUST NOT be triggered by rendering. |
| `RP-025` | Missing evidence MUST remove or weaken only its dependent result. |

The local 0.9.3 QA oracle is a 50-lap Kentucky-style fixture with a 49.3-lap supported all-green range. It must produce one required stop and an equal-stint target at Lap 25.

## Briefing output

| ID | Requirement |
| --- | --- |
| `RP-040` | The title MUST use selected friendly track and car names. |
| `RP-041` | The briefing MUST show supported green/caution fuel use, range, reserve, stops, and pit targets. |
| `RP-042` | It SHOULD include tire/long-run guidance, qualifying intent, starts/restarts, relevant in-car adjustments, risks, and exactly useful race triggers. |
| `RP-043` | Car-specific controls may appear only when the car actually exposes them. |
| `RP-044` | Supported corner/load-zone guidance SHOULD distinguish tire/run phases and evidence strength. |
| `RP-045` | Unsupported corner rows MUST be omitted instead of repeating missing-data prose across a matrix. |
| `RP-046` | Comparable history MUST identify its recorded nature and uncertainty; it is not a prediction. |
| `RP-047` | The word “optimal” MUST NOT be used without position, pit-loss, rules, future-caution/overtime, and relevant-history evidence. |
| `RP-048` | Assumptions and calculation provenance MUST be available in a subordinate disclosure. |
| `RP-049` | Generic instructions that do not change with the selected race MUST be omitted. Repeated methodology and invariant limitations belong in subordinate disclosure, not primary briefing space. |

## Empty and failure states

- With no recorded race, the page directs the user to Race Analysis.
- With no exact comparable race, the build action remains unavailable rather than manufacturing a reference.
- Backend failure is contained by the application error boundary and preserves source recordings.
- Garage61 and AI outages do not remove the local-history briefing.
