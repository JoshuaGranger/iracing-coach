# iRacing Coach

[![CI](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml)

iRacing Coach is a local-first Windows race-engineering and driver-coaching application. The desktop app presents telemetry, race analysis, planning, setup workflows, and progressive tuning. The deterministic Python backend remains the source of truth for calculations, archives, evidence labels, setup safeguards, and credential handling.

## Current baseline

- Latest accepted packaged release: `0.14.2`
- Current source: `0.16.0` development
- Desktop: C#/.NET 10, WPF host, Blazor Hybrid UI
- Backend: standard-library Python 3.10+, exposed through bounded MCP/CLI contracts

The exact implementation and remaining limits are recorded in [the current development snapshot](docs/06-reality/implementation-snapshot-0.16.0-development.md). Stable `0.14.2` package evidence remains immutable in [its release snapshot](docs/06-reality/implementation-snapshot-0.14.2.md).

## Repository layout

- [`companion-app/`](companion-app/) - Windows application, coordinator, tests, and packaging tools.
- [`iracing-coach/`](iracing-coach/) - authoritative deterministic telemetry and coaching backend.
- [`contracts/`](contracts/) - checked-in API and data-contract snapshots used by the app and tests.
- [`config/`](config/) - shared repository configuration and generated-UI inputs.
- [`test-data/`](test-data/) - sanitized deterministic fixtures; never private race data.
- [`tools/`](tools/) - repository verification and fixture/contract generation scripts.
- [`docs/`](docs/) - product intent, architecture, implementation evidence, and known gaps.

Start with [the documentation index](docs/README.md) and [the contributor instructions](AGENTS.md).

## Verify the source

From the repository root on Windows:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\verify-repository.ps1

dotnet test .\companion-app\iRacingCoach.sln -c Release
dotnet build .\companion-app\iRacingCoach.sln -c Release --no-restore
```

Regenerate checked-in backend contracts or sanitized fixtures with `tools/export_contracts.py` and `tools/generate-fixtures.ps1`. CI fails when generated contracts drift from backend code.

## Private data boundary

Raw telemetry, replays, simulator-loadable setups, purchased source files, credentials, machine-local settings, archives, logs, and the private regression corpus are excluded from source control. Sanitized fixtures under [`test-data/`](test-data/) are the only race-like data intended for GitHub.
