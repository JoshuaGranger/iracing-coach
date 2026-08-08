# Evidence and Truthfulness

The application may be useful only to the extent that it accurately describes what it knows. Every user-facing conclusion must remain traceable to recorded data, an explicit calculation, a comparison source, or a clearly identified user statement.

## Evidence classes

| Class | Meaning | Permitted wording |
| --- | --- | --- |
| Recorded | Present in an iRacing telemetry/session field or a local artifact | "Recorded", "observed", or the field's plain-language meaning |
| Derived | Deterministically calculated from recorded values | "Calculated" or "estimated", with inputs and limitations available |
| Compared | Derived from aligned laps, sessions, setups, or an approved external source | Comparative language naming the reference and confidence |
| User-reported | Entered by the user | "You reported" or equivalent |
| Inferred | A bounded interpretation supported by evidence | Qualified language with confidence and alternatives |
| Unavailable | Required source is absent or unusable | Explicitly unavailable; no placeholder value |

## Requirements

| ID | Requirement |
| --- | --- |
| `EV-001` | Every material metric, recommendation, grade, and claim MUST have an inspectable provenance path. |
| `EV-002` | The UI MUST distinguish recorded, calculated, compared, user-reported, inferred, and unavailable facts when the distinction changes interpretation. |
| `EV-003` | Missing data MUST remain missing. Zero, average, neutral, or fixture values MUST NOT substitute for absent evidence. |
| `EV-004` | Exact targets MUST require an aligned and relevant comparison source; otherwise coaching MUST remain relative. |
| `EV-005` | Confidence MUST reflect coverage, channel quality, exclusions, sample size, and comparison fitness rather than visual completeness. |
| `EV-006` | Technical identifiers MAY appear in evidence/support views but SHOULD NOT displace human-readable labels in primary workflows. |
| `EV-007` | A source failure MUST be contained and described without converting stale or partial data into a current claim. |
| `EV-008` | Setup recommendations MUST identify whether a setup is readable, opaque, fixed, imported, recorded, or user-selected. |
| `EV-009` | Damage, repair, pit, caution, incomplete, and other confounding states MUST be excluded from ordinary clean-lap conclusions unless the conclusion explicitly studies them. |
| `EV-010` | External data MUST identify provider, scope, retrieval state, and any alignment uncertainty. |
| `EV-011` | Participant identity, class, car number, position, lap state, flags, and track position MAY be shown for another car only from a recorded supported field at the relevant time. The application MUST NOT infer or decorate another car with throttle, brake, ABS, fuel, tires, setup, damage, incidents, pace traces, or strategy that the source did not record. |
| `EV-012` | A prediction MUST expose its model version, matching scope, evidence support, confidence, and bounds. A prediction is never a measurement; low-confidence styling or a large interval does not authorize an otherwise unsupported point estimate. |

## Prohibited behavior

- Seeded or manufactured content presented as personal history.
- Hidden interpolation across unsupported gaps.
- Treating a file boundary as a run or event boundary without corroborating session evidence.
- Claiming setup contents from an opaque `.sto` file.
- Treating an absent boolean/channel as false or zero.
- Calling a feasible strategy optimal without the evidence needed to compare outcomes.
- Calling a replay or fixture real-time iRacing acceptance.

## Implementation reality

The Python engine models provenance, exclusions, setup readability, and deterministic analysis. The .NET mapping and UI expose much of this material through badges, evidence panels, confidence text, and support details. Coverage is not yet proven for every production channel combination. Real-session claims require direct evidence from a named local executable and scenario; no separate QA role owns that responsibility.

Primary evidence: `iracing-coach/skills/analyze-iracing-race/scripts/analysis_engine.py`, `reporting.py`, `tuning_engine.py`, and `companion-app/src/iRacingCoach.UI/EvidenceBadge.razor`.
