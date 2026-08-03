# iRacing Coach 0.5.0 — Coach Engine release acceptance

Date: 2026-08-02  
App version: 0.5.0  
Deterministic backend: `iracing-coach-local` 0.3.0, MCP 2025-06-18, 16 tools  
Coach Engine runtime: signed OpenAI Codex 0.146.0-alpha.9.2

## Release outcome

The companion app now owns its complete runtime. A normal installation contains the WPF/Blazor application, .NET runtime, portable Python runtime, deterministic backend, bundled skills and resources, exact Codex app-server schemas, a pinned OpenAI-signed Codex runtime, the uninstaller, repair source, and component manifests.

The app starts and supervises Coach Engine itself. It uses JSONL over redirected standard input/output, performs the required initialize/initialized handshake, checks the private account state, restarts failed child processes within a bounded policy, and shuts them down with the app. No global Codex installation, plugin, Python, Node, console, localhost service, or manual configuration is required.

## Connections and privacy

- ChatGPT authentication is handled by the private Codex app-server state under `%LOCALAPPDATA%\iRacingCoach\CoachEngine`. The companion app starts the managed browser or verification-code flow and never receives a ChatGPT password or token.
- Garage61 is entered from Connections. Windows protects it for the current Windows user with DPAPI at `%LOCALAPPDATA%\iRacingCoach\credentials\garage61.pat.dpapi`.
- `Documents\iRacing Coach\settings.json` contains portable preferences only. The legacy Garage61 property is explicitly migrated and removed; the model cannot serialize it again.
- Connections are intentionally not copied to another PC. Deterministic local race analysis, planning, setups, tuning, live telemetry, and cached data continue to work while optional services are offline.

## First run, repair, and update behavior

First run checks iRacing data, offers ChatGPT and Garage61 connection, verifies local components, and opens the app. Existing protected connections are recognized without asking for the value again.

The installer validates every required component and the exact signed Coach Engine runtime before replacement. It stages the new payload, stops a running prior version, replaces the complete application directory, removes prior-version residue, registers repair/uninstall entries, and keeps the Documents repository untouched. A failed replacement can roll back to the previous payload. Repair uses a cached installer copy.

## UI acceptance

Native Windows QA covered:

- first-run detection, both ChatGPT connection launches, optional-service skips, verification, and completion;
- Home shortcuts and every primary navigation route;
- Live Telemetry pause/resume and both persistent monitor options;
- Live Monitor open/close, expanded/compact, lock/unlock, hide/show controls, and reset position;
- Race Analysis search and every filter;
- Race Planning, setup-library, and Progressive Tuning empty and disabled states without seeded production data;
- Connections and Garage61 connected state without displaying the credential;
- Settings inputs, toggles, layout, opacity, save, logs, and full health test;
- navigation collapse/expand accessibility names and native minimize/maximize/restore/close behavior.

The final visual pass confirmed the charcoal title bar and icon, human-readable evidence labels, revised live-race wording, revised setup-library wording, and removal of the duplicate Diagnostics action.

## Automated acceptance

- Full .NET solution build: 0 warnings, 0 errors.
- Companion tests: 29 passed, 0 failed.
- Backend/handoff verifier: passed; 109 files and 1,945,125 bytes verified.
- Backend contracts: 17; fixtures: 14; MCP tools: 16.
- Synthetic MCP end-to-end analysis: passed with high data quality.
- Packaged Coach Engine probe: installed, running, signed-out private account state, exact runtime 0.146.0-alpha.9.2.
- Coach Engine cleanup: no scoped child-process or temporary-directory residue.
- Installer replacement test: first install and replacement both exited 0; running prior app stopped; old marker removed; all eight required release components present; staging and backup directories clean.

## Final artifact

- Installer: `artifacts\dist\iRacingCoach-0.5.0-Setup.exe`
- Checksum file: `artifacts\dist\iRacingCoach-0.5.0-Setup.exe.sha256`
- Size: 498,550,822 bytes
- SHA-256: `04a490f2cbbf55bd15a4b5f275548d32e0485a2005a523d15b31ee1f5bc7a325`

Copy the installer and checksum to another PC and run the installer normally. It replaces prior versions of the program while preserving `Documents\iRacing Coach`. ChatGPT and Garage61 must be connected once for that Windows user after the move.

The original plaintext `Desktop\garage61-key.txt` is no longer needed by the app. It was not deleted automatically because that is user-owned source data.

## Deliberate limitations

- No production race, car, setup, lap, or telemetry values are fabricated. Empty states remain empty until real iRacing data exists.
- Exact iRacing ownership entitlement is not claimed; the car list combines real recorded, installed, and setup-linked cars found on the PC.
- `.sto` files remain read-only. A recorded IBT setup remains authoritative for what was driven.
- ChatGPT coaching requires the user-managed connection and valid service availability. It cannot replace or upgrade deterministic evidence.
