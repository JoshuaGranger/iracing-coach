# Live Telemetry and Live Monitor

All high-rate live math is local. Live support must remain useful without AI and must never treat a changing IBT as finalized archive evidence.

## Live Telemetry page

| ID | Requirement |
| --- | --- |
| `LT-001` | The page MUST reconnect automatically to iRacing SDK shared memory in the logged-on Windows session. |
| `LT-002` | Connected state SHOULD show track position, position/class position, lap timing/trend, physical gaps, controls, gear/RPM, dynamics, fuel, temperatures, and supported race state. |
| `LT-003` | High-rate values, rolling traces, gaps, pace range, fuel range, and cues MUST be computed locally. |
| `LT-004` | AI MUST NOT be called per sample, frame, or render. |
| `LT-005` | Physical time gaps MUST remain distinct from pace comparison. |
| `LT-006` | Pace/input cues require a clean personal baseline; repair-confounded laps cannot create them. |
| `LT-007` | Ordinary cue changes MUST obey safe-glance gating on straights, caution, pit road, or lap completion. Critical warnings MAY bypass the gate. |
| `LT-008` | Fuel hard limit MUST remain distinct from a strategic pit window. |
| `LT-009` | Disconnection MUST clear session-specific baselines and stale actionable cues. |
| `LT-010` | Disconnected state MUST retain a professional structural preview and one clear waiting explanation. |
| `LT-011` | A deterministic replay interface MUST exercise connected behavior without iRacing or private telemetry. |
| `LT-012` | The full page and miniature monitor MUST use the same named layout, grid, tile definitions, catalog, persisted logical preferences, and live readings. |
| `LT-013` | The full page MUST provide a right-side Toolbox that fades in and out, can be hidden without ending edit mode, and never changes the telemetry grid's width. Hiding or reopening it MUST preserve the active layout and edit state. |
| `LT-014` | The Toolbox MUST support named-layout selection/create/duplicate/reset/delete, 1–8 rows and columns, tile selection/move/resize/remove, display style/unit/precision controls, metric search, and metric addition. Every telemetry metric MUST support Number, Bar, Gauge, and Chart forms, including truthful state encoding and history for boolean or categorical values. |
| `LT-015` | Default MUST remain immutable. Every other dashboard MUST support rename and delete; invalid moves or sizes MUST preserve the prior valid layout and explain the conflict concisely. |
| `LT-016` | Primary live screen space MUST favor glanceable telemetry. Detailed driving traces MAY remain in a subordinate expandable region. |
| `LT-017` | No full-page tile or catalog option may be fixture-only, seeded, or disconnected from the real live catalog. |
| `LT-018` | When the SDK publishes at 60 Hz, live frames MUST be captured at native cadence and an open trace canvas MUST paint at the display refresh cadence. Display-time motion MAY interpolate the horizontal position between real samples, but MUST NOT invent a higher telemetry sampling rate or fabricated intermediate measurements. A 60 Hz source may not be reduced to a 4–10 Hz chart by UI throttling. |
| `LT-019` | High-rate trace painting MUST be isolated from slower text, layout, status, and navigation rendering. Rendering MUST be bounded and MUST preserve brief brake, steering, and other material extrema when reducing samples for display. |
| `LT-020` | Time-window trends MUST use recorded sample timestamps, and lap-window trends MUST use recorded lap plus lap-distance progression. Boolean and categorical traces MUST use step transitions. Missing samples MUST remain gaps rather than zeroes. Reduced-motion mode MUST disable nonessential between-sample movement without suppressing newly recorded samples. |

## Live Monitor grid editor

