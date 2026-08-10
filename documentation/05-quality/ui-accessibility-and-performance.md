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
| `UI-017` | A user-selected primary theme color MUST influence accent, selected, hover, and keyboard-focus treatment consistently across the web shell and native telemetry popout. Palette changes MUST preserve text/focus contrast and MUST NOT recolor semantic telemetry channels or status meanings into ambiguity. |
| `UI-018` | The global command bar MUST use the shared compact 28 px height token. Live Telemetry and Race Analysis nonmodal toolboxes MUST use one shared motion token for drawer opacity/translation and owning-page width reflow, so content shrink/expand and drawer fade start and finish together. A toolbox MUST reflow only its owning workbench region beside it rather than cover controls or change the width of an unaffected sibling column. Closing MUST not use delayed visibility, cleanup, or a discrete sizing change that produces an end-of-motion stutter. Race Track/Laps bubble toggles MUST apply the same synchronization principle to vertical panel and horizontal context-column transitions. Reduced-motion preferences MUST disable every participating transition together. Source-contract checks are necessary but explicit open/close animation observation at supported window sizes is required for acceptance. |
| `UI-019` | Telemetry, Technical data overview, each Technical data investigation, and Race replay MUST each fit beneath the compact Race event bar at 1280x720 without a page-level scrollbar. Dense rails, libraries, and investigations MAY scroll inside an explicitly bounded region, but tabs, Back, playback, map legend, layout controls, and primary comparisons MUST remain visible and unclipped. |
| `UI-020` | Vector map classes, lap traces, flags, participant classes, leader/user emphasis, pit entry/exit, and metric strength MUST combine color with labels, numbers, line style, geometry, or size. The exact map palette in `RA-050B` MUST remain distinguishable against the selected theme and in both ordinary and emphasized replay states. |
| `UI-021` | Section changes, Technical data drill-in/back, map-layer changes, context/toolbox reflow, replay playback, leader-rail auto-scroll, and running-order/continuous-flag-timeline updates MUST use coordinated motion with no intermediate clipping, overlap, stale frame, or asynchronous endpoint jump. Reduced motion MUST preserve state synchronization while removing or shortening nonessential interpolation. |
| `UI-022` | All structural expansion, collapse, drawer, and post-drop reflow motion MUST consume the single generated `motionMs.structure` / `--motion-structure` duration, currently 500 ms. Pointer tracking, chart cursors, live telemetry paint, scrolling, hover response, and active drag feedback MUST remain frame-local and MUST NOT inherit that structural duration. Reduced motion sets structural duration to zero without suppressing new telemetry samples. |

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
| `PERF-009` | Race replay MUST decode/cache a bounded frame window and render all synchronized surfaces from one monotonic playback clock. Seeking or changing speed MUST cancel obsolete work rather than queue every skipped frame; the participant count, frame duration, memory bound, render cadence, and dropped-frame behavior MUST be measured on representative sessions. |
| `PERF-010` | Track geometry extraction, raw-IBT retention, and tire-model updates MUST be incremental and content-addressed where specified. App startup and ordinary navigation MUST not recopy unchanged IBTs, rebuild unchanged geometry, or retrain an unchanged observation set. |

## 0.15.0 performance repair evidence

The 0.15.0 development iteration addressed the work occurring on hot interaction paths rather than replacing the application stack. Instrumented source/harness comparisons recorded the following changes:

| Path | Before | After |
| --- | --- | --- |
| Race Analysis chart cursor | Approximately 27–40 synchronous text/layout probes per pointer frame, with redundant pointer work able to accumulate | Zero forced-layout probes in the pointer frame; pointer input is coalesced through one `requestAnimationFrame`, cached measurements are reused, and no timer or managed-render callback drives cursor movement |
| Live telemetry idle rendering | Idle animation frames and layout probes continued even when no visual update was required | Zero idle animation frames and zero idle layout probes; rendering is requested only for new work |
| Live telemetry sampling/paint | Allocation-heavy per-pixel maps and repeatedly rebuilt intermediates amplified garbage-collection and paint cost | Reused bounded buffers and `Path2D` envelopes remove the high-frequency allocation pattern while retaining the visible extrema |
| Live dashboard active drag | Geometry was reread throughout movement, measuring approximately 3,900 layout reads per second in the drag harness | Zero layout reads during active movement; geometry is cached at drag start and visual feedback is coalesced to the display frame |
| Canvas resize | Backing-store work could repeat throughout a resize gesture | CSS sizing remains responsive during movement and the backing store is rebuilt once after resize settles |
| Default iRacing discovery | Probing an unavailable mapped drive stalled startup for approximately 21 seconds on the measured machine | Automatic discovery scans ready fixed drives only and initializes in the background, removing that mapped-drive stall from the UI startup path |

These are static-analysis, instrumentation, and local harness results from the development source. They establish that the known forced-layout, idle-loop, allocation, resize, drag, and discovery bottlenecks were removed. They do **not** establish real iRacing sampling cadence, end-to-end display latency, or accepted 244 Hz smoothness on the simulator PC. Those claims require the exact executable running with a real session on the target high-refresh display.

The evidence supports retaining the existing .NET/WPF shell with Blazor/WebView content. The bottlenecks were in browser-side layout reads, scheduling, allocation, canvas lifecycle, and synchronous discovery—not a demonstrated limit of C# or the WebView compositor. A C++ rewrite would add major delivery and correctness risk without addressing the measured causes. Native canvas/browser-side rendering paths, bounded allocation, frame coalescing, and background/fixed-drive discovery are the justified fixes; a lower-level renderer should be reconsidered only if exact-artifact profiling on the simulator still misses a defined frame-time target after these repairs.

Implementation references: `coach.css`, `theme.generated.css`, shell/page Razor components, `UI_DESIGN_SYSTEM.md`, `UI_UX_AUDIT_0.8.0.md`, and `iRacingCoach.PerformanceProbe`.

Version 0.14.0 retains the expanded palette, glass treatment, compact Settings hierarchy, frame-coalesced Race cursor, and retained native-popout visuals. A dedicated Windows reader requests a high-resolution waitable timer at a 2 ms connected interval and uses a 40 ms disconnected discovery interval. Live scalar reads bypass history projection; seed projections are cached and bounded; pending UI queues are capped and compacted while preserving newest values, semantic gaps, and extrema; and detailed driving traces lazy-mount only when expanded. Race Analysis serializes cursor data only when managed state changes, then updates crosshair, tooltips, markers, map, and readout in JavaScript without per-frame .NET callbacks. The Home scheduler still yields background cache work to live telemetry and interactive analysis.

These are implementation facts and focused-test boundaries. The roughly 170 frames captured in roughly 810 ms from a synthetic 240 Hz source show that the reader is no longer structurally capped near 125 Hz; they do not measure iRacing, the packaged app on racing hardware, or display delivery. These results are not a WCAG audit, a real-telemetry cadence measurement, a combined-load benchmark, or proof of 60 Hz capture or accepted 244 Hz presentation on the user's display.
