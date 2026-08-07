# iRacing Coach 0.14.2 Race Analysis trace-editor parity

Date: 2026-08-07

## Artifact identity

- Source commit: `PENDING`
- Installer: `artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Setup.exe`
- Installer bytes: `PENDING`
- Installer SHA-256: `PENDING`
- Portable package: `artifacts/dist/v0.14.2/iRacingCoach-0.14.2-Portable-win-x64.zip`
- Portable bytes: `PENDING`
- Portable SHA-256: `PENDING`

Artifact measurements are completed only after packaging from the named source commit. This focused release does not claim a repeated installer lifecycle matrix for unchanged installer behavior.

## Release focus

This release replaces the Race Analysis trace configuration form with a direct-manipulation editor that follows the established Live Telemetry interaction model. It also repairs the narrow trace labels that previously stretched, clipped, and exposed implementation guidance in the chart itself.

## Corrected behavior

- Trace titles use normal HTML text rather than stretched SVG text. Bold names wrap within approximately 12 characters and units render on a separate, regular-weight line.
- Solid/dashed identity, independent-scale behavior, and missing-channel guidance appear only in an accessible hover/focus tooltip.
- The actual chart is the one-column editing canvas. Drag a chart title to reorder it, or drag telemetry from anywhere on a toolbox item onto a chart to pair or replace a trace.
- Pointer dragging now uses the same interaction qualities as Live Telemetry: activation threshold, pointer capture, a floating ghost, insertion/replacement previews, automatic scrolling, cancellation, commit locking, and animated reflow.
- The right-side toolbox stays over the analysis workspace without shrinking the chart canvas and provides search, reset, selected-chart removal, and keyboard fallbacks.
- Trace order and pairings persist through the existing portable settings file.

## Regression cause

The prior trace editor was introduced as a separate native HTML drag/drop form with dropdowns and arrow buttons. Its tests checked that drag attributes existed, but did not enforce interaction parity with Live Telemetry. Labels remained unwrapped SVG text, and static solid/dashed explanations were placed in the fixed chart gutter. The result technically exposed configuration controls while missing the requested interaction and presentation model.

## Verification completed before packaging

- Required handoff verifier: passed.
- .NET: 144 passed, 0 failed.
- Python: 187 passed, 0 failed.
- JavaScript syntax: 5 passed.
- Release application build: 0 warnings, 0 errors.
- Direct saved-race walkthrough: Iowa opened with the default ten rows; bold labels wrapped normally with units below; the tooltip exposed line-style guidance only on title hover; the right toolbox opened without shrinking the chart; dragging Speed below Time delta reordered both labels and plots; dragging Brake from the body of its library item onto Time delta produced a solid/dashed pair; Reset restored the exact default order and pairings.

## Known limits and acceptance pending

- The interaction walkthrough uses saved local recordings, not a live iRacing session.
- Real telemetry cadence, combined-load performance, and while-driving usefulness remain separate acceptance work.
- Unchanged install replacement, rollback, uninstall, reinstall, and durable-data preservation behavior is not rerun for this focused UI correction.
- User acceptance remains pending.
