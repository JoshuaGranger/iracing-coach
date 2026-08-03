# Archive, Portability, and Security

The durable-data home is `%USERPROFILE%\Documents\iRacing Coach`. It is intended to be the complete portable user-owned repository. Program binaries belong in the installation directory and are reproducible.

## Durable-data contract

| ID | Requirement |
| --- | --- |
| `PORT-001` | Settings, analysis history, reports, tuning history, setup-library metadata, logs, exports, backups, and other user-owned durable state MUST live under the Documents home. |
| `PORT-002` | Copying the complete Documents home to another Windows PC MUST be sufficient to migrate user-owned application state, subject to machine-specific paths and credentials being revalidated. |
| `PORT-003` | Program binaries, framework runtimes, build outputs, and installer payloads MUST NOT be required inside the durable-data home. |
| `PORT-004` | The archive manifest MUST identify schema/application versions and MUST permit compatibility checks before restore or upgrade. |
| `PORT-005` | Export and backup operations MUST be atomic enough that interruption does not replace a known-good archive with a partial archive. |
| `PORT-006` | Logs and caches MUST be bounded and MUST be safe to regenerate or remove without deleting user-authored data. |
| `PORT-007` | The application MUST preserve durable data during ordinary update, repair, and uninstall. Destructive data removal requires a separate explicit user choice. |

## Settings and credentials

| ID | Requirement |
| --- | --- |
| `SEC-001` | Ordinary settings MUST persist in a portable settings file under the Documents home. |
| `SEC-002` | Secrets MUST NOT be embedded in source control, logs, support details, screenshots, installer command lines, or exported diagnostic bundles. |
| `SEC-003` | A Garage61 credential entered in Settings MUST persist across app use and PC transfer only through an explicitly designed credential representation. |
| `SEC-004` | Credential storage MUST describe its portability/security tradeoff truthfully. If encrypted to one Windows account or machine, the archive MUST say that re-entry is required after transfer. |
| `SEC-005` | Secret reads and writes MUST use least-privilege file permissions reasonably available to the per-user application. |
| `SEC-006` | Support details MUST redact tokens, authorization headers, cookies, private paths when unnecessary, and raw telemetry payloads. |
| `SEC-007` | Raw personal IBT files and user setup files MUST NOT enter the public or private source repository unless explicitly sanitized and approved as fixtures. |
| `SEC-008` | Path handling MUST reject traversal outside an operation's allowed roots even though the desktop process can read normal user-accessible files. |

## Repository boundary

The source repository contains application source, deterministic engine source, contracts, sanitized fixtures, test code, development handoffs, and this documentation. It excludes installed binaries, generated release payloads, local settings, credentials, logs, user archives, raw personal telemetry, and user setups. The two committed `.ibt` fixtures are synthetic/truncated test artifacts and are not personal race recordings.

Implementation references: `SettingsStore.cs`, `Garage61CredentialStore.cs`, `DurableArchive.cs`, `secure_store.py`, `path_security.py`, and `.gitignore`.

## Known design tension

The original product preference asks for a key that follows the portable folder. Strong machine-bound encryption conflicts with that behavior. The implementation and UI must choose and document a concrete model rather than claiming both seamless portability and machine-bound secrecy. This remains a suitable target for agentic security criticism.
