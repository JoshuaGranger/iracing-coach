# iRacing Coach 0.6.0 — portable archive and clean uninstall acceptance

Date: 2026-08-02  
Archive schema: 1  
Portable settings schema: 3

## Implemented contract

- Resolves Documents with `SHGetKnownFolderPath(FOLDERID_Documents)` and derives `iRacing Coach` and `iRacing` from that result.
- Creates the complete durable component layout without moving, deleting, or invalidating existing backend paths.
- Retains the backend's established `data\reports`, `data\season-cache`, `data\tuning`, and `data\history.sqlite3` paths while indexing them in the portable manifest.
- Writes `archive-manifest.json` and `portable-state.json` atomically. The manifest contains versions, a stable archive ID, expected directories, migrations, component counts/bytes/hashes, unresolved IBT identities, and integrity time.
- Stops without archive mutation when a manifest schema is newer than the app supports.
- Uses a recoverable prior-manifest backup and persistent migration journal for material schema migration. Retrying an interrupted migration is safe.
- Keeps Garage61 downloads, setup copies, analysis, strategies, tuning experiments, redacted activity records, structured AI answers, questions, evidence, and thread mapping beneath the durable root.
- Loads portable AI coaching records independently of Codex thread availability and shows them in Review a race while offline.
- Detects archived reports whose original IBT is absent, keeps those reports valid, and offers a native file picker that records a SHA-256 stable source identity plus relocatable filename mapping.
- Keeps Garage61 and ChatGPT credentials machine-bound. No token or private Codex state is included in portable component hashes or backups.
- Keeps diagnostic logs and exact monitor identity/pixel geometry under `%LOCALAPPDATA%\iRacingCoach`; portable logical overlay choices remain in `settings.json`.
- Prepare Backup / Migration Copy blocks on active jobs/AI, checkpoints and integrity-checks SQLite, hashes durable components, reports the exact resolved path, and marks the folder safe to copy.
- Clean shutdown finalizes the archive when no durable work remains active.
- The installer replaces a running prior version through staged install/rollback and never writes the durable root.
- The uninstaller removes exact app-owned machine roots and Windows integration, preserves both the Coach archive and source iRacing folders, and has no delete-data option.

## Automated coverage

The .NET suite covers empty real-data behavior, settings/credential migration, portable preferences, machine-only display placement, known-folder resolution, archive creation, path relocation, content continuity, Garage61 offline-cache continuity, tire/driver model continuity, AI record continuity, missing raw telemetry, non-destructive migration, interrupted migration recovery, idempotence, and newer-schema refusal.

The packaged installer acceptance performs checksum validation, a first install, launch, replacement while the old app is running, stale-version removal, required-runtime validation, guarded clean-uninstall simulation, pre/post SHA-256 checks of the durable archive and iRacing source, removal of every seeded app-owned external root, uninstall of the installed payload, reinstall, and a second clean uninstall.

## Safety boundaries

Archive deletion is intentionally not part of Windows uninstall. Destructive cleanup canonicalizes each target, requires an exact app-owned leaf beneath an exact permitted parent, rejects unresolved/UNC/device paths, and rejects the drive root, user profile, Documents, `Documents\iRacing`, and the resolved durable archive.

The clean-PC release remains self-contained: the installer owns .NET, Python, the deterministic backend, Coach Engine, signed Codex app-server, and schemas. Restoring useful data never requires a separate plugin, global Codex configuration, Node, Python, or a development SDK.

## Final release evidence

- Installer: `artifacts\dist\iRacingCoach-0.6.0-Setup.exe`
- Installer size: 498,583,590 bytes
- Installer SHA-256: `7ebba8dbcba36529ec9e39ac6355ec6462af6ad59b44dcbd81ab223f133adf62`
- .NET tests: 36 passed, 0 failed.
- Python backend tests: 173 passed, 0 failed.
- App, installer, and uninstaller builds: 0 warnings, 0 errors.
- Handoff verification: 109 files and 17 contracts verified; sanitized MCP end-to-end analysis passed with high data quality.
- Packaged Coach Engine probe: bundled runtime installed and running (`codex-cli 0.146.0-alpha.9.2`); isolated account state truthfully reported not connected.
- Packaged lifecycle acceptance: checksum, first install, running-app replacement, simulated rollback, stale-payload removal, durable-data continuity, exact machine-state cleanup, uninstall, reinstall, second uninstall, and detached-uninstaller cleanup all passed.
- Native WPF QA covered every primary page and the non-destructive interactive controls. The final build render reconfirmed the dark native title bar, warning-only header status, humanized home copy, and automatic-refresh wording. Credential disconnect and external-account authorization were intentionally excluded from destructive/transmitting UI actions.

The lifecycle harness uses guarded Windows temporary namespaces. It SHA-256 checks the seeded Coach archive and iRacing source before and after cleanup, and refuses cleanup outside its named fixture roots. The final uninstaller stages itself outside the install folder, removes the validated installation, deletes its detached runner promptly, and falls back to reboot-deferred self-removal only if the helper cannot start.
