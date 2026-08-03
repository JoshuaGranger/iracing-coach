# NASCAR-first open-setup tuning

Use this contract for new-week setup packages and open-session handling complaints. A "package" is a coaching plan and fingerprinted baseline record; it does not produce or edit a simulator-loadable setup file.

## Authority and limits

- Treat the IBT `CarSetup` tree and its setup fingerprint as the authority for what was driven.
- Treat HTML exports as readable snapshots and builder notes. Treat same-stem STO files as opaque, read-only artifacts identified by path, stat metadata, and SHA-256.
- Preserve source files unchanged. The driver applies a planned change in the iRacing garage and saves a candidate under a new name.
- Use driver feedback to identify tight/loose/bottoming/bump/traction symptoms and their entry, center, exit, or long-run phase.
- Use telemetry to corroborate the phase, platform response, controls, pace, and tires. Telemetry never uniquely proves which setup parameter caused the symptom.
- Do not recommend a garage change for a fixed session. Continue with driving, tire, fuel, and strategy coaching.

## 2026 Season 3 O'Reilly study baseline

The inspected local library contains exactly 16 paired NASCAR O'Reilly Toyota Supra packages: 16 HTML exports and 16 same-stem STO files. They cover AtlantaSS, Chicagoland, Coronado, Indy, Iowa, Michigan, NHMS, and Sonoma, each with separate Q and R variants.

Keep these study conclusions attached to that library and season:

- All 16 HTML headers report `newhampshire oval`, including files named for the other seven tracks. Record the filename claim and header export context separately. A conflict makes the filename-selected package provisional unless an IBT from the target track also names that exact setup artifact.
- All 16 exports report right-rear `Travel to coil bind: OFF`, even though the current manual explains when coil binding may be available. Never infer that coil bind is enabled from track length, spring rate, a donor label, or the manual alone.
- Q and R are different setup lineages. Oval Q files often use materially different crossweight, pressures, springs, or warm-up intent; R files prioritize a repeatable stint. Road-course Q/R pairs may be closer, but still require an explicit comparison. Never silently convert Q into R or vice versa.
- Builder notes are artifact-scoped evidence, not universal physics. Preserve their provenance and transfer only a stated adjustment sequence that is compatible with the current baseline.
- Suppress builder-note tuning directions while a baseline is provisional or donor-derived. They become eligible only after the exact target-track baseline is confirmed; retain the confirmation basis with the experiment.

The study found four shock/platform families:

| Family | 26S3 members | Shared transfer basis |
| --- | --- | --- |
| Smooth/high-speed oval | AtlantaSS, Indy, Michigan | stiff, controlled aero platform; reclassify the individual track's draft and transient demands |
| Continuous-load intermediate | Chicagoland | sustained lateral load and long platform dwell |
| Flat/moderate short oval | Iowa, NHMS | greater braking/traction influence and lower-to-moderate banking |
| Road/street | Coronado, Sonoma | left/right load, braking, traction, and curb/bump response |

Still distinguish every donor individually: Atlanta is the pack/draft baseline; Michigan is smooth, wide, and unrestricted; Indy is flat with discrete high-speed corners and long straights; Chicagoland is the sustained-load intermediate; Iowa is compact with moderate/progressive banking; NHMS is the flat paperclip brake-and-drive baseline; Coronado is the bumpy street/curb baseline; and Sonoma is the elevated, flowing road-course baseline.

An exact car, body/package, current season, exact layout, and Q/R setup always outranks a donor. A donor transfers hypotheses and tuning order, not assumed legality or an STO file.

## Current official O'Reilly sources

