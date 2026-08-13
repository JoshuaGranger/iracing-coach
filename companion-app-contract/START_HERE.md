# Companion app product and build contract

This permanent contract package contains the product rules, schemas, sanitized fixtures, tests, deployment requirements, and verification scripts needed to build and release the companion app. It can also be copied with the repository to a separate Windows development machine.

Version boundary: `0.14.2` is the latest accepted stable packaged release. The current tree identifies `0.16.0` development source and is producing an explicitly requested simulator-feedback installer; it is not accepted merely because it builds. The current source gates passed 255/255 .NET tests, 247/247 Python tests, 9/9 first-party JavaScript syntax checks, and a Release solution build with zero warnings and zero errors. The exact 0.16.0 source commit, contract inventory, installer hash, and lifecycle evidence remain package-stage records and are not inherited from 0.15.0.

Real-data browser evidence for this source includes the August 9 Iowa legacy Race replay (7,775 frames across five segments) at 1280x720 and 1920x1080, a complete logical 82-lap selection with bounded rendering, and an automated bounded synthetic 500-lap case. These are development checks, not a stable-package designation, real high-refresh cadence certification, or Joshua's acceptance. See `../documentation/06-reality/implementation-snapshot-0.16.0-development.md` for measurements and known limits.

## Current development round

Read `NEXT_DEVELOPMENT_PROMPT.md` first. It contains Joshua's latest approved product decisions and supersedes older frontend workflow, technology-binding, diagnostics-visibility, AI/local-responsibility, and progressive-tuning guidance where they conflict. Backend evidence rules, source protections, credentials, and executable contracts remain authoritative.

## Instruction for the build agent

Use this exact direction:

> Read `companion-app-contract/START_HERE.md` and every document it marks required. Build the Windows companion app in C#/.NET 10 with a WPF host and Blazor Hybrid views around the existing `iracing-coach` MCP/CLI backend. Implement `UI_DESIGN_SYSTEM.md` and generate resources from `config/theme.dark.json`. Treat backend telemetry calculations, archive writes, evidence labels, setup safeguards, and credential handling as authoritative. Do not reimplement them in the UI. Complete the acceptance checklist before producing a self-contained `win-x64` installer or portable package.

## Required reading order

1. `BUILD_SPEC.md` - product, screens, architecture, and delivery sequence.
2. `UI_DESIGN_SYSTEM.md` and `config/theme.dark.json` - binding technology choice, high-contrast graphite visual language, components, telemetry palette, and accessibility rules.
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
- Bounded MCP tools plus JSON CLI fallbacks; use the generated contracts and current `tools/list` rather than a prose count.
- Race selection, telemetry decoding, tires, fuel, cautions, strategy, damage/tow/repair, corner phases, groove evidence, archives, setup packages, and controlled tuning history.
- Garage61 client/auth plumbing, ready to activate after API approval and local PAT configuration.
- Product and UI requirements for all four workflows.
- A binding C#/.NET 10 WPF + Blazor Hybrid decision and a machine-readable high-contrast graphite theme with controlled vivid semantic color.
- Sanitized frontend fixtures, partial forward-compatible JSON Schemas, a contract exporter, and a contract verifier.
- The complete backend unit suite.

## What is deliberately not included

- Joshua's Garage61 token, browser cookies, Codex/ChatGPT tokens, or password state.
- Raw `.ibt`, replay, `.sto`, or purchased HTML source files.
- A redistributable Codex executable or OpenAI credential.
- Private real-race telemetry/replay captures or simulator-PC cadence evidence. The current product source contains the live SDK and bounded replay-capture paths, but this sanitized contract package does not include personal recordings or turn those paths into real-system acceptance.
- A claim that Garage61 global-visible lap search is approved. It remains disabled until Garage61 explicitly grants it.

The copied `data/` folder is optional private material. It contains derived race/setup artifacts and absolute racing-PC paths. Use the sanitized fixtures for normal frontend tests. Native telemetry queries on the development PC require synthetic test IBTs or separately supplied source recordings.

## Current 0.16 verification and delivery sequence

1. Verify the contract package and generated contracts against the frozen source.
2. Run all .NET, Python, and JavaScript gates and the warning-free Release solution build.
3. Repeat the binding browser/native matrices, real-recording checks, and high-density performance cases against the exact candidate executable.
4. Build the self-contained installer/portable artifacts only from a clean identified commit, then record immutable sizes and hashes.
5. Exercise install, prior-version replacement, rollback, running-app replacement, data preservation, uninstall, reinstall, and final uninstall against that exact installer.
6. Keep deterministic local workflows complete when Codex or Garage61 is absent; activate broader Garage61 behavior only after approval.

Run the verifier before beginning and after any backend or contract change:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\verify-contract.ps1
```

For a clean transfer instead of copying the full private workspace, run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\prepare-transfer.ps1
```

It verifies the backend, contracts, fixtures, manifest, and checksums before producing a ZIP containing only build inputs.

On the development machine, run the read-only prerequisite and contract check before scaffolding the solution:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\check-build-machine.ps1
```

It requires a .NET 10 SDK and Python 3.10 or newer. Codex app-server is reported separately and is optional for every deterministic workflow.
