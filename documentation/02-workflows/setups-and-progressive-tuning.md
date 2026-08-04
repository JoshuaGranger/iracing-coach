# Setups, Starting Tune, and Progressive Tuning

## Internal setup indexing

| ID | Requirement |
| --- | --- |
| `SU-001` | The application MUST inventory actual local setup artifacts without modifying originals. |
| `SU-002` | Setup indexing is a supporting service for Starting Tune and analysis, not a first-class browse/library screen. |
| `SU-003` | Opaque STO files MAY expose path-independent identity, size/time, and SHA-256 but MUST NOT claim decoded parameters. |
| `SU-004` | The recorded IBT `CarSetup` tree is authoritative for what was driven. |
| `SU-005` | Readable HTML/embedded parameters SHOULD be grouped into human systems such as tires, aero, springs, shocks, bars, heights, differential, gears, and brakes. |
| `SU-006` | Filename/header conflicts MUST remain visible and provisional. |
| `SU-007` | Readable setups MAY be compared internally by normalized field, but comparison MUST retain provenance and uncertainty. |
| `SU-008` | No workflow may overwrite the source setup. |
| `SU-009` | Raw folder identifiers and fingerprints belong only in technical details. |

Version 0.11.0 removes the user-facing Setup Library because it did not support a useful decision. Real local indexing remains available to Starting Tune and analysis.

## Starting Tune package

| ID | Requirement |
| --- | --- |
| `ST-001` | Starting Tune MUST be available only for open setups and MUST distinguish qualifying and race intent. |
| `ST-002` | It MUST select season, exact car/layout, distance/intent, and an exact or defensible donor source. |
| `ST-003` | Exact current-season car/layout/intent evidence MUST outrank donor logic. |
| `ST-004` | Output is a coaching worksheet/package with baseline provenance, fingerprint, validation groups, risks, success criteria, and rollback. |
| `ST-005` | The application MUST NOT generate or overwrite a simulator-loadable STO. |
| `ST-006` | Seasonal research MUST be cached and invalidated by relevant content/physics identity. |

Version 0.11.0 exposes the backend package workflow as `Event → Source → Checks → Run` inside Setups. It requires exact season/car/track input, presents source identity and SHA-256 in subordinate detail, refuses to relabel a race source as qualifying, and records package/rollback identity. iRacing remains the only place setups are applied or saved.

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
| `PT-026` | Selecting the tuning page or a different eligible race MUST load that race's recorded track/telemetry context automatically. |
| `PT-027` | When recorded zones are absent, the builder MAY accept Whole lap or typed feedback but MUST NOT invent corner names or map regions. |

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

Version 0.11.0 fixture QA verified automatic event loading, a real track selector, zone-specific feedback, a generated controlled recommendation, and result controls. Real clean-run A/B acceptance remains pending.
