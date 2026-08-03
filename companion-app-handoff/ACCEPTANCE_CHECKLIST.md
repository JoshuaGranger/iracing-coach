# Release acceptance checklist

The app is not complete until every required item passes on synthetic fixtures and then on the racing PC.

## Build and compatibility

- [ ] Self-contained signed or checksum-published Windows x64 package.
- [ ] No Visual Studio, .NET SDK, Python installation, Node, or console window required on the racing PC.
- [ ] Compatibility manifest shown in diagnostics.
- [ ] MCP `initialize`, `ping`, and `tools/list` match the checked-in contracts.
- [ ] Unknown optional JSON fields are tolerated; missing required fields fail clearly.
- [ ] Backend Python suite passes.
- [ ] Frontend unit, contract, and end-to-end smoke tests pass.

## Home and lifecycle

- [ ] Dashboard starts offline and shows backend/Codex/Garage61 status independently.
- [ ] The detected/configured iRacing install root is shown as read-only; inventory may read metadata/version files but no companion process writes, patches, or unpacks game content.
- [ ] Latest default means latest finalized metadata-identified Race, not newest file.
- [ ] Active/changing IBT is deferred with a useful message.
- [ ] Jobs remain visible across navigation and app restart.
- [ ] Duplicate write jobs for the same session/package are blocked.
- [ ] Cancelling a disposable worker terminates its process tree and does not advertise a partial artifact.

## Visual design and accessibility

- [ ] All native and Hybrid surfaces consume generated values from `config/theme.dark.json`; literal one-off colors and spacing do not drift across components.
- [ ] Default UI is gentle charcoal rather than pure black/high-contrast panels, with no pure white body text, neon glow, decorative gradients, or generic unthemed controls.
- [ ] Normal text meets 4.5:1 contrast; large text and meaningful UI boundaries meet 3:1.
- [ ] Color is never the only carrier for evidence, status, faster/slower, warning, or trace identity.
- [ ] Measured, Calculated, Coach estimate, Approximate, and Unavailable labels remain visible and accessible alongside color.
- [ ] Keyboard-only navigation, visible focus, screen-reader names, and logical focus order work on every primary screen.
- [ ] Windows reduced-motion and high-contrast preferences are honored.
- [ ] Every primary screen is reviewed at 100%, 150%, and 200% scaling and at the 1100 x 720 minimum window.
- [ ] Empty, loading, unavailable, warning, repair-confounded, long-text, and background-job states have reviewed screenshots.
- [ ] Telemetry traces use labels and line/hatch patterns as well as color; short peaks survive screen-pixel downsampling.
- [ ] Navigation remains available during work; no global spinner or modal AI wait blocks the app.
- [ ] The app verifies WebView2 availability and provides an actionable runtime/bootstrap path.

## Race Analysis

- [ ] Deterministic Race Card appears before optional AI/network enrichment.
- [ ] Oval card stays within its compact contract.
- [ ] Runs, green/caution exposure, fuel, tire endpoints, service, and evidence limitations render correctly.
- [ ] Missing tire readings never become estimated wear.
- [ ] Requested service is distinct from confirmed service.
- [ ] Repair/tow clocks are parallel and non-additive.
- [ ] Incident-only data never becomes a damage claim.
- [ ] Repair-affected laps/runs are excluded from clean trends and targets by default.
- [ ] Leaving with optional repair remaining badges the following run.
- [ ] Fast-repair request is distinct from confirmed use.
- [ ] Truncated/in-progress tow retains remaining time.

## Corner and track view

- [ ] Map and traces share a synchronized cursor.
- [ ] Tire-age slider exposes only supported phases/bounds; unsupported interpolation is not shown.
- [ ] Exact numeric target trace appears only for a usable aligned comparison.
- [ ] Groove direction appears only with calibrated inside/outside geometry.
- [ ] Repair/caution/pit/traffic samples can be inspected but are screened by default.
- [ ] No track geometry is fabricated when coordinates are unavailable.

## Race Planning

- [ ] Same-season car/exact-layout/fixed-open cache is reused.
- [ ] Historical strategy matches race distance and identifies legacy unscreened rows.
- [ ] Fuel feasibility, reserve, all-green, and caution-sensitive alternatives are distinct.
- [ ] Missing position, pit loss, rules, or future-caution evidence prevents an `optimal` claim.
- [ ] Offline planning remains useful from local history.

## Starting Tune

- [ ] Fixed session prevents garage recommendations.
- [ ] Source artifacts remain byte-identical after all workflows.
- [ ] Exact target baseline outranks donor logic.
- [ ] Q and race packages remain separate.
- [ ] Filename/header conflicts are visible and provisional.
- [ ] Package contains fingerprint, provenance, expected validation, risks, and rollback.
- [ ] No simulator-loadable STO is generated or overwritten.

## Progressive Tuning

- [ ] Original driver symptom wording is retained.
- [ ] Telemetry corroboration remains separate from causal inference.
- [ ] One setup system per controlled A/B experiment.
- [ ] Fuel, tires, weather, track state, traffic, and line comparability are shown.
- [ ] Damage/tow-affected evidence blocks a setup conclusion until a clean repaired-car run.
- [ ] Improved/worse/no-change/inconclusive and rollback are persisted.
- [ ] Failed experiments remain searchable.

## AI and authentication

- [ ] Deterministic workflows work with Codex absent, signed out, rate-limited, or cancelled.
- [ ] Codex app-server authentication uses its managed flow; no token is handled by the companion UI.
- [ ] AI output conforms to the coaching schema or is discarded without replacing local evidence.
- [ ] AI cannot invent unavailable values or silently upgrade evidence status.
- [ ] Garage61 PAT is configured only on the racing PC through DPAPI.
- [ ] Garage61 unavailable/unauthorized/offline states do not block local workflows.
- [ ] Global-visible Garage61 search remains disabled until explicitly approved.

## Performance

- [ ] Cached deterministic Race Card comfortably under one minute; target is seconds.
- [ ] Uncached ordinary analysis/planning/tuning under two minutes end-to-end.
- [ ] Starting-package research under four minutes.
- [ ] Performance diagnostics split selection/hash, decode/analysis, report persistence, optional network, and optional AI.

## Commands

From the copied workspace root:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\check-build-machine.ps1

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\verify-handoff.ps1

python -m unittest discover -s .\iracing-coach\tests -p "test_*.py"

'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}' |
  python -X utf8 -u .\iracing-coach\skills\analyze-iracing-race\scripts\mcp_server.py
```

Expected MCP smoke response:

```json
{"jsonrpc":"2.0","id":1,"result":{}}
```
