# Archive, Portability, and Security

The durable-data home is `%USERPROFILE%\Documents\iRacing Coach`. It is intended to be the complete portable user-owned repository. Program binaries belong in the installation directory and are reproducible.

## Durable-data contract

| ID | Requirement |
| --- | --- |
| `PORT-001` | Settings, analysis history, user-created reports, tuning history, learned seasonal knowledge, setup-library metadata, retained diagnostics, exports, backup metadata, and other user-owned durable state MUST live under the Documents home. Routine machine logs, crash data, and disposable runtime diagnostics MUST live under `%LOCALAPPDATA%\iRacingCoach` and are not portable history. |
| `PORT-002` | Copying the complete Documents home to another Windows PC MUST be sufficient to migrate user-owned application state, subject to machine-specific paths and credentials being revalidated. |
| `PORT-003` | Program binaries, framework runtimes, build outputs, and installer payloads MUST NOT be required inside the durable-data home. |
| `PORT-004` | The archive manifest MUST identify schema/application versions and MUST permit compatibility checks before restore or upgrade. |
| `PORT-005` | Export and backup operations MUST be atomic enough that interruption does not replace a known-good archive with a partial archive. |
| `PORT-006` | Machine logs and caches MUST be bounded and safe to regenerate or remove. A diagnostic or report explicitly retained by the user becomes durable Documents data and MUST NOT be removed with routine machine logs. |
| `PORT-007` | The application MUST preserve durable data during ordinary update, repair, and uninstall. Destructive data removal requires a separate explicit user choice. |

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

The source repository contains application source, deterministic engine source, contracts, sanitized fixtures, test code, development handoffs, and this documentation. It excludes installed binaries, generated release payloads, local settings, credentials, logs, user archives, raw personal telemetry, and user setups. The two committed `.ibt` fixtures are synthetic/truncated test artifacts and are not personal race recordings.

Implementation references: `SettingsStore.cs`, `Garage61CredentialStore.cs`, `DurableArchive.cs`, `secure_store.py`, `path_security.py`, and `.gitignore`.

## Schema namespaces

The schema numbers in the portable tree are independent contracts and MUST NOT be compared or upgraded as though they were one global counter:

- The top-level `Documents\iRacing Coach\archive-manifest.json` is owned by the C# `DurableArchiveService` and remains durable-archive manifest schema **1**. Its compatibility and migration journal protect the portable folder as a whole.
- Portable application settings remain settings schema **4**.
- The coordinator's portable `ui-analysis-cache` entries use UI cache schema **6**, including exact selector and phase validation.
- The deterministic Python backend uses archive/index/cache schema **2** for its cache manifests and history/index migration. Schema 2 adds durable event-phase fields and concurrent-safe SQLite migration; it does not change the top-level archive manifest to schema 2.

An implementation or support report must name the contract before naming its version. Saying only "archive schema 2" is ambiguous and would incorrectly imply that the top-level portable manifest changed.

## Credential portability decision

External-service credentials are deliberately machine-bound. Copying `Documents\iRacing Coach` restores user-created state and ordinary preferences, but Garage61 and AI require one reconnection on the destination Windows account. This resolves the earlier portability/security tension in favor of preventing portable secrets.
