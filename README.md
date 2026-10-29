# iRacing Coach

[![CI](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml/badge.svg)](https://github.com/JoshuaGranger/iracing-coach/actions/workflows/ci.yml)

iRacing Coach is a race-engineering and driver-coaching system: a Rust collector and live overlay on the sim PC, a Rust service with the Python analysis worker in a Docker container on the mini PC, raw recordings on the NAS, and a TypeScript web UI for desktop, rig screen, tablet and phone. The deterministic Python backend remains the source of truth for calculations, evidence labels, setup safeguards, and credential handling.

Coach owns the website, the deployed sim-PC tray companion (`coach-collector.exe`),
and reusable parsing tools, including tracks and `.sto` files.
[iDriver](https://github.com/JoshuaGranger/iDriver) is the separate agent driving
mechanism for collecting laps and experimental data, including its driving
skills, MCP servers and plugins. iDriver consumes Coach's public APIs and
versioned parser package; Coach does not depend on iDriver.

## Current baseline

- Rewrite decided 2026-10-02: [owner decisions](docs/08-rewrite/owner-decisions-2026-10-02.md), [architecture](docs/08-rewrite/architecture.md), [workstreams](docs/08-rewrite/workstreams.md)
- Workspace version `0.19.0`
- The retired Windows desktop app (C#/.NET, `companion-app/`) was deleted in rewrite wave 4; its source lives in Git history (last present at `3ff5e87`). Its last accepted packaged release, `0.14.2`, is recorded in [its release snapshot](docs/06-reality/implementation-snapshot-0.14.2.md).

## Repository layout

- [`collector/`](collector/), [`crates/`](crates/), [`server/`](server/) - Rust collector, shared crates, and mini-PC service.
- [`web/`](web/) - TypeScript web UI.
- [`iracing-coach/`](iracing-coach/) - authoritative deterministic telemetry and coaching backend.
- [`parsers/`](parsers/) - installable offline track readers for Coach and external consumers such as iDriver; STO parsing stays in the authoritative Python backend.
- [`deploy/`](deploy/) - mini-PC image and Compose files.
- [`contracts/`](contracts/) - checked-in API and data-contract snapshots of the Python backend.
- [`config/`](config/) - shared repository configuration and generated-UI inputs.
- [`test-data/`](test-data/) - sanitized deterministic fixtures; never private race data.
- [`tools/`](tools/) - repository verification and fixture/contract generation scripts.
- [`docs/`](docs/) - product intent, architecture, implementation evidence, and known gaps.

Start with [the documentation index](docs/README.md) and [the contributor instructions](AGENTS.md).

Track extraction and telemetry position research is recorded in the
[track-registration handoff](parsers/TELEMETRY-REGISTRATION.md), including
coverage, formulas, validation, private artifact locations and remaining work.
The offline helpers are in source; app integration and release remain open.

## Verify the source

From the repository root on Windows:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\tools\check.ps1
```

`tools\check.ps1` runs `tools\verify-repository.ps1`, Rust format, lints and tests, wire and theme drift, and the web typecheck, tests and build; `-Linux` runs the WSL Ubuntu gate. Regenerate checked-in backend contracts or sanitized fixtures with `tools/export_contracts.py` and `tools/generate-fixtures.ps1`. CI fails when generated contracts drift from backend code.

## Private data boundary

Raw telemetry, replays, simulator-loadable setups, purchased source files, credentials, machine-local settings, archives, logs, and the private regression corpus are excluded from source control. Sanitized fixtures under [`test-data/`](test-data/) are the only race-like data intended for GitHub.
