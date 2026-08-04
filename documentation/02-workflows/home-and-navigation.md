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
| `HOME-001` | Home MUST show the current iRacing connection and direct actions for Live Telemetry and Live Monitor. |
| `HOME-002` | Home MUST show up to six recent recorded races with friendly track, layout, car, date, setup type, result when known, and analysis state. |
| `HOME-003` | Open/Analyze on a recent race MUST select that exact event and open its deterministic workflow. |
| `HOME-004` | Planning and tuning actions MUST appear only when real eligible history exists. |
| `HOME-005` | The empty state MUST explain automatic discovery and MUST NOT offer a redundant manual refresh. |
| `HOME-006` | A backend failure MUST replace the ordinary empty state with one actionable troubleshooting path. |
| `HOME-007` | Home SHOULD expose the single highest-value next action and an upcoming event when a supported provider exists. |
| `HOME-008` | Home MUST NOT display raw source paths, car folders, SubSessionIDs, fingerprints, or fixture identifiers. |

## Current implementation note

The 0.11.0 `HomePage` implements connection status, concise workflow actions, automatic discovery, recent races, result formatting, direct race opening, and warning-only troubleshooting. Upcoming official event discovery and a computed single “highest-value next action” are not complete first-class surfaces.
