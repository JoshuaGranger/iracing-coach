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
| `LT-013` | The full page MUST provide a right-side Toolbox that can collapse without leaving an empty column and reopen without losing selection or layout state. |
| `LT-014` | The Toolbox MUST support named-layout selection/create/duplicate/reset/delete, 1–8 rows and columns, tile selection/move/resize/remove, display style/unit/precision controls, metric search, and metric addition. Every telemetry metric MUST support Number, Bar, Gauge, and Chart forms, including truthful state encoding and history for boolean or categorical values. |
| `LT-015` | Default MUST remain immutable. Every other dashboard MUST support rename and delete; invalid moves or sizes MUST preserve the prior valid layout and explain the conflict concisely. |
| `LT-016` | Primary live screen space MUST favor glanceable telemetry. Detailed driving traces MAY remain in a subordinate expandable region. |
| `LT-017` | No full-page tile or catalog option may be fixture-only, seeded, or disconnected from the real live catalog. |
| `LT-018` | When the SDK publishes at 60 Hz, live frames MUST be captured at native cadence and the open trace canvas MUST paint at the display refresh cadence. A 60 Hz source may not be reduced to a 4–10 Hz chart by UI throttling. |
| `LT-019` | High-rate trace painting MUST be isolated from slower text, layout, status, and navigation rendering. Trace downsampling MUST preserve within-pixel minima and maxima so brief brake or steering inputs remain visible. |

## Live Monitor grid editor

| ID | Requirement |
| --- | --- |
| `LM-001` | Live Monitor MUST be a distinct movable, always-on-top window whose native resize border is disabled. |
| `LM-002` | The default layout MUST be a locked 3-column by 2-row grid with six glanceable tiles. |
| `LM-003` | Disconnected and replay states MUST be visibly distinguishable. |
| `LM-004` | Monitor physical position, display identity, and overall scale MUST be machine-local. Named logical grid layouts, tile choices, styles, units, precision, trend duration, and accent choices MUST be portable. |
| `LM-005` | Opening/hiding the monitor MUST change the actual window, not only an internal boolean. |
| `LMG-001` | The top strip MUST contain only an always-available move grip, named-layout selector, lock/unlock icon, separate grid and physical-scale controls, and close icon, with tooltips, accessible names, focus states, and keyboard access. |
| `LMG-002` | Grid rows and columns MUST each support 1 through 8 inside a fixed logical footprint. Cells MUST remain square and shrink as row or column count increases. Popout-only overall scale MUST support 70% through 200%; each control's Cancel action MUST restore only that control's prior values. The fixed/full-page surface MUST fit its window without a physical-scale control. |
| `LMG-003` | The monitor MUST start locked. Unlocking MUST expand the editor below the grid without covering a tile. Reduced-motion preference MUST suppress nonessential transition animation. |
| `LMG-004` | The catalog MUST list every supported live scalar, boolean, enum, and derived metric alphabetically, with search, a fixed scroll region, current value or truthful unavailable reason, source label, available forms, and an Add control. A catalog item MUST be draggable from the full row while its Add control remains independently operable. |
| `LMG-005` | Missing live data MUST never be displayed as zero, neutral, average, or fixture data. Disconnect MUST invalidate stale readings. |
| `LMG-006` | Tiles MUST support drag/drop cell preview, snap/reflow without overlap, 1×1 minimum resizing, deletion, keyboard move/resize alternatives, and at least one undo level. |
| `LMG-007` | Every metric MUST support Number, Gauge, Bar, and Trend styles. Boolean and categorical trends MUST use truthful stable scalar encodings and step transitions; missing data MUST remain missing. Trend tiles MUST support 15/30/60 seconds and 1/3 laps. Trend ranges MUST remain stable while visible and downsample without hiding extrema. |
| `LMG-008` | Per-tile controls MUST expose only valid style, unit, 0–3 decimal precision, trend duration, size, and accent choices. |
| `LMG-009` | Default MUST be the only immutable dashboard. Race and Qualifying MUST be seeded once as ordinary portable dashboards so they and every user-created dashboard support create, duplicate, rename, reset, and delete without a deleted dashboard being restored on restart. |
| `LMG-010` | Legacy 0.9.3 monitor preferences MUST migrate once. Invalid layouts MUST fall back to Default and preserve rejected monitor data in machine-local support evidence. |
| `LMG-011` | Moving between PCs MUST retain logical layouts while recomputing physical placement and scale from destination-machine state. Off-screen placement MUST recover to a visible working area. |
| `LMG-012` | The monitor render path MUST remain local, bounded, and independent of AI or network calls. Replay tests MUST measure sustained update behavior and dropped-frame/latency counters. |
| `LMG-013` | Edit mode MUST not reserve permanent space for drag instructions, routine success messages, or readiness text. During resize, the preview MUST show only the proposed column-by-row size unless the placement is invalid. |

## Tray and shutdown

| ID | Requirement |
| --- | --- |
| `TRAY-001` | The app SHOULD support minimize-to-tray and configurable close-to-tray. |
| `TRAY-002` | The tray menu SHOULD expose Show App, Show/Hide Live Monitor, connection state, and Exit. |
| `TRAY-003` | Exit MUST terminate live SDK, backend, and optional Coach Engine workers cleanly. |
| `TRAY-004` | Selecting Exit MUST immediately hide the main window, Live Monitor, and tray icon; defer resource disposal until the tray callback has returned; and execute shutdown exactly once. A slow or failed cleanup step MUST NOT leave a frozen visible window or prevent the remaining shutdown steps. |

## Current evidence

Version 0.11.1 adds native-cadence SDK capture and a display-synchronized canvas trace renderer while retaining slower bounded updates for text and layout surfaces. Version 0.11.0 added the shared full-page layout surface and collapsible Toolbox while retaining the miniature monitor as the same persisted view model. Deterministic replay remains development evidence only. Real simulator timing, multi-monitor ergonomics, and sustained live resource use require direct local validation on representative systems; they are not owned by a separate QA role.

Current main also uses one idempotent, deferred tray-exit path. Process probes cover exit with the main window visible, the app hidden/minimized, and both main window and Live Monitor visible.