Use the [NASCAR O'Reilly Cars User Manual V1](https://s100.iracing.com/wp-content/uploads/2026/03/UM-NASCAR-Oreilly_Manual_V1.pdf) as the car-specific setup reference. Its principal dynamic platform targets are:

- about `0.25 in` (just over 6 mm) as the minimum ideal center-front splitter height, measured with `CFSRrideheight`; going below it can stall the aero platform or cause contact;
- `4.5-5.0 in` (114-127 mm) dynamic rear ride height for the maximum-downforce range.

These are O'Reilly dynamic targets, not generic NASCAR values and not substitutes for garage tech limits. The official [2026 Season 2 Initial Release Notes](https://support.iracing.com/support/solutions/articles/31000178217-2026-season-2-initial-release-notes-2026-03-09-03-) separately record the 2026 O'Reilly rules package, including a `4.25 in` ride-height minimum, `2.2 deg` rear camber, engine/drafting changes, and updated iRacing setups. Preserve the distinction between static/legal checks and dynamic aero targets, and check later release notes when the sim physics fingerprint changes.

## New-week package workflow

1. Call `catalog_iracing_setups`; preserve HTML/STO pairing, hashes, filename/header conflicts, Q/R role, and parser warnings.
2. Prefer an exact current-season package. Otherwise classify banking, peak/minimum speed, corner duty, surface/curbs, draft role, and left-only versus left/right before selecting a donor family.
3. Call `build_open_setup_package` with the exact car/layout/session intent. Include baseline provenance and fingerprint, donor reasoning if used, manual/release-note sources, tech/dynamic targets, gearing checks, and rollback identity.
4. Run a clean baseline before changing the setup. Confirm normal fuel, tires, weather, track state, line, and intended Q or R use.
5. If the symptom is transient, first call `find_iracing_telemetry_events` on the exact analyzed IBT for only the relevant brake, torque, shock, or wheel-divergence event types and lap/track zone. The tuning workflow reuses matching event caches by source SHA-256; it never runs the detector implicitly.
6. If the driver reports a problem, call `iracing_setup_history` and then `recommend_open_setup_tuning`.
7. After the A/B run, call `record_open_setup_feedback` with the candidate analysis, setup fingerprint, original symptom, outcome, and keep/rollback decision.

## Tuning order

Work from platform and legality toward balance; do not tune around a bottoming or invalid platform.

1. Verify car/package, exact baseline fingerprint, Q/R intent, gearing, and tech.
2. Establish dynamic splitter and rear-height behavior at representative pace. For the O'Reilly car, use the official targets above.
3. Stabilize the platform in manual order: front shock-spring choice, packers, then front ARB/crossweight; rear spring choice and perches, with coil-bind/truck-arm logic only when actually enabled and eligible.
4. Diagnose entry first. Separate brake application/release and driver overlap from a true balance issue before changing brake bias or chassis settings.
5. Diagnose center next. Use the smallest sourced static-balance adjustment and restore intended heights/preload before testing.
6. Diagnose exit after center. Change rear-grip geometry only when the exit complaint remains after line, steering unwind, and throttle timing are checked.
7. Tune high-speed damping for sharp bumps/curbs only after travel and bottoming limits are understood. Validate long-run balance with confirmed post-service tire readings.

Re-pass tech and recheck dynamic heights, ARB/truck-arm preload, and coupled ride-height effects after any relevant spring, packer, perch, ARB, or geometry change.

## One-change A/B and rollback

- Record a clean baseline with its exact setup fingerprint.
- Change one setup system, or one explicitly coupled builder/manual sequence required to preserve tech and heights.
- Match fuel, tire state/age, weather, track state, traffic, and intended line. Use about five laps for a transient issue and 10-15 laps for long-run balance or tire life.
- Compare the reported symptom plus the named telemetry checks. Tire wear is usable only from a confirmed fresh post-service observation.
- Use cached native events only as exact-record A/B alignment markers for raw trace comparison. A threshold event or wheel-speed proxy does not diagnose a setup cause and never overrides the driver's symptom.
- Keep the change only when the target symptom improves without triggering the named side effect. Otherwise restore the fingerprinted baseline and record `rollback` or `inconclusive` rather than stacking another change.
- Preserve unsuccessful experiments in `iracing_setup_history`; they prevent repeating a disproven path under comparable conditions.
