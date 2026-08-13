# Implementation plan for the companion app

This plan turns the product contract into buildable milestones. Keep every milestone runnable and tested; do not wait for Garage61 or Codex before delivering a useful local application.

## Suggested solution layout

```text
companion-app/
  iRacingCoach.sln
  src/
    iRacingCoach.App/             WPF host, Blazor views, dependency injection
    iRacingCoach.Contracts/       generated/handwritten forward-compatible DTOs
    iRacingCoach.Coordinator/     jobs, process supervision, settings, view models
    iRacingCoach.BackendClient/   MCP JSONL and CLI fallback adapters
    iRacingCoach.AI/              optional Codex app-server adapter
  tests/
    iRacingCoach.Contracts.Tests/
    iRacingCoach.BackendClient.Tests/
    iRacingCoach.Coordinator.Tests/
    iRacingCoach.App.Tests/
  packaging/
```

Names may change, but keep UI, process orchestration, contracts, deterministic backend access, and optional AI separated.

## Milestone 0: prove the contract package

1. From the workspace root, run `powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\companion-app-contract\scripts\verify-contract.ps1`.
2. Load every schema and fixture without touching the private `data/` directory.
3. Start the MCP backend, perform `initialize`, `ping`, and `tools/list`, and compare the tool list with `contracts/mcp-tools.v1.json`.
4. Run `python -X utf8 .\companion-app-contract\scripts\mcp_e2e_smoke.py` to execute a real dashboard and full analysis against the sanitized synthetic IBT.
5. Record the compatibility manifest in a diagnostics model.

Exit gate: the build machine can run the backend and tests with no racing-PC data or credentials.

## Milestone 1: coordinator and diagnostics shell

1. Create a self-contained .NET 10 C# WPF application with Blazor Hybrid views targeting `net10.0-windows10.0.17763.0` or the current supported equivalent.
2. Load `config/theme.dark.json`, generate CSS/WPF resources from it, and build a component-gallery route covering every surface, control, evidence tag, trace, loading state, and focus state before composing product screens.
3. Implement validated settings for iRacing root, archive root, packaged Python, and optional Codex executable.
4. Implement a hidden child-process supervisor with UTF-8 JSONL framing, stderr capture, timeouts, process-tree cancellation, and restart.
5. Parse both MCP response layers and normalize domain errors.
6. Add health checks and a diagnostics screen showing versions, roots, contract compatibility, and stage timings without secrets.

Exit gate: theme/contrast/scaling tests, settings, backend health, lifecycle, cancellation, and fixture-driven UI tests pass.

## Milestone 2: fast dashboard and race analysis

1. Render Home from `dashboard` immediately and select latest finalized Race by metadata.
2. Implement the background job tray, deduplication, retries, and persisted job state.
3. Render the deterministic Race Card before any optional network or AI call.
4. Add Overview, Corner Plan, Runs/Tires, Fuel/Strategy, Interruptions, Telemetry, and Evidence views.
5. Align tow, stall, service, repair countdowns, and pit-road spans without adding overlapping clocks.
6. Add repair-confounded badges and default screening from trends/targets.

Exit gate: populated, empty, repair-heavy, error, truncated-IBT, and cancellation scenarios pass end to end.

## Milestone 3: synchronized track and tire-age coaching

1. Render the provided track/phase fixture with a shared map/trace cursor.
2. Snap the tire-age slider only to backend-supported phases or bounds.
3. Show exact target traces only when comparison status is `usable`.
4. Show groove direction only when geometry is calibrated; otherwise use neutral path-movement language.
5. Shade pit, caution, traffic, tow, repair, and candidate interruption windows and screen them by default.

Exit gate: no UI interpolation or label implies evidence the backend did not provide.

## Milestone 4: planning and setup workflows

1. Build Race Planning from same-season car/exact-layout/fixed-open cache and local history.
2. Separate all-green feasibility, caution alternatives, reserves, and unproven optimality.
3. Build Starting Tune with source provenance, hashes, Q/race separation, donor limitations, risks, validation, and rollback.
4. Build Progressive Tuning with original symptom text, telemetry corroboration, one-system experiments, comparability controls, outcome capture, and retained failed experiments.
5. Block setup advice for fixed or repair/tow-confounded evidence as specified.

Exit gate: workflows remain fully useful offline and never create or overwrite a simulator-loadable STO.

## Milestone 5: optional Codex synthesis

1. Integrate Codex app-server over stdio behind an adapter and capability check.
2. Let Codex own its authentication flow; the companion app never handles an OpenAI token.
3. Send compact evidence and artifact references, not raw high-frequency telemetry.
4. Enforce `contracts/ai-coaching-output.schema.json`; discard invalid synthesis while retaining the deterministic result.
5. Persist thread mappings as convenience state, not as the racing database.

Exit gate: sign-out, cancellation, malformed AI output, rate limits, and absence of Codex never block local results.

## Milestone 6: packaging and racing-PC validation

1. Bundle a pinned Python 3.10+ runtime and the complete backend directory.
2. Verify WebView2 at installation/startup and bundle the Evergreen bootstrapper or a documented fixed-runtime alternative.
3. Publish self-contained `win-x64`, hide helper consoles, and terminate process trees on shutdown.
4. Add installer/portable upgrade and rollback tests that preserve archives and settings.
5. Run the full acceptance checklist first with sanitized fixtures, then with user-approved racing-PC recordings.
6. Publish SHA-256 checksums and a diagnostics export manifest.

Garage61 activation and the semi-live IRSDK sidecar are subsequent milestones; neither is a release blocker for the post-race companion app.
