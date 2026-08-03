# iRacing Coach 0.9.3 release candidate

Date: 2026-08-03

## Release artifacts

- Installer: `artifacts/dist/v0.9.3/iRacingCoach-0.9.3-Setup.exe`
- Installer bytes: `498726950`
- Installer SHA-256: `990b836b935221d405bf1132bef5e246962902570895ec9f4ae2cb26a8b82b7a`
- Portable package: `artifacts/dist/v0.9.3/iRacingCoach-0.9.3-Portable-win-x64.zip`
- Portable package bytes: `382734680`
- Portable package SHA-256: `9c896c150505b4bd6394e9ac5d8dfc3ad38e09574e4b56889b0c83042a0b0452`
- Source revision: unversioned local workspace snapshot
- Source fingerprint: `fa95ecf910fc132775bb218ecf0bc57970a9e6fc141b0f665f0e0a7abc7b53d1` over 160 sorted files in `src`, `tests`, `tools`, and the solution-level build configuration

Both checksum sidecars were regenerated from the final artifacts and independently matched. The installer reports file version `0.9.3.0` and product version `0.9.3`.

## Completed work

- Added a deterministic, isolated QA fixture/replay mode with its own temporary settings, credentials, archive, diagnostics, and recovery state. It cannot call Garage61 or AI services.
- Reworked the home page around useful personal information, automatic refresh, warning-only status, and human-readable wording.
- Rebuilt race review around one-click archived sessions and the exact Overview, Telemetry, Corner Coaching, Runs & Tires, Fuel & Strategy, Damage & Repairs, and Setup & Evidence workspaces.
- Added synchronized multi-lap telemetry with recorded timestamps, shared per-channel scales, real time delta, real slip angle, and recorded yaw/lateral/longitudinal dynamics. Missing evidence remains visibly unavailable rather than synthesized.
- Corrected lap-zero exclusion, pit-span presentation, setup/evidence grouping, and race/strategy language.
- Corrected race planning to use the requested lap or time distance. The verified 50-lap fixture produces one stop with an equal-stint target at lap 25.
- Kept car, track, setup, and reference choices aligned with real indexed content; no seeded production options were added.
- Reworked Progressive Tuning so multiple feedback cards are retained and all contribute to the generated controlled experiment. The verified scenario combines two distinct track-map/corner observations.
- Removed redundant refresh/status UI, the UI gallery, account-oriented wording, and unsupported controls. Settings and Connections report fixture/offline state truthfully.
- Preserved the portable Documents archive while keeping machine-only credentials and private runtime state outside transfer packages.
- Bumped the app, installer, uninstaller, contracts, Coach Engine client, cached setup name, and release tooling consistently to 0.9.3.

## Verification

- Release solution build: 0 warnings, 0 errors.
- Automated regression suite: 63 passed, 0 failed, 0 skipped.
- Deterministic Python backend suite: 173 passed, 0 failed.
- Authoritative final-product packet: all 8 validation groups passed.
- Final portable payload: 9,290 entries; app, uninstaller, deterministic backend, Python, signed Codex runtime, Coach Engine manifest, and schemas present.
- Privacy scan: 0 raw `.ibt`, `auth.json`, Garage61 key, or PAT files in the portable package or release directory.
- Native UI walkthrough: every primary page, analysis tab, form, dropdown, and major action exercised against isolated real-derived fixtures.
- Responsive UI checks included 960 x 640 Progressive Tuning and 900 x 640 Race Planning; no horizontal overflow or control overlap remained.
- Fixture navigation stayed local: Garage61 requests 0 and AI requests 0.

## Installer and uninstall acceptance

The final installer checksum was verified before execution. The guarded lifecycle test passed first install, stopping a running prior app, simulated replacement failure and rollback, successful replacement, old-marker removal, staging cleanup, uninstall, reinstall, and second uninstall.

The same test proved that the durable iRacing Coach archive hash and the source iRacing directory hash were unchanged. Six app-owned machine roots were removed, while the portable Documents archive remained intact. Seeded machine-only Garage61 and private Codex fixtures were removed on clean uninstall.

## Evidence boundary

The screenshot and walkthrough evidence is in `artifacts/qa/v0.9.3`. It uses the packet's isolated sanitized fixtures, not presentation-only mockups. HOME_QA still owns real live-telemetry acceptance; this release does not claim that verdict. Garage61 authorization and AI account behavior were intentionally not exercised in fixture mode, and no external requests were made.
