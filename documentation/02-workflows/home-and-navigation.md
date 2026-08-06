# Home and navigation

## Information architecture

The final-product navigation order is:

1. Home
2. Live Telemetry
3. Race Analysis
4. Race Planning
5. Setups
6. Progressive Tuning
7. Settings

Version 0.11.0 uses this order. Connection management is a Settings section rather than a primary destination.

## Navigation requirements

| ID | Requirement | Acceptance oracle |
| --- | --- | --- |
| `NAV-001` | Primary navigation MUST remain available while background work runs. | Start an analysis and switch pages without cancellation or a global modal. |
| `NAV-002` | Changing pages MUST NOT itself make backend, Garage61, or AI requests. | Two complete navigation cycles produce zero request-counter deltas. |
| `NAV-003` | The selected item MUST be visually and programmatically identifiable. | Visible accent plus `aria-current="page"`. |
| `NAV-004` | The rail MUST collapse at supported smaller widths without making labels or controls unreadable. | 900 × 640 current token minimum and required Windows scaling checks. |
| `NAV-005` | Global service status MUST be compact and truthful. | Local reads/cache hits, Garage61 requests/state, and AI state reflect actual counters. |
| `NAV-006` | Routine “updated just now” or “refreshing” chatter MUST NOT occupy the command bar. | Header remains empty unless an actionable problem exists. |
| `NAV-007` | Normal navigation copy MUST describe racing tasks, not workspace/account/developer concepts. | No `local workspace`, account menu, roadmap copy, or raw service implementation terms. |

## Home contract

Home is a race desk, not a generic card dashboard.

| ID | Requirement |
| --- | --- |
| `HOME-001` | Home MUST show the current iRacing connection and one `Open live telemetry` action that opens the always-on-top telemetry popout. The full Live Telemetry page remains available through primary navigation; Home MUST NOT present a second competing Live Monitor action. |
| `HOME-002` | Home MUST show up to six recent recorded races with friendly track, layout, car, date, setup type, result when known, and compact race-specific measurements. Useful measurements include green/caution laps, longest green run, stop/run count, pace trend or consistency, and measured tire remaining when supported. |
| `HOME-003` | The complete recent-race row MUST select that exact event, open Race Analysis, and begin loading its Race telemetry without a second action. A detached Analyze chip or Open Race Analysis link is forbidden. |
| `HOME-004` | Planning and tuning actions MUST appear only when real eligible history exists. |
| `HOME-005` | The section heading MUST be simply `Recent races`. Its empty state MAY briefly explain how real recordings arrive and MUST NOT offer a redundant manual refresh. |
| `HOME-006` | A backend failure MUST replace the ordinary empty state with one actionable troubleshooting path. |
| `HOME-007` | Home SHOULD expose the single highest-value next action and an upcoming event when a supported provider exists. |
| `HOME-008` | Home MUST NOT display raw source paths, car folders, SubSessionIDs, fingerprints, or fixture identifiers. |
| `HOME-009` | After finalized races are discovered, the app SHOULD analyze every Race recording that lacks a valid current UI-analysis cache in one quiet sequential background queue. A valid cache MUST be reused on later starts; a changed source or cache-schema change MUST make it eligible again. Before starting another background item, the scheduler MUST yield while live telemetry is connected or an interactive analysis is active. Queue work MUST not block navigation, expose a successful-job notification, or require the user to open each race. |
| `HOME-010` | The workflow-card region MUST appear as one settled set after initial capability data is ready. The page MUST NOT briefly render a misleading two-card subset while race-dependent capabilities are still loading. |

## Current implementation note

Version 0.13.0 source implements one Home action for the native telemetry popout, gates the workflow-card set on completed Home discovery, and queues every finalized Race without a valid schema-5 UI cache whose source timestamp matches when that source still exists. A failed identity receives one quiet retry, then leaves the active set so a later refresh or application run can retry it. Successful results are written to the portable analysis cache and immediately update the relevant Race session plus immutable event-group projections used by Home and Race Analysis. The queue checks a shared priority gate before each item and waits while live telemetry is connected or an interactive analysis is active, then resumes sequential catch-up after the foreground activity ends.

Current Home also implements whole-row recent-race opening and real analysis-derived race summaries. Tire remaining is labeled as measured only when a recorded service observation supports it; otherwise the field is omitted or replaced by a supported control-load comparison. The priority gate and background queue are source/test-verified behavior, not evidence that every historical private recording can be analyzed successfully or that contention is eliminated on Joshua's hardware. `GAP-015` is closed in current source while representative combined-load measurement remains under the broader performance gap. Upcoming official event discovery and a computed single highest-value next action are not complete first-class surfaces.
