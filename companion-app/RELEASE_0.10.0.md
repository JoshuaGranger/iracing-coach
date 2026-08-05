# iRacing Coach 0.10.0 development candidate

Date: 2026-08-03

## Release artifacts

- Installer: `artifacts/dist/v0.10.0/iRacingCoach-0.10.0-Setup.exe`
- Installer bytes: `498784296`
- Installer SHA-256: `04472b3cee31cef1299f4e42e84a36275ca653cdce08e4425736de3313d408a6`
- Portable package: `artifacts/dist/v0.10.0/iRacingCoach-0.10.0-Portable-win-x64.zip`
- Portable package bytes: `382790899`
- Portable package SHA-256: `778686903fb5f0bae5fde63bd20bea674fca17985d2cc369aeae1a811529496e`
- Source revision: `d6a56146e544b5ea3e2108c8c67adf9d4dc5cc74`
- Installer file version: `0.10.0.0`
- Installer product version: `0.10.0+d6a56146e544b5ea3e2108c8c67adf9d4dc5cc74`

Both SHA-256 sidecars were regenerated from the final artifacts and independently matched. The artifacts are not Authenticode-signed; their published SHA-256 hashes are the integrity mechanism for this development candidate.

## Completed work

- Replaced the old Live Monitor with a separate topmost window, a locked 3 x 2 default layout, a one-to-eight-tile editor, keyboard and drag reordering, reversible grid and scale settings, monitor-aware placement recovery, and dark native window chrome.
- Added recorded, calculated, and coach metric types with truthful source labels, unavailable states, number, gauge, bar, trend, and status presentations, per-tile formatting, and an alphabetized metric catalog.
- Added Default, Race, Qualifying, and user-named layout management. Logical layouts travel in the durable Documents archive; display placement and scale remain machine-local.
- Migrated legacy Live Monitor preferences into schema version 4 and preserved malformed legacy files for diagnosis while restoring a safe default.
- Added fuel amount and fuel percentage to the SDK snapshot contract and expanded the shared charcoal theme tokens across the monitor editor, selectors, tooltips, and native title surfaces.
- Added the Starting Tune workflow to Setups. It uses an exact season, car, track, and setup package, exposes source and fingerprint evidence, separates race and qualifying paths, and never fabricates or rewrites an iRacing setup file.
- Stabilized review grades at five categories. Missing evidence is shown as `Not graded`, excluded from the overall result, and accompanied by input, calibration, provenance, and limitation details.
- Reconciled the documentation tree, capability matrix, traceability, portability, security, and quality gates with the 0.10 implementation.

## Automated verification

| Check | Result |
| --- | --- |
| Release solution build | 0 warnings, 0 errors |
| .NET regression suite | 73 passed, 0 failed, 0 skipped |
| Python backend suite | 173 passed, 0 failed |
| Handoff/contract verifier | 109 files, 16 tools, 17 contracts, 14 fixtures; passed |
| MCP end-to-end smoke test | Passed; approximately 54.6 ms |
| Portable payload | 9,266 files; required app, backend, Python, Codex runtime, manifests, and schemas present |
| Final checksum comparison | Installer and portable package matched their sidecars |

## Packaged UI and runtime verification

The exact release payload was launched against the isolated sanitized fixture and replay inputs. The main window and Live Monitor were reviewed through native Windows capture. Primary navigation, Setups, the Live page, monitor launch, layout selection, unlock/edit mode, metric search and catalog scrolling, layout management, rows, columns, scale, cancel, done, lock, and close behavior were exercised. The final monitor showed live recorded and calculated values in the 3 x 2 grid with no white system-themed selector, tooltip, or title-bar surface.

Visual corrections found during the walkthrough were fixed before the final artifacts were produced: dropdowns and popup lists now inherit the dark theme, long tile names wrap instead of clipping, the navigation label is `Setups`, and monitor tooltips use the same charcoal theme. The final payload remained responsive during a 15-second warm replay sample at approximately 7.3% of one CPU core, 232.7 MB working set, and 161.9 MB private memory. These are local fixture measurements, not a hardware guarantee.

## Installer, upgrade, and uninstall verification

The guarded lifecycle test used the exact installer checksum above and passed first install, stopping a running prior version, simulated replacement failure and rollback, successful replacement, old-marker removal, staging cleanup, uninstall, reinstall, and second uninstall. The durable iRacing Coach archive hash and the source iRacing directory hash remained unchanged. Six app-owned machine roots were removed, while the durable Documents archive remained intact. Seeded machine-only credential and private Codex fixtures were removed on clean uninstall.

## Privacy verification

The Garage61 key on the development machine was loaded only for exact-match scanning; its value was never printed or copied into the repository or packages. Exact secret matches in the source tree and release payload were zero. Staged-source inspection found no credential, authentication, local settings, or real/private telemetry files. The two tracked `.ibt` files are documented synthetic parser fixtures only.

## Acceptance boundary and known limitations

- Acceptance with real iRacing telemetry, a real race session, supported multi-monitor/DPI combinations, and sustained use still requires direct local validation. This candidate does not claim that verdict.
- Garage61 authorization and personal ChatGPT/Codex account behavior were not exercised in isolated fixture mode.
- Starting Tune is source- and contract-verified, but exact real-car and real-track usefulness remains a direct validation target.
- Grade calibration against a broad set of real sessions remains an open quality item even though missing evidence and provenance are now represented correctly.
- The artifacts are checksum-published but not Authenticode-signed, so Windows may show an unknown-publisher warning.
