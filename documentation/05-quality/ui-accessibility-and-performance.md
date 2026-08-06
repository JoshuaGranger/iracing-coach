# UI, Accessibility, and Performance

The UI should feel like a calm professional race-engineering workspace: black/charcoal foundations, restrained translucent depth, and a small purposeful palette of mint, amber, coral, violet, and telemetry-specific colors. It should not collapse into generic gray/blue monochrome. Information remains dense but legible, hierarchy remains clear, and copy remains human. Success noise is suppressed; only actionable abnormal status deserves persistent prominence.

## Visual and interaction quality

| ID | Requirement |
| --- | --- |
| `UI-001` | Native title bar, app surface, dialogs, menus, monitor windows, icon, and installer metadata MUST use a coherent dark product identity. |
| `UI-002` | Primary navigation MUST be stable and contain only implemented first-class destinations. |
| `UI-003` | Pages MUST auto-refresh when underlying local state changes; manual refresh is reserved for recovery or explicit reanalysis. |
| `UI-004` | The global header MUST remain quiet when healthy. "Updating", "updated just now", local-workspace labels, and healthy-system dashboards MUST not compete with user tasks. |
| `UI-005` | Copy MUST use familiar human wording and explain unavoidable motorsport or technical terms in context. |
| `UI-006` | Empty states MUST explain what real action or source will populate the view; fake personal data and inert sample controls are forbidden in production. |
| `UI-007` | Dangerous, destructive, external, and long-running actions MUST communicate their scope before execution. |
| `UI-008` | Content MUST remain usable at supported minimum window dimensions and common Windows scaling settings without clipped actions or inaccessible regions. |
| `UI-009` | Maps, charts, tables, and rails MUST share selection/state consistently and MUST avoid encoding meaning by color alone. |
| `UI-010` | Error states MUST preserve surrounding context and offer retry plus redacted support details where useful. |
| `UI-011` | Primary views MUST favor changing decisions and measurements over explanatory prose. Invariant limitations, methodology, and provenance belong in tooltips or subordinate disclosures. |
| `UI-012` | Controls MUST sit beside the content they affect, and labels MUST state ambiguous scope such as the selected lap or layout. |
| `UI-013` | Recorded-race comparison SHOULD fit its primary telemetry, lap/run selection, and compact changing insights into one coherent desktop workspace without lower navigation tabs. |
| `UI-014` | Successful background work MUST settle silently. Only running work that materially benefits from progress visibility or failed work requiring action may occupy persistent UI. |
| `UI-015` | Color and glass effects MUST add hierarchy and character without reducing contrast or becoming decorative noise. Positive/add actions MAY use green/mint, destructive actions SHOULD use red/coral, and telemetry channels SHOULD retain distinct semantic colors; no meaning may rely on color alone. |
| `UI-016` | Settings MUST reveal common decisions before technical detail. A user SHOULD be able to confirm data readiness, prepare a portable copy, set ordinary behavior, and configure the telemetry popout without first reading file-system, service, or diagnostic implementation detail. |

## Accessibility

| ID | Requirement |
| --- | --- |
| `ACC-001` | Every interactive element MUST be reachable and operable with keyboard alone in a logical order. |
| `ACC-002` | Focus MUST be visible against all supported backgrounds and MUST not be removed for aesthetic reasons. |
| `ACC-003` | Controls, icons, tabs, graphs, and status indicators MUST expose useful accessible names, roles, values, and states. |
| `ACC-004` | Text and meaningful non-text indicators MUST meet WCAG 2.2 AA contrast targets where applicable to the desktop surface. |
| `ACC-005` | Motion, blinking, and continuous status updates MUST be minimized and respect user/system preferences. |
| `ACC-006` | Layout SHOULD tolerate text enlargement and long localized/user values even before formal localization exists. |

## Performance

| ID | Requirement |
| --- | --- |
| `PERF-001` | Cached event lists and analyses SHOULD feel immediate under representative libraries. |
| `PERF-002` | Telemetry rendering MUST remain responsive at representative channel counts, lap counts, and sample density. |
| `PERF-003` | UI work MUST avoid unbounded polling, full-library rescans on every view update, and redundant reanalysis. |
| `PERF-004` | Expensive work MUST report meaningful progress only when it lasts long enough to matter and MUST not flood the interface with transient success messages. |
| `PERF-005` | Performance probes MUST publish input size, environment, percentile/maximum measurements, and thresholds rather than a bare pass label. |
| `PERF-006` | A display-synchronized telemetry renderer MUST keep source sampling cadence, data timestamp, arrival time, and display refresh conceptually separate. `requestAnimationFrame` or a high-refresh display MUST NOT be reported as a higher SDK sampling rate. Claims such as 60 Hz capture or 244 Hz presentation require measurements from the exact artifact and environment. |
| `PERF-007` | High-rate pointer and trace interactions SHOULD coalesce redundant work to the display frame. When all required cursor data is already serialized to the rendering runtime, cursor motion SHOULD update DOM/SVG directly rather than crossing into managed component rendering per frame. Cursor marker and value-card DOM MUST be bounded by the visible tooltip capacity rather than the number of selected laps; wheel paging SHOULD rebind that fixed pool to another selected-lap slice. Aggregate calculations that explicitly cover all selected laps MAY still inspect the full selection. Any unavoidable cross-runtime callback MUST be bounded so a slow callback cannot create a queue. |
| `PERF-008` | Opportunistic background analysis MUST yield before starting another item whenever live telemetry is connected or an interactive analysis is active. Source-level priority gating does not replace combined-load measurement on representative libraries and racing hardware. |

Implementation references: `coach.css`, `theme.generated.css`, shell/page Razor components, `UI_DESIGN_SYSTEM.md`, `UI_UX_AUDIT_0.8.0.md`, and `iRacingCoach.PerformanceProbe`.

Version 0.13.0 source applies the expanded palette and glass treatment app-wide, uses a compact Settings hierarchy, drives full-page live tile canvas painting through `requestAnimationFrame`, and bounds display vertices with per-pixel first/minimum/maximum/last retention. The native popout incrementally updates retained tile visuals and schedules trend painting through the display dispatcher instead of rebuilding the visual tree at 5 Hz. Race Analysis serializes cursor data when component state changes, then performs frame-coalesced crosshair, tooltip, marker, map, and readout updates entirely in JavaScript DOM/SVG without per-frame .NET callbacks. Its marker/value-card pool is bounded by tooltip capacity and rebound to the current wheel-visible lap slice, while the explicitly aggregate track summary still reads all selected laps. The Home scheduler yields queued cache analysis to live telemetry and interactive analysis before starting another item.

These are implementation facts and focused-test boundaries. They are not a WCAG audit, a real-telemetry cadence measurement, a combined-load benchmark, or proof of 60 Hz capture or 244 Hz presentation on the user's display.
