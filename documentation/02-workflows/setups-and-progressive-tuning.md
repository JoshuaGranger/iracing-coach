# Setups, Starting Tune, and Progressive Tuning

## Setup Library

| ID | Requirement |
| --- | --- |
| `SU-001` | The library MUST inventory actual local setup artifacts without modifying originals. |
| `SU-002` | Search and rows MUST use friendly car, track, role/purpose, and setup names where known. |
| `SU-003` | Opaque STO files MAY expose path-independent identity, size/time, and SHA-256 but MUST NOT claim decoded parameters. |
| `SU-004` | The recorded IBT `CarSetup` tree is authoritative for what was driven. |
| `SU-005` | Readable HTML/embedded parameters SHOULD be grouped into human systems such as tires, aero, springs, shocks, bars, heights, differential, gears, and brakes. |
| `SU-006` | Filename/header conflicts MUST remain visible and provisional. |
| `SU-007` | Readable setups MAY be compared by normalized field, but comparison MUST retain provenance and uncertainty. |
| `SU-008` | “Open containing folder” MAY be provided; no workflow may overwrite the source setup. |
| `SU-009` | Raw folder identifiers and fingerprints belong only in technical details. |

The 0.9.3 UI implements search, selection, readable grouped fields, same-car comparison, opaque-STO truthfulness, and technical hashes.

## Starting Tune package

| ID | Requirement |
| --- | --- |
| `ST-001` | Starting Tune MUST be available only for open setups and MUST distinguish qualifying and race intent. |
| `ST-002` | It MUST select season, exact car/layout, distance/intent, and an exact or defensible donor source. |
| `ST-003` | Exact current-season car/layout/intent evidence MUST outrank donor logic. |
| `ST-004` | Output is a coaching worksheet/package with baseline provenance, fingerprint, validation groups, risks, success criteria, and rollback. |
| `ST-005` | The application MUST NOT generate or overwrite a simulator-loadable STO. |
| `ST-006` | Seasonal research MUST be cached and invalidated by relevant content/physics identity. |

The backend contains package workflows; 0.9.3 does not expose the complete `Context → Source → Package → Baseline Run` first-class UI. This is a material partial capability.

## Progressive Tuning prerequisites

| ID | Requirement |
| --- | --- |
| `PT-001` | Tuning begins from an analyzed open-setup recording with embedded setup context. |
| `PT-002` | Fixed, insufficient, tow/repair-confounded, or otherwise invalid runs MUST be excluded or rejected with one concise reason. |
| `PT-003` | The exact baseline fingerprint and rollback path MUST remain attached to the experiment. |

## Feedback builder

| ID | Requirement |
| --- | --- |
| `PT-020` | A selectable track map or friendly load-zone list MUST identify the complaint location. |
| `PT-021` | Feedback MUST capture run phase, corner phase, symptom/balance, severity, confidence, optional priority, and optional note. |
| `PT-022` | The user MUST be able to add and remove multiple sparse feedback cards. |
| `PT-023` | Driver wording and telemetry corroboration MUST remain separately identifiable. |
| `PT-024` | All saved feedback cards MUST contribute to recommendation input; the last form state may not silently replace earlier cards. |
| `PT-025` | The builder MUST NOT require feedback for every corner or phase. |

## Experiment contract

| ID | Requirement |
| --- | --- |
| `PT-040` | The recommendation MUST identify the setup system, proposed change, expected benefit, risk, comparison checks, and rollback. |
| `PT-041` | The default SHOULD be one setup system at a time for causal attribution. |
| `PT-042` | Multiple changes MAY be bundled only when independent or explicitly coupled, with per-change effects, risks, success criteria, and rollback. |
| `PT-043` | Contradictory complaints MUST be explained and may require the user to choose priority. |
| `PT-044` | A platform/legality problem MUST be resolved before balance tuning. |
| `PT-045` | Improved, Worse, No change, and Inconclusive outcomes MUST be recordable and durable. |
| `PT-046` | Failed experiments MUST remain searchable so equivalent disproven changes are not repeated. |

Version 0.9.3 fixture QA verified a real track selector, two feedback cards on different zones, a generated controlled recommendation, and result controls. Real clean-run A/B acceptance remains pending.
