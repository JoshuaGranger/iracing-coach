# iRacing Coach

[![CI](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml)

iRacing Coach is a local-first Windows race-engineering and driver-coaching application. The desktop app presents telemetry, race analysis, planning, setup workflows, and progressive tuning while the deterministic Python backend remains the source of truth for calculations, archives, evidence labels, setup safeguards, and credential handling.

## Current baseline

- Latest accepted packaged release: `0.14.2`
- Current source: `0.16.0` development
- Desktop: C#/.NET 10, WPF host, Blazor Hybrid UI
- Backend: standard-library Python 3.10+, exposed through bounded MCP/CLI contracts
- Current verified gates: 255 .NET tests, 247 Python tests, 9 JavaScript syntax checks, and a warning-free Release build

The exact current implementation and its remaining limits are recorded in [the 0.16.0 development snapshot](documentation/06-reality/implementation-snapshot-0.16.0-development.md). Stable `0.14.2` package evidence remains immutable in [its release snapshot](documentation/06-reality/implementation-snapshot-0.14.2.md).

## Repository layout

- [`companion-app/`](companion-app/) — Windows application, coordinator, tests, and packaging tools.
- [`iracing-coach/`](iracing-coach/) — authoritative deterministic telemetry and coaching backend.
- [`companion-app-contract/`](companion-app-contract/) — active product/build contract, schemas, sanitized fixtures, design tokens, and release gates.
- [`documentation/`](documentation/) — reviewable product intent, architecture, implementation evidence, and known gaps.

Start engineering work with [the companion-app contract](companion-app-contract/START_HERE.md). The deeper documentation reading path begins at [documentation/README.md](documentation/README.md).

## Verify the source

From the repository root on Windows:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\verify-contract.ps1

dotnet test .\companion-app\iRacingCoach.sln -c Release
dotnet build .\companion-app\iRacingCoach.sln -c Release --no-restore
```

To create a verified, credential-free build-input archive:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\prepare-transfer.ps1
```

## Private data boundary

Raw telemetry, replays, simulator-loadable setups, purchased source files, credentials, machine-local settings, archives, logs, and Joshua's private regression corpus are excluded from source control. Sanitized fixtures under `companion-app-contract/fixtures/` are the normal development and CI inputs.
