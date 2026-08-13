# Archive, Portability, and Security

The durable-data home is `%USERPROFILE%\Documents\iRacing Coach`. It is intended to be the complete portable user-owned repository. Program binaries belong in the installation directory and are reproducible.

## Durable-data contract

| ID | Requirement |
| --- | --- |
| `PORT-001` | Settings, analysis history, permanently retained raw IBTs, track-configuration geometry, Race Analysis layouts, tire-learning observations/models, approved external-reference summaries, user-created reports, tuning history, learned seasonal knowledge, setup-library metadata, retained diagnostics, exports, backup metadata, and other user-owned durable state MUST live under the Documents home. Routine machine logs, crash data, and disposable runtime diagnostics MUST live under `%LOCALAPPDATA%\iRacingCoach` and are not portable history. |
| `PORT-002` | Copying the complete Documents home to another Windows PC MUST be sufficient to migrate user-owned application state, subject to machine-specific paths and credentials being revalidated. |
| `PORT-003` | Program binaries, framework runtimes, build outputs, and installer payloads MUST NOT be required inside the durable-data home. |
| `PORT-004` | The archive manifest MUST identify schema/application versions and MUST permit compatibility checks before restore or upgrade. |
| `PORT-005` | Export and backup operations MUST be atomic enough that interruption does not replace a known-good archive with a partial archive. |
| `PORT-006` | Machine logs and caches MUST be bounded and safe to regenerate or remove. A diagnostic or report explicitly retained by the user becomes durable Documents data and MUST NOT be removed with routine machine logs. |
| `PORT-007` | The application MUST preserve durable data during ordinary update, repair, and uninstall. Destructive data removal requires a separate explicit user choice. |
| `PORT-008` | Finalized raw IBTs MUST be stored as verified content-addressed copies under `data/telemetry-traces/raw/<sha256>/` and referenced by durable event identity. The application MUST deduplicate identical bytes, MUST NOT rely on a hard link or the continued existence of the iRacing source path, and MUST NOT automatically age, prune, recompress, or delete these files. |
| `PORT-009` | Raw telemetry retention MUST expose storage use and copy failures without turning routine success into persistent status noise. Backup/migration MUST either include every referenced raw object or report an explicit incomplete backup before replacing a known-good backup. Support bundles and source control MUST continue to exclude raw IBTs. |
| `PORT-010` | Track geometry, analysis layouts, tire observations/models, predictions, and Garage61 reference summaries MUST be atomic, schema/version identified, and portable. Machine-local placement or rendering caches MAY be regenerated, but deleting them MUST NOT erase the durable source record needed to reproduce an accepted analysis. |

## Settings and credentials

| ID | Requirement |
| --- | --- |
| `SEC-001` | Ordinary settings MUST persist in a portable settings file under the Documents home. |
| `SEC-002` | Secrets MUST NOT be embedded in source control, logs, support details, screenshots, installer command lines, or exported diagnostic bundles. |
| `SEC-003` | A Garage61 credential entered in Connections MUST persist during normal use for the same Windows user and machine in protected machine storage. It MUST NOT be written to the portable Documents archive or silently transfer to another PC. |
| `SEC-004` | Migration MUST restore nonsecret Garage61 and AI connection preferences while clearly requesting reconnection. Legacy portable secrets MUST be moved to protected machine storage when possible, removed from portable files, and redacted from logs, support data, exports, and migration records. |
| `SEC-005` | Secret reads and writes MUST use least-privilege file permissions reasonably available to the per-user application. |
| `SEC-006` | Support details MUST redact tokens, authorization headers, cookies, private paths when unnecessary, and raw telemetry payloads. |
| `SEC-007` | Raw personal IBT files and user setup files MUST NOT enter the public or private source repository unless explicitly sanitized and approved as fixtures. |
| `SEC-008` | Path handling MUST reject traversal outside an operation's allowed roots even though the desktop process can read normal user-accessible files. |

## Repository boundary

The source repository contains application source, deterministic engine source, contracts, sanitized fixtures, test code, and this documentation. It excludes installed binaries, generated release payloads, local settings, credentials, logs, user archives, raw personal telemetry, and user setups. The two committed `.ibt` fixtures are synthetic/truncated test artifacts and are not personal race recordings.

Implementation references: `SettingsStore.cs`, `Garage61CredentialStore.cs`, `DurableArchive.cs`, `secure_store.py`, `path_security.py`, and `.gitignore`.

## Schema namespaces

The schema numbers in the portable tree are independent contracts and MUST NOT be compared or upgraded as though they were one global counter:

- The top-level `Documents\iRacing Coach\archive-manifest.json` is owned by the C# `DurableArchiveService` and remains durable-archive manifest schema **1**. Its compatibility and migration journal protect the portable folder as a whole.
- Portable application settings remain settings schema **4**.
- The coordinator's portable `ui-analysis-cache` entries use UI cache schema **10**. Schema 7 introduced exact selector/phase validation and invalidated responses produced before tire temperatures respected each recorded channel's source unit; later schema bumps through 10 invalidate older mapped responses that cannot carry the exact-configuration map, replay, Technical data, tire-learning, authoritative geometry hash, and tuning identity fields now consumed by the app.
- The deterministic Python backend uses archive/index/cache schema **2** for its cache manifests and history/index migration. Schema 2 adds durable event-phase fields and concurrent-safe SQLite migration; it does not change the top-level archive manifest to schema 2.
- Each content-addressed raw-IBT manifest uses raw-retention schema **1**. Exact-configuration track geometry, Race replay, tire learning, and Garage61 representative-lap records each use their own named schema **1**; sharing the number does not make them interchangeable contracts.

An implementation or support report must name the contract before naming its version. Saying only "archive schema 2" is ambiguous and would incorrectly imply that the top-level portable manifest changed.

## Credential portability decision

External-service credentials are deliberately machine-bound. Copying `Documents\iRacing Coach` restores user-created state and ordinary preferences, but Garage61 and AI require one reconnection on the destination Windows account. This resolves the earlier portability/security tension in favor of preventing portable secrets.
