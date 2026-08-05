# Live telemetry acceptance record - 0.12.0

Date: 2026-08-05

Scope: live telemetry only. This record completes the applicable development-machine portion of `companion-app-handoff/ACCEPTANCE_CHECKLIST.md`; it does not claim unrelated product workflows or racing-PC-only checks were re-certified.

## Interaction and layout

- [x] Full-height editor drawer opens, closes, and reserves horizontal workspace at normal and compact widths.
- [x] Edit mode provides dashed card boundaries, visible drag handles, edge/corner resize targets, configure actions, and direct per-card trash actions.
- [x] Existing widgets drag between targeted grid cells and deterministically reflow displaced widgets.
- [x] Widget-library entries drag from anywhere on the row into targeted grid cells; an explicit Add action remains independently available as an accessible fallback.
- [x] Resizing snaps only after crossing the configured cell threshold and previews only the proposed column-by-row size when valid.
- [x] The dashboard keeps a fixed host footprint; increasing rows or columns shrinks square cells rather than growing the panel.
- [x] Pointer capture, capture-loss cancellation, window-blur cancellation, Escape cancellation, edge auto-scroll, and serialized commits prevent stuck or overlapping gestures.
- [x] Move, add, and resize reject layouts that cannot pack without corrupting the current layout.
- [x] Undo and redo restore placement, sizing, addition, removal, and configuration changes; stale history is invalidated after an external editor change.
- [x] Keyboard alternatives support move, resize, and remove from the card handle with visible focus and accessible names.

## Widget presentation and configuration

- [x] Every catalog metric renders product-styled number, gauge, bar, and chart presentations rather than placeholder blocks; state metrics may also use status.
- [x] Boolean and categorical chart histories use stable scalar encodings, step transitions, and missing values when source evidence is absent.
- [x] The configure menu and selected-widget inspector expose the universal forms and only units supported by the metric.
- [x] Applicable widgets expose accent color, chart history, precision, unit, and trend direction controls.
- [x] Per-card trash was selected as the primary removal pattern because it is discoverable, local to the object, keyboard reachable, and does not require a long cross-screen drag.
- [x] Metric values use semantic catalog ranges, including the brake-bias range, for meaningful bar and gauge fill.
- [x] Persisted layouts repair duplicate IDs, invalid spans/positions, unsupported styles/units, invalid precision, trend values, and accent colors without overlapping tiles.

## Visual quality and accessibility

- [x] Normal-width and compact cards preserve readable titles; compact titles wrap to two lines at the design-system minimum font size.
- [x] Drawer, fixed square grid, toolbar, card actions, waiting states, and configuration panels were reviewed at wide and compact window sizes.
- [x] A duplicate-title specificity defect and compact gauge/chart clipping risk found during review were corrected and re-verified.
- [x] The grid never sits under the fixed drawer, and the drawer does not place a pointer-blocking backdrop over editable cards.
- [x] Reduced-motion, high-contrast/forced-color, focus-visible, screen-reader naming, semantic grouping, and Escape behavior are implemented and covered by static contracts.
- [x] Long card titles, unavailable telemetry copy, drawer content, and toolbar status use wrapping or intentional ellipsis rather than uncontrolled overflow.
- [x] Permanent drag instructions, routine success/readiness text, and the prior editor status strip no longer consume dashboard space.

## Data and shared-editor safety

- [x] Deterministic backend telemetry math remains authoritative; UI changes do not rewrite telemetry analysis or evidence rules.
- [x] Targeted add/move/resize operations are atomic and preserve the prior layout on rejection.
- [x] Default is the only immutable dashboard. Race and Qualifying seed once as ordinary renameable/deletable layouts, and deleted layouts are not recreated on restart.
- [x] Resetting Race or Qualifying uses its own template; resetting any other custom layout uses Default while retaining that dashboard's identity and name.
- [x] The page editor and native Live Monitor detect external layout edits symmetrically and clear unsafe undo/settings snapshots.
- [x] Layout validation preserves supported user configuration while repairing corrupt persisted state.

## Verification evidence

- [x] Release build: 0 warnings, 0 errors.
- [x] Full focused .NET suite: 83 passed, 0 failed.
- [x] Handoff verifier and MCP end-to-end fixture check: passed.
- [x] JavaScript parser check and repository whitespace check: passed.
- [x] Hands-on normal and compact Windows UI passes: completed for the fixed square web grid, universal catalog forms, Default-only protection, and separate pop-out Grid/Monitor size controls; all temporary layout mutations were undone.
- [x] Current Release executable built directly from this source snapshot. Existing 0.12.0 installer/portable artifacts predate the final August 5 refinement and are not claimed as the current build.

## Racing-PC checks still required

- [ ] Validate values, redraw behavior, reconnects, and chart history during a real iRacing session.
- [ ] Review the editor at 150% and 200% Windows scaling on the racing PC.
- [ ] Validate Live Monitor placement and shared-editor synchronization across the intended multi-monitor arrangement.
- [ ] Run a sustained race-length telemetry session and review CPU, memory, and input smoothness under real SDK load.
