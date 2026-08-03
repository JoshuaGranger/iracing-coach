# Race Analysis

Race Analysis is the deepest workflow. It must let the user choose an event, open it directly, and inspect supported evidence without an extra chat or preview step.

## Race browser

| ID | Requirement |
| --- | --- |
| `RA-001` | The base page MUST list finalized Race events newest first. |
| `RA-002` | Reconnected/split recordings from one SubSessionID/session MUST group into one event. |
| `RA-003` | Qualifying and Race recordings from the same event MUST group under one event. |
| `RA-004` | The complete event row MUST perform one action: open that event's full analysis. |
| `RA-005` | The browser MUST NOT contain a permanent preview pane, detached Race Review, nested child-session buttons, or a second “open full analysis” step. |
| `RA-006` | Each row MUST use friendly available facts: local time, track/layout, car, series/scope, setup type, result, distance, and analysis state. |
| `RA-007` | Search and filters MUST operate only on recorded/indexed fields and MUST adapt when a category is absent. |
| `RA-008` | Cached analysis SHOULD open effectively immediately; reanalysis MUST be explicit. |
| `RA-009` | Active, changing, zero-record, truncated, or trailing-partial IBTs MUST be deferred without advertising an artifact. |

## Workspace hierarchy

| ID | Requirement |
| --- | --- |
| `RA-020` | The workspace MUST lead with a friendly event header, phase selector when applicable, strict grade, and concise priorities. |
| `RA-021` | Only recorded, openable phases may appear. Nonexistent phases and roadmap controls MUST be absent. |
| `RA-022` | Switching Qualifying/Race MUST remain inside the event and preserve separate setup, conditions, laps, and conclusions. |
| `RA-023` | The detail tabs MUST be exactly: Overview, Telemetry, Corner Coaching, Runs & Tires, Fuel & Strategy, Damage & Repairs, Setup & Evidence. |
| `RA-024` | A tab MUST contain useful supported content or be contextually absent; a capability word alone is never a valid panel. |
| `RA-025` | A mapping/backend/render failure MUST be contained and provide Retry plus Copy Support Details without closing the app. |

## Vertical Runs/Laps rail

| ID | Requirement |
| --- | --- |
| `RA-030` | The rail MUST be vertical, independently scrollable, and left of the map/charts. |
| `RA-031` | Every row MUST render `Lap <integer>` and a separate aligned lap-time field. |
| `RA-032` | Rows MUST expose selection color/control and available green, caution, mixed, pit, incomplete, off-track, incident, service, repair, or tow state with text/icon as well as color. |
| `RA-033` | The rail MUST support multi-select, focus, clean/green filtering, Best Three, and Show All. |
| `RA-034` | Lap zero, partial parade, and otherwise invalid laps MUST NOT enter clean pace or reference selection. |
| `RA-035` | Tire age, fuel, incident, repair, and fastest/sector markers MUST appear only when supported. |

## Map and telemetry

| ID | Requirement |
| --- | --- |
| `RA-040` | The map MUST use recorded position geometry; when absent, use a normalized distance strip rather than invented geometry. |
| `RA-041` | Map and charts MUST share a bidirectional cursor within one source sample/bin. |
| `RA-042` | Multiple selected laps MUST use consistent per-lap trace colors across the rail, map, legend, and every chart. |
| `RA-043` | Required distance-aligned channels are Speed, Time Delta, Throttle, Brake, Gear, RPM, and Steering. |
| `RA-044` | RPM MUST have an independent readable scale and MUST vary when the recorded source varies. |
| `RA-045` | Slip Angle, Yaw Rate, Lateral Acceleration, and Longitudinal Acceleration SHOULD appear when the source channels exist. |
| `RA-046` | Each channel MUST use one shared scale across selected laps; per-lap normalization is forbidden. |
| `RA-047` | Screen drawing MUST preserve short extrema through min/max-aware downsampling. |
| `RA-048` | The workspace MUST support focus/hide, legends with units, zoom/reset, and keyboard-accessible selection. |
| `RA-049` | A target/reference trace MUST identify provenance, scope, scenario, and confidence and MUST require a usable aligned comparison. |

## Tab contracts

### Overview

- `RA-060`: MUST summarize result, totals, grades, priorities, tire/fuel/damage context, confidence, and exclusions without repeating a detached Race Card.

### Corner Coaching

- `RA-070`: MUST include every supported material corner/load zone.
- `RA-071`: SHOULD separate early/middle/late phase and entry/center/exit where evidence supports it.
- `RA-072`: SHOULD show entry speed, brake onset/peak/release, minimum speed, steering work, throttle pickup, exit speed, and tire-management instruction when supported.
- `RA-073`: Exact targets and groove direction MUST obey comparison and geometry gates; relative coaching remains acceptable when exact targets are unavailable.

### Runs & Tires

- `RA-080`: MUST define runs from confirmed service boundaries, not file boundaries or pit-road crossing alone.
- `RA-081`: MUST show available lap bounds, flag mix, pace/falloff, fuel, tire endpoint, service, and clean/confounded status.
- `RA-082`: Measured tire remaining at a confirmed service belongs to the preceding run.
- `RA-083`: Final-run wear MUST remain unmeasured when no service observation exists.
- `RA-084`: Blank headings/columns are forbidden; omit unsupported fields.

### Fuel & Strategy

- `RA-090`: MUST show supported green/caution burn, observed consumption, range, reserve, required stops, pit targets, and recorded stop context.
- `RA-091`: MUST call deterministic output fuel feasibility unless position, pit-loss, rules, cautions, overtime, and history justify stronger optimization language.
- `RA-092`: A page MUST NOT present contradictory range availability and exact range claims without a traceable distinction.

### Damage & Repairs

- `RA-100`: MUST separate incident points, pit road, stall, service, tow, mandatory repair, optional repair, and fast-repair confirmation.
- `RA-101`: Overlapping clocks MUST be parallel and MUST NOT be added into repair-exclusive loss.
- `RA-102`: Zero recorded repair/tow channels may support “No recorded tow/repair workload”; absent channels may not.
- `RA-103`: Repair/tow/candidate laps MUST be excluded from ordinary clean trends, targets, setup comparisons, and pit-loss learning by default.

### Setup & Evidence

- `RA-110`: Setup MUST show friendly type/source and readable recorded parameters; fixed events emphasize driving-only changes.
- `RA-111`: Evidence MUST show coverage, exclusions, provenance, and technical identifiers without leaking those identifiers into primary views.

## Grading

Grades use `A+` through `F` and evaluate Pace & Corner Execution, Consistency & Smoothness, Tire Management, Pit & Fuel Strategy, and Racecraft & Incident Avoidance. A+ requires unusually complete, high-confidence execution near a defensible strong 3k-iRating standard. Unavailable categories require an explicit rubric decision; they may not silently receive a neutral score.

The current fixture mapping exposes only a subset of category grades in some sanitized scenarios. Full rubric calibration and real-field validation remain a product/test gap.
