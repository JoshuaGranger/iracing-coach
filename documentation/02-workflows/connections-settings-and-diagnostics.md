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

## Diagnostics

| ID | Requirement |
| --- | --- |
| `DIAG-001` | Diagnostics MUST be available within Settings and SHOULD be visible by default under a strong divider. |
| `DIAG-002` | The section SHOULD show app/backend/runtime versions, contract compatibility, root validation, process health, service readiness, cache/archive state, channel coverage, and recent stage timings. |
| `DIAG-003` | The last error MUST include a useful recovery action and a copyable redacted support reference. |
| `DIAG-004` | Diagnostics MAY expose Health Test, Open Logs, Verify Installation, and Prepare Backup/Migration actions when each action works. |
| `DIAG-005` | Request/cache counters MUST be truthful and reset only through an explicit diagnostics action. |
| `DIAG-006` | Diagnostics MUST NOT dominate Home or appear as normal top-bar activity chatter. |

## Backup preparation

Preparing a copy must checkpoint relevant databases, reject active durable jobs, recompute the portable manifest/integrity hash, and mark the archive safe to copy. Ordinary app activity marks it active again. Credentials and private Coach Engine state are never included.

## Current implementation note

Version 0.11.0 embeds Garage61 and ChatGPT connection controls directly in Settings. Connections is no longer a primary navigation item.
