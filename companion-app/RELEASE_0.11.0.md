# iRacing Coach 0.11.0 development candidate

Date: 2026-08-03

## Release artifacts

- Installer: `artifacts/dist/v0.11.0/iRacingCoach-0.11.0-Setup.exe`
- Installer bytes: `498788392`
- Installer SHA-256: `4a9a8898bf3e1b00e039ca8fc2b199e262c44b89e1e554aeab5a9a0eebc1b6f8`
- Portable package: `artifacts/dist/v0.11.0/iRacingCoach-0.11.0-Portable-win-x64.zip`
- Portable package bytes: `382793547`
- Portable package SHA-256: `8336e69baba44573dcdd7702714e75e6f6141814c6f12c699471edf95dca4056`
- Source revision: `b259c75dce30d107ca6fb81dd7dee51165de9368`
- Installer file version: `0.11.0.0`
- Installer product version: `0.11.0+b259c75dce30d107ca6fb81dd7dee51165de9368`

Both SHA-256 sidecars were regenerated from the final artifacts and independently matched. The artifacts are not Authenticode-signed; their published SHA-256 hashes are the integrity mechanism for this development candidate.

## Completed work

- Rebuilt the full Live Telemetry page around the same layout model, grid behavior, telemetry catalog, live readings, and durable custom layouts as the miniature Live Monitor. A collapsible right-side Toolbox manages layouts, rows, columns, tiles, positions, spans, display styles, units, and precision.
- Kept track position and recent driving traces available as a subordinate expandable view while making the configurable race dashboard the primary live surface.
- Reoriented Race Analysis around recorded telemetry. Selecting a race now opens the event and starts loading its Race session immediately; a gentle telemetry-panel skeleton represents the true loading state without blocking the rest of the page.
- Compressed event identity, grading, and race focus into small contextual surfaces. Removed generic invariant coaching prose from the primary view and placed provenance and limitations in subordinate details.
- Polished the lap list with green/yellow flag cues, fastest-lap treatment, and delta-to-best values. Moved the map color selector beside the map, identified the displayed lap, removed the misplaced wear disclaimer, and corrected chart pointer alignment against rendered bounds.
- Removed fabricated track-zone fallbacks. Progressive Tuning automatically opens the selected real recorded event, exposes its measured track map, and accepts feedback against only recorded zones.
- Removed the Setup Library product surface. Setups now presents a concise Event, Source, Checks, and Run Starting Tune workflow, while internal indexing remains available to source exact packages.
- Moved Connections into Settings, removed it from primary navigation, reduced Race Planning to measured and changing outputs, and tightened human-facing terminology throughout the reviewed surfaces.
- Updated the specification tree, traceability, capability matrix, quality gates, and implementation-reality documentation so agentic review can compare the English contract with the implemented 0.11 behavior.

## Automated verification

| Check | Result |
| --- | --- |
| Release solution build | 0 warnings, 0 errors |
| .NET regression suite | 74 passed, 0 failed, 0 skipped |
| Python backend suite | 173 passed, 0 failed |
| Handoff/contract verifier | 109 files, 16 tools, 17 contracts, 14 fixtures; passed |
| MCP end-to-end smoke test | Passed; approximately 50.5 ms in the full handoff run |
| Portable payload | 9,266 files; required app, backend, Python, Codex runtime, manifests, and schemas present |
| Final checksum comparison | Installer and portable package matched their sidecars |

## Packaged UI and interaction verification

The exact release payload was launched against the isolated sanitized final-product fixture. Native Windows interaction and capture verified the full Live Telemetry grid, Toolbox layout, Toolbox collapse and reopen behavior, full-width grid reflow, primary navigation, recorded-race list, and direct Kentucky race opening. The selected race opened directly into its recorded laps, track-position map, and aligned traces without requiring a separate Race or Qualifying selection.

The broader source-matched walkthrough also exercised custom layout creation, duplication, reset, deletion, rename, row and column changes, tile selection, tile movement and resizing, display style, unit and precision controls, catalog search and add/remove, and synchronization with the miniature Live Monitor. Analysis chart pointer alignment was checked against the visible crosshair. Progressive Tuning was exercised through recorded-zone selection, balance feedback, save, recommendation, and rollback guidance. Planning, Setups, Settings with embedded Connections, Home, and responsive dense layouts were visually reviewed before packaging.

## Installer, upgrade, and uninstall verification

The guarded lifecycle test used the exact installer checksum above and passed first install, stopping a running prior version, simulated replacement failure and rollback, successful replacement, old-marker removal, staging cleanup, uninstall, reinstall, and second uninstall. The durable iRacing Coach archive hash and the source iRacing directory hash remained unchanged. Six app-owned machine roots were removed, while the durable Documents archive remained intact. Seeded machine-only credential and private Codex fixtures were removed on clean uninstall.

## Privacy verification

The Garage61 key on the development machine was loaded only for an in-memory exact-match scan; its value was never printed or copied into the repository or packages. A scan of 9,940 tracked and packaged files, including both final release binaries, found zero exact secret matches. The release payload contains no settings, credential, authentication, Garage61 key, or raw telemetry files. The only tracked `.ibt` files are the documented synthetic and deliberately truncated parser fixtures.

## Acceptance boundary and known limitations

- HOME_QA still owns acceptance with real iRacing telemetry, a real race session, real setup files, supported multi-monitor/DPI combinations, and sustained use. This candidate does not claim that verdict.
- Garage61 authorization and personal ChatGPT/Codex account behavior were not exercised in isolated fixture mode.
- Live custom-layout persistence and miniature-monitor synchronization are fixture- and contract-verified; their usefulness during an actual race remains a HOME_QA decision.
- Starting Tune and per-corner tuning are source- and contract-verified, but real-car, real-track, and real-driver usefulness remains part of HOME_QA.
- Grade and recommendation calibration against a broad set of real sessions remains an open quality item even though missing evidence, provenance, and limitations are represented truthfully.
- The artifacts are checksum-published but not Authenticode-signed, so Windows may show an unknown-publisher warning.
