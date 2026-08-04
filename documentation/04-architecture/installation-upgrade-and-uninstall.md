# Installation, Upgrade, and Uninstall

The product uses a normal per-user Windows installation while keeping user-owned durable state under Documents.

## Installation

| ID | Requirement |
| --- | --- |
| `INST-001` | The installer MUST install the app, bundled runtime, deterministic engine, icons, shortcuts, and uninstall registration without requiring administrator rights for the supported per-user path. |
| `INST-002` | Installation MUST use a staging/rollback strategy so an interrupted or failed install does not leave a partially active version. |
| `INST-003` | The installed app MUST launch from Start Menu and standard shortcuts without a console window or repository dependency. |
| `INST-004` | The installed executable, shortcuts, and Add/Remove Programs entry MUST use the product logo and consistent name/version/publisher metadata. |
| `INST-005` | Installer payload integrity MUST be checked before activation. |

## Upgrade and prior-version removal

| ID | Requirement |
| --- | --- |
| `UPG-001` | Installing a new version MUST detect and remove or replace prior program files so obsolete binaries cannot remain active. |
| `UPG-002` | Upgrade MUST preserve the Documents data home and migrate its schemas transactionally when required. |
| `UPG-003` | Upgrade MUST stop running managed processes or provide a clear close-and-retry path. |
| `UPG-004` | A failed upgrade MUST restore the last usable program version or clearly report that recovery is required. |
| `UPG-005` | Version comparison MUST prevent an accidental downgrade unless the user explicitly selects a supported downgrade path. |

## Uninstall

| ID | Requirement |
| --- | --- |
| `UN-001` | Ordinary uninstall MUST remove installed binaries, shortcuts, protocol/task registrations owned by the app, uninstall metadata, machine credentials, routine logs, crash data, and disposable private runtime state. |
| `UN-002` | Ordinary uninstall MUST preserve the Documents data home, including user-retained reports/diagnostics, analysis and tuning history, learned knowledge, exports, backup metadata, and ordinary preferences. |
| `UN-003` | Removing durable user data MUST be a separate, explicit, high-friction choice that enumerates the exact target. |
| `UN-004` | Uninstall MUST NOT remove shared runtimes, unrelated files, or user data outside the resolved product paths. |
| `UN-005` | Reinstall after ordinary uninstall MUST rediscover and reuse the preserved Documents home. |

Migration to another PC restores the Documents data home and validates machine-specific paths. Garage61 and AI connection preferences may be restored, but credentials and private authentication state are never transferred; Connections must request reconnection once on the destination Windows account.

## Current evidence

The 0.9.3 release process includes installer build, upgrade/rollback tests, uninstall preservation checks, and an immutable release packet. This is development evidence, not a substitute for representative installed-PC acceptance across supported Windows configurations.

Implementation references: `companion-app/src/iRacingCoach.Installer/Program.cs`, `iRacingCoach.Uninstaller/Program.cs`, `tools/BuildRelease.ps1`, and `tools/TestInstallerUpgrade.ps1`.
