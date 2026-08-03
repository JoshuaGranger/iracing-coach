# Start here: iRacing Coach companion app

This packet is intended to be copied with the entire `iRacing Coach` folder to the Windows development machine. It contains the product contract, deterministic backend, schemas, sanitized fixtures, tests, deployment rules, and verification scripts needed to build the companion app.

## Current development round

Read `NEXT_DEVELOPMENT_PROMPT.md` first. It contains Joshua's latest approved product decisions and supersedes older frontend workflow, technology-binding, diagnostics-visibility, AI/local-responsibility, and progressive-tuning guidance where they conflict. Backend evidence rules, source protections, credentials, and executable contracts remain authoritative.

## Instruction for the build agent

Use this exact direction:

> Read `companion-app-handoff/START_HERE.md` and every document it marks required. Build the Windows companion app in C#/.NET 10 with a WPF host and Blazor Hybrid views around the existing `iracing-coach` MCP/CLI backend. Implement `UI_DESIGN_SYSTEM.md` and generate resources from `config/theme.dark.json`. Treat backend telemetry calculations, archive writes, evidence labels, setup safeguards, and credential handling as authoritative. Do not reimplement them in the UI. Complete the acceptance checklist before producing a self-contained `win-x64` installer or portable package.

## Required reading order

1. `BUILD_SPEC.md` - product, screens, architecture, and delivery sequence.
2. `UI_DESIGN_SYSTEM.md` and `config/theme.dark.json` - binding technology choice, gentle dark visual language, components, telemetry palette, and accessibility rules.
3. `BACKEND_INTEGRATION.md` - MCP/CLI process contract and environment configuration.
4. `AI_ORCHESTRATION.md` - optional Codex background synthesis and authentication boundary.
5. `SECURITY_AND_TRANSFER.md` - private data, credentials, logging, packaging, and upgrade rules.
6. `ACCEPTANCE_CHECKLIST.md` - release gates and exact verification commands.
7. `IMPLEMENTATION_PLAN.md` - milestone order and suggested solution structure.
8. `contracts/compatibility.json` and `contracts/mcp-tools.v1.json` - machine-readable compatibility and tool input contracts.
9. The backend's primary source references:
   - `../iracing-coach/skills/analyze-iracing-race/references/companion-app.md`
   - `../iracing-coach/skills/analyze-iracing-race/references/data-and-analysis.md`
   - `../iracing-coach/skills/analyze-iracing-race/references/report-contract.md`
   - `../iracing-coach/skills/analyze-iracing-race/references/setup-tuning.md`
   - `../iracing-coach/skills/analyze-iracing-race/references/garage61-and-web.md`

If prose and executable behavior differ, the current MCP `tools/list`, compatibility manifest, tests, and backend source are authoritative in that order. Preserve backward compatibility with contract version 1 and tolerate new optional JSON fields.

## What is included

- A standard-library-only Python 3.10+ deterministic backend.
- Sixteen bounded MCP tools plus JSON CLI fallbacks.
- Race selection, telemetry decoding, tires, fuel, cautions, strategy, damage/tow/repair, corner phases, groove evidence, archives, setup packages, and controlled tuning history.
- Garage61 client/auth plumbing, ready to activate after API approval and local PAT configuration.
- Product and UI requirements for all four workflows.
- A binding C#/.NET 10 WPF + Blazor Hybrid decision and a machine-readable gentle charcoal dark theme.
- Sanitized frontend fixtures, partial forward-compatible JSON Schemas, a contract exporter, and a handoff verifier.
- The complete backend unit suite.

## What is deliberately not included

- Joshua's Garage61 token, browser cookies, Codex/ChatGPT tokens, or password state.
- Raw `.ibt`, replay, `.sto`, or purchased HTML source files.
- A redistributable Codex executable or OpenAI credential.
- The later IRSDK shared-memory live sidecar.
- A claim that Garage61 global-visible lap search is approved. It remains disabled until Garage61 explicitly grants it.

The copied `data/` folder is optional private material. It contains derived race/setup artifacts and absolute racing-PC paths. Use the sanitized fixtures for normal frontend tests. Native telemetry queries on the development PC require synthetic test IBTs or separately supplied source recordings.

## Delivery sequence

1. Build the process supervisor, settings, diagnostics, dashboard, and deterministic Race Card.
2. Build Race Analysis, the Interruptions panel, charts, and track/tire-age visualization.
3. Build Race Planning, starting-package creation, and progressive tuning with experiment history.
4. Add optional Codex app-server synthesis without putting it on the local-analysis critical path.
5. Add Garage61 after approval; keep offline behavior complete.
6. Add semi-live IRSDK lap blocks only after the post-race release is stable.

Run the verifier before beginning and after any backend or contract change:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\verify-handoff.ps1
```

For a clean transfer instead of copying the full private workspace, run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\prepare-transfer.ps1
```

It verifies the backend, contracts, fixtures, manifest, and checksums before producing a ZIP containing only build inputs.

On the development machine, run the read-only prerequisite and handoff check before scaffolding the solution:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\check-build-machine.ps1
```

It requires a .NET 10 SDK and Python 3.10 or newer. Codex app-server is reported separately and is optional for every deterministic workflow.
