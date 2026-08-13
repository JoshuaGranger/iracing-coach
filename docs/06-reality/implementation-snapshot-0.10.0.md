# Implementation Snapshot: 0.10.0

Status: development candidate. Real telemetry and real-race monitor usefulness still require direct local validation.

## Material changes from 0.9.3

- Live Monitor is a locked, topmost 3×2 grid by default with icon-only top controls, 1–8 row/column settings, 70–200% local scale, a typed searchable catalog, tile drag/drop/reflow/resize, keyboard editing, undo, five display styles, per-tile units/precision/history/accent, and factory/custom named layouts.
- Portable settings schema 4 retains logical monitor layouts. Physical position, display identity, scale, and external credentials remain machine-local. Legacy 0.9.3 monitor settings migrate once; rejected layouts are preserved locally for support.
- Starting Tune is a first-class four-stage flow within Setups and calls `build_open_setup_package`. It creates a coaching/rollback record only; it does not create or modify simulator setup files. Missing qualifying sources are not replaced with race sources.
- Track View and Setup Comparison capability classifications now match their real conditional implementations.
- Race grades expose five stable categories. A category without evidence is `Not graded`, has no numeric score, and is excluded from the overall grade. Each category exposes inputs, calibration, provenance, limitations, and next action.

## Verification at snapshot creation

- Release build: zero warnings and zero errors.
- .NET: 73 passed.
- Python: 173 passed with `unittest` discovery.
- Live Monitor tests cover layout packing, migration, corruption fallback, portability, catalog truthfulness, accessibility hooks, and an interaction performance bound.

Packaged screenshots, artifact hashes, installer lifecycle results, and timing belong in the 0.10.0 release record. Real SDK behavior requires direct validation on the racing PC.
