# iRacing Coach 0.9.1 corrective release

## Release artifacts

- Installer: `artifacts/dist/v0.9.1/iRacingCoach-0.9.1-Setup.exe`
- Portable package: `artifacts/dist/v0.9.1/iRacingCoach-0.9.1-Portable-win-x64.zip`
- Installer SHA-256: `59d09ac1dd5a98795af674495476fd0b7221e84bee5e21ab100e194ca3b0b651`
- Portable SHA-256: `72062d1d403f39b55ead3e04bac11b6e49508a6bc7af989599f8c05a76dfc3d9`

## Corrective work completed

- Optional JSON numbers, strings, arrays, and partially indexed sessions are mapped without treating null as zero or crashing the process.
- Analysis/background failures are contained, redacted to a structured local log, and surfaced as recoverable UI with Retry and copyable support information.
- Race Analysis is a direct, single-click event table. Archived analyses open locally without a network request or raw telemetry dependency.
- The real Kentucky archived report opens successfully despite its null pace, load, tire, pit-service, and strategy fields.
- The telemetry workstation uses an independently scrolling vertical run/lap rail when native-rate lap traces are present. It supports clean-lap filtering, multi-select, fastest-lap defaults, focus mode, synchronized track/chart cursor behavior, and distinct trace colors.
- Secondary race analysis is organized into Overview, Corner Coaching, Runs & Tires, Fuel & Strategy, Damage & Repairs, Setup, and Evidence tabs.
- Race Planning exposes manual car, track/layout, lap-or-minute distance, and fixed/open inputs while refusing to invent unmatched historical guidance.
- Setup Library catalogs both local files and real setup parameters embedded in archived race reports, with search, selected rows, structured parameters, and read-only comparison.
- Progressive Tuning supports multiple feedback cards tied to run phase, corner phase/zone, balance, severity, and confidence, followed by a controlled experiment and rollback record.
- Navigation no longer initiates refreshes or Garage61 requests. Concurrent identical backend calls are coalesced.
- Optional Coach Engine failures no longer produce a global warning banner; only required local-data failures do.
- Installer atomically replaces prior versions and removes legacy install locations while preserving the portable Documents repository.

## Verification

- Solution build: succeeded with 0 warnings and 0 errors.
- Automated tests: 56 passed, 0 failed.
- New crash fixture: `tests/iRacingCoach.Tests/fixtures/analysis-nullable-kentucky-shape.json`.
- Navigation audit: two complete page cycles generated 0 additional backend calls and 0 additional Garage61 requests.
- Native UI audit exercised Home, Race Browser, Kentucky analysis, Planning, populated Setup Library, Progressive Tuning, Connections, and Settings.
- Native Kentucky open: process remained running; archived report rendered 13 laps, one run, fixed setup fingerprint, tire evidence, damage/strategy status, and supported Race Card content.
- Installer test: first install, running-version replacement, simulated rollback, uninstall, reinstall, and second uninstall all exited 0.
- Installer test confirmed prior payload removal, staging cleanup, rollback restoration, durable archive hash preservation, iRacing source hash preservation, and removal of machine-only credential/Coach Engine state.
- Portable ZIP audit confirmed 9,267 entries and the required app, uninstaller, backend, Python, Coach Engine, and schema files.

## Known evidence limitations

- Joshua's original Kentucky `.ibt` is no longer present at the handed-off source path. The app was therefore exercised against the real 961 KB archived `analysis.json`, not regenerated native-rate traces. The archived report truthfully shows that detailed per-lap traces are unavailable; it does not manufacture overlays.
- Qualifying is grouped with its race event, but a dedicated deep qualifying-analysis workspace is still unavailable and is reported as such.
- Current Garage61 use remains limited to approved authentication/status/cache behavior; the app does not claim unavailable official API data.
- A new screenshot matrix and demonstration video were not generated in this round. Native Windows UI inspection was performed directly at the current desktop scale.
