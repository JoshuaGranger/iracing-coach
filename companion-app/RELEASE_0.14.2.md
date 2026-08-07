# iRacing Coach 0.14.2 Race Analysis trace-editor parity

Date: 2026-08-07

## Artifact identity

- Source commit: `0bb46a872722e9b72c1616e2cdc807895b46b6ad`
- Installer: `artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Setup.exe`
- Installer bytes: `498976808`
- Installer SHA-256: `b21a2d229c68f085c3aa614c75697157f2fd17a574af9068643bea0e666ba65b`
- Portable package: `artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Portable-win-x64.zip`
- Portable bytes: `382983898`
- Portable SHA-256: `f2a9ba06304c8d27d6f46147c8899cd6e92d80151c36271b71c71a0bc6ac2f0c`

The package contains 9,281 payload files. The ZIP contains those 9,281 files plus 24 directory entries, with one root application executable and one coach manifest. The packaged runtime is `0.146.0-alpha.9.2`; its measured SHA-256 `ecd7a3eaff5e42723dbba03b5c91514b3986b5db5cbca8f34619620b5356f31f` matches the manifest. The installer reports file version `0.14.2.0` and product version `0.14.2+0bb46a872722e9b72c1616e2cdc807895b46b6ad`. Static inspection found no unsafe ZIP paths, raw `.ibt` or `.log` files, private settings/auth files, machine-local state, or backend user-data roots.

Artifact measurements were completed after packaging from the named source commit. This focused release does not claim a repeated installer lifecycle matrix for unchanged installer behavior.

## Release focus

This release replaces the Race Analysis trace configuration form with a direct-manipulation editor that follows the established Live Telemetry interaction model. It also repairs the narrow trace labels that previously stretched, clipped, and exposed implementation guidance in the chart itself.

## Corrected behavior

- Trace titles use normal HTML text rather than stretched SVG text. Bold names wrap within approximately 12 characters and units render on a separate, regular-weight line.
- Solid/dashed identity, independent-scale behavior, and missing-channel guidance appear only in an accessible hover/focus tooltip.
- The actual chart is the one-column editing canvas. Drag a chart title to reorder it, or drag telemetry from anywhere on a toolbox item onto a chart to pair or replace a trace.
- Pointer dragging now uses the same interaction qualities as Live Telemetry: activation threshold, pointer capture, a floating ghost, insertion/replacement previews, automatic scrolling, cancellation, commit locking, and animated reflow.
- The right-side toolbox stays over the analysis workspace without shrinking the chart canvas and provides search, reset, selected-chart removal, full-card keyboard pairing/replacement, and focus return when closed.
- Pointer targeting rejects chart rows hidden underneath the open toolbox and recalculates the drop at pointer release, preventing occluded or stale-target commits.
- The selected-chart inspector derives solid/dashed identity from the traces that can actually render; unavailable channels are identified separately.
- Trace order and pairings persist through the existing portable settings file.

## Regression cause

The prior trace editor was introduced as a separate native HTML drag/drop form with dropdowns and arrow buttons. Its tests checked that drag attributes existed, but did not enforce interaction parity with Live Telemetry. Labels remained unwrapped SVG text, and static solid/dashed explanations were placed in the fixed chart gutter. The result technically exposed configuration controls while missing the requested interaction and presentation model.

## Verification completed before packaging

- Required handoff verifier: passed.
- .NET: 144 passed, 0 failed.
- Python: 187 passed, 0 failed.
- JavaScript syntax: 5 passed.
- Release application build: 0 warnings, 0 errors.
- Direct saved-race walkthrough: Iowa opened with the default ten rows; bold labels wrapped normally with units below; the tooltip exposed line-style guidance only on title hover; the right toolbox opened without shrinking the chart; dragging Speed below Time delta reordered both labels and plots; full-card and keyboard placement paired Brake with the selected Speed chart; dragging entirely within the toolbox did not mutate a hidden chart; Reset restored the exact default order and pairings; closing the drawer returned the visible focus treatment to its toggle.

## Known limits and acceptance pending

- The interaction walkthrough uses saved local recordings, not a live iRacing session.
- Real telemetry cadence, combined-load performance, and while-driving usefulness remain separate acceptance work.
- Unchanged install replacement, rollback, uninstall, reinstall, and durable-data preservation behavior is not rerun for this focused UI correction.
- User acceptance remains pending.
