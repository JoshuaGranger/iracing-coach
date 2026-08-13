# Connections, Settings, and Diagnostics

## Connections

Connections manages optional services, not application users.

| ID | Requirement |
| --- | --- |
| `CON-001` | The page MUST treat Garage61 and AI coaching as independent optional services. |
| `CON-002` | It MUST show actionable configured/connected/offline/unauthorized/rate-limited states without exposing credentials. |
| `CON-003` | Connecting Garage61 MUST send the token only through the machine-bound credential helper's standard input. |
| `CON-004` | The token MUST NOT appear in UI replacement fields, settings JSON, arguments, environment variables, logs, support bundles, AI context, or the repository. |
| `CON-005` | ChatGPT authentication MUST remain owned by the private Coach Engine/Codex managed flow. |
| `CON-006` | Fixture mode MUST report services truthfully offline and MUST make zero production requests. |
| `CON-007` | No application account, profile, sign-out, or workspace switcher may be introduced. |
| `CON-008` | Connections MUST be a section inside Settings, not a primary navigation destination. Legacy internal navigation requests for Connections MUST land on Settings. |

## Portable settings

| ID | Requirement |
| --- | --- |
| `SET-001` | Roaming user preferences MUST persist in `Documents\iRacing Coach\settings.json`. |
| `SET-002` | Settings MUST resolve Windows Known Documents instead of hard-coding a user profile. |
| `SET-003` | The configured iRacing source and install roots MUST be local, validated, and read-only. UNC/device roots are rejected. |
| `SET-004` | Setup copies MUST use `Documents\iRacing Coach\setups`. |
| `SET-005` | Physical monitor geometry, logs, and other machine-only state MUST remain under `%LOCALAPPDATA%\iRacingCoach`. |
| `SET-006` | A legacy credential accidentally found in portable settings MUST migrate to protected machine storage and be removed from the portable file. |
| `SET-007` | Saving settings MUST be atomic enough to avoid a partial JSON file after interruption. |
| `SET-008` | The primary Settings surface MUST be compact and task-oriented. Data, backup/migration, app behavior, and telemetry-popout preferences SHOULD be the first visible cards; full paths, service credentials, and health detail SHOULD remain in labeled disclosures until requested. |
| `SET-009` | Connections MUST remain discoverable as one subordinate Settings section, but its Garage61 and optional coaching controls MUST NOT add their full visual burden to the default Settings view. |
| `SET-010` | App behavior MUST offer a compact, keyboard-accessible primary theme-color choice. The selected palette MUST preview immediately, persist in portable `settings.json` only when Settings is saved, restore on the next launch, and apply to both the main shell and native telemetry popout. Invalid or removed palette identifiers MUST repair to the documented default. Theme choice MUST alter primary accent/focus hierarchy without replacing semantic telemetry, success, warning, incident, or danger colors. |

## Diagnostics

| ID | Requirement |
| --- | --- |
| `DIAG-001` | Troubleshooting MUST be discoverable within Settings under a clear divider. Its heading/control SHOULD remain visible, while detailed diagnostics MAY stay collapsed until the user asks for them or an actionable failure requires attention. |
| `DIAG-002` | The section SHOULD show app/backend/runtime versions, contract compatibility, root validation, process health, service readiness, cache/archive state, channel coverage, and recent stage timings. |
| `DIAG-003` | The last error MUST include a useful recovery action and a copyable redacted support reference. |
| `DIAG-004` | Diagnostics MAY expose Health Test, Open Logs, Verify Installation, and Prepare Backup/Migration actions when each action works. |
| `DIAG-005` | Request/cache counters MUST be truthful and reset only through an explicit diagnostics action. |
| `DIAG-006` | Diagnostics MUST NOT dominate Home or appear as normal top-bar activity chatter. |

## Backup preparation

Preparing a copy must checkpoint relevant databases, reject active durable jobs, recompute the portable manifest/integrity hash, and mark the archive safe to copy. Ordinary app activity marks it active again. Credentials and private Coach Engine state are never included.

## Current implementation note

Version 0.14.0 source presents four compact primary cards for Data, Backup or move PCs, App behavior, and Telemetry popout. Folder locations, Connections, and detailed Troubleshooting are disclosures; the portable-preferences save bar remains directly available. Garage61 and ChatGPT connection controls are still embedded and functional inside Settings rather than removed or moved back into primary navigation.

This compact hierarchy is verified by current source inspection. Integrated visual, keyboard, screen-reader, Windows scaling, external-authentication, and migration cases retain their separate acceptance gates.
