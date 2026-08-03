# Telemetry, Sessions, and Analysis Data

This document defines the data behavior that underpins event discovery, analysis, live telemetry, planning, and tuning.

## Input discovery and lifecycle

| ID | Requirement |
| --- | --- |
| `TD-001` | The product MUST discover eligible local iRacing telemetry without requiring the user to copy a file into the app. |
| `TD-002` | A changing, zero-record, truncated, or trailing-partial IBT MUST NOT be advertised as a finalized event. |
| `TD-003` | Discovery MUST be repeatable and MUST avoid duplicate durable events for the same source identity. |
| `TD-004` | Split recordings and reconnects sharing a defensible session identity MUST group into one event while preserving source-part provenance. |
| `TD-005` | Qualifying and Race phases for the same event MUST remain separate analyses under one event context. |
| `TD-006` | Source files MUST be treated as read-only. |

## Normalization and analysis

| ID | Requirement |
| --- | --- |
| `TD-020` | Channel presence, units, sampling coverage, and validity MUST be recorded before calculations consume a channel. |
| `TD-021` | Lap validity MUST account for lap number, completeness, pit/service, incident, tow/repair, flag, and source-integrity evidence where available. |
| `TD-022` | Distance alignment MUST preserve lap-relative order and MUST not invent track geometry. |
| `TD-023` | Downsampling for display MUST preserve short extrema important to braking, throttle, RPM, steering, and acceleration interpretation. |
| `TD-024` | Run segmentation MUST use confirmed service or state transitions, not telemetry-file boundaries alone. |
| `TD-025` | Tire wear measurements at service MUST attach to the run that produced them; the final run remains unmeasured without a later observation. |
| `TD-026` | Fuel calculations MUST distinguish observed green/caution burn, inferred consumption, reserve, feasibility, and unsupported optimization. |
| `TD-027` | Damage analysis MUST keep incident, pit-road, stall, service, tow, mandatory repair, optional repair, and overlapping clocks distinct. |
| `TD-028` | Deterministic analysis MUST produce the same result for the same source, configuration, and engine version. |

## Durable identities and cache behavior

| ID | Requirement |
| --- | --- |
| `TD-040` | Durable records MUST use stable identifiers sufficient to reopen the same event after app restart or relocation of the portable data folder. |
| `TD-041` | Cached analysis MUST record its source fingerprint, analysis version, configuration context, creation time, and invalidation reason. |
| `TD-042` | Reanalysis MUST be explicit when an existing result remains valid; invalid results MUST never be silently presented as current. |
| `TD-043` | User annotations and tuning feedback MUST reference durable event, phase, lap, setup, and recommendation identities where applicable. |

## Live data boundary

Live telemetry is a separate transient path. It may update at driving cadence, but it must not mutate finalized history until a recording is complete and accepted by the ingestion pipeline. A fixture replay is test evidence for rendering and state transitions, not evidence that the iRacing SDK integration works on a real session.

Implementation references: `ibt_reader.py`, `native_events.py`, `storage.py`, `analysis_engine.py`, `IRacingSdkTelemetrySource.cs`, and `LiveTelemetry.cs`.
