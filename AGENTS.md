# iRacing Coach contributor instructions

This repository contains the Windows application, its deterministic backend, shared contracts, sanitized test data, and product documentation.

## Product north star

Joshua's direction (September 22, 2026): **"I would rather an app that has 5 features that win me more races than an app that has 1000 features, graphics, and charts that don't help me win any more races."**

As capability increases, complexity cannot increase from the user's interface or experience. Favor a small number of complete, effective racing workflows over feature count. Before adding a screen, chart, menu or button, identify the racing decision it improves, the action it enables, and how its benefit can be checked in a later recording. Remove or consolidate tools that fail that test. Use restrained, beautiful presentation and concise racing language; remove agentic prose and generic assurances. See `docs/01-product/companion-design-principles.md` for the design and acceptance method.

## Development boundaries

Before changing code, read `docs/README.md` and run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\tools\verify-repository.ps1
```

Build the Windows application under `companion-app/`. The `iracing-coach/` directory is the authoritative deterministic backend. Its telemetry math, archive writes, evidence labels, damage/repair screening, setup safeguards, and credential handling must not be reimplemented in the UI. If a backend defect is fixed, add regression tests and regenerate affected contracts and fixtures.

Hard boundaries:

- Never modify raw iRacing telemetry, replay, setup, or purchased source files.
- Never place Garage61, Codex, browser, or other credentials in the repository, application settings, arguments, logs, or fixtures.
- Keep deterministic local analysis usable when Codex, Garage61, or the internet is unavailable.
- Do not display exact target telemetry, directional groove labels, damage onset, or repair-only time unless backend evidence supports the claim.
- Use disposable backend workers for cancellable long jobs; the current MCP server is synchronous.
- Preserve unknown optional JSON fields and fail clearly on missing required fields or incompatible contract versions.
- Treat `config/theme.dark.json` as the canonical UI token source; generate UI resources from it.

Run repository verification and affected application tests before delivery. Release work must also produce a self-contained `win-x64` package and checksums.
