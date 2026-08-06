# iRacing Coach 0.12.0 live telemetry milestone

Date: 2026-08-05

## Current release artifacts

- Source commit: `42bcbde` (`main`)
- Executable: `src/iRacingCoach.App/bin/Release/net10.0-windows10.0.17763.0/iRacing Coach.exe`
- Installer: `artifacts/dist/v0.12.0/iRacingCoach-0.12.0-Setup.exe`
- Installer bytes: `498866216`
- Installer SHA-256: `67cf63be822fd5a879b0db18fb8e43ded000791de65a77ff5234b28a371afc44`
- Portable package: `artifacts/dist/v0.12.0/iRacingCoach-0.12.0-Portable-win-x64.zip`
- Portable bytes: `382874133`
- Portable SHA-256: `4aa8498686e316d4e59305c543e88eeb8a6803e4ef1193aab4790bd660f7b156`
- Payload files: `9272`
- These artifacts were rebuilt after the final August 5 telemetry copy refinement. The focused packaging run did not repeat the unrelated installer lifecycle certification suite.

## Live telemetry milestone

Live telemetry now has a full-height fixed editor drawer and a direct-manipulation dashboard studio. Widgets can be dragged from anywhere on a library row into a targeted grid cell, moved between cells, and resized from every edge or corner. Pointer movement uses a visible snap preview, a 0.56-cell hysteresis threshold, deterministic packing, animated reflow, edge auto-scroll, pointer capture, and serialized commits.

The logical dashboard occupies a fixed footprint. Increasing its row or column count shrinks square cells instead of growing the panel, while the miniature pop-out has a separate physical-scale control. Editable cards use dashed boundaries and expose direct configure and remove actions. Every real metric can switch among number, gauge, bar, and chart presentations; state metrics retain status as an additional form. Boolean and categorical charts use stable numeric encodings and stepped traces, and missing evidence remains missing.

Default is the only protected dashboard. Race, Qualifying, migrated, and newly created dashboards are ordinary portable layouts that can be renamed and deleted, and intentional deletion survives restart. The editor includes 40-level undo/redo, keyboard move/resize/remove commands, reduced-motion behavior, compact-card adaptations, responsive drawer space reservation, and synchronized stale-history protection between the page and native Live Monitor editors. Routine instruction/readiness chrome was removed, and a valid resize preview reports only the proposed grid size.

The telemetry catalog and layout coordinator now enforce semantic numeric ranges, atomic targeted placement, deterministic reflow, robust persisted-layout repair, and safe no-op/reset behavior. Deterministic backend telemetry math remains authoritative and was not reimplemented in the UI.

## Verification

- Release application build: zero warnings and zero errors.
- .NET suite: 83 passed, 0 failed.
- Handoff verifier: passed before implementation, including its deterministic backend contract checks.
- Repository whitespace check: passed.
- Hands-on Windows UI verification: fixed 3 x 3 and 3 x 4 grids remained square inside the same host footprint at wide and compact widths; the temporary grid change was undone.
- Hands-on pop-out verification: separate Grid and Monitor size controls, independent explanatory copy, fixed square-cell workspace, universal catalog styles, Default-only protection, and ordinary custom-layout controls were inspected in the built application.
- All temporary visual-test layout mutations were restored with the application undo path.

## Remaining racing-PC validation

Real-session behavior with the native iRacing SDK, 150%/200% Windows scaling, and multi-monitor placement still require direct validation on the racing PC. These environment-specific checks are not represented as completed by this development-machine release.
