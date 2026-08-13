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
- [ ] Exact-configuration vector maps never borrow main-course, pit, entry, exit, commitment, merge, or start/finish geometry from another layout; every unavailable layer has a truthful reason/action.
- [ ] Named Race Analysis trace layouts remain globally portable and separate from Live Telemetry layouts; Default cannot be renamed or deleted.
- [ ] Technical data renders exactly four evidence-gated categories and their full-area investigations without inventing unsupported facts.
- [ ] Race replay uses only canonically recorded participant/player/event fields, keeps one synchronized clock across map/grid/timeline/telemetry, and never infers ABS or competitor controls.
- [ ] Tire/capability observations, session-local calculated wear, learned predictions, and external references remain visibly separate; weak or out-of-domain evidence returns unavailable rather than a point estimate.
- [ ] Finalized raw IBTs are copied atomically into the portable content-addressed store, hash-verified, deduplicated, retained without automatic pruning, and excluded from source/release/support artifacts.
- [ ] Runs, green/caution exposure, fuel, tire endpoints, service, and evidence limitations render correctly.
- [ ] Missing tire readings never become estimated wear.
- [ ] Requested service is distinct from confirmed service.
- [ ] Repair/tow clocks are parallel and non-additive.
- [ ] Incident-only data never becomes a damage claim.
- [ ] Repair-affected laps/runs are excluded from clean trends and targets by default.
- [ ] Leaving with optional repair remaining badges the following run.
- [ ] Fast-repair request is distinct from confirmed use.
- [ ] Truncated/in-progress tow retains remaining time.
- [ ] The complete changed-surface matrix in `documentation/05-quality/race-analysis-overhaul-acceptance-matrix.md` is reviewed against the exact development executable before product acceptance.

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

- [ ] The representative recording and open target retain exact analysis, car, configuration, setup fingerprint, and source identities; two same-car/same-track recordings cannot be confused.
- [ ] A fixed race may supply driving evidence, but no garage candidate appears without a compatible analyzed open target.
- [ ] The highest-quality clean green run is selected automatically, manual override works, and Early/Middle/Late use each tire run's real lap IDs.
- [ ] Exact-configuration turn labels retain source, alignment confidence, and geometry/content fingerprint; telemetry zones are never promoted to official turns.
- [ ] Hover highlights the complete turn segment, keyboard selection works, and a low-confidence map can be corrected without mutating iRacing content.
- [ ] Each turn can retain multiple Early/Middle/Late Entry/Center/Exit/Whole symptoms, severity, driver confidence, priority, and note; `Good` is distinct from skipped.
- [ ] The generic note is retained as context but cannot independently authorize a setup candidate.
- [ ] Drafts autosave atomically in portable app state and never create a second canonical experiment archive.
- [ ] Original driver wording is retained and telemetry corroboration remains separate from causal inference.
- [ ] Fuel, tires, weather, track state, traffic, line, damage, and setup comparability are shown or explicitly unavailable.
- [ ] Candidate settings come only from a versioned exact-car/sim-build rules catalog; absent/locked/out-of-range/unsupported settings produce no candidate.
- [ ] Exact target values require a verified current value, adjustment step, legal range, and coupled constraints; otherwise use a manual one-step instruction.
- [ ] Deterministic validation produces the candidate whitelist before AI; invalid/offline/interrupted AI cannot replace or weaken the deterministic result.
- [ ] AI-selected candidate and evidence IDs are membership-validated and raw IBT/credential content is not sent.
- [ ] One primary setup-system change, expected effect, tradeoff, test plan, success criteria, and rollback remain visible.
- [ ] Damage/tow-affected evidence blocks a setup conclusion until a clean repaired-car run.
- [ ] Improved/unchanged/worse/inconclusive remains distinct for subjective and linked-analysis outcomes and rollback is persisted.
- [ ] Failed/no-change experiments remain searchable and suppress an equivalent candidate under comparable conditions.
- [ ] No workflow creates, modifies, or overwrites a simulator-loadable STO.
- [ ] The complete visual matrix in `documentation/05-quality/progressive-tuning-overhaul-acceptance-matrix.md` is reviewed before product acceptance.

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
  -File .\companion-app-contract\scripts\check-build-machine.ps1

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\verify-contract.ps1

python -m unittest discover -s .\iracing-coach\tests -p "test_*.py"

'{"jsonrpc":"2.0","id":1,"method":"ping","params":{}}' |
  python -X utf8 -u .\iracing-coach\skills\analyze-iracing-race\scripts\mcp_server.py
```

Expected MCP smoke response:

```json
{"jsonrpc":"2.0","id":1,"result":{}}
```
