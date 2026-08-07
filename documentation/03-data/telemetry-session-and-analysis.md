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
| `TD-029` | Vehicle sideslip MAY be derived from recorded velocity components only when paired finite `VelocityX` and `VelocityY` samples exist, forward `VelocityX` is positive and usable, and planar speed is at least 5 m/s. The signed value MUST be `atan2(VelocityY, VelocityX)` converted to degrees; a failed guard MUST remain a gap. This derived vehicle-sideslip value MUST retain derived provenance and MUST NOT be described as a native `SlipAngle` channel, tire slip angle, or Yaw Rate. |

## Durable identities and cache behavior

| ID | Requirement |
| --- | --- |
| `TD-040` | Durable records MUST use stable identifiers sufficient to reopen the same event after app restart or relocation of the portable data folder. |
| `TD-041` | Cached analysis MUST record its source fingerprint, analysis version, configuration context, creation time, and invalidation reason. |
| `TD-042` | Reanalysis MUST be explicit when an existing result remains valid; invalid results MUST never be silently presented as current. |
| `TD-043` | User annotations and tuning feedback MUST reference durable event, phase, lap, setup, and recommendation identities where applicable. |
| `TD-044` | After discovery completes, finalized Race recordings without a valid current UI-analysis cache SHOULD enter one sequential background-analysis queue. Valid cached results MUST be reused across app starts, and the queue MUST deduplicate the same effective recording identity within a running process. |
| `TD-045` | Background cache generation MUST be lower priority than interactive use and MUST wait before starting another item while live telemetry is connected or an interactive analysis is active. It MUST NOT expose successful-job noise and MUST contain a failure to the affected recording. A failed or unsupported recording MUST remain explicitly retryable from Race Analysis. Cache entries MUST identify their schema, source identity or freshness evidence, and saved time so stale entries can be rejected. |
| `TD-046` | When Qualifying and Race share a SubSessionID, analysis MUST use the discovery `group_id` or an equally phase-qualified selector. Cache keys and cache validation MUST include both selector and phase; a response or cache for the sibling phase MUST be rejected rather than relabeled. |

## Live data boundary

Live telemetry is a separate transient path. It may update at driving cadence, but it must not mutate finalized history until a recording is complete and accepted by the ingestion pipeline. Display-rate movement between real samples does not create additional source samples and must not be persisted as telemetry. A fixture replay is test evidence for rendering and state transitions, not evidence that the iRacing SDK integration works on a real session.

## Current cache reality

Current source queues all discovered finalized Race sessions that lack a valid schema-7 UI cache whose stored selector, phase, and source-write timestamp match when the source still exists. Schema 7 deliberately rejects pre-fix responses whose tire temperatures may have been converted without honoring the recorded source unit. Discovery `group_id` is the primary selector; numeric SubSessionID remains a backward-compatible fallback. Interactive and background responses are checked against both the requested phase and, when available, the exact requested group selector before use. A sibling-phase or same-phase/different-event response is rejected rather than relabeled. The cache key contains the selector and phase, and cached response metadata is revalidated when read.

The deterministic workflow also qualifies its core analysis-cache identity and emitted `analysis_id` with the normalized selection identity: group ID, SubSessionID, simulator session number, and phase. Unknown legacy/file-only selections retain the base analysis ID rather than receiving invented session identity. The backend history index persists group, SubSessionID, simulator session number/type, and normalized phase. Reopening and history joins prefer exact group identity, then SubSessionID plus phase, then narrower legacy fallbacks; a Race result therefore cannot displace Qualifying merely because they share a SubSessionID.

The queue processes sequentially, persists successful responses under the portable archive's `ui-analysis-cache` component, and updates Home summaries without creating a visible success job. Live telemetry and foreground analysis pause the queue. A failed identity receives one quiet retry, then leaves the active set so a later refresh or app run can retry it instead of suppressing it for the remainder of the process. This is implementation reality, not proof that the source-write timestamp alone satisfies the stronger fingerprint/context contract in `TD-041` for every relocation or restoration scenario.

Deterministic backend archive/index/cache schema 2 adds the persisted session-identity fields and backfills legacy rows from their saved `analysis.json` when possible. SQLite initialization uses a 30-second busy timeout, retries transient locked/busy opens, enters `BEGIN IMMEDIATE` before the idempotent column/index/backfill migration, and rolls back on failure. This protects concurrent app/backend startup from racing the schema update; it does not turn a missing or ambiguous legacy identity into exact phase evidence.

Current analysis also emits a signed derived vehicle-sideslip trace from paired recorded `VelocityX`/`VelocityY` samples under the `TD-029` guards; Yaw Rate remains an independent channel. Focused Python tests passed 19 of 19, the coordinator mapper check passed 1 of 1, and one recorded Iowa IBT produced 542 valid derived samples plus 9 intentionally guarded gaps across four laps. That evidence validates the implemented derivation and gap behavior for the named recording; it is not proof of a native `SlipAngle` channel, tire-level slip, or representative correctness across every car and session.

Implementation references: `ibt_reader.py`, `native_events.py`, `storage.py`, `analysis_engine.py`, `IRacingSdkTelemetrySource.cs`, and `LiveTelemetry.cs`.