| ID | Requirement |
| --- | --- |
| `LM-001` | Live Monitor MUST be a distinct movable, always-on-top window whose native resize border is disabled. |
| `LM-002` | The default layout MUST be a locked 3-column by 2-row grid with six glanceable tiles. |
| `LM-003` | Disconnected and replay states MUST be visibly distinguishable. |
| `LM-004` | Monitor physical position, display identity, and overall scale MUST be machine-local. Named logical grid layouts, tile choices, styles, units, precision, trend duration, and accent choices MUST be portable. |
| `LM-005` | Opening/hiding the monitor MUST change the actual window, not only an internal boolean. |
| `LMG-001` | The popout top strip MUST contain only an always-available move grip, named-layout selector, physical-scale control, and close control, with tooltips, accessible names, focus states, and keyboard access. Grid editing and layout authoring belong only in the full application. |
| `LMG-002` | Grid rows and columns MUST each support 1 through 8. Every row MUST receive an equal share of the available height and every column an equal share of the available width, so cells may be rectangular and the layout always fills its host. The popout alone MUST provide 70% through 200% physical scale; the full-page surface MUST fit its window without a physical-scale control. |
| `LMG-003` | The full application MUST start in viewing mode. Customize MUST enter edit mode without shrinking or offsetting the grid. The Toolbox MAY overlay the right side, MUST be hideable while editing continues, and MUST use a reduced-motion-safe transition. The native popout MUST remain display-only. |
| `LMG-004` | The catalog MUST list every supported live scalar, boolean, enum, and derived metric alphabetically, with search, a fixed scroll region, current value or truthful unavailable reason, source label, available forms, and an Add control. A catalog item MUST be draggable from the full row while its Add control remains independently operable. |
| `LMG-005` | Missing live data MUST never be displayed as zero, neutral, average, or fixture data. Disconnect MUST invalidate stale readings. |
| `LMG-006` | In the full application, tiles MUST support drag/drop cell preview, snap/reflow without overlap, 1×1 minimum resizing, deletion, keyboard move/resize alternatives, and at least one undo level. The preview for dropping onto an occupied tile MUST identify the replacement before it occurs. |
| `LMG-007` | Every metric MUST support Number, Gauge, Bar, and Trend styles. Boolean and categorical trends MUST use truthful stable scalar encodings and step transitions; missing data MUST remain missing. Trend tiles MUST support 15/30/60 seconds and 1/3 laps. Trend ranges MUST remain stable while visible and downsample without hiding extrema. |
| `LMG-008` | Per-tile controls MUST expose only valid style, unit, 0–3 decimal precision, trend duration, size, and accent choices. |
| `LMG-009` | Default MUST be the only immutable dashboard. Race and Qualifying MUST be seeded once as ordinary portable dashboards so they and every user-created dashboard support create, duplicate, rename, reset, and delete without a deleted dashboard being restored on restart. |
| `LMG-010` | Legacy 0.9.3 monitor preferences MUST migrate once. Invalid layouts MUST fall back to Default and preserve rejected monitor data in machine-local support evidence. |
| `LMG-011` | Moving between PCs MUST retain logical layouts while recomputing physical placement and scale from destination-machine state. Off-screen placement MUST recover to a visible working area. |
| `LMG-012` | The monitor render path MUST remain local, bounded, and independent of AI or network calls. Incoming readings MUST update existing tile visuals incrementally, and open native trend tiles SHOULD paint at the display dispatcher cadence; rebuilding the complete popout visual tree on a slow periodic timer is forbidden. Display cadence MUST remain distinct from source sampling cadence. Replay tests MUST measure sustained update behavior and dropped-frame/latency counters. |
| `LMG-013` | Edit mode MUST not reserve permanent space for drag instructions, routine success messages, readiness text, or the Toolbox. During resize, the preview MUST show only the proposed column-by-row size unless the placement is invalid. |
| `LMG-014` | The native popout MUST be a fitted renderer and named-layout selector for layouts authored in the full application. It MUST not expose tile editing, grid-size editing, rename, reset, or delete controls. Labels and values MUST scale or trim within their allocated cells instead of clipping controls outside the window. |

## Tray and shutdown

| ID | Requirement |
| --- | --- |
| `TRAY-001` | The app SHOULD support minimize-to-tray and configurable close-to-tray. |
| `TRAY-002` | The tray menu SHOULD expose Show App, Show/Hide Live Monitor, connection state, and Exit. |
| `TRAY-003` | Exit MUST terminate live SDK, backend, and optional Coach Engine workers cleanly. |
| `TRAY-004` | Selecting Exit MUST immediately hide the main window, Live Monitor, and tray icon; defer resource disposal until the tray callback has returned; and execute shutdown exactly once. A slow or failed cleanup step MUST NOT leave a frozen visible window or prevent the remaining shutdown steps. |

## Current evidence

Version 0.14.0 uses equal fractional/Star tracks that fill the measured viewport remaining below the actual header. The editor remains full width beneath its status bar and the Toolbox is an independent overlay. Scalar tile/catalog reads no longer project trend history. Trend seeds and pending queues are bounded; latest-value display compaction preserves newest samples, semantic gaps, and material minima/maxima instead of allowing an unbounded producer queue to become UI work. Detailed driving traces are not mounted until expanded.

SDK reading now runs on a dedicated background thread. On Windows it requests a high-resolution waitable timer, uses a 2 ms connected observation interval, and falls back to a normal waitable timer or thread sleep if the high-resolution handle is unavailable; disconnected discovery drops to 40 ms. Source tick deltas, not presentation frames, feed the dropped-frame counter. Disposal signals the loop, joins it for a bounded interval, and then disposes the source.

The live evidence engine resets at a connected-session boundary. Partial first laps, skipped-lap intervals, caution/pit/repair/tow/black-flag laps, and missing dynamics are rejected for the claims they cannot support. Fuel projection requires at least two complete clean observed burns. Run phase is explicitly the observed clean-lap count since pit road, not a claim of confirmed service. The native popout updates retained elements, automatic showing never activates or steals focus, manual hiding while connected suppresses reopening until the next connection, ordinary window Close hides it, and app shutdown explicitly destroys it.

These are implementation-path and automated-test statements only. A focused synthetic 240 Hz source run captured roughly 170 frames in roughly 810 ms and therefore exercises the dedicated reader above the former approximately 125 Hz polling ceiling. It is structural scheduler evidence, not a real iRacing measurement, not a display-cadence measurement, and specifically not acceptance of 244 Hz presentation. Neither animation-frame interpolation, bounded queues, the synthetic source, nor the native dispatcher proves 60 Hz SDK capture, 244 Hz delivery, latency, or a particular frame rate on the user's hardware.

These statements describe inspected current source and targeted local tests, not real-system acceptance. Deterministic replay and a fresh development-executable walkthrough can verify rendering and interaction paths, but they do not prove sustained real iRacing cadence, 244 Hz presentation on a particular display, multi-monitor ergonomics, or usefulness while driving. Those claims require measurements from the exact executable, simulator session, display mode, and hardware.

Current main also retains one idempotent, deferred tray-exit path. Its prior process probes are unchanged by this focused iteration; the installer lifecycle was not re-certified merely because telemetry presentation changed.
