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
| `PT-001` | Tuning begins by selecting an exact analyzed Race, qualifying, or suitable test recording and its embedded setup context; it MUST NOT begin as an unscoped text prompt. |
| `PT-002` | Fixed, insufficient, tow/repair-confounded, traffic-confounded, or otherwise invalid runs MUST be excluded or identified with one concise reason. |
| `PT-003` | The exact analysis/session/configuration identity, baseline setup fingerprint, and rollback identity MUST remain attached to every draft and experiment. |
| `PT-004` | A fixed-setup recording MAY provide driving evidence, but a garage recommendation requires an explicitly selected compatible open-setup target with a recorded setup fingerprint. |
| `PT-005` | The application SHOULD select the highest-quality longest clean green run or compatible runs automatically and MUST allow the user to override that selection. |
| `PT-006` | Early, Middle, and Late mean stages within each selected green-flag tire run, not chronological thirds of the complete event; the evidence packet MUST retain the exact source run and lap IDs. |
| `PT-007` | The default tuning goal is long-run race pace; the user MAY instead select tire life, restart pace, or stability, and the selected goal MUST affect ranking without changing measured evidence. |

## Exact-configuration turn map

| ID | Requirement |
| --- | --- |
| `PT-010` | The tuning map MUST use the exact track-configuration identity and the same canonical vector geometry/projection as Race Analysis. |
| `PT-011` | Official turn labels and bounds MUST come from a provenance-bearing annotation catalog, in priority order: supported iRacing asset, isolated iRacing Track Map capture, official NASCAR/venue reference, then explicit user verification/correction. |
| `PT-012` | Telemetry-derived curvature or load zones MAY propose a correction but MUST NOT be labeled as an official turn without a verified source. |
| `PT-013` | NASCAR ovals MUST expose individual numbered turns and MAY also expose paired-end groupings such as Turns 1-2 without replacing the individual identities. |
| `PT-014` | Automatic iRacing Track Map capture MUST use a dedicated coach HUD profile and MUST NOT alter the user's normal Baseline profile or inspect simulator process memory. |
| `PT-015` | Each turn MUST have a broad path hit target; hover highlights the complete turn segment and selection opens its feedback editor without rerunning analysis. |
| `PT-016` | Keyboard and assistive-technology users MUST be able to select every turn through a labeled list or equivalent control, not only the SVG path. |
| `PT-017` | Low-confidence alignment MUST be identified and MAY request a one-time correction of turn label, entry, apex, exit, and label anchor before it is called verified. |
| `PT-018` | Turn annotations MUST be versioned by exact configuration and geometry/content fingerprint so a changed layout cannot silently reuse stale boundaries. |

## Feedback builder

| ID | Requirement |
| --- | --- |
| `PT-020` | A selectable track map or friendly load-zone list MUST identify the complaint location. |
| `PT-021` | Feedback MUST capture official turn/load-zone identity plus the exact current `start_pct`, `apex_pct`, and `end_pct` annotation bounds, run phase, one or more corner phases, one or more symptoms, severity, confidence, optional priority, and optional note. The three bounds remain required even with a corner ID so a stale or corrected map cannot silently retarget saved feedback. |
| `PT-022` | The user MUST be able to add and remove multiple sparse feedback cards. |
| `PT-023` | Driver wording and telemetry corroboration MUST remain separately identifiable. |
| `PT-024` | All saved feedback cards MUST contribute to recommendation input; the last form state may not silently replace earlier cards. |
| `PT-025` | The builder MUST NOT require feedback for every corner or phase. |
| `PT-026` | Selecting the tuning page or a different eligible race MUST load that race's recorded track/telemetry context automatically. |
| `PT-027` | When recorded zones are absent, the builder MAY accept Whole lap or typed feedback but MUST NOT invent corner names or map regions. |
| `PT-028` | Feedback drafts and the whole-race note MUST autosave atomically by exact analysis and setup identity and restore after navigation, restart, and race switching. |
| `PT-029` | Generic free text provides context but MUST NOT independently authorize a garage candidate without at least one structured turn or whole-car symptom. |
| `PT-030` | A stage may contain multiple simultaneous symptoms and MAY distinguish Entry, Center, Exit, and Whole turn; `Good` MUST remain distinct from unassessed. |

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
| `PT-047` | Garage candidates MUST come from a versioned car-family/sim-build rule catalog that records availability, source setup path, unit, range/step knowledge, legal/coupled constraints, expected effects, risks, and source provenance. |
| `PT-048` | An exact target value MAY be emitted only when the current value, adjustment step, legal range, and relevant constraints are verified; otherwise the most precise permitted instruction is one garage step plus a manual confirmation gate. |
| `PT-049` | Setup application remains manual in iRacing; Progressive Tuning MUST NOT create, modify, or overwrite a simulator-loadable STO. |
| `PT-050` | Deterministic validation and a candidate whitelist MUST complete before optional AI synthesis. |
| `PT-051` | AI MAY rank or explain only supplied candidate IDs and evidence IDs; it MUST NOT invent a setting/value, bypass a blocker, or upgrade evidence status. |
| `PT-052` | Invalid, unavailable, interrupted, signed-out, or contradictory AI output MUST fall back to the deterministic recommendation without losing the draft. |
| `PT-053` | The visible result SHOULD lead with one primary next change; lower-ranked alternatives MAY remain subordinate. |
| `PT-054` | A recommendation MUST distinguish driver report, measured/derived corroboration, inferred setup reasoning, contradictory evidence, limitations, and confidence components. |
| `PT-055` | O'Reilly/Xfinity is the first verified rules family, followed by Next Gen, Truck, and ARCA; unverified families MUST remain explicitly unsupported rather than inheriting another car's rules. |

## Result comparison

| ID | Requirement |
| --- | --- |
| `PT-060` | A result MAY link a later practice, test, qualifying, or race analysis when car, exact configuration, setup lineage, and comparison conditions are compatible. |
| `PT-061` | Subjective and evidence-backed outcomes MUST remain distinct; an immediate driver rating MUST NOT masquerade as a matched telemetry comparison. |
| `PT-062` | Result review SHOULD capture Improved, Unchanged, Worse, or Inconclusive for each original feedback item, with optional revised severity and note. |
| `PT-063` | Compatible prior Worse or No-change experiments MUST be exposed as contradictory history and must suppress or materially penalize an equivalent candidate. |
| `PT-064` | The canonical backend experiment record is the sole racing-data source of truth; UI caches and drafts MUST NOT create a competing experiment archive. |

## Layout and performance

| ID | Requirement |
| --- | --- |
| `PT-070` | At ordinary desktop sizes the map, active turn editor, whole-race note, and Analyze action SHOULD fit the viewport without page scrolling; only the inspector MAY scroll on unusually short windows. |
| `PT-071` | Hover feedback MUST remain within one display frame and MUST NOT trigger telemetry decoding, backend work, or full-map geometry reconstruction. |
| `PT-072` | Turn and editor transitions MUST honor reduced motion, preserve focus, avoid popup clipping, and remain stable for 2-, 4-, 9-, and 20-turn layouts. |

Portable coordinator drafts live under `portable-settings/tuning-drafts`; exact-configuration user turn corrections live under `portable-settings/tuning-turn-annotations`. These are resumable UI/workflow state, not a competing racing archive. Canonical experiments and their outcomes remain backend-owned under the deterministic archive.

The former version 0.11.0 form and load-zone prototype does not satisfy the binding exact-turn, structured-stage, rules-catalog, AI-boundary, draft, or linked-result requirements above. It remains migration input only while this overhaul is under development.
