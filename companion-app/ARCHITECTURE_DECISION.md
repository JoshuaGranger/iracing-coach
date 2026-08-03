# Architecture decision: companion application foundation

Status: current for the 0.6 development line  
Date: 2026-08-02

## Decision

Keep the .NET 10 WPF host with Blazor Hybrid views and the existing Python MCP backend.

- WPF owns the native window, themed title bar, icon, lifecycle, WebView2 check, and Windows integration.
- Razor/CSS owns navigation, forms, event browsing, Race Card presentation, and later telemetry views.
- The coordinator owns portable settings, background jobs, process supervision, cancellation, filtering, and view models.
- The Python backend remains authoritative for discovery, telemetry decoding, evidence, analysis, archives, setup safeguards, and Garage61 calls.
- Both the backend and Coach Engine use redirected stdio. No localhost production service is introduced.
- The released app owns a pinned, signed Codex app-server runtime and exact-version schemas under Program Files. It launches that runtime with a private `CODEX_HOME` under `%LOCALAPPDATA%\iRacingCoach\CoachEngine` and never depends on a global plugin or Codex configuration.

## Data location decision

Joshua's explicit portable-data decision is retained: the Windows Documents known-folder API resolves `Documents\iRacing Coach`; no username, drive, or unredirected-Documents assumption is compiled into the app. This is the user-data home for settings, reports, indexes, learned state, Garage61 caches, AI coaching records, history, exports, and portable setup copies. The installed executable and bundled runtimes remain under Program Files. User-data folders and `settings.json` are ignored by source control and release-source archives.

The portable folder never contains credentials, private Codex state, diagnostic logs, crash data, or physical monitor placement. Those disposable items live under app-owned machine-local roots. The settings schema 3 migration moves monitor identity and pixel geometry to `%LOCALAPPDATA%\iRacingCoach\machine-settings.json` while retaining logical overlay preferences in the portable settings file. The earlier Garage61 migration moves any legacy token from `settings.json` into the Windows user-bound DPAPI store and removes the portable value. ChatGPT authentication is owned entirely by private Codex app-server state. Both account connections must be completed once for each Windows user or PC.

`archive-manifest.json` is the portable archive contract. Schema 1 records a stable archive ID, expected directories, versions, migration history, component inventories and hashes, missing raw-source identities, and the last successful integrity check. `portable-state.json` records whether durable writes are active or whether the archive was prepared for copying. Initialization refuses newer schemas before creating or changing archive content. Migrations are atomic, idempotent, journaled, and retain the prior manifest in `backups`.

The Settings action **Prepare Backup / Migration Copy** acquires the coordinator write boundary, reports active work, checkpoints and integrity-checks SQLite through the packaged Python runtime, hashes every durable component, and marks the entire resolved Coach folder safe to copy. Normal clean shutdown performs the same finalization when no job remains active.

The ordinary uninstaller has an exact allowlist of app-owned Program Files, LocalAppData, RoamingAppData, ProgramData, startup, shortcut, registry, credential, task, service, firewall, crash, and temporary locations. It rejects broad, unresolved, network, device, Documents, profile, drive-root, and iRacing targets. It never offers archive deletion and displays the preserved known-folder path after uninstall.

## Contract strategy

Contract version 1 remains supported. The first milestone consumes existing `iracing_companion_dashboard` and `discover_iracing_sessions` responses additively, tolerates unknown fields, and derives only UI grouping/filter state. It does not parse IBTs or infer unsupported official/hosted/AI labels. New backend schemas will be added only when an existing bounded tool cannot provide the required evidence.

## Consequences

- The proven deterministic engine and its tests are preserved.
- Qualifying and Race recordings can be grouped without moving telemetry logic into C#.
- A self-contained Windows package remains practical.
- Garage61, Codex, and live telemetry can be added behind capability checks without blocking offline race analysis.
