# Implementation Snapshot: 0.17.0 Development Round 1

Evidence date: September 20, 2026. Status: development candidate for hands-on evaluation, not a user-accepted stable release.

## Scope and source

This round implements the connected analysis, next-event planning, and setup-library work identified in the September technical review. It starts from `414ca18d7c64f7563a158266946ff283ed32efda`, the latest `claude/crew-chief-round-1` development baseline, in the isolated `codex/development-round-1` branch. The older main checkout and existing installation are preserved. The package manifest records the exact new source commit.

## Race Analysis and runtime reliability

- The host resolves its bundled backend before considering its working directory. Launching the packaged application from an older checkout therefore does not silently select that checkout's backend.
- Analysis requests now retain the recording's native sample rate. The previous desktop requests explicitly downsampled to 20 Hz. UI rendering budgets still apply; retaining source samples does not mean drawing every sample on every frame.
- The analysis profile advances to `post-race-foundations-v16`. Incompatible UI caches trigger regeneration instead of being presented as current. If the recording is unavailable, archived analysis remains inspectable with an explicit instruction to restore the IBT; an obsolete summary card is withheld.
- Cache parsing and mapping run off the UI thread, with selection identity checked before publication. Cancelling the cache read clears its loading state.
- Analysis errors are distinct from optional-service errors. An unavailable Garage61 connection no longer turns otherwise healthy local analysis into a failure indication. Diagnostics report the actual backend tool count rather than assuming it always equals 17.
- Analyzer bundle identity is normalized across Windows and Unix line endings. The reviewed profile hash still changes when analysis source changes.

Real-data check: the isolated August 3 Iowa recording regenerated with **136,529 samples**, versus **45,510** in its older analysis. This proves the sampling change for that recording; it is not a throughput or high-refresh performance claim.

## Race Planning

- Planning can start from the available car/layout catalog without requiring a prior race. A recorded reference is optional. The empty-reference option clearly selects practice preparation or estimates.
- Optional inputs accept green fuel use, available starting fuel, and representative lap time. Their use is labeled **Estimate**. Missing measurements produce preparation guidance and an unavailable fuel decision, never a fabricated zero-stop result.
- The new local `plan_iracing_race` tool computes the requested event through the existing deterministic race-plan decision engine. The UI does not calculate stop counts or fuel range. Laps and timed events use the requested distance; timed projections disclose their pace and green-running assumptions.
- Changed event inputs immediately hide the prior result. A result finishing after a selection change is discarded. Inventory refresh preserves an intentional practice event and its setup type.
- Recorded-reference fuel projections remain qualified by source conditions. Existing history is labeled comparable runs; wider weather/fuel/driver matching is not implemented in this round.
- Plans are saved as portable event-plan artifacts. Local planning works with AI and Garage61 unavailable.

Boundary checks: 50 laps at 0.4 US gal/lap with 20 gallons produces a 49-lap conservative range and one stop. At 49 laps, the same assumptions authorize zero stops. Exactly one green lap is reserved in the per-tank range. Tests also cover changed distance, timed estimates, absent inputs, unreadable authoritative decisions, and stale asynchronous results.

## Setups and Progressive Tuning

- Setups now opens a library, with search and car, track, and season filters. Missing season remains **Unknown**.
- The library distinguishes local `.sto` files from setup values embedded in recorded telemetry. It displays the actual available fields, grouped by setup section, and supports value comparison between matching car/track entries. A recorded setup is not represented as an editable `.sto` export.
- Linked performance appears only when a setup has an explicit archive link to that recording. Matching car/track names alone cannot create an evidence link. Fresh analysis updates those links immediately.
- **Tune this setup** loads linked evidence, verifies the setup fingerprint, and retains the selected setup's context in tuning. Discovery labels such as Iowa and SDK labels such as Iowa Speedway no longer invalidate the same exact recording's map. Backend IDs and fingerprints remain intact.
- The existing starting-tune workflow remains available through **New setup**. Opening it clears any previous library-origin tuning context.
- Fixed-setup evidence still requires an appropriate open-setup target for changes. Unverified turn bounds still block corner analysis.

Real-data check: the Iowa setup displayed **84 recorded fields and one linked recording**. Its tuning handoff opened the correct map and retained the fixed-setup and turn-verification restrictions. Setup-value differences are not claims that a setup caused a pace improvement.

## Verification and packaging

| Check | Result |
| --- | --- |
| Repository verification | 1,008 backend tests passed; contracts, fixtures, security checks, and MCP end-to-end smoke passed |
| Release application test suite | 369 tests passed |
| Real-data component verification | Analysis regeneration, changed-distance recorded plan, explicit practice fuel estimate, setup library, and tuning handoff exercised in the isolated Blazor preview |
| Preview build | Zero warnings and errors |

The Windows app and browser preview share the same UI and coordinator. Native screenshot automation did not reliably target the app in this session, so browser checks are not represented as a completed native visual acceptance matrix. The deliverable is a self-contained `win-x64` development package, with installer, portable ZIP, source manifest, and SHA-256 checksums. Packaging details and final binary smoke results belong in the accompanying delivery record.

## Remaining development

The Python backend remains the production authority. Integrating the native decoder throughout production analysis, unifying the live display canvas, broader condition-matched setup A/B evaluation, and writing simulator-compatible changed `.sto` files remain separate work. Full simulator-PC responsiveness, actual SDK capture while driving, DPI/window behavior, and upgrade/uninstall acceptance remain to be evaluated against the packaged executable. This round does not declare those review findings resolved.
