# Companion app build-agent instructions

This workspace contains the iRacing Coach Windows companion app, its deterministic backend, and its machine-checked product/build contract.

Before changing code, read `companion-app-contract/START_HERE.md` and every file in its required reading order. Then run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\verify-contract.ps1
```

Build the application under `companion-app/`. The existing `iracing-coach/` directory is the authoritative deterministic backend. Its telemetry math, archive writes, evidence labels, damage/repair screening, setup safeguards, and credential handling must not be reimplemented in the UI. If a backend defect must be fixed, add regression tests and regenerate contracts, fixtures, and the contract manifest.

Hard boundaries:

- Never modify raw iRacing telemetry, replay, setup, or purchased source files.
- Never place Garage61, Codex, browser, or other credentials in this workspace, application settings, arguments, logs, or fixtures.
- Keep deterministic local analysis usable when Codex, Garage61, or the internet is unavailable.
- Do not display exact target telemetry, directional groove labels, damage onset, or repair-only time unless the backend evidence supports that claim.
- Use disposable backend workers for cancellable long jobs; the current MCP server is synchronous.
- Preserve unknown optional JSON fields and fail clearly on missing required fields or incompatible contract versions.
- Treat `companion-app-contract/UI_DESIGN_SYSTEM.md` and `config/theme.dark.json` as binding. Generate UI resources from the tokens; do not substitute pure-black/high-contrast generic styling.

Run the contract verifier and all new app tests before delivery. Complete `companion-app-contract/ACCEPTANCE_CHECKLIST.md`, publish a self-contained `win-x64` package, and include release checksums.
